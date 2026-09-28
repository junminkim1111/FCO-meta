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

DEFAULT_MODEL = "gemini-3.5-flash"
# 기본 모델이 과부하(503)·한도(429)로 계속 실패하면 차례로 시도할 모델
DEFAULT_FALLBACK_MODELS = ("gemini-2.5-flash",)
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


def describe_error(exc: Exception) -> str:
    """Short Korean description of a Gemini failure for the user (no key values)."""
    try:
        from google.genai import errors
    except ImportError:  # pragma: no cover
        errors = None
    if errors is not None and isinstance(exc, errors.APIError):
        hint = {
            400: "요청 형식 또는 API 키 문제",
            401: "API 키 인증 실패",
            403: "API 키 권한 없음 (키 제한·결제 설정 확인)",
            404: "모델을 찾을 수 없음 (--model 로 다른 모델 지정)",
            429: "사용량 한도 초과 (잠시 후 다시 시도)",
        }.get(exc.code, "Gemini 서버 오류" if (exc.code or 0) >= 500 else "")
        return f"Gemini API 오류 {exc.code} {exc.status or ''}: {exc.message or ''}" + (f" — {hint}" if hint else "")
    return f"Gemini 처리 중 오류 ({type(exc).__name__}): {exc}"


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

        last_error: Exception | None = None
        for model in [self.model, *self.fallback_models]:
            for attempt in range(len(RETRY_DELAYS) + 1):
                try:
                    response = self.client.models.generate_content(model=model, contents=self.contents, config=self.config)
                except errors.APIError as exc:
                    if exc.code not in RETRYABLE:
                        raise
                    last_error = exc
                    if attempt < len(RETRY_DELAYS):
                        log.warning("%s: %s %s, retrying in %.0fs", model, exc.code, exc.status, RETRY_DELAYS[attempt])
                        self.sleep(RETRY_DELAYS[attempt])
                    continue
                if model != self.model:
                    log.warning("answered by fallback model %s", model)
                self.last_model = model
                return response
            log.warning("%s unavailable, trying next model", model)
        assert last_error is not None
        raise last_error

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
