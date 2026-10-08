"""Gemini backend: the model picks tools, the Toolbox runs them against SQLite.

Default chatbot backend. Needs GEMINI_API_KEY (or GOOGLE_API_KEY), e.g. in `.env`.
"""

from __future__ import annotations

import json
import logging
import time
import itertools
from collections import Counter
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .numbers import unsupported_numbers
from .prompt import SYSTEM_PROMPT
from .tools import TOOLS, Toolbox, model_view

log = logging.getLogger(__name__)

# 2026-10: 응답 속도 때문에 lite가 기본. flash 계열은 생각하는 시간이 길고(한 단어 답에 8~17초) 무료 등급에서 혼잡(503)이
# 잦아 질문 하나에 수십 초가 걸렸고, lite는 같은 질문에 5~6초
DEFAULT_MODEL = "gemini-3.5-flash-lite"
# 기본 모델이 과부하(503)·한도(429)로 계속 실패하거나 사용 불가(404)면 차례로 시도할 모델
# 무료 한도는 모델마다 따로라 여러 개를 차례로 쓴다 (--list-models로 확인한 이 키의 모델, 2026-09-30)
DEFAULT_FALLBACK_MODELS = ("gemini-3.1-flash-lite", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash")
UNAVAILABLE = (404,)  # 이 모델을 쓸 수 없음 → 재시도 없이 다음 모델
MAX_DISCOVERED = 3  # 설정한 모델이 모두 실패하면 키로 쓸 수 있는 모델 목록에서 추가로 시도할 수
# 채팅·도구 호출에 맞지 않는 모델 (이미지·음성·임베딩 등)
_NOT_CHAT = (
    "image", "tts", "audio", "live", "embedding", "embed", "veo", "imagen", "robotics", "computer-use", "aqa", "gemma",
    "learnlm", "native", "transcribe",
)  # fmt: skip
OVERLOAD_COOLDOWN = 120.0  # 재시도해도 혼잡(5xx)이던 모델은 잠시 건너뜀 (초)
QUOTA_RESET_TZ = ZoneInfo("America/Los_Angeles")  # Gemini API 하루 한도는 태평양 시간 자정에 초기화
KST = timezone(timedelta(hours=9))
# 모델 → 이 시각(epoch 초)까지 건너뜀. 한도 소진·사용 불가 모델을 도구 호출마다 다시 부르지 않도록 프로세스 전체가 공유한다
_COOLDOWN: dict[str, float] = {}


class CoolingDown(Exception):
    """A model skipped because it recently ran out of quota or was unavailable."""


class AnswerTimeout(Exception):
    """The question used up ANSWER_DEADLINE (retries, fallbacks and tool rounds together)."""


def _cooldown_until(exc: Any, now: float) -> float:
    if exc.code in UNAVAILABLE:
        return now + 24 * 3600
    if exc.code == 429:
        delay, daily = quota_info(exc)
        if daily:
            local = datetime.fromtimestamp(now, QUOTA_RESET_TZ)
            return (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return now + max(delay or 60.0, 60.0)
    return now + OVERLOAD_COOLDOWN
RETRYABLE = (429, 500, 503, 504)
RETRY_DELAYS = (1.0, 3.0)  # 같은 모델 재시도 간격(초) → 그다음 대체 모델
MID_STREAM_RETRIES = 1  # 답이 흘러나오던 중 끊기면 그 단계를 다시 받는 횟수 (그래도 끊기면 혼잡 안내)
MAX_QUOTA_WAIT = 15.0  # 429(분당 한도)에서 구글이 알려 준 대기 시간이 이 이하면 한 번 기다렸다 재시도
MAX_TOOL_ROUNDS = 10  # 이만큼 도구를 부른 뒤에는 도구를 끄고 지금까지의 결과로 답하게 한다
MAX_EVIDENCE = 4  # GeminiTurn.evidence 줄 수
# 한 질문의 전체 대기 상한(초): 재시도·대체 모델·도구 단계를 모두 합쳐. 넘으면 그 질문은 실패(화면에는 혼잡 안내).
# 요청마다 남은 시간을 타임아웃으로 걸어, 응답이 늦은 요청도 이 안에서 끊는다
ANSWER_DEADLINE = 120.0
MIN_REQUEST_TIMEOUT = 10.0  # 구글이 받는 최소 요청 타임아웃(초): 더 짧으면 400 "deadline is too short"


class Reset:
    """Streamed text so far was a preamble to tool calls, not the answer: clear it (ask_stream event)."""


RESET = Reset()


@dataclass
class ToolCall:
    """The model is calling a tool now (ask_stream event, before the tool runs)."""

    name: str
    args: dict[str, Any]


@dataclass
class Recheck:
    """The answer had figures no tool result backs up; a rewrite follows (ask_stream event, after RESET)."""

    numbers: list[str]


@dataclass
class Thought:
    """What the model thought before answering (ask_stream event, only when asked for with thoughts=True)."""

    text: str


@dataclass
class ToolResult:
    """A tool just ran (ask_stream event, after its ToolCall): a short preview of what the model receives."""

    name: str
    ok: bool
    preview: str


@dataclass
class Escalate:
    """The question moved to a heavier model mid-turn (ask_stream event); the tool results so far carry over."""

    model: str
    reason: str


TRACE_PREVIEW = 600  # ToolResult.preview 길이 (관리자 생각 표시용)


def _preview(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    return text if len(text) <= TRACE_PREVIEW else text[:TRACE_PREVIEW] + "…"


# 답에 도구 결과에 없는 수치가 있을 때 한 번 다시 쓰게 하는 요청
RECHECK_REQUEST = (
    "방금 답에 쓴 수치 중 {numbers}는 이번 도구 결과에 없습니다. 도구 결과에 있는 값만 써서 답 전체를 다시 쓰세요. "
    "결과에 없는 값은 빼고, 결과에 있는 값은 미수집이라고 하지 말고 그대로 쓰세요. 다시 쓴다는 말은 하지 마세요."
)


def _started(stream: Any) -> Iterator[Any]:
    """Pull the first chunk now, so quota/overload errors surface inside the retry loop."""
    it = iter(stream)
    first = next(it, None)
    return itertools.chain([] if first is None else [first], it)


def _drain(gen: Generator[Any, None, Any]) -> Any:
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


@dataclass
class GeminiTurn:
    text: str
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    finish_reason: str | None = None
    evidence: list[str] = field(default_factory=list)  # 도구 결과로 만든 근거 줄 (답 본문에는 넣지 않는다)


def unavailable_reason() -> str | None:
    """Why the Gemini backend can't run here (None = ready). Checked before choosing a backend."""
    import os

    try:
        import google.genai  # noqa: F401
    except ImportError:
        return 'google-genai가 설치되지 않았습니다 (pip install -e ".[web]")'
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        return "GEMINI_API_KEY가 없습니다 (.env에 GEMINI_API_KEY=... 추가)"
    return None


def choose_backend(requested: str) -> tuple[str, str | None]:
    """(실제 백엔드, 안내 문구). Gemini를 쓸 수 없으면 규칙 기반으로 바꾸고 이유를 돌려준다."""
    if requested == "gemini" and (reason := unavailable_reason()):
        return "rules", f"Gemini를 쓸 수 없어 규칙 기반으로 실행합니다: {reason}"
    return requested, None


class GeminiUnavailable(Exception):
    """Every configured model failed with a retryable or 'model unavailable' error."""

    def __init__(self, failures: list[tuple[str, Exception]]):
        self.failures = failures
        super().__init__("; ".join(f"{m}: {e}" for m, e in failures))


_HINTS = {
    400: "요청 형식 또는 API 키 문제",
    401: "API 키 인증 실패",
    403: "API 키 권한 없음 (키 제한·결제 설정 확인)",
    404: "이 키로 쓸 수 없는 모델 (.env의 GEMINI_MODEL로 다른 모델 지정)",
    429: "사용량 한도 초과 (잠시 후 다시 시도)",
    503: "구글 서버 혼잡 (잠시 후 다시 시도)",
}


def quota_info(exc: Any) -> tuple[float | None, bool]:
    """(구글이 알려 준 재시도 대기 초, 일일 한도 여부) from a 429 error body."""
    import re

    text = json.dumps(getattr(exc, "details", None) or {}, ensure_ascii=False)
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', text)
    return (float(m.group(1)) if m else None), ("PerDay" in text)


def _api_error_text(exc: Any) -> str:
    hint = _HINTS.get(exc.code, "Gemini 서버 오류" if (exc.code or 0) >= 500 else "")
    if exc.code == 429:
        delay, daily = quota_info(exc)
        if daily:
            hint = "오늘 무료 사용량 소진 (내일 다시 시도하거나 .env의 GEMINI_MODEL로 다른 모델 지정)"
        elif delay is not None:
            hint = f"분당 사용량 한도 초과 (약 {delay:.0f}초 후 다시 시도)"
    message = (exc.message or "").split(". ")[0]  # 긴 안내문은 첫 문장만
    return f"{exc.code} {exc.status or ''}: {message}" + (f" — {hint}" if hint else "")


def describe_error(exc: Exception) -> str:
    """Short Korean description of a Gemini failure for the user (no key values)."""
    try:
        from google.genai import errors
    except ImportError:  # pragma: no cover
        errors = None
    if isinstance(exc, AnswerTimeout):
        return f"답변 대기 시간 상한({ANSWER_DEADLINE:.0f}초) 초과 — {exc}"
    if isinstance(exc, GeminiUnavailable):
        per_model = " / ".join(
            f"{m}: {_api_error_text(e) if errors and isinstance(e, errors.APIError) else e}" for m, e in exc.failures
        )
        return f"Gemini 모델을 모두 쓸 수 없습니다 — {per_model}"
    if errors is not None and isinstance(exc, errors.APIError):
        return f"Gemini API 오류 {_api_error_text(exc)}"
    return f"모델 처리 중 오류 ({type(exc).__name__}): {exc}"


def available_models(client: Any) -> list[str]:
    """Text chat models this API key can call, best candidates first (asks the API, 1 request)."""
    names = []
    for m in client.models.list():
        name = (m.name or "").removeprefix("models/")
        actions = m.supported_actions or []
        if "gemini" in name and "generateContent" in actions and not any(w in name for w in _NOT_CHAT):
            names.append(name)
    return sorted(set(names), key=_model_preference)


def _model_preference(name: str) -> tuple:
    """Newest version first; flash before flash-lite before pro; stable before preview/exp."""
    import re

    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    version = float(m.group(1)) if m else 0.0
    tier = 0 if "flash" in name and "lite" not in name else 1 if "lite" in name else 2
    unstable = any(w in name for w in ("preview", "exp", "latest"))
    return (unstable, -version, tier, name)


def models_from_env(model: str | None = None) -> tuple[str, list[str]]:
    """(기본 모델, 대체 모델들). 인자 > .env의 GEMINI_MODEL / GEMINI_FALLBACK_MODELS > 기본값."""
    import os

    primary = model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL
    raw = os.environ.get("GEMINI_FALLBACK_MODELS")
    fallbacks = [m.strip() for m in raw.split(",")] if raw is not None else list(DEFAULT_FALLBACK_MODELS)
    return primary, [m for m in fallbacks if m]


def _types():
    from google.genai import types  # 선택 의존성: gemini 백엔드를 쓸 때만 필요

    return types


def function_declarations() -> list[Any]:
    types = _types()
    return [
        types.FunctionDeclaration(
            name=t["name"], description=t["description"], parameters_json_schema=t["input_schema"]
        )
        for t in TOOLS
    ]


class GeminiChat:
    """Multi-turn chat with manual function calling.

    The model's `Content` is appended to the history exactly as returned, so thought
    signatures that Gemini 3 models attach to function calls are sent back unchanged.
    """

    def __init__(
        self,
        client: Any,
        toolbox: Toolbox,
        *,
        model: str = DEFAULT_MODEL,
        fallback_models: tuple[str, ...] | list[str] = DEFAULT_FALLBACK_MODELS,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ):
        types = _types()
        self.client = client
        self.toolbox = toolbox
        self.model = model
        self.fallback_models = [m for m in fallback_models if m and m != model]
        self.on_tool_call = on_tool_call
        self.sleep = sleep
        self.now = now
        self.last_model: str | None = None  # 마지막 응답을 만든 모델 (대체 모델로 바뀌었는지 확인용)
        self.tokens: Counter[str] = Counter()  # 입력·출력·생각 토큰 합 (비용 비교용)
        self.deadline = ANSWER_DEADLINE  # 한 질문의 전체 대기 상한(초)
        self.thoughts = False  # 이번 질문의 생각을 Thought 이벤트로 (ask_stream이 정한다)
        self._escalate: str | None = None  # 두 번째 도구부터 이 모델이 이어받는다 (ask_stream이 정한다)
        self._ends_at = float("inf")  # 이번 질문을 끝내야 하는 시각 (ask_stream이 정한다)
        self.discover = True  # 설정한 모델이 모두 실패하면 키로 쓸 수 있는 모델 목록에서 찾아 시도
        self._discovered: list[str] | None = None
        self.contents: list[Any] = []
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=function_declarations())],
            # 도구는 이 루프에서 직접 실행한다 (SDK 자동 호출 끔)
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        # 도구 호출 한도에 닿았을 때: 같은 도구 정의를 두되 호출은 막아, 받은 결과만으로 답을 쓰게 한다
        self.final_config = self.config.model_copy(
            update={"tool_config": types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="NONE"))}
        )

    def _left(self) -> float:
        """Seconds left for this question."""
        return self._ends_at - self.now()

    def _open(self, model: str, config: Any) -> Iterator[Any]:
        left = self._left()
        if left < MIN_REQUEST_TIMEOUT:  # 남은 시간으로는 요청을 보낼 수도 없다
            raise AnswerTimeout(f"{model} 요청 전")
        config = config or self.config
        if left != float("inf"):  # 응답이 늦어도 남은 시간 안에서 끊는다 (밀리초)
            config = config.model_copy(update={"http_options": _types().HttpOptions(timeout=int(left * 1000))})
        if self.thoughts:  # 생각 요약을 함께 받는다 (생각하지 않는 모델은 빈 채로 온다)
            config = config.model_copy(update={"thinking_config": _types().ThinkingConfig(include_thoughts=True)})
        import httpx

        try:
            return _started(self.client.models.generate_content_stream(model=model, contents=self.contents, config=config))
        except httpx.TimeoutException as exc:
            raise AnswerTimeout(f"{model} 응답 없음") from exc

    def _generate(self, config: Any = None) -> Iterator[Any]:
        """A response stream (chunks), with retries on overload/quota, then the fallback models in order."""
        from google.genai import errors

        failures: list[tuple[str, Exception]] = []
        configured = [self.model, *self.fallback_models]

        def fail(model: str, exc: Any) -> None:
            failures.append((model, exc))
            _COOLDOWN[model] = _cooldown_until(exc, self.now())

        def cooling(model: str) -> bool:
            until = _COOLDOWN.get(model, 0.0)
            if until <= self.now():
                return False
            at = datetime.fromtimestamp(until, KST).strftime("%m-%d %H:%M")
            failures.append((model, CoolingDown(f"최근 한도 소진·혼잡 — {at} KST까지 건너뜀")))
            return True

        for model in configured:
            if cooling(model):
                continue
            for attempt in range(len(RETRY_DELAYS) + 1):
                try:
                    response = self._open(model, config)
                except errors.APIError as exc:
                    if exc.code in UNAVAILABLE:
                        fail(model, exc)
                        log.warning("%s: %s %s, trying next model", model, exc.code, exc.status)
                        break
                    if exc.code not in RETRYABLE:
                        raise
                    wait = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else None
                    if exc.code == 429:
                        # 한도: 같은 모델을 곧바로 다시 부르면 한도만 더 쓴다. 구글이 알려 준 대기 시간이 짧을 때만 한 번 기다림
                        delay, daily = quota_info(exc)
                        wait = delay if (attempt == 0 and not daily and delay is not None and delay <= MAX_QUOTA_WAIT) else None
                    if wait is None:
                        fail(model, exc)
                        log.warning("%s: %s %s, trying next model", model, exc.code, exc.status)
                        break
                    if wait >= self._left():  # 기다리면 상한을 넘는다
                        fail(model, exc)
                        raise AnswerTimeout(f"{model} 재시도 대기 중")
                    log.warning("%s: %s %s, retrying in %.0fs", model, exc.code, exc.status, wait)
                    self.sleep(wait)
                    continue
                if model != self.model:
                    log.warning("answered by fallback model %s", model)
                self.last_model = model
                return response
        # 설정한 모델이 모두 혼잡·사용 불가 → 이 키로 쓸 수 있는 다른 모델을 한 번씩
        for model in self._discover(set(configured)):
            if cooling(model):
                continue
            try:
                response = self._open(model, config)
            except errors.APIError as exc:
                if exc.code not in RETRYABLE + UNAVAILABLE:
                    raise
                fail(model, exc)
                continue
            log.warning("answered by discovered model %s", model)
            self.last_model = model
            return response
        raise GeminiUnavailable(failures)

    def _discover(self, tried: set[str]) -> list[str]:
        if not self.discover:
            return []
        if self._discovered is None:
            try:
                self._discovered = available_models(self.client)
            except Exception as exc:  # 목록 조회 실패는 무시 (원래 오류를 보여 준다)
                log.warning("could not list models: %s", exc)
                self._discovered = []
        return [m for m in self._discovered if m not in tried][:MAX_DISCOVERED]

    def ask(self, question: str) -> GeminiTurn:
        """One user turn, without streaming."""
        return _drain(self.ask_stream(question))

    def ask_stream(
        self, question: str, *, model: str | None = None, escalate_to: str | None = None, thoughts: bool = False,
        deadline: float | None = None,
    ) -> Generator[str | Reset | ToolCall | Recheck, None, GeminiTurn]:
        """One user turn: yields answer text as it arrives (RESET = drop what was yielded so far,
        ToolCall = a tool is about to run, Recheck = figures are being rewritten) and
        returns the GeminiTurn. On an error or when the caller stops early (closes the generator), the
        history is rolled back so the next question starts clean.

        For this question only: `model` answers instead of the usual one, `escalate_to` takes over from the second
        tool call on (Escalate event), `thoughts` adds Thought events, `deadline` replaces the time cap."""
        self._drop_tool_history()
        self._ends_at = self.now() + (deadline or self.deadline)
        checkpoint, usual = len(self.contents), self.model
        self.model, self._escalate, self.thoughts = model or usual, escalate_to, thoughts
        try:
            return (yield from self._ask(question))
        except BaseException:  # GeneratorExit = 사용자가 정지 → 그 질문은 기록에 남기지 않는다
            del self.contents[checkpoint:]
            raise
        finally:
            self.model, self._escalate, self.thoughts = usual, None, False

    def remember(self, question: str, answer: str) -> None:
        """Add a question answered without the model (answer cache), so follow-ups keep the context."""
        types = _types()
        self.contents += [
            types.Content(role="user", parts=[types.Part.from_text(text=question)]),
            types.Content(role="model", parts=[types.Part.from_text(text=answer)]),
        ]

    def _drop_tool_history(self) -> None:
        """Earlier turns keep only the question and the answer text. Their tool calls and (large JSON)
        results would otherwise be resent on every model call, so each follow-up costs more quota."""
        types = _types()
        kept = []
        for content in self.contents:
            parts = [p for p in content.parts or [] if p.text and not p.thought]
            if parts:
                kept.append(types.Content(role=content.role, parts=parts))
        self.contents = kept

    def _round(self, config: Any = None) -> Generator[str | Reset, None, tuple[list[Any], str, Any]]:
        """One model call: yields its text as it arrives and returns (parts, finish reason, block reason).
        The chunks' parts are kept as they came, to be recorded as one model turn (signatures intact).

        A stream that breaks after it started (5xx/429, dropped connection) is asked again once — the retry
        and fallback in `_generate` only cover opening it. Text already shown is cleared with RESET."""
        import httpx
        from google.genai import errors

        for attempt in range(MID_STREAM_RETRIES + 1):
            received: list[Any] = []
            finish, blocked, started, shown, usage = "None", None, False, False, None
            try:
                for chunk in self._generate(config):
                    started = True
                    usage = chunk.usage_metadata or usage  # 마지막 조각에 이 요청의 합계가 온다
                    if chunk.prompt_feedback and chunk.prompt_feedback.block_reason:
                        blocked = chunk.prompt_feedback.block_reason
                    candidate = chunk.candidates[0] if chunk.candidates else None
                    if candidate is None:
                        continue
                    if candidate.finish_reason:
                        finish = str(candidate.finish_reason.value)
                    for part in (candidate.content.parts if candidate.content else None) or []:
                        received.append(part)
                        if part.text and not part.thought:
                            shown = True
                            yield part.text
                        elif part.text and self.thoughts:
                            yield Thought(part.text)
                if usage is not None:
                    self.tokens.update(input=usage.prompt_token_count or 0, output=usage.candidates_token_count or 0)
                    if usage.thoughts_token_count:  # 생각 토큰은 출력 단가로 청구되지만 candidates에 안 들어간다
                        self.tokens.update(reasoning=usage.thoughts_token_count)
                return received, finish, blocked
            except (errors.APIError, httpx.TransportError) as exc:
                code = getattr(exc, "code", None)
                if not started or attempt == MID_STREAM_RETRIES or (code is not None and code not in RETRYABLE):
                    raise
                if code is not None:  # 혼잡·한도로 끊긴 모델은 잠시 건너뛰어 다시 받을 때는 다음 모델로
                    _COOLDOWN[self.last_model] = _cooldown_until(exc, self.now())
                log.warning("%s: stream broke mid-answer (%s), asking again", self.last_model, describe_error(exc))
                if shown:
                    yield RESET
        raise AssertionError("unreachable")

    def _checked(
        self, question: str, text: str, finish: str, known: list[Any]
    ) -> Generator[str | Reset | Recheck, None, tuple[str, str]]:
        """If the answer has figures no tool result (or earlier answer) backs up, ask once for a rewrite
        without tools. The flagged answer and the request are then dropped from the history."""
        missing = unsupported_numbers(text, known, question)
        if not missing:
            return text, finish
        if self._left() <= 0:  # 다시 쓸 시간이 없으면 처음 답을 그대로 둔다
            log.warning("numbers not in tool results %s — no time left for a rewrite", missing)
            return text, finish
        log.warning("numbers not in tool results %s — asking for one rewrite", missing)
        yield RESET
        yield Recheck(missing)
        types = _types()
        flagged = len(self.contents) - 1
        request = RECHECK_REQUEST.format(numbers=", ".join(missing))
        self.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=request)]))
        received, new_finish, _ = yield from self._round(self.final_config)
        new_text = "".join(p.text for p in received if p.text and not p.thought).strip()
        if not new_text:  # 다시 쓰기가 비면 처음 답을 그대로 둔다
            del self.contents[flagged + 1 :]
            yield RESET
            yield text
            return text, finish
        self.contents[flagged:] = [types.Content(role="model", parts=received)]
        if still := unsupported_numbers(new_text, known, question):
            log.warning("rewrite still has numbers not in tool results %s", still)
        return new_text, new_finish

    def _ask(self, question: str) -> Generator[str | Reset | ToolCall | Recheck, None, GeminiTurn]:
        types = _types()
        # 앞선 대화의 답에 나온 수치는 이번 답에 다시 써도 된다 (수치 검증용)
        known: list[Any] = [p.text for c in self.contents for p in c.parts or [] if p.text]
        self.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))
        calls: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()  # 이번 질문에서 이미 실행한 (도구, 인자)
        evidence: list[str] = []  # 모델이 아니라 도구 결과로 만든 근거 줄
        results: list[Any] = []  # 이번 질문의 도구 결과 (수치 검증용)
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            final = round_no == MAX_TOOL_ROUNDS
            if final:
                log.warning("tool round limit (%d) reached, asking for an answer without tools", MAX_TOOL_ROUNDS)
            received, finish, blocked = yield from self._round(self.final_config if final else None)
            if not received:
                note = f"응답을 받지 못했습니다{f' ({blocked})' if blocked else ''}. 질문을 바꿔 주세요."
                yield note
                return GeminiTurn(note, calls, finish)
            self.contents.append(types.Content(role="model", parts=received))

            function_calls = [p.function_call for p in received if p.function_call]
            if function_calls and not final and self._escalate and len(calls) + len(function_calls) > 1:
                # 두 번째 도구부터는 더 무거운 모델이 이어받는다: 이번 응답은 버리고, 받은 도구 결과는 그대로 넘긴다
                self.contents.pop()
                self.model, self._escalate = self._escalate, None
                if any(p.text and not p.thought for p in received):
                    yield RESET
                yield Escalate(self.model, "두 번째 도구 호출")
                continue
            if not function_calls or final:
                text = "".join(p.text for p in received if p.text and not p.thought).strip()
                if not text:  # 도구를 끈 마지막 요청에서도 글이 없으면
                    note = "답을 만들지 못했습니다. 질문을 좀 더 구체적으로 해 주세요."
                    yield note
                    return GeminiTurn(note, calls, "tool_limit")
                if results:  # 도구로 조회한 답만 검증한다 (인사·범위 밖 질문은 대조할 결과가 없음)
                    text, finish = yield from self._checked(question, text, finish, [*results, *known])
                if finish == "MAX_TOKENS":
                    yield "\n\n(답변이 길이 제한으로 잘렸습니다)"
                    text += "\n\n(답변이 길이 제한으로 잘렸습니다)"
                return GeminiTurn(text, calls, "tool_limit" if final and function_calls else finish, evidence[:MAX_EVIDENCE])
            if any(p.text and not p.thought for p in received):  # 도구를 부르기 전에 쓴 글("찾아볼게요")은 답이 아니다
                yield RESET

            parts = []
            for fc in function_calls:
                args = dict(fc.args or {})
                key = json.dumps([fc.name, args], sort_keys=True, ensure_ascii=False)
                if key in seen:
                    # 같은 호출 반복 → 실행하지 않고 앞 결과를 쓰라고 알린다 (반복 루프 방지)
                    log.warning("tool %s(%s) repeated, not executed", fc.name, args)
                    payload: dict[str, Any] = {"result": {"note": "같은 도구를 같은 인자로 이미 호출했습니다. 앞서 받은 결과로 답하세요."}}
                else:
                    seen.add(key)
                    calls.append((fc.name, args))
                    yield ToolCall(fc.name, args)
                    if self.on_tool_call:
                        self.on_tool_call(fc.name, args)
                    content, is_error = self.toolbox.run(fc.name, args)
                    result = json.loads(content)
                    if is_error:
                        log.warning("tool %s(%s) → error: %s", fc.name, args, result.get("error"))
                        payload = {"error": result["error"]}
                    else:
                        log.info("tool %s(%s) → %d chars", fc.name, args, len(content))
                        payload = {"result": model_view(result)}  # 비율은 %, 소수는 반올림해 모델이 그대로 쓰게
                        results.append(result)
                        line = self.toolbox.evidence(fc.name, result)
                        if line and line not in evidence:
                            evidence.append(line)
                    yield ToolResult(fc.name, not is_error, _preview(payload.get("result", payload)))
                part = types.Part.from_function_response(name=fc.name, response=payload)
                if fc.id:
                    part.function_response.id = fc.id
                parts.append(part)
            # 병렬 호출 결과는 한 번에 돌려준다
            self.contents.append(types.Content(role="user", parts=parts))
        raise AssertionError("unreachable")
