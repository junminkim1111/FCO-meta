"""FastAPI app: JSON API over the chatbot tools + a single static page."""

from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..chatbot import gemini
from ..chatbot.gemini import RESET, Escalate, Recheck, Thought, ToolCall, ToolResult, describe_error
from ..chatbot.router import REFUSAL, Route, classify, jev_ready
from ..chatbot.rules import RuleBot
from ..chatbot.tools import Toolbox
from .chatlog import ChatLog
from .team import Team, TeamNotFound, fetch_team

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
MAX_SESSIONS = 200
MAX_CACHED = 500  # 캐시에 두는 첫 질문 답 수
BUSY_MESSAGE = "지금 서버가 혼잡해 답을 드리지 못했어요. 잠시 후 다시 물어봐 주세요."
KST = timezone(timedelta(hours=9))
FLASH_MODEL = "gemini-3.5-flash"  # 스쿼드·복잡한 질문, 그리고 Flash-Lite가 두 번째 도구를 부르면 이어받는 모델
DEEPSEEK_MODEL = "deepseek/deepseek-v4-pro"  # /deep, /compare (OpenRouter, 서버 환경 변수 OPENROUTER_API_KEY)
LONG_DEADLINE = 300.0  # /deep·/compare의 질문당 대기 상한(초). 일반 질문은 120초
COMPARE_PER_HOUR = 2  # /compare는 비싸서 IP당 1시간에 이만큼
TEAM_PER_HOUR = 20  # @닉네임 팀 불러오기: 한 사람이 1시간에 (넥슨 API 4~5회씩)
TEAM_CACHE_SECONDS = 600  # 같은 닉네임은 이 동안 다시 부르지 않는다
# 스쿼드 구성·복잡한 질문에 자주 나오는 말 (띄어쓰기 무시) → 처음부터 3.5 Flash
HEAVY_WORDS = re.compile(r"짜(줘|봐|라|주세요|$)|스쿼드|라인업|베스트11|업그레이드|대신|바꾸|바꿔|급여합|합쳐서|케미|단일|현역|달수있")
DEEP_LABEL = {"deep": "DeepSeek V4 Pro 생각 끔", "deep_r": "DeepSeek V4 Pro 생각 켬"}


def heavy_word(question: str) -> str | None:
    """The first heavy-question word in `question` (spaces ignored), or None."""
    m = HEAVY_WORDS.search("".join(question.split()))
    return m.group(0) if m else None


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


class HourlyLimit:
    """At most `n` uses per person per hour (in memory, like RateLimit)."""

    def __init__(self, n: int, now: Callable[[], float] = time.time):
        self.n, self.now, self.used = n, now, {}
        self.lock = threading.Lock()

    def check(self, who: str) -> bool:
        """Counts one use; False (nothing counted) when the hour's uses are spent."""
        with self.lock:
            now = self.now()
            recent = self.used.setdefault(who, deque())
            while recent and recent[0] <= now - 3600:
                recent.popleft()
            if len(recent) >= self.n:
                return False
            recent.append(now)
            return True


class _Session:
    """One conversation: the questions and answers so far, and a chat per model. A model that did not answer some
    turns gets them as text (remember) before its next question, so follow-ups keep the context across models."""

    def __init__(self) -> None:
        self.lock = threading.Lock()  # 같은 대화의 질문은 차례로
        self.history: list[tuple[str, str]] = []
        self.bots: dict[str, list[Any]] = {}  # key → [chat, history 중 이 chat이 아는 개수]
        self.team: Team | None = None  # @닉네임으로 붙인 팀 (뗄 때까지 질문마다 모델에 함께 보낸다)

    def ask(self, message: str) -> str:
        """What the model gets: the attached team ahead of the question."""
        # ponytail: 질문마다 팀 블록(약 400토큰)을 다시 붙인다 — 대화가 길어지면 바뀐 때만 붙이도록
        return f"{self.team.for_model()}\n\n{message}" if self.team else message

    def bot(self, key: str, make: Callable[[], Any]) -> Any:
        entry = self.bots.get(key)
        if entry is None:
            entry = self.bots[key] = [make(), 0]
        for q, a in self.history[entry[1]:]:
            entry[0].remember(q, a)
        entry[1] = len(self.history)
        return entry[0]

    def answered(self, key: str | None, question: str, answer: str) -> None:
        self.history.append((question, answer))
        if key in self.bots:  # 답한 chat은 이 턴을 이미 안다
            self.bots[key][1] = len(self.history)


class _TraceLog:
    """What /trace shows for one question — kept for the admin page whether or not the visitor turned it on."""

    def __init__(self) -> None:
        self.started, self.lines, self.answer = time.monotonic(), [], None

    def add(self, kind: str, **fields: Any) -> None:
        if kind == "thought" and self.lines and self.lines[-1]["kind"] == "thought":  # 생각은 조각으로 와서 한 줄로 잇는다
            self.lines[-1]["text"] += fields["text"]
            return
        self.lines.append({"at": round(time.monotonic() - self.started, 1), "kind": kind, **fields})

    def detail(self) -> dict[str, Any]:
        return {"trace": self.lines, "answer": self.answer}


def _relay(stream: Any, trace: bool, keep: _TraceLog | None = None, **extra: Any):
    """ask_stream events → NDJSON lines (trace adds the model's thoughts, tool results and hand-offs; `keep` records
    them all for the admin page either way); returns the turn."""
    keep = keep or _TraceLog()
    started = False
    while True:
        try:
            piece = next(stream)
        except StopIteration as stop:
            return stop.value
        line: dict[str, Any] | None = None  # trace 한 줄
        if piece is RESET:
            yield _event(type="reset", **extra)
        elif isinstance(piece, ToolCall):
            keep.add("tool", name=piece.name, args=piece.args)
            yield _event(type="tool", name=piece.name, args=piece.args, **extra)
        elif isinstance(piece, Recheck):  # 수치를 다시 쓰는 중 (화면에는 진행 문구)
            yield _event(type="tool", name="check_numbers", args={}, **extra)
            line = {"kind": "recheck", "text": "결과에 없는 수치: " + ", ".join(piece.numbers)}
        elif isinstance(piece, Escalate):
            yield _event(type="tool", name="escalate", args={}, **extra)
            line = {"kind": "escalate", "text": f"{piece.reason} → {piece.model}이 이어받음"}
        elif isinstance(piece, Thought):
            line = {"kind": "thought", "text": piece.text}
        elif isinstance(piece, gemini.Fallback):
            line = {"kind": "fallback", "text": f"{piece.failed} {piece.reason} → {piece.model}이 대신"}
        elif isinstance(piece, ToolResult):
            line = {"kind": "result", "name": piece.name, "ok": piece.ok, "text": piece.preview}
        else:
            if not started:
                started = True
                keep.add("first", text="답이 나오기 시작")
            yield _event(type="delta", text=piece, **extra)
        if line:
            keep.add(**line)
            if trace:
                yield _event(type="trace", **line, **extra)


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


class CompareChoice(BaseModel):
    compare_id: str = Field(min_length=1, max_length=64)
    pane: Literal["flash", "deep_r", "deep"]


class TeamRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=30)
    session_id: str | None = Field(default=None, max_length=64)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, max_length=64)
    mode: Literal["auto", "deep", "deep_r"] = "auto"  # deep = /deep (DeepSeek 생각 끔), deep_r = /deep --r (생각 켬)
    trace: bool = False  # /trace: 모델의 생각·도구 결과·모델 전환도 보낸다


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
    sessions: OrderedDict[str, _Session] = OrderedDict()
    compare_limit = HourlyLimit(COMPARE_PER_HOUR)
    team_limit = HourlyLimit(TEAM_PER_HOUR)
    teams: dict[str, tuple[float, Team]] = {}  # 닉네임 → (가져온 시각, 팀)
    team_lock = threading.Lock()  # 넥슨 API 클라이언트는 한 번에 하나씩
    compares: OrderedDict[str, dict[str, Any]] = OrderedDict()  # compare_id → 질문·칸별 답 (답 고르기용, 최근 것만)

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

    def session_for(session_id: str) -> _Session:
        with lock:
            session = sessions.get(session_id)
            if session is None:
                if len(sessions) >= MAX_SESSIONS:
                    sessions.popitem(last=False)
                session = sessions[session_id] = _Session()
            return session

    def make_bot(key: str) -> Callable[[], Any]:
        if key == "gemini":
            return lambda: _gemini_chat(gemini_tools, gemini_model)
        if key == "flash":
            return lambda: _flash_chat(gemini_tools)
        return lambda: _deepseek_chat(gemini_tools, reasoning=key == "deep_r")

    @app.post("/api/chat")
    def chat(req: ChatRequest, request: Request) -> StreamingResponse:
        """답을 한 줄에 하나씩 JSON 이벤트로 흘려보낸다: start(session_id) → delta(text)… → done.
        reset = 지금까지 보낸 글을 지운다 (도구를 부르기 전에 쓴 글이었음). tool = 지금 부르는 도구(name, args).
        trace = (/trace) 모델 선택·생각·도구 결과·모델 전환. 질문 수 제한에 걸리면 429 (detail = 안내 문구)."""
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
        session = session_for(session_id)

        def events():
            # 관리자 페이지용 기록: 끝까지 가지 못하고 닫히면(사용자가 정지·연결 끊김) cancelled로 남는다
            started = time.monotonic()
            entry: dict[str, Any] = {"q": req.message, "session": session_id[:8], "outcome": "cancelled"}
            if session.team:
                entry["team"] = session.team.nickname
            keep = _TraceLog()  # 관리자 페이지의 답변 기록에서 줄을 누르면 보이는 trace·답변
            try:
                yield from answer_events(entry, keep)
            finally:
                chat_log.record(**entry, ms=round((time.monotonic() - started) * 1000), detail=keep.detail())

        def answer_events(entry: dict[str, Any], keep: _TraceLog):
            yield _event(type="start", session_id=session_id)
            # 모델을 기다리는 동안 DB 잠금을 잡지 않는다 (도구 실행 때만 gemini_tools가 잡는다).
            # 같은 대화의 질문은 차례로. 사용자가 정지하면 이 생성기가 닫히고 그 질문은 기록에서 빠진다
            with session.lock:
                first = not session.history  # 대화의 첫 질문만 캐시한다
                route = _jev_route(req.message, session.history[-1][0] if session.history else None)
                if route and route.blocked:  # 범위 밖·프롬프트 공격 → LLM 없이 거절 문구
                    session.answered(None, req.message, REFUSAL)
                    entry.update(outcome="blocked", route=f"{route.scope} ({route.describe()})")
                    keep.add("route", text=f"{route.describe()} → 거절 문구 (모델 호출 없음)")
                    keep.answer = REFUSAL
                    if req.trace:
                        yield _event(type="trace", kind="route", text=f"{route.describe()} → 거절 문구 (모델 호출 없음)")
                    yield _event(type="delta", text=REFUSAL)
                    yield _event(type="done", tool_calls=[], blocked=True)
                    return
                if req.mode == "auto":
                    cached = answers.get(req.message) if first and not session.team else None
                    if cached is not None:
                        session.answered(None, req.message, cached)  # 이어지는 질문이 이 답을 맥락으로 쓰도록
                        entry.update(outcome="cached", chars=len(cached))
                        keep.add("route", text="같은 첫 질문의 저장된 답 (모델 호출 없음)")
                        keep.answer = cached
                        yield _event(type="delta", text=cached)
                        yield _event(type="done", tool_calls=[], cached=True)
                        return
                    key = "gemini"
                    if route:  # Jev가 판단 (없거나 실패하면 키워드)
                        heavy, why = route.heavy, route.describe()
                    else:
                        word = heavy_word(req.message)
                        heavy, why = bool(word), f"키워드 '{word}'" if word else "키워드 없음"
                    options: dict[str, Any] = {"model": FLASH_MODEL} if heavy else {"escalate_to": FLASH_MODEL}
                    why += f" → {FLASH_MODEL}" if heavy else f" → 기본 Flash-Lite (두 번째 도구부터 {FLASH_MODEL})"
                    if _flash_blocked() and _deepseek_ready():  # 3.5 Flash 한도 소진·혼잡: 그 몫은 DeepSeek 생각 끔이
                        if heavy:
                            key, options = "deep", {"deadline": LONG_DEADLINE}
                            why += f" (한도 소진 → {DEEP_LABEL['deep']})"
                        else:  # 넘겨받을 모델이 없으니 Flash-Lite가 끝까지 답한다
                            options = {}
                            why += " (3.5 Flash 한도 소진 → Flash-Lite가 끝까지)"
                    entry["route"] = why
                else:
                    if not _deepseek_ready():
                        entry.update(outcome="error", error="OPENROUTER_API_KEY 없음")
                        yield _event(type="delta", text="지금은 DeepSeek을 쓸 수 없어요 (서버에 OpenRouter 키가 없습니다).")
                        yield _event(type="done", tool_calls=[], error="unavailable")
                        return
                    key, options, why = req.mode, {"deadline": LONG_DEADLINE}, f"/deep → {DEEP_LABEL[req.mode]}"
                    entry["mode"] = req.mode
                options["thoughts"] = True  # 관리자 페이지에 남기려고 /trace를 안 켜도 생각을 받는다
                keep.add("route", text=why)
                if req.trace:
                    yield _event(type="trace", kind="route", text=why)
                # 3.5 Flash로 가는 질문은 Gemini 대체 모델을 쓰지 않고, 실패하면 DeepSeek 생각 끔이 처음부터 답한다
                handover = key == "gemini" and options.get("model") == FLASH_MODEL and _deepseek_ready()
                if handover:
                    options["fallback"] = False
                bot = session.bot(key, make_bot(key))
                stream = bot.ask_stream(session.ask(req.message), **options)
                try:
                    try:
                        turn = yield from _relay(stream, req.trace, keep)
                    except gemini.GeminiUnavailable as exc:
                        if not handover:
                            raise
                        stream.close()
                        line = {"kind": "fallback", "text": f"{FLASH_MODEL} 실패 ({describe_error(exc)}) → {DEEP_LABEL['deep']}이 처음부터"}
                        keep.add(**line)
                        entry["route"] = f"{entry.get('route', '')} (3.5 Flash 실패 → {DEEP_LABEL['deep']})"
                        if req.trace:
                            yield _event(type="trace", **line)
                        yield _event(type="reset")
                        key = "deep"
                        bot = session.bot(key, make_bot(key))
                        stream = bot.ask_stream(session.ask(req.message), deadline=LONG_DEADLINE, thoughts=True)
                        turn = yield from _relay(stream, req.trace, keep)
                except Exception as exc:  # 모델 실패 → 원인은 서버 로그에만, 사용자에게는 혼잡 안내만
                    log.exception("chat failed (%s): %s", key, describe_error(exc))
                    entry.update(outcome="busy", error=describe_error(exc), model=getattr(bot, "last_model", None))
                    keep.add("error", text=describe_error(exc))
                    keep.answer = BUSY_MESSAGE
                    yield _event(type="reset")
                    yield _event(type="delta", text=BUSY_MESSAGE)
                    yield _event(type="done", tool_calls=[], error="unavailable")
                    return
                finally:  # 정지로 끊겨도 잠금을 풀기 전에 닫아, 그 질문을 기록에서 빼는 일이 다음 질문보다 먼저 끝나게 한다
                    stream.close()
                session.answered(key, req.message, turn.text)
            # 근거 줄과 대체 모델은 서버 로그에만 남기고 사용자 답에는 넣지 않는다
            log.info("answered with %s; evidence: %s", bot.last_model, turn.evidence)
            if first and req.mode == "auto" and not entry.get("team") and turn.finish_reason == "STOP":  # 잘리거나 도구 한도에 걸린 답은 캐시하지 않는다
                answers.put(req.message, turn.text)
            entry.update(outcome="ok", model=bot.last_model, tools=[n for n, _ in turn.tool_calls],
                         finish=turn.finish_reason, chars=len(turn.text))  # fmt: skip
            keep.add("done", text=f"모델 {bot.last_model}")
            keep.answer = turn.text
            yield _event(type="done", tool_calls=[n for n, _ in turn.tool_calls], model=bot.last_model)

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/api/compare")
    def compare(req: ChatRequest, request: Request) -> StreamingResponse:
        """/compare: 같은 질문을 3.5 Flash·DeepSeek 생각 켬·끔이 동시에 답한다. 이벤트는 /api/chat과 같고 pane으로 구분하며,
        칸마다 done이 온 뒤 마지막에 all_done. 대화의 앞선 질문·답은 맥락으로 주되 이 비교 답은 대화에 남기지 않는다."""
        who = _visitor(request)
        if backend != "gemini":
            raise HTTPException(status_code=400, detail="비교는 AI 모델이 켜져 있을 때만 쓸 수 있어요.")
        if not compare_limit.check(who):
            chat_log.record(q=req.message, outcome="limited", error="compare")
            raise HTTPException(status_code=429, detail=f"비교는 1시간에 {COMPARE_PER_HOUR}번까지 쓸 수 있어요. 잠시 후 다시 해 주세요.")
        if refused := limit.check(who):
            chat_log.record(q=req.message, outcome="limited", error=refused)
            raise HTTPException(status_code=429, detail=refused)
        session_id = req.session_id or uuid.uuid4().hex
        history = list(session_for(session_id).history)
        ask = session_for(session_id).ask(req.message)
        route = _jev_route(req.message, history[-1][0] if history else None)
        if route and route.blocked:  # 범위 밖·공격은 세 모델 모두 부르지 않는다
            chat_log.record(q=req.message, outcome="blocked", route=f"{route.scope} ({route.describe()})")
            lines_ = [_event(type="start", session_id=session_id, compare_id=None, panes=list(COMPARE_PANES)),
                      *(_event(type=t, pane=p, **kw) for p in COMPARE_PANES for t, kw in (("delta", {"text": REFUSAL}), ("done", {"tool_calls": []}))),
                      _event(type="all_done", blocked=True)]  # fmt: skip
            return StreamingResponse(iter(lines_), media_type="application/x-ndjson")
        compare_id = uuid.uuid4().hex[:12]
        lines: queue.Queue[str | None] = queue.Queue()
        results: dict[str, dict[str, Any]] = {}  # 칸별 모델·걸린 시간·도구·답 (관리자 기록용)
        started_all = time.monotonic()

        def finish() -> None:
            """마지막 칸이 끝나면: 답 고르기용으로 두고 관리자 기록에 남긴다 (사용자가 비교 창을 닫았어도)."""
            panes = {p: {k: v for k, v in results[p].items() if k != "finished"} for p in COMPARE_PANES}
            with lock:
                compares[compare_id] = {"q": req.message, "session_id": session_id, "panes": panes}
                while len(compares) > MAX_SESSIONS:
                    compares.popitem(last=False)
            chat_log.record(q=req.message, outcome="compare", session=session_id[:8], compare_id=compare_id, panes=panes,
                            ms=round((time.monotonic() - started_all) * 1000))  # fmt: skip

        def run(pane: str) -> None:
            # ponytail: 사용자가 창을 닫아도 세 칸은 끝까지 답한다 (스레드를 멈출 방법이 없음 — 비교는 시간당 2번이라 둠)
            started, bot = time.monotonic(), None
            result = results[pane] = {"label": COMPARE_PANES[pane]}
            try:
                if pane != "flash" and not _deepseek_ready():
                    raise RuntimeError("OPENROUTER_API_KEY 없음")
                bot = make_bot(pane)()
                for q, a in history:
                    bot.remember(q, a)
                turn = yield_into(lines, bot.ask_stream(ask, deadline=LONG_DEADLINE, thoughts=req.trace), pane)
                tools = [n for n, _ in turn.tool_calls]
                result.update(answer=turn.text, tools=tools, finish=turn.finish_reason)
                lines.put(_event(type="done", pane=pane, model=bot.last_model, tool_calls=tools))
            except Exception as exc:
                log.exception("compare %s failed: %s", pane, describe_error(exc))
                result.update(answer=None, error=describe_error(exc))
                lines.put(_event(type="reset", pane=pane))
                lines.put(_event(type="delta", pane=pane, text=BUSY_MESSAGE))
                lines.put(_event(type="done", pane=pane, tool_calls=[], error="unavailable"))
            finally:
                result.update(model=getattr(bot, "last_model", None), ms=round((time.monotonic() - started) * 1000))
                with lock:
                    result["finished"] = True
                    last = all(results.get(p, {}).get("finished") for p in COMPARE_PANES)
                if last:
                    finish()
                lines.put(None)

        def yield_into(out: queue.Queue, stream: Any, pane: str) -> Any:
            relay = _relay(stream, req.trace, pane=pane)
            while True:
                try:
                    out.put(next(relay))
                except StopIteration as stop:
                    return stop.value

        def events():
            yield _event(type="start", session_id=session_id, compare_id=compare_id, panes=list(COMPARE_PANES))
            for pane in COMPARE_PANES:
                threading.Thread(target=run, args=(pane,), daemon=True).start()
            finished = 0
            while finished < len(COMPARE_PANES):
                line = lines.get()
                if line is None:
                    finished += 1
                else:
                    yield line
            yield _event(type="all_done")

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/api/team")
    def attach_team(req: TeamRequest, request: Request) -> dict[str, Any]:
        """@닉네임: that user's latest starting XI, attached to the conversation (sent ahead of each question)."""
        if not os.environ.get("NEXON_API_KEY"):
            raise HTTPException(status_code=503, detail="지금은 팀을 불러올 수 없어요 (서버에 넥슨 API 키가 없습니다).")
        nickname = req.nickname.strip()
        cached = teams.get(nickname)
        if cached and time.time() - cached[0] < TEAM_CACHE_SECONDS:
            team = cached[1]
        else:
            if not team_limit.check(_visitor(request)):
                raise HTTPException(status_code=429, detail=f"팀 불러오기는 1시간에 {TEAM_PER_HOUR}번까지 할 수 있어요.")
            try:
                with team_lock, closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as team_db:
                    team = fetch_team(_nexon(), team_db, nickname)
            except TeamNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except Exception as exc:  # 넥슨 API 혼잡·점검·하루 호출 예산
                log.warning("team lookup failed: %s", exc)
                raise HTTPException(status_code=503, detail="넥슨 서버에서 팀을 가져오지 못했어요. 잠시 후 다시 해 주세요.") from None
            teams[nickname] = (time.time(), team)
        session_id = req.session_id or uuid.uuid4().hex
        session_for(session_id).team = team
        return {"session_id": session_id, **team.to_json()}

    @app.delete("/api/team")
    def detach_team(session_id: str = Query(max_length=64)) -> dict[str, bool]:
        session_for(session_id).team = None
        return {"ok": True}

    @app.post("/api/compare/choice")
    def compare_choice(choice: CompareChoice) -> dict[str, Any]:
        """비교에서 고른 답: 그 질문과 답을 대화에 남겨 이어지는 질문의 맥락으로 쓰고, 관리자 기록에 고른 칸을 남긴다."""
        with lock:
            found = compares.get(choice.compare_id)
        answer = found and found["panes"].get(choice.pane, {}).get("answer")
        if not answer:
            raise HTTPException(status_code=404, detail="고를 수 있는 답이 없습니다.")
        session_for(found["session_id"]).answered(None, found["q"], answer)
        chat_log.record(q=found["q"], outcome="compare_pick", compare_id=choice.compare_id, pane=choice.pane)
        return {"ok": True, "pane": choice.pane, "label": COMPARE_PANES[choice.pane]}

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

    @app.get("/api/admin/detail", include_in_schema=False)
    def admin_detail(request: Request, id: str = Query(max_length=40), x_admin_key: str | None = Header(default=None)) -> dict[str, Any]:
        """A question's trace and answer (saved ones always, others for the last week)."""
        admin.check(_visitor(request), x_admin_key)
        if (found := chat_log.detail(id)) is None:
            raise HTTPException(status_code=404, detail="일주일이 지나 지워졌거나 없는 기록입니다.")
        return found

    @app.post("/api/admin/saved/{item_id}", include_in_schema=False)
    def admin_save(item_id: str, request: Request, x_admin_key: str | None = Header(default=None)) -> dict[str, Any]:
        admin.check(_visitor(request), x_admin_key)
        if (item := chat_log.save(item_id)) is None:
            raise HTTPException(status_code=404, detail="일주일이 지나 지워졌거나 없는 기록입니다.")
        return item

    @app.delete("/api/admin/saved/{item_id}", include_in_schema=False)
    def admin_unsave(item_id: str, request: Request, x_admin_key: str | None = Header(default=None)) -> dict[str, bool]:
        admin.check(_visitor(request), x_admin_key)
        if not chat_log.unsave(item_id):
            raise HTTPException(status_code=404, detail="저장한 기록이 아닙니다.")
        return {"ok": True}

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


COMPARE_PANES = {"flash": "Gemini 3.5 Flash", "deep_r": DEEP_LABEL["deep_r"], "deep": DEEP_LABEL["deep"]}


def _gemini_chat(toolbox: Toolbox | _LockedTools, model: str | None):
    from google import genai

    from ..chatbot.gemini import GeminiChat, models_from_env

    primary, fallbacks = models_from_env(model)
    return GeminiChat(genai.Client(), toolbox, model=primary, fallback_models=fallbacks)


def _flash_chat(toolbox: Toolbox | _LockedTools):
    """3.5 Flash only (/compare): no fallback, so the pane really shows that model."""
    from google import genai

    from ..chatbot.gemini import GeminiChat

    chat = GeminiChat(genai.Client(), toolbox, model=FLASH_MODEL, fallback_models=[])
    chat.discover = False
    return chat


_HTTP: Any = None


def _http() -> Any:
    import httpx

    global _HTTP
    _HTTP = _HTTP or httpx.Client()
    return _HTTP


def _jev_route(question: str, previous: str | None) -> Route | None:
    """Jev's routing for this question, or None (no OPENROUTER_API_KEY, error or timeout → the keyword rule)."""
    if not jev_ready():
        return None
    try:
        return classify(_http(), question, previous)
    except Exception as exc:
        log.warning("jev routing failed, using keywords: %s", exc)
        return None


_NEXON: Any = None


def _nexon() -> Any:
    """One NEXON Open API client for @닉네임 lookups (its own daily call budget, in memory)."""
    global _NEXON
    if _NEXON is None:
        from ..openapi.budget import CallBudget
        from ..openapi.client import NexonOpenApiClient

        _NEXON = NexonOpenApiClient(budget=CallBudget(daily_limit=int(os.environ.get("NEXON_DAILY_LIMIT", "2000"))))
    return _NEXON


def _flash_blocked() -> bool:
    """3.5 Flash is being skipped now: it ran out of its daily quota (until midnight Pacific) or was overloaded."""
    return gemini._COOLDOWN.get(FLASH_MODEL, 0.0) > time.time()


def _deepseek_ready() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_COMPAT_API_KEY"))


def _deepseek_chat(toolbox: Toolbox | _LockedTools, *, reasoning: bool):
    from ..chatbot.llm import OpenAIChat

    return OpenAIChat(_http(), toolbox, model=DEEPSEEK_MODEL, extra={"reasoning": {"enabled": reasoning}})
