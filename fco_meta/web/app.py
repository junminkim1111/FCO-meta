"""FastAPI app: JSON API over the chatbot tools + a single static page."""

from __future__ import annotations

import hmac
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..chatbot.gemini import RESET, Recheck, ToolCall, describe_error
from ..chatbot.rules import RuleBot
from ..chatbot.tools import Toolbox
from .chatlog import ChatLog

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
MAX_SESSIONS = 200
MAX_CACHED = 500  # 캐시에 두는 첫 질문 답 수
BUSY_MESSAGE = "지금 서버가 혼잡해 답을 드리지 못했어요. 잠시 후 다시 물어봐 주세요."
KST = timezone(timedelta(hours=9))


class RateLimit:
    """Questions per person per minute and per day, and for the whole service per day — the Gemini quota
    is shared by everyone. A person is the visitor's IP as the tunnel or proxy passes it (see chat())."""

    def __init__(self, per_minute: int = 6, per_day: int = 60, total_per_day: int = 300, now: Callable[[], float] = time.time):
        self.per_minute, self.per_day, self.total_per_day, self.now = per_minute, per_day, total_per_day, now
        self.recent: dict[str, deque[float]] = {}
        self.today: dict[str, int] = {}
        self.total, self.day = 0, None
        self.lock = threading.Lock()

    def check(self, who: str) -> str | None:
        """Counts one question; returns why it is refused instead (nothing is counted then)."""
        with self.lock:
            now = self.now()
            day = datetime.fromtimestamp(now, KST).date()
            if day != self.day:  # 한국 시간 자정에 하루 횟수를 비운다
                self.today.clear()
                self.total, self.day = 0, day
            recent = self.recent.setdefault(who, deque())
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if self.total >= self.total_per_day:
                return "오늘 서비스 전체 질문 수가 다 찼어요. 내일 다시 물어봐 주세요."
            if self.today.get(who, 0) >= self.per_day:
                return f"하루 질문 수({self.per_day}개)를 다 썼어요. 내일 다시 물어봐 주세요."
            if len(recent) >= self.per_minute:
                return f"질문이 너무 빨라요. 1분에 {self.per_minute}개까지 물어볼 수 있어요. 잠시 후 다시 물어봐 주세요."
            recent.append(now)
            self.today[who] = self.today.get(who, 0) + 1
            self.total += 1
            return None


class AdminGuard:
    """Admin password checks per visitor: a warning from the WARN_AT-th wrong password, and from the BLOCK_AT-th
    that visitor (IP) can't reach the admin page at all, even with the right password. Kept in memory: a restart
    or redeploy (Render sleeps after 15 minutes without visitors) clears it."""

    WARN_AT, BLOCK_AT = 3, 5

    def __init__(self, key: str):
        self.key = key
        self.failures: dict[str, int] = {}
        self.lock = threading.Lock()

    def open_to(self, who: str) -> None:
        """Raises 404 (no admin page) or 403 (this visitor is blocked)."""
        if not self.key:
            raise HTTPException(status_code=404)
        if self.failures.get(who, 0) >= self.BLOCK_AT:
            raise HTTPException(status_code=403, detail=self._blocked())

    def check(self, who: str, given: str | None) -> None:
        """Raises 404 (no admin page), 403 (blocked) or 401 (wrong or missing password)."""
        with self.lock:
            self.open_to(who)
            if not given:  # 아직 입력 전 (틀린 횟수에 넣지 않음)
                raise HTTPException(status_code=401, detail="관리자 비밀번호를 입력하세요")
            if hmac.compare_digest(given.encode(), self.key.encode()):
                self.failures.pop(who, None)
                return
            n = self.failures[who] = self.failures.get(who, 0) + 1
        log.warning("wrong admin password from %s (%d)", who, n)
        if n >= self.BLOCK_AT:
            raise HTTPException(status_code=403, detail=self._blocked())
        detail = "관리자 비밀번호가 맞지 않습니다."
        if n >= self.WARN_AT:
            detail += f" {n}회 틀렸습니다 — {self.BLOCK_AT}회 틀리면 이 IP에서 관리자 페이지 접근이 막힙니다."
        raise HTTPException(status_code=401, detail=detail)

    def _blocked(self) -> str:
        return f"비밀번호를 {self.BLOCK_AT}회 틀려 이 IP에서 관리자 페이지 접근이 막혔습니다."


def _visitor(request: Request) -> str:
    """The visitor's IP as the tunnel (CF-Connecting-IP) or proxy (first X-Forwarded-For) passes it."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return request.headers.get("cf-connecting-ip") or forwarded or (request.client.host if request.client else "?")


def _event(**fields: Any) -> str:
    """One line of the /api/chat stream (NDJSON)."""
    return json.dumps(fields, ensure_ascii=False) + "\n"


class AnswerCache:
    """Answers to the first question of a conversation, reused for the same question until the DB file
    changes (daily collection, price refresh) — repeated questions cost no Gemini quota. Follow-ups are
    not cached: their answer depends on the conversation."""

    def __init__(self, db_path: Path, size: int = MAX_CACHED):
        self.files = [db_path, db_path.with_name(db_path.name + "-wal")]
        self.size, self.version = size, None
        self.items: OrderedDict[str, str] = OrderedDict()
        self.lock = threading.Lock()

    @staticmethod
    def key(question: str) -> str:
        # 띄어쓰기·대소문자·끝 문장부호만 무시하는 정확 일치 (뜻이 비슷한 질문은 묶지 않는다)
        return " ".join(question.lower().split()).rstrip("?!.？！。 ")

    def _check_version(self) -> None:
        version = tuple(f.stat().st_mtime_ns for f in self.files if f.exists())
        if version != self.version:  # 데이터가 바뀌었으면 예전 답은 버린다
            self.items.clear()
            self.version = version

    def get(self, question: str) -> str | None:
        with self.lock:
            self._check_version()
            answer = self.items.get(self.key(question))
            if answer is not None:
                self.items.move_to_end(self.key(question))
            return answer

    def put(self, question: str, answer: str) -> None:
        with self.lock:
            self._check_version()
            self.items[self.key(question)] = answer
            self.items.move_to_end(self.key(question))
            if len(self.items) > self.size:
                self.items.popitem(last=False)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, max_length=64)


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    # 웹은 조회만 한다. 테이블(CREATE IF NOT EXISTS)은 쓰기 연결로 한 번 보장한 뒤 읽기 전용으로 연다
    Toolbox(sqlite3.connect(str(db_path))).conn.close()
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)


def create_app(
    db_path: Path | str,
    *,
    backend: str = "rules",
    gemini_model: str | None = None,
    limit: RateLimit | None = None,
    chat_log: ChatLog | None = None,
    admin_key: str | None = None,
) -> FastAPI:
    """`admin_key` (기본: 환경 변수 ADMIN_KEY) 가 있으면 /admin 에서 답변 기록을 볼 수 있다."""
    db_path = Path(db_path)
    limit = limit or RateLimit()
    chat_log = chat_log or ChatLog()
    admin = AdminGuard(admin_key if admin_key is not None else os.environ.get("ADMIN_KEY", ""))
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    conn = _open_readonly(db_path)
    lock = threading.Lock()  # sqlite 연결 하나를 스레드풀 요청이 나눠 쓴다
    toolbox = Toolbox(conn)
    gemini_tools = _LockedTools(toolbox, lock)
    rules = RuleBot(toolbox)
    answers = AnswerCache(db_path)
    # 화면 조회(포메이션·팀컬러 정보 등)도 DB가 바뀔 때까지 결과를 재사용한다 (Render 무료 CPU에선 한 번에 수 초)
    pages = AnswerCache(db_path, size=300)
    gemini_sessions: dict[str, Any] = {}

    app = FastAPI(title="FCO 랭커 메타", docs_url="/api/docs", redoc_url=None)
    app.state.conn = conn
    app.state.chat_log = chat_log
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def revalidate(request, call_next):
        # 브라우저가 옛 화면을 저장해 두고 쓰지 않도록 매번 확인하게 한다 (바뀌지 않았으면 304로 가볍게)
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    def tool(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        key = name + json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
        if (content := pages.get(key)) is None:
            with lock:
                content, is_error = toolbox.run(name, tool_input)
            if is_error:
                raise HTTPException(status_code=400, detail=json.loads(content)["error"])
            pages.put(key, content)
        return json.loads(content)

    def warm() -> None:
        """첫 화면이 부르는 조회를 미리 계산해 둔다 (서버가 뜬 직후 첫 방문이 느리지 않게; 같은 키로 pages에 담긴다)."""
        try:
            tool("list_available_data", {})
            formations = tool("list_formations", {"team_color": None})["formations"]
            if formations:
                tool("get_formation_overview", {"formation": formations[0]["formation"]})
            colors = tool("query_rankers", {"group_by": "team_color", "limit": 30})["rows"]
            if colors:
                tool("get_team_color_overview", {"team_color": colors[0]["team_color"]})
        except Exception:
            log.exception("warming the page cache failed")

    app.state.warm = warm

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

    @app.get("/api/teamcolors")
    def teamcolors() -> dict[str, Any]:
        """팀컬러 분포 (랭킹 상위 10,000명, 랭커 많은 순 30개; groups = 전체 팀컬러 수).
        icon = 랭킹 화면의 엠블럼·국기·특수 아이콘 (넥슨 CDN externalAssets/common 아래 경로, 모르면 없음)."""
        out = tool("query_rankers", {"group_by": "team_color", "limit": 30})
        if (icons := pages.get("team_color_icons")) is None:
            with lock:
                icons = json.dumps(toolbox.team_color_icons(), ensure_ascii=False)
            pages.put("team_color_icons", icons)
        icon_of = json.loads(icons)
        for r in out["rows"]:
            r["icon"] = icon_of.get(r["team_color"])
        return out

    @app.get("/api/teamcolor")
    def teamcolor(name: str = Query(min_length=1, max_length=40)) -> dict[str, Any]:
        """팀컬러 하나의 종합 정보 (사용률·승률·포메이션·순위 구간·최상위 랭커 스쿼드·베스트 11)."""
        return tool("get_team_color_overview", {"team_color": name})

    @app.post("/api/chat")
    def chat(req: ChatRequest, request: Request) -> StreamingResponse:
        """답을 한 줄에 하나씩 JSON 이벤트로 흘려보낸다: start(session_id) → delta(text)… → done.
        reset = 지금까지 보낸 글을 지운다 (도구를 부르기 전에 쓴 글이었음). tool = 지금 부르는 도구(name, args).
        질문 수 제한에 걸리면 429 (detail = 안내 문구)."""
        who = _visitor(request)
        if refused := limit.check(who):
            chat_log.record(q=req.message, outcome="limited", error=refused)
            raise HTTPException(status_code=429, detail=refused)
        if backend != "gemini":
            with lock:
                answer = rules.ask(req.message)
            chat_log.record(q=req.message, outcome="ok", model="rules", tools=[answer.tool] if answer.tool else [])
            lines = [_event(type="start", session_id=req.session_id), _event(type="delta", text=answer.text),
                     _event(type="done", tool_calls=[answer.tool] if answer.tool else [])]  # fmt: skip
            return StreamingResponse(iter(lines), media_type="application/x-ndjson")

        session_id = req.session_id or uuid.uuid4().hex
        with lock:
            session = gemini_sessions.get(session_id)
            if session is None:
                if len(gemini_sessions) >= MAX_SESSIONS:
                    gemini_sessions.pop(next(iter(gemini_sessions)))
                session = gemini_sessions[session_id] = (_gemini_chat(gemini_tools, gemini_model), threading.Lock())
        bot, busy = session

        def events():
            # 관리자 페이지용 기록: 끝까지 가지 못하고 닫히면(사용자가 정지·연결 끊김) cancelled로 남는다
            started = time.monotonic()
            entry: dict[str, Any] = {"q": req.message, "session": session_id[:8], "outcome": "cancelled"}
            try:
                yield from answer_events(entry)
            finally:
                chat_log.record(**entry, ms=round((time.monotonic() - started) * 1000))

        def answer_events(entry: dict[str, Any]):
            yield _event(type="start", session_id=session_id)
            # 모델을 기다리는 동안 DB 잠금을 잡지 않는다 (도구 실행 때만 gemini_tools가 잡는다).
            # 같은 대화의 질문은 차례로. 사용자가 정지하면 이 생성기가 닫히고 그 질문은 기록에서 빠진다
            with busy:
                first = not bot.contents  # 대화의 첫 질문만 캐시한다
                cached = answers.get(req.message) if first else None
                if cached is not None:
                    bot.remember(req.message, cached)  # 이어지는 질문이 이 답을 맥락으로 쓰도록
                    entry.update(outcome="cached", chars=len(cached))
                    yield _event(type="delta", text=cached)
                    yield _event(type="done", tool_calls=[], cached=True)
                    return
                stream = bot.ask_stream(req.message)
                try:
                    while True:
                        try:
                            piece = next(stream)
                        except StopIteration as stop:
                            turn = stop.value
                            break
                        if piece is RESET:
                            yield _event(type="reset")
                        elif isinstance(piece, ToolCall):
                            yield _event(type="tool", name=piece.name, args=piece.args)
                        elif isinstance(piece, Recheck):  # 수치를 다시 쓰는 중 (화면에는 진행 문구)
                            yield _event(type="tool", name="check_numbers", args={})
                        else:
                            yield _event(type="delta", text=piece)
                except Exception as exc:  # Gemini 실패 → 원인은 서버 로그에만, 사용자에게는 혼잡 안내만
                    log.exception("gemini chat failed: %s", describe_error(exc))
                    entry.update(outcome="busy", error=describe_error(exc), model=getattr(bot, "last_model", None))
                    yield _event(type="reset")
                    yield _event(type="delta", text=BUSY_MESSAGE)
                    yield _event(type="done", tool_calls=[], error="unavailable")
                    return
                finally:  # 정지로 끊겨도 잠금을 풀기 전에 닫아, 그 질문을 기록에서 빼는 일이 다음 질문보다 먼저 끝나게 한다
                    stream.close()
            # 근거 줄과 대체 모델은 서버 로그에만 남기고 사용자 답에는 넣지 않는다
            log.info("answered with %s; evidence: %s", bot.last_model, turn.evidence)
            if first and turn.finish_reason == "STOP":  # 잘리거나 도구 한도에 걸린 답은 캐시하지 않는다
                answers.put(req.message, turn.text)
            entry.update(outcome="ok", model=bot.last_model, tools=[n for n, _ in turn.tool_calls],
                         finish=turn.finish_reason, chars=len(turn.text))  # fmt: skip
            yield _event(type="done", tool_calls=[n for n, _ in turn.tool_calls], model=bot.last_model)

        return StreamingResponse(events(), media_type="application/x-ndjson")

    # --- 관리자: 답변 기록 (ADMIN_KEY가 없으면 없는 페이지, 5회 틀린 IP는 막힘) ---
    @app.get("/admin", include_in_schema=False)
    def admin_page(request: Request) -> FileResponse:
        admin.open_to(_visitor(request))
        return FileResponse(STATIC_DIR / "admin.html")  # 비밀번호는 페이지에서 입력받아 아래 API에 헤더로 보낸다

    @app.get("/api/admin/logs", include_in_schema=False)
    def admin_logs(
        request: Request, days: int = Query(default=7, ge=1, le=90), x_admin_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        admin.check(_visitor(request), x_admin_key)
        return chat_log.summary(days)

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
