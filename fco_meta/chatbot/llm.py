"""Other LLM backends for comparing answer quality with Gemini — the same turn as GeminiChat: the model picks
tools, the Toolbox runs them, figures no tool result backs up get one rewrite, the question has the same time cap.

- ClaudeChat: Anthropic API (ANTHROPIC_API_KEY), default claude-sonnet-5-5
- OpenAIChat: any OpenAI-compatible chat API — OpenRouter by default (OPENROUTER_API_KEY), so one key reaches Qwen,
  DeepSeek, GPT and others; OPENAI_COMPAT_BASE_URL / OPENAI_COMPAT_API_KEY for another provider (Groq, DashScope …)

For now only `python -m fco_meta.chatbot.evaluate --backend claude|openai` uses them.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

from .gemini import (
    ANSWER_DEADLINE, CHECK_REQUEST, MAX_EVIDENCE, MAX_TOOL_ROUNDS, MIN_REQUEST_TIMEOUT, RECHECK_REQUEST, RESET, AnswerTimeout,
    GeminiTurn, Recheck, Reset, Thought, ToolCall, ToolResult, _drain, _preview, unchecked_squad,
)  # fmt: skip
from .numbers import unsupported_numbers
from .prompt import JUDGE_PROMPT, SYSTEM_PROMPT, judges
from .tools import Toolbox, for_model, tools_for

log = logging.getLogger(__name__)

# OpenRouter 등은 처리하는 동안 연결 유지 신호를 보내 읽기 타임아웃이 끝나지 않으므로, 요청을 따로 돌리고 전체 시간으로 끊는다
# ponytail: 끊긴 요청은 서버가 답할 때까지 그 스레드에서 계속 돈다 (스트리밍으로 바꾸면 바로 닫을 수 있음)
_REQUESTS = ThreadPoolExecutor(max_workers=16)

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
        self.deadline = ANSWER_DEADLINE
        self.history: list[tuple[str, str]] = []  # (질문, 답)
        self.tokens: Counter[str] = Counter()  # 입력·출력 토큰 합 (비용 비교용)
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
        return [m for q, a in self.history for m in ({"role": "user", "content": q}, {"role": "assistant", "content": a})]

    def remember(self, question: str, answer: str) -> None:
        self.history.append((question, answer))

    def ask(self, question: str) -> GeminiTurn:
        return _drain(self.ask_stream(question))

    def ask_stream(
        self, question: str, *, thoughts: bool = False, deadline: float | None = None
    ) -> Generator[str | Reset | ToolCall | Recheck, None, GeminiTurn]:
        self._ends_at = self.now() + (deadline or self.deadline)
        self.thoughts = thoughts
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

    def _ask(self, question: str) -> Generator[str | Reset | ToolCall | Recheck, None, GeminiTurn]:
        messages = self._start(question)
        known: list[Any] = [a for _, a in self.history]  # 앞선 답에 나온 수치는 다시 써도 된다
        calls: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        evidence: list[str] = []
        results: list[Any] = []
        nudged = False  # 검사 없이 답한 스쿼드를 이미 돌려보냈는가
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            final = round_no == MAX_TOOL_ROUNDS
            reply = yield from self._round(messages, final)
            if reply.stop == "refusal":
                yield RESET
                yield REFUSED
                return GeminiTurn(REFUSED, calls, "refusal")
            messages.append(reply.message)  # 받은 그대로 (생각 블록 포함)
            if not reply.uses or final:
                text = reply.text.strip()
                if not text:
                    note = "답을 만들지 못했습니다. 질문을 좀 더 구체적으로 해 주세요."
                    yield note
                    return GeminiTurn(note, calls, "tool_limit")
                if not final and not nudged and unchecked_squad(calls):
                    nudged = True
                    log.warning("squad answered without check_squad — sending it back once")
                    yield RESET
                    messages.append({"role": "user", "content": CHECK_REQUEST})
                    continue
                if results:
                    text = yield from self._checked(messages, question, text, [*results, *known])
                if reply.stop == "max_tokens":
                    yield "\n\n(답변이 길이 제한으로 잘렸습니다)"
                    text += "\n\n(답변이 길이 제한으로 잘렸습니다)"
                return GeminiTurn(text, calls, "STOP" if reply.stop == "end" else reply.stop, evidence[:MAX_EVIDENCE])
            if reply.text.strip():  # 도구를 부르기 전에 쓴 글("찾아볼게요")은 답이 아니다
                yield RESET
            out: list[tuple[str, str, bool]] = []
            for call_id, name, args in reply.uses:
                key = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
                if key in seen:  # 같은 호출 반복 → 실행하지 않는다
                    out.append((call_id, "같은 도구를 같은 인자로 이미 호출했습니다. 앞서 받은 결과로 답하세요.", False))
                    continue
                seen.add(key)
                calls.append((name, args))
                yield ToolCall(name, args)
                content, is_error = self.toolbox.run(name, args)
                result = json.loads(content)
                yield ToolResult(name, not is_error, _preview(result.get("error") if is_error else for_model(name, result)))
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
        if not missing or self._ends_at - self.now() < MIN_REQUEST_TIMEOUT:
            return text
        log.warning("numbers not in tool results %s — asking for one rewrite", missing)
        yield RESET
        yield Recheck(missing)
        messages.append({"role": "user", "content": RECHECK_REQUEST.format(numbers=", ".join(missing))})
        reply = yield from self._round(messages, final=True)
        if not reply.text.strip():  # 다시 쓰기가 비면 처음 답
            yield RESET
            yield text
            return text
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
    """OpenAI-compatible /chat/completions (one request per round, not streamed — for comparing models)."""

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
        future = _REQUESTS.submit(
            self.http.post, f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"}, timeout=left,
            json={"model": self.model, "messages": messages, "tools": self.tools, "tool_choice": "none" if final else "auto", **self.extra},
        )  # fmt: skip
        try:
            res = future.result(timeout=left)
        except FutureTimeout:
            raise AnswerTimeout(f"{self.model} 응답 없음") from None
        res.raise_for_status()
        data = res.json()
        if "error" in data:  # OpenRouter는 200에 error를 담기도 한다
            raise RuntimeError(f"{self.model}: {data['error']}")
        self.last_model = data.get("model", self.model)
        usage = data.get("usage") or {}
        self.tokens.update(input=usage.get("prompt_tokens", 0), output=usage.get("completion_tokens", 0))  # 출력에 생각 포함
        if reasoning := (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"):
            self.tokens.update(reasoning=reasoning)
        if cached := (usage.get("prompt_tokens_details") or {}).get("cached_tokens"):  # 입력 중 캐시에서 읽은 부분
            self.tokens.update(cached=cached)
        choice = data["choices"][0]
        msg = choice["message"]
        uses = []
        for call in msg.get("tool_calls") or []:
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_arguments": call["function"].get("arguments")}  # 도구가 오류로 돌려준다
            uses.append((call["id"], call["function"]["name"], args))
        text = msg.get("content") or ""
        if self.thoughts and msg.get("reasoning"):  # OpenRouter는 생각을 켠 모델의 추론을 reasoning에 준다
            yield Thought(msg["reasoning"])
        if text and not uses:
            yield text
        stop = {"stop": "end", "tool_calls": "tool", "length": "max_tokens", "content_filter": "refusal"}.get(choice.get("finish_reason"), "end")
        keep = {k: msg[k] for k in ("role", "content", "tool_calls") if msg.get(k) is not None}
        return Reply(text=text, uses=uses, stop=stop, message={"role": "assistant", **keep})

    def _tool_results(self, results: list[tuple[str, str, bool]]) -> list[dict[str, Any]]:
        return [{"role": "tool", "tool_call_id": call_id, "content": text} for call_id, text, _ in results]
