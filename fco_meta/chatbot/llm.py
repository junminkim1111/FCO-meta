"""LLM backends: the model picks tools, the Toolbox runs them against SQLite, figures no tool result backs up get one
rewrite, and the question has a time cap.

- ClaudeChat: Anthropic API (ANTHROPIC_API_KEY), default claude-sonnet-5-5
- OpenAIChat: any OpenAI-compatible chat API — OpenRouter by default (OPENROUTER_API_KEY), so one key reaches GPT,
  Qwen and others; base_url / api_key for another provider (OpenGateway for /model deepseek), or OPENAI_COMPAT_BASE_URL /
  OPENAI_COMPAT_API_KEY (Groq, DashScope …)

The web uses OpenAIChat; `python -m fco_meta.chatbot.evaluate --backend claude|openai` can use either.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any

import httpx

from .numbers import RECHECK_AT, unsupported_numbers
from .prompt import JUDGE_PROMPT, SYSTEM_PROMPT, judges
from .tools import Toolbox, for_model, tools_for

log = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 10  # 이전 질문·답은 최근 이만큼만 모델에 다시 보낸다 (긴 대화의 토큰과 흐려지는 맥락을 막는다)
MAX_TOOL_ROUNDS = 10  # 이만큼 도구를 부른 뒤에는 도구를 끄고 지금까지의 결과로 답하게 한다
MAX_TOOL_CALLS = 25  # 한 질문에서 실행하는 도구 호출 상한 (한 번에 여러 개를 부르면 ROUNDS로는 못 막는다. 2026-11-22 운영 최대 23번)
MAX_EVIDENCE = 4  # Turn.evidence 줄 수
# 한 질문의 전체 대기 상한(초): 도구 단계를 모두 합쳐. 넘으면 그 질문은 실패(화면에는 혼잡 안내).
# 요청마다 남은 시간을 타임아웃으로 걸어, 응답이 늦은 요청도 이 안에서 끊는다
ANSWER_DEADLINE = 120.0
MIN_REQUEST_TIMEOUT = 10.0  # 남은 시간이 이보다 적으면 새 요청을 보내지 않는다 (초)


class AnswerTimeout(Exception):
    """The question used up ANSWER_DEADLINE (tool rounds together)."""


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
    result: dict[str, Any] | None = None  # 전체 결과 (스쿼드 도구면 화면 왼쪽 스쿼드 칸에 그린다)


TRACE_PREVIEW = 600  # ToolResult.preview 길이 (관리자 생각 표시용)


def _preview(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    return text if len(text) <= TRACE_PREVIEW else text[:TRACE_PREVIEW] + "…"


# 답에 도구 결과에 없는 수치가 있을 때 한 번 다시 쓰게 하는 요청
RECHECK_REQUEST = (
    "방금 답에 쓴 수치 중 {numbers}는 이번 도구 결과에 없습니다. 도구 결과에 있는 값만 써서 답 전체를 다시 쓰세요. "
    "결과에 없는 값은 빼고, 결과에 있는 값은 미수집이라고 하지 말고 그대로 쓰세요. 다시 쓴다는 말은 하지 마세요."
)


# 스쿼드 후보를 받고 검사 없이 답했을 때 한 번 돌려보내는 요청 (작은 모델은 지시문만으로는 검사를 건너뛴다)
CHECK_REQUEST = (
    "고른 라인업을 아직 check_squad로 검사하지 않았습니다. squad_candidates에 준 조건과 picks로 check_squad를 부르고, "
    "ok인 라인업과 그 결과의 총 급여·총액으로 답 전체를 다시 쓰세요. 다시 쓴다는 말은 하지 마세요."
)


UNVERIFIED_SQUAD = "요청한 조건을 만족하는 스쿼드를 검증하지 못했습니다. 조건을 조정해 다시 요청해 주세요."


def unchecked_squad(pending: bool, name: str, result: dict[str, Any], is_error: bool) -> bool:
    """Candidates invalidate the previous check; only a successful final squad clears it."""
    if name in ("squad_candidates", "check_squad"):
        return not (name == "check_squad" and not is_error and result.get("ok") is True)
    if name == "recommend_squad" and not is_error and result.get("lineup"):
        return False
    return pending


def _drain(gen: Generator[Any, None, Any]) -> Any:
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


@dataclass
class Turn:
    text: str
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    finish_reason: str | None = None
    evidence: list[str] = field(default_factory=list)  # 도구 결과로 만든 근거 줄 (답 본문에는 넣지 않는다)


def describe_error(exc: Exception) -> str:
    """Short Korean description of a model failure for the logs (no key values)."""
    if isinstance(exc, AnswerTimeout):
        return f"답변 대기 시간 상한({ANSWER_DEADLINE:.0f}초) 초과 — {exc}"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"모델 API 오류 {exc.response.status_code}"
    return f"모델 처리 중 오류 ({type(exc).__name__}): {exc}"


LUNA_MODEL = "openai/gpt-6-luna"  # 기본 모델 (OpenRouter) — 웹과 CLI가 같이 쓴다
REFUSED = "이 질문에는 답할 수 없어요. FC온라인 랭커 데이터에 관한 질문으로 다시 물어봐 주세요."


@dataclass
class Reply:
    text: str
    uses: list[tuple[str, str, dict[str, Any]]]  # (호출 id, 도구 이름, 인자)
    stop: str  # end | tool | max_tokens | refusal
    message: dict[str, Any]  # 이 응답을 다음 요청에 그대로 붙일 assistant 메시지


class ToolChat:
    """Provider-neutral turn loop. Earlier turns are resent as question and answer text only (no tool results)."""

    def __init__(self, toolbox: Toolbox, model: str, now: Callable[[], float] = time.time, judge: bool | None = None):
        self.toolbox, self.model, self.now = toolbox, model, now
        self.judge = judges(model) if judge is None else judge  # 스쿼드를 모델이 직접 고르는가
        self.prompt = JUDGE_PROMPT if self.judge else SYSTEM_PROMPT
        self.last_model: str | None = None
        self.last_provider: str | None = None  # OpenRouter가 고른 제공사 (DeepInfra 등) — 제공사별 품질 차이를 찾으려고
        self.deadline = ANSWER_DEADLINE
        self.history: list[tuple[str, str]] = []  # (질문, 답)
        self.tokens: Counter[str] = Counter()  # 입력·출력 토큰 합 (비용 비교용)
        self.billed: dict[str, Counter[str]] = {}  # 모델별 청구 토큰 (input·output) — 웹의 질문당 비용
        self.unverified: list[str] = []  # 이번 답에 끝내 도구 결과로 확인되지 않은 수치
        self._ends_at = float("inf")
        self.thoughts = False  # 이번 질문의 생각을 Thought 이벤트로 (ask_stream이 정한다)

    # --- provider hooks ---
    def _round(self, messages: list[dict[str, Any]], final: bool) -> Generator[str, None, Reply]:
        raise NotImplementedError

    def _tool_results(self, results: list[tuple[str, str, bool]]) -> list[dict[str, Any]]:
        """[(호출 id, 결과 글, 오류인가)] → messages to append."""
        raise NotImplementedError

    def _start(self, question: str) -> list[dict[str, Any]]:
        return [*self.contents, {"role": "user", "content": question}]

    # --- shared ---
    @property
    def contents(self) -> list[dict[str, Any]]:
        """Earlier turns as messages (the web checks it to tell a conversation's first question)."""
        return [m for q, a in self.history[-MAX_HISTORY_TURNS:] for m in ({"role": "user", "content": q}, {"role": "assistant", "content": a})]

    def remember(self, question: str, answer: str) -> None:
        self.history.append((question, answer))

    def ask(self, question: str) -> Turn:
        return _drain(self.ask_stream(question))

    def ask_stream(
        self, question: str, *, thoughts: bool = False, deadline: float | None = None
    ) -> Generator[str | Reset | ToolCall | Recheck, None, Turn]:
        self._ends_at = self.now() + (deadline or self.deadline)
        self.thoughts, self.unverified = thoughts, []
        try:
            turn = yield from self._ask(question)  # 실패·정지면 기록에 남기지 않는다
        finally:
            self.thoughts = False
        self.history.append((question, turn.text))
        return turn

    def _left(self) -> float:
        left = self._ends_at - self.now()
        if left < MIN_REQUEST_TIMEOUT:
            raise AnswerTimeout(f"{self.model} 요청 전")
        return left

    def _ask(self, question: str) -> Generator[str | Reset | ToolCall | Recheck, None, Turn]:
        messages = self._start(question)
        calls: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        evidence: list[str] = []
        results: list[Any] = []
        pending_squad = False
        nudged = False  # 검사 없이 답한 스쿼드를 이미 돌려보냈는가
        capped = False  # 도구 호출 상한에 닿으면 다음 번은 도구 없이 답만
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            final = round_no == MAX_TOOL_ROUNDS or capped
            reply = yield from self._round(messages, final)
            if reply.stop == "refusal":
                yield RESET
                yield REFUSED
                return Turn(REFUSED, calls, "refusal")
            messages.append(reply.message)  # 받은 그대로 (생각 블록 포함)
            if not reply.uses or final:
                text = reply.text.strip()
                if not text:
                    note = "답을 만들지 못했습니다. 질문을 좀 더 구체적으로 해 주세요."
                    yield note
                    return Turn(note, calls, "tool_limit")
                if not final and not nudged and pending_squad:
                    nudged = True
                    log.warning("squad answered without check_squad — sending it back once")
                    yield RESET
                    messages.append({"role": "user", "content": CHECK_REQUEST})
                    continue
                if pending_squad:
                    yield RESET
                    yield UNVERIFIED_SQUAD
                    return Turn(UNVERIFIED_SQUAD, calls, "unverified_squad")
                if results:
                    text = yield from self._checked(messages, question, text, results)
                if reply.stop == "max_tokens":
                    yield "\n\n(답변이 길이 제한으로 잘렸습니다)"
                    text += "\n\n(답변이 길이 제한으로 잘렸습니다)"
                return Turn(text, calls, "STOP" if reply.stop == "end" else reply.stop, evidence[:MAX_EVIDENCE])
            if reply.text.strip():  # 도구를 부르기 전에 쓴 글("찾아볼게요")은 답이 아니다
                yield RESET
            out: list[tuple[str, str, bool]] = []
            for call_id, name, args in reply.uses:
                key = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
                if key in seen:  # 같은 호출 반복 → 실행하지 않는다
                    out.append((call_id, "같은 도구를 같은 인자로 이미 호출했습니다. 앞서 받은 결과로 답하세요.", False))
                    continue
                if len(calls) >= MAX_TOOL_CALLS:  # 거의 끝없이 부르는 경우만 막는다 → 지금까지의 결과로 답하게
                    capped = True
                    out.append((call_id, f"도구 호출 한도({MAX_TOOL_CALLS}번)에 닿았습니다. 더 부르지 말고 지금까지 받은 결과로 답하세요"
                                         " (확인하지 못한 범위는 답에 밝힐 것).", True))
                    continue
                seen.add(key)
                calls.append((name, args))
                yield ToolCall(name, args)
                content, is_error = self.toolbox.run(name, args)
                result = json.loads(content)
                pending_squad = unchecked_squad(pending_squad, name, result, is_error)
                yield ToolResult(name, not is_error, _preview(result.get("error") if is_error else for_model(name, result)), result)
                if is_error:
                    out.append((call_id, result["error"], True))
                    continue
                results.append(result)
                out.append((call_id, json.dumps(for_model(name, result), ensure_ascii=False), False))
                line = self.toolbox.evidence(name, result)
                if line and line not in evidence:
                    evidence.append(line)
            messages += self._tool_results(out)  # 병렬 호출 결과는 한 번에
        raise AssertionError("unreachable")

    def _checked(self, messages: list[dict[str, Any]], question: str, text: str, known: list[Any]) -> Generator[Any, None, str]:
        """Figures no tool result backs up → one rewrite without tools (appended, never edited in place)."""
        missing = unsupported_numbers(text, known, question)
        if len(missing) < RECHECK_AT:  # 없거나 하나뿐: 다시 쓰지 않는다 (하나면 관리자 기록에 '수치 미확인')
            self.unverified = missing
            return text
        if self._ends_at - self.now() < MIN_REQUEST_TIMEOUT:
            self.unverified = missing
            return text
        log.warning("numbers not in tool results %s — asking for one rewrite", missing)
        yield RESET
        yield Recheck(missing)
        messages.append({"role": "user", "content": RECHECK_REQUEST.format(numbers=", ".join(missing))})
        reply = yield from self._round(messages, final=True)
        if not reply.text.strip():  # 다시 쓰기가 비면 처음 답
            self.unverified = missing
            yield RESET
            yield text
            return text
        self.unverified = unsupported_numbers(reply.text, known, question)
        return reply.text.strip()


class ClaudeChat(ToolChat):
    """Anthropic Messages API, streamed. Thinking blocks go back unchanged within a turn; earlier turns carry none."""

    DEFAULT_MODEL = "claude-sonnet-5-5"
    EFFORT = "low"  # 채팅·조회: low면 간단한 질문은 생각을 건너뛰어 첫 글자가 빨리 나온다
    MAX_TOKENS = 8000  # 생각 + 답
    FALLBACK_BETA = "server-side-fallback-2026-07-01"  # 안전 분류기가 거절하면 서버가 다른 모델로 다시 답한다

    def __init__(self, client: Any, toolbox: Toolbox, *, model: str | None = None, now: Callable[[], float] = time.time,
                 judge: bool | None = None):  # fmt: skip
        super().__init__(toolbox, model or self.DEFAULT_MODEL, now, judge)
        self.client = client
        self.tools = [{k: t[k] for k in ("name", "description", "input_schema")} for t in tools_for(self.judge)]

    def _round(self, messages: list[dict[str, Any]], final: bool) -> Generator[str, None, Reply]:
        with self.client.with_options(timeout=self._left(), max_retries=1).beta.messages.stream(
            model=self.model, max_tokens=self.MAX_TOKENS, system=self.prompt, tools=self.tools, messages=messages,
            output_config={"effort": self.EFFORT}, cache_control={"type": "ephemeral"},  # 지시문·도구 설명은 캐시로
            tool_choice={"type": "none"} if final else {"type": "auto"},
            betas=[self.FALLBACK_BETA], fallbacks="default",
        ) as stream:  # fmt: skip
            yield from stream.text_stream
            message = stream.get_final_message()
        self.last_model = message.model
        usage = getattr(message, "usage", None)
        if usage is not None:
            self.tokens.update(input=usage.input_tokens or 0, output=usage.output_tokens or 0,
                               cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0)  # fmt: skip
            self.billed.setdefault(self.model, Counter()).update(input=usage.input_tokens or 0, output=usage.output_tokens or 0)
        stop = {"end_turn": "end", "tool_use": "tool", "max_tokens": "max_tokens", "refusal": "refusal"}.get(message.stop_reason, "end")
        return Reply(
            text="".join(b.text for b in message.content if b.type == "text"),
            uses=[(b.id, b.name, dict(b.input or {})) for b in message.content if b.type == "tool_use"],
            stop=stop, message={"role": "assistant", "content": message.content},
        )  # fmt: skip

    def _tool_results(self, results: list[tuple[str, str, bool]]) -> list[dict[str, Any]]:
        return [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": text, **({"is_error": True} if error else {})}
            for call_id, text, error in results
        ]}]  # fmt: skip


class OpenAIChat(ToolChat):
    """OpenAI-compatible /chat/completions, streamed (one request per round): answer text is yielded as it arrives."""

    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, http: Any, toolbox: Toolbox, *, model: str, base_url: str | None = None, api_key: str | None = None,
                 extra: dict[str, Any] | None = None, now: Callable[[], float] = time.time, judge: bool | None = None):  # fmt: skip
        super().__init__(toolbox, model, now, judge)
        self.http = http
        self.extra = extra or {}  # 요청 본문에 더할 값 (예: OpenRouter {"reasoning": {"enabled": false}})
        self.base_url = (base_url or os.environ.get("OPENAI_COMPAT_BASE_URL") or self.DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_COMPAT_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                        "parameters": t["input_schema"]}} for t in tools_for(self.judge)]  # fmt: skip

    def _start(self, question: str) -> list[dict[str, Any]]:
        return [{"role": "system", "content": self.prompt}, *super()._start(question)]

    def _round(self, messages: list[dict[str, Any]], final: bool) -> Generator[str, None, Reply]:
        left = self._left()
        body = {"model": self.model, "messages": messages, "tools": self.tools, "tool_choice": "none" if final else "auto",
                "stream": True, "stream_options": {"include_usage": True}, **self.extra}  # fmt: skip
        text, reasoning, finish = "", "", None
        calls: dict[int, dict[str, Any]] = {}  # 도구 호출은 조각으로 온다: index → 모은 호출
        usage: dict[str, Any] = {}
        try:
            with self.http.stream("POST", f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"},
                                  timeout=left, json=body) as res:  # fmt: skip
                if res.status_code >= 400:
                    res.read()  # 오류 본문을 예외에 담으려면 먼저 읽어야 한다
                res.raise_for_status()
                for line in res.iter_lines():
                    # OpenRouter는 처리하는 동안 연결 유지 줄(": OPENROUTER PROCESSING")을 보내 읽기 타임아웃이 끝나지 않는다 → 줄마다 전체 시간을 본다
                    if self.now() >= self._ends_at:
                        raise AnswerTimeout(f"{self.model} 응답 없음")
                    if not line.startswith("data:") or (payload := line[5:].strip()) == "[DONE]":
                        continue
                    data = json.loads(payload)
                    if "error" in data:  # 200으로 시작한 뒤에도 error를 담아 보내기도 한다
                        raise RuntimeError(f"{self.model}: {data['error']}")
                    self.last_model = data.get("model") or self.last_model or self.model
                    self.last_provider = data.get("provider") or self.last_provider
                    usage = data.get("usage") or usage  # 마지막 조각에 온다
                    for choice in data.get("choices") or []:
                        delta = choice.get("delta") or {}
                        finish = choice.get("finish_reason") or finish
                        reasoning += delta.get("reasoning") or delta.get("reasoning_content") or ""  # 생각: OpenRouter는 reasoning, OpenGateway(DeepSeek)는 reasoning_content
                        for part in delta.get("tool_calls") or []:
                            call = calls.setdefault(part.get("index", 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            call["id"] = part.get("id") or call["id"]
                            fn = part.get("function") or {}
                            call["function"]["name"] += fn.get("name") or ""
                            call["function"]["arguments"] += fn.get("arguments") or ""
                        if piece := delta.get("content"):
                            if reasoning and self.thoughts and not text:
                                yield Thought(reasoning)
                            text += piece
                            yield piece  # 도구를 부르기 전에 쓴 글이면 _ask가 RESET으로 지운다
        except httpx.TimeoutException:
            raise AnswerTimeout(f"{self.model} 응답 없음") from None
        if reasoning and self.thoughts and not text:
            yield Thought(reasoning)
        self.tokens.update(input=usage.get("prompt_tokens", 0), output=usage.get("completion_tokens", 0))  # 출력에 생각 포함
        self.billed.setdefault(self.model, Counter()).update(input=usage.get("prompt_tokens", 0), output=usage.get("completion_tokens", 0))
        if thought := (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"):
            self.tokens.update(reasoning=thought)
        if cached := (usage.get("prompt_tokens_details") or {}).get("cached_tokens"):  # 입력 중 캐시에서 읽은 부분
            self.tokens.update(cached=cached)
        tool_calls = [calls[i] for i in sorted(calls)]
        uses = []
        for call in tool_calls:
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_arguments": call["function"]["arguments"]}  # 도구가 오류로 돌려준다
            uses.append((call["id"], call["function"]["name"], args))
        stop = {"stop": "end", "tool_calls": "tool", "length": "max_tokens", "content_filter": "refusal"}.get(finish, "end")
        message = {"role": "assistant", **({"content": text} if text else {}), **({"tool_calls": tool_calls} if tool_calls else {})}
        return Reply(text=text, uses=uses, stop=stop, message=message)

    def _tool_results(self, results: list[tuple[str, str, bool]]) -> list[dict[str, Any]]:
        return [{"role": "tool", "tool_call_id": call_id, "content": text} for call_id, text, _ in results]
