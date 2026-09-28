"""Gemini backend: the model picks tools, the Toolbox runs them against SQLite.

Requires `pip install -e ".[gemini]"` and GEMINI_API_KEY (or GOOGLE_API_KEY).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .prompt import SYSTEM_PROMPT
from .tools import TOOLS, Toolbox

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash"
MAX_TOOL_ROUNDS = 8


@dataclass
class GeminiTurn:
    text: str
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    finish_reason: str | None = None


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
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        types = _types()
        self.client = client
        self.toolbox = toolbox
        self.model = model
        self.on_tool_call = on_tool_call
        self.contents: list[Any] = []
        self.config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[types.Tool(function_declarations=function_declarations())],
            # 도구는 이 루프에서 직접 실행한다 (SDK 자동 호출 끔)
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def ask(self, question: str) -> GeminiTurn:
        types = _types()
        self.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))
        calls: list[tuple[str, dict[str, Any]]] = []
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.models.generate_content(model=self.model, contents=self.contents, config=self.config)
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
