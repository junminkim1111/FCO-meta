"""Conversation loop: Claude decides which tools to call; the Toolbox runs them against SQLite."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .prompt import SYSTEM_PROMPT
from .tools import TOOLS, Toolbox

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_ROUNDS = 8


@dataclass
class Turn:
    text: str
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    stop_reason: str | None = None


class Chatbot:
    """Multi-turn chat with tool use.

    `client` is an `anthropic.Anthropic` (or anything with the same `beta.messages.create`).
    The history keeps every assistant `content` block as returned (thinking blocks included),
    and only ever appends, so prompt caching and thinking continuity keep working across turns.
    """

    def __init__(
        self,
        client: Any,
        toolbox: Toolbox,
        *,
        model: str = DEFAULT_MODEL,
        effort: str | None = None,
        max_tokens: int = 16000,
        fallbacks: bool = True,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.client = client
        self.toolbox = toolbox
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks
        self.on_tool_call = on_tool_call
        self.messages: list[dict[str, Any]] = []

    def _create(self) -> Any:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "tools": TOOLS,
            "messages": self.messages,
            "thinking": {"type": "adaptive"},
            "cache_control": {"type": "ephemeral"},  # 대화가 길어져도 이전 턴까지는 캐시에서 읽음
        }
        if self.effort:
            params["output_config"] = {"effort": self.effort}
        if self.fallbacks:
            # 안전 분류기가 거절하면 서버가 권장 모델로 같은 요청을 다시 실행
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return self.client.beta.messages.create(**params)

    def ask(self, question: str) -> Turn:
        self.messages.append({"role": "user", "content": question})
        calls: list[tuple[str, dict[str, Any]]] = []
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._create()
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                return Turn("요청을 처리할 수 없습니다. 질문을 바꿔서 다시 시도해 주세요.", calls, "refusal")
            if response.stop_reason == "pause_turn":
                continue
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    text += "\n\n(답변이 길이 제한으로 잘렸습니다)"
                return Turn(text, calls, response.stop_reason)

            results = []
            for block in tool_uses:
                tool_input = dict(block.input) if isinstance(block.input, dict) else {}
                calls.append((block.name, tool_input))
                if self.on_tool_call:
                    self.on_tool_call(block.name, tool_input)
                content, is_error = self.toolbox.run(block.name, tool_input)
                log.info("tool %s(%s) → %d chars%s", block.name, tool_input, len(content), " (error)" if is_error else "")
                result: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": content}
                if is_error:
                    result["is_error"] = True
                results.append(result)
            # 병렬 호출 결과는 한 메시지로 돌려준다
            self.messages.append({"role": "user", "content": results})
        return Turn("도구 호출이 너무 많아 중단했습니다. 질문을 좁혀 주세요.", calls, "tool_limit")
