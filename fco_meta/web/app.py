"""FastAPI app: JSON API over the chatbot tools + a single static page."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..chatbot.gemini import describe_error
from ..chatbot.rules import RuleBot
from ..chatbot.tools import Toolbox

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
MAX_SESSIONS = 200


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, max_length=64)


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    # 웹은 조회만 한다. 테이블(CREATE IF NOT EXISTS)은 쓰기 연결로 한 번 보장한 뒤 읽기 전용으로 연다
    Toolbox(sqlite3.connect(str(db_path))).conn.close()
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)


def create_app(db_path: Path | str, *, backend: str = "rules", gemini_model: str | None = None) -> FastAPI:
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    conn = _open_readonly(db_path)
    lock = threading.Lock()  # sqlite 연결 하나를 스레드풀 요청이 나눠 쓴다
    toolbox = Toolbox(conn)
    gemini_tools = _LockedTools(toolbox, lock)
    rules = RuleBot(toolbox)
    gemini_sessions: dict[str, Any] = {}

    app = FastAPI(title="FCO 랭커 메타", docs_url="/api/docs", redoc_url=None)
    app.state.conn = conn
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def revalidate(request, call_next):
        # 브라우저가 옛 화면을 저장해 두고 쓰지 않도록 매번 확인하게 한다 (바뀌지 않았으면 304로 가볍게)
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    def tool(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        with lock:
            content, is_error = toolbox.run(name, tool_input)
        result = json.loads(content)
        if is_error:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        """수집 범위와 챗봇 방식 (화면 머리말)."""
        data = tool("list_available_data", {})
        return {"backend": backend, "daily_scope": data.get("daily_scope")}

    @app.get("/api/formations")
    def formations(team_color: str | None = Query(default=None, max_length=40)) -> dict[str, Any]:
        return tool("list_formations", {"team_color": team_color or None})

    @app.get("/api/formation")
    def formation(name: str = Query(min_length=1, max_length=20)) -> dict[str, Any]:
        """포메이션 하나의 종합 정보 (사용률·승률·팀컬러·순위 구간·최상위 랭커 스쿼드·베스트 11·상대 전적)."""
        return tool("get_formation_overview", {"formation": name})

    @app.post("/api/chat")
    def chat(req: ChatRequest) -> dict[str, Any]:
        if backend == "gemini":
            session_id = req.session_id or uuid.uuid4().hex
            with lock:
                session = gemini_sessions.get(session_id)
                if session is None:
                    if len(gemini_sessions) >= MAX_SESSIONS:
                        gemini_sessions.pop(next(iter(gemini_sessions)))
                    session = gemini_sessions[session_id] = (_gemini_chat(gemini_tools, gemini_model), threading.Lock())
            bot, busy = session
            # 모델을 기다리는 수십 초 동안 DB 잠금을 잡지 않는다 (도구 실행 때만 gemini_tools가 잡는다).
            # 같은 대화의 질문은 차례로 (정지한 요청이 서버에서 아직 도는 중이면 끝나기를 기다린다)
            with busy:
                try:
                    turn = bot.ask(req.message)
                except Exception as exc:  # Gemini 실패 → 원인을 알리고 규칙 기반으로 대신 답한다
                    log.exception("gemini chat failed")
                    reason = describe_error(exc)
                    with lock:
                        answer = rules.ask(req.message)
                    return {
                        "session_id": session_id,
                        "answer": f"⚠ {reason}\n(이번 질문은 규칙 기반으로 답합니다)\n\n{answer.text}",
                        "tool_calls": [answer.tool] if answer.tool else [],
                        "error": reason,
                    }
            # 근거 줄과 대체 모델은 서버 로그에만 남기고 사용자 답에는 넣지 않는다
            log.info("answered with %s; evidence: %s", bot.last_model, turn.evidence)
            return {
                "session_id": session_id, "answer": turn.text,
                "tool_calls": [n for n, _ in turn.tool_calls], "model": bot.last_model,
            }  # fmt: skip
        with lock:
            answer = rules.ask(req.message)
        return {"session_id": req.session_id, "answer": answer.text, "tool_calls": [answer.tool] if answer.tool else []}

    return app


class _LockedTools:
    """The toolbox as Gemini sees it: the DB lock is held only while a tool runs."""

    def __init__(self, toolbox: Toolbox, lock: threading.Lock):
        self.toolbox, self.lock = toolbox, lock

    def run(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        with self.lock:
            return self.toolbox.run(name, tool_input)

    def evidence(self, name: str, result: dict[str, Any]) -> str | None:
        return self.toolbox.evidence(name, result)


def _gemini_chat(toolbox: Toolbox | _LockedTools, model: str | None):
    from google import genai

    from ..chatbot.gemini import GeminiChat, models_from_env

    primary, fallbacks = models_from_env(model)
    return GeminiChat(genai.Client(), toolbox, model=primary, fallback_models=fallbacks)
