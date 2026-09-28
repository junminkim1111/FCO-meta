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
from ..chatbot.rules import ROLE_LABELS, RuleBot
from ..chatbot.tools import Toolbox
from ..market.money import parse_bp
from ..market.roles import ROLES

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
    rules = RuleBot(toolbox)
    gemini_sessions: dict[str, Any] = {}

    app = FastAPI(title="FCO 랭커 메타", docs_url="/api/docs", redoc_url=None)
    app.state.conn = conn
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
        """집계된 조합과 역할 목록 (화면 선택지)."""
        data = tool("list_available_data", {})
        return {
            "backend": backend,
            "combos": data["combos"],
            "daily_scope": data.get("daily_scope"),
            "roles": [{"code": code, "label": ROLE_LABELS.get(code, code), "positions": list(pos)} for code, pos in ROLES.items()],
        }

    @app.get("/api/recommend")
    def recommend(
        role: str = Query(min_length=1, max_length=20),
        team_color: str | None = Query(default=None, max_length=40, description="비우면 상위 랭커 전체"),
        formation: str | None = Query(default=None, max_length=20),
        top_n: int = Query(default=5, ge=1, le=20),
        strict: bool = False,
        max_price: str | None = Query(default=None, max_length=30, description="예: 5억, 3000만"),
    ) -> dict[str, Any]:
        budget = None
        if max_price and max_price.strip():
            try:
                budget = parse_bp(max_price)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"예산 형식을 알 수 없습니다: {max_price}") from None
        return tool("recommend_players", {
            "team_color": team_color or None, "role": role, "formation": formation or None,
            "top_n": top_n, "strict": strict, "max_price_bp": budget,
        })  # fmt: skip

    @app.get("/api/formations")
    def formations(team_color: str | None = Query(default=None, max_length=40)) -> dict[str, Any]:
        return tool("list_formations", {"team_color": team_color or None})

    @app.get("/api/player")
    def player(name: str = Query(min_length=1, max_length=40)) -> dict[str, Any]:
        return tool("get_player_detail", {"name": name})

    @app.post("/api/chat")
    def chat(req: ChatRequest) -> dict[str, Any]:
        if backend == "gemini":
            session_id = req.session_id or uuid.uuid4().hex
            with lock:
                bot = gemini_sessions.get(session_id)
                if bot is None:
                    if len(gemini_sessions) >= MAX_SESSIONS:
                        gemini_sessions.pop(next(iter(gemini_sessions)))
                    bot = gemini_sessions[session_id] = _gemini_chat(toolbox, gemini_model)
                try:
                    turn = bot.ask(req.message)
                except Exception as exc:  # Gemini 실패 → 원인을 알리고 규칙 기반으로 대신 답한다
                    log.exception("gemini chat failed")
                    reason = describe_error(exc)
                    answer = rules.ask(req.message)
                    return {
                        "session_id": session_id,
                        "answer": f"⚠ {reason}\n(이번 질문은 규칙 기반으로 답합니다)\n\n{answer.text}",
                        "tool_calls": [answer.tool] if answer.tool else [],
                        "error": reason,
                    }
            return {"session_id": session_id, "answer": turn.text, "tool_calls": [n for n, _ in turn.tool_calls]}
        with lock:
            answer = rules.ask(req.message)
        return {"session_id": req.session_id, "answer": answer.text, "tool_calls": [answer.tool] if answer.tool else []}

    return app


def _gemini_chat(toolbox: Toolbox, model: str | None):
    from google import genai

    from ..chatbot.gemini import DEFAULT_MODEL, GeminiChat

    return GeminiChat(genai.Client(), toolbox, model=model or DEFAULT_MODEL)
