"""Gemini backend: the model picks tools, the Toolbox runs them against SQLite.

Default chatbot backend. Needs GEMINI_API_KEY (or GOOGLE_API_KEY), e.g. in `.env`.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .prompt import SYSTEM_PROMPT
from .tools import TOOLS, Toolbox

log = logging.getLogger(__name__)

# 2026-09 기준: gemini-2.5-flash는 신규 사용자에게 404, API가 gemini-3.8-flash를 권장
DEFAULT_MODEL = "gemini-3.8-flash"
# 기본 모델이 과부하(503)·한도(429)로 계속 실패하거나 사용 불가(404)면 차례로 시도할 모델
DEFAULT_FALLBACK_MODELS = ("gemini-3.5-flash",)
UNAVAILABLE = (404,)  # 이 모델을 쓸 수 없음 → 재시도 없이 다음 모델
MAX_DISCOVERED = 3  # 설정한 모델이 모두 실패하면 키로 쓸 수 있는 모델 목록에서 추가로 시도할 수
# 채팅·도구 호출에 맞지 않는 모델 (이미지·음성·임베딩 등)
_NOT_CHAT = ("image", "tts", "audio", "live", "embedding", "embed", "veo", "imagen", "robotics", "computer-use", "aqa", "gemma", "learnlm", "native")
RETRYABLE = (429, 500, 503, 504)
RETRY_DELAYS = (1.0, 3.0)  # 같은 모델 재시도 간격(초) → 그다음 대체 모델
MAX_TOOL_ROUNDS = 8


@dataclass
class GeminiTurn:
    text: str
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    finish_reason: str | None = None


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


def _api_error_text(exc: Any) -> str:
    hint = _HINTS.get(exc.code, "Gemini 서버 오류" if (exc.code or 0) >= 500 else "")
    message = (exc.message or "").split(". ")[0]  # 긴 안내문은 첫 문장만
    return f"{exc.code} {exc.status or ''}: {message}" + (f" — {hint}" if hint else "")


def describe_error(exc: Exception) -> str:
    """Short Korean description of a Gemini failure for the user (no key values)."""
    try:
        from google.genai import errors
    except ImportError:  # pragma: no cover
        errors = None
    if isinstance(exc, GeminiUnavailable):
        per_model = " / ".join(
            f"{m}: {_api_error_text(e) if errors and isinstance(e, errors.APIError) else e}" for m, e in exc.failures
        )
        return f"Gemini 모델을 모두 쓸 수 없습니다 — {per_model}"
    if errors is not None and isinstance(exc, errors.APIError):
        return f"Gemini API 오류 {_api_error_text(exc)}"
    return f"Gemini 처리 중 오류 ({type(exc).__name__}): {exc}"


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
    ):
        types = _types()
        self.client = client
        self.toolbox = toolbox
        self.model = model
        self.fallback_models = [m for m in fallback_models if m and m != model]
        self.on_tool_call = on_tool_call
        self.sleep = sleep
        self.last_model: str | None = None  # 마지막 응답을 만든 모델 (대체 모델로 바뀌었는지 확인용)
        self.discover = True  # 설정한 모델이 모두 실패하면 키로 쓸 수 있는 모델 목록에서 찾아 시도
        self._discovered: list[str] | None = None
        self.contents: list[Any] = []
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=function_declarations())],
            # 도구는 이 루프에서 직접 실행한다 (SDK 자동 호출 끔)
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _generate(self) -> Any:
        """generate_content with retries on overload/quota, then the fallback models in order."""
        from google.genai import errors

        failures: list[tuple[str, Exception]] = []
        configured = [self.model, *self.fallback_models]
        for model in configured:
            for attempt in range(len(RETRY_DELAYS) + 1):
                try:
                    response = self.client.models.generate_content(model=model, contents=self.contents, config=self.config)
                except errors.APIError as exc:
                    if exc.code in UNAVAILABLE:
                        failures.append((model, exc))
                        log.warning("%s: %s %s, trying next model", model, exc.code, exc.status)
                        break
                    if exc.code not in RETRYABLE:
                        raise
                    if attempt == len(RETRY_DELAYS):
                        failures.append((model, exc))
                    else:
                        log.warning("%s: %s %s, retrying in %.0fs", model, exc.code, exc.status, RETRY_DELAYS[attempt])
                        self.sleep(RETRY_DELAYS[attempt])
                    continue
                if model != self.model:
                    log.warning("answered by fallback model %s", model)
                self.last_model = model
                return response
        # 설정한 모델이 모두 혼잡·사용 불가 → 이 키로 쓸 수 있는 다른 모델을 한 번씩
        for model in self._discover(set(configured)):
            try:
                response = self.client.models.generate_content(model=model, contents=self.contents, config=self.config)
            except errors.APIError as exc:
                if exc.code not in RETRYABLE + UNAVAILABLE:
                    raise
                failures.append((model, exc))
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
        """One user turn. On any error the history is rolled back so the next question starts clean."""
        checkpoint = len(self.contents)
        try:
            return self._ask(question)
        except Exception:
            del self.contents[checkpoint:]
            raise

    def _ask(self, question: str) -> GeminiTurn:
        types = _types()
        self.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))
        calls: list[tuple[str, dict[str, Any]]] = []
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._generate()
            candidate = response.candidates[0] if response.candidates else None
            finish = str(candidate.finish_reason.value if candidate and candidate.finish_reason else None)
            if candidate is None or candidate.content is None:
                blocked = response.prompt_feedback.block_reason if response.prompt_feedback else None
                return GeminiTurn(f"응답을 받지 못했습니다{f' ({blocked})' if blocked else ''}. 질문을 바꿔 주세요.", calls, finish)
            self.contents.append(candidate.content)

            function_calls = response.function_calls or []
            if not function_calls:
                text = "".join(p.text for p in candidate.content.parts or [] if p.text and not p.thought).strip()
                if finish == "MAX_TOKENS":
                    text += "\n\n(답변이 길이 제한으로 잘렸습니다)"
                return GeminiTurn(text, calls, finish)

            parts = []
            for fc in function_calls:
                args = dict(fc.args or {})
                calls.append((fc.name, args))
                if self.on_tool_call:
                    self.on_tool_call(fc.name, args)
                content, is_error = self.toolbox.run(fc.name, args)
                log.info("tool %s(%s) → %d chars%s", fc.name, args, len(content), " (error)" if is_error else "")
                payload = json.loads(content)
                part = types.Part.from_function_response(
                    name=fc.name, response={"error": payload["error"]} if is_error else {"result": payload}
                )
                if fc.id:
                    part.function_response.id = fc.id
                parts.append(part)
            # 병렬 호출 결과는 한 번에 돌려준다
            self.contents.append(types.Content(role="user", parts=parts))
        return GeminiTurn("도구 호출이 너무 많아 중단했습니다. 질문을 좁혀 주세요.", calls, "tool_limit")
