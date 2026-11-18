"""FastAPI app: JSON API over the chatbot tools + a single static page."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter, OrderedDict, deque
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager, closing
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

from ..chatbot import gemini
from ..chatbot.gemini import RESET, Escalate, Recheck, Thought, ToolCall, ToolResult, describe_error
from ..chatbot.router import REFUSAL, Route, classify, jev_ready
from ..chatbot.rules import RuleBot
from ..chatbot.tools import Toolbox
from .chatlog import ChatLog
from .squadfeed import FeedError, squad_link, squad_view
from .team import Team, TeamNotFound, fetch_feed, fetch_team
from ..config import KST

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
GUIDE_TOPICS = ("screen", "ask", "team", "feed", "commands", "send", "data")  # /guide/<주제> → static/guide-<주제>.html
MAX_SESSIONS = 200
MAX_CACHED = 500  # 캐시에 두는 첫 질문 답 수
BUSY_MESSAGE = "지금 서버가 혼잡해 답을 드리지 못했어요. 잠시 후 다시 물어봐 주세요."
FLASH_MODEL = "gemini-3.5-flash"  # 스쿼드·복잡한 질문, 그리고 Flash-Lite가 두 번째 도구를 부르면 이어받는 모델
DEEPSEEK_MODEL = "deepseek/deepseek-v4.1-flash-1017"  # 기본(/model 없을 때), /model deepseek (OpenRouter, 서버 환경 변수 OPENROUTER_API_KEY)
LUNA_MODEL = "openai/gpt-6-luna"  # /model gpt (OpenRouter, OpenAI 제공사로 고정 — 2026-11-12 평가와 같은 설정)
LONG_DEADLINE = 300.0  # /model deepseek·gpt의 질문당 대기 상한(초). 일반 질문은 120초
WARM_FORMATIONS, WARM_TEAM_COLORS = 15, 20  # 서버가 켜질 때 미리 계산할 상세 (화면의 더보기 범위와 같게)
# 유료 등급 기준 100만 토큰당 달러 (입력, 출력 — 생각 포함). 앞에서부터 모델 이름에 들어 있는 것으로 (lite를 flash보다 먼저).
# 무료 등급이라 실제 청구는 없고 유료 전환 판단용 추정치. ponytail: 캐시 할인은 빼고 셈 (실제보다 조금 비싸게 나옴)
PRICES = {"deepseek": (0.30, 1.20), "luna": (0.10, 0.50), "flash-lite": (0.30, 2.50), "flash": (1.50, 9.00)}  # 앞에서부터: deepseek-v4.1-flash가 "flash"로 잡히지 않게
FEEDBACK_PER_HOUR = 60  # 👍/👎: 한 사람이 1시간에
TEAM_PER_HOUR = 20  # @닉네임 팀 불러오기: 한 사람이 1시간에 (넥슨 API 4~5회씩)
TEAM_CACHE_SECONDS = 600  # 같은 닉네임은 이 동안 다시 부르지 않는다
SQUAD_TOOLS = ("recommend_squad", "check_squad")
# 스쿼드 구성·복잡한 질문에 자주 나오는 말 (띄어쓰기 무시) → 처음부터 3.5 Flash
HEAVY_WORDS = re.compile(r"짜(줘|봐|라|주세요|$)|스쿼드|라인업|베스트11|업그레이드|대신|바꾸|바꿔|급여합|합쳐서|케미|단일|현역|달수있")
DEEP_LABEL = {"deep": "DeepSeek V4.1 Flash 생각 끔", "deep_r": "DeepSeek V4.1 Flash 생각 켬"}
LUNA_LABEL = {"luna": "GPT-6 Luna 생각 끔", "luna_r": "GPT-6 Luna 생각 켬"}
GEMINI_LABEL = "Gemini 3.5 Flash"  # /model gemini의 Gemini (Jev 판단 없이 이 모델)


def gemini_fallbacks(models: Iterable[str]) -> list[str]:
    """/model gemini·flash: when 3.5 Flash fails, the other Flash models first, then the Lite ones."""
    others = [m for m in models if m != FLASH_MODEL]
    return [m for m in others if "lite" not in m] + [m for m in others if "lite" in m]


def heavy_word(question: str) -> str | None:
    """The first heavy-question word in `question` (spaces ignored), or None."""
    m = HEAVY_WORDS.search("".join(question.split()))
    return m.group(0) if m else None


def gemini_pick(question: str) -> tuple[dict[str, Any], str]:
    """(GeminiChat options, why) for a question on a server without OpenRouter: a heavy word → 3.5 Flash from the start,
    otherwise Flash-Lite handing over to 3.5 Flash from the second tool call."""
    word = heavy_word(question)
    why = f"키워드 '{word}'" if word else "키워드 없음"
    if word:
        return {"model": FLASH_MODEL}, f"{why} → {FLASH_MODEL}"
    return {"escalate_to": FLASH_MODEL}, f"{why} → 기본 Flash-Lite (두 번째 도구부터 {FLASH_MODEL})"


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
    LOCK_AT = 20  # 모든 IP를 합쳐 1시간에 이만큼 틀리면 1시간 잠금 (IP 헤더를 바꿔 가며 시도하는 것을 막는다)

    def __init__(self, key: str, now: Callable[[], float] = time.time):
        self.key, self.now = key, now
        self.failures: dict[str, int] = {}
        self.recent: deque[float] = deque()  # 최근 1시간의 틀린 시각 (모든 IP)
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
            while self.recent and self.recent[0] <= self.now() - 3600:
                self.recent.popleft()
            if len(self.recent) >= self.LOCK_AT:  # 맞는 비밀번호도 잠금이 풀릴 때까지 받지 않는다
                raise HTTPException(status_code=429, detail="틀린 비밀번호가 많아 관리자 로그인이 잠시 잠겼습니다. 1시간 뒤에 다시 해 주세요.")
            if hmac.compare_digest(given.encode(), self.key.encode()):
                self.failures.pop(who, None)
                return
            n = self.failures[who] = self.failures.get(who, 0) + 1
            self.recent.append(self.now())
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
        self.team: Team | None = None  # @닉네임으로 붙인 팀: 다음 질문 하나가 가져간다 (멘션처럼)
        self.last: str | None = None  # 마지막 질문 (팀 블록 없이, Jev가 이어지는 질문인지 볼 때)
        self.squad: dict[str, Any] | None = None  # 마지막으로 짠 스쿼드의 recommend_squad 인자 (기록엔 답 글만 남아 다음 질문에 붙인다)
        self.before_lineup: list[dict[str, Any]] | None = None
        self.turn_squad: dict[str, Any] | None = None
        self.before: dict[str, Any] | None = None  # 그 앞에 짠 스쿼드의 인자 ("다시 돌려놔")
        self.lineup: list[dict[str, Any]] | None = None  # 마지막으로 답한 스쿼드의 선발 (다시 짜면 바뀐 자리를 도구 결과에 붙인다)
        self.turn_lineup: list[dict[str, Any]] | None = None  # 이번 질문에서 짠 선발 (답을 마치면 lineup이 된다)

    def take(self, message: str) -> tuple[str, Team | None]:
        """What the model gets for this question: the attached team ahead of it, used up by this question
        (its block stays in the history, so follow-ups still know the team)."""
        team, self.team = self.team, None
        self.turn_lineup = None  # 실패한 앞 질문의 선발은 버린다
        self.turn_squad = None
        if self.squad:  # "WG 말고 UC로" 같은 이어지는 바람이 앞 조건(예산 등)을 잃지 않게
            message += f"\n\n(앞서 짠 스쿼드의 recommend_squad 인자: {json.dumps(self.squad, ensure_ascii=False)})"
            if self.before:
                message = message[:-1] + f"; 그 앞 스쿼드의 인자(되돌려 달라면 이것): {json.dumps(self.before, ensure_ascii=False)})"
        if self.lineup:
            message += "\n\n[확정 스쿼드: " + json.dumps(self.lineup, ensure_ascii=False) + "]"
            if self.before_lineup:
                message += "\n[되돌릴 스쿼드: " + json.dumps(self.before_lineup, ensure_ascii=False) + "]"
        return (f"{team.for_model()}\n\n{message}" if team else message), team

    def bot(self, key: str, make: Callable[[], Any]) -> Any:
        entry = self.bots.get(key)
        if entry is None:
            entry = self.bots[key] = [make(), 0]
        for q, a in self.history[entry[1]:]:
            entry[0].remember(q, a)
        entry[1] = len(self.history)
        return entry[0]

    def answered(self, key: str | None, question: str, answer: str, plain: str | None = None, calls: Iterable[tuple[str, Any]] = ()) -> None:
        self.history.append((question, answer))
        if self.turn_lineup is not None:
            if self.turn_lineup != self.lineup:
                self.before, self.before_lineup = self.squad, self.lineup
            self.squad = self.turn_squad or self.squad
            self.lineup, self.turn_lineup = self.turn_lineup, None
            self.turn_squad = None
        self.last = plain or question
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
    """ask_stream events → NDJSON lines (trace adds the model's thoughts and one line per tool call and result, names
    only; `keep` records everything — args, results, thoughts, hand-offs — for the admin page either way); returns the turn."""
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
            if trace:
                yield _event(type="trace", kind="tool", name=piece.name, **extra)
        elif isinstance(piece, Recheck):  # 수치를 다시 쓰는 중 (화면에는 진행 문구)
            yield _event(type="tool", name="check_numbers", args={}, **extra)
            if trace:
                yield _event(type="trace", kind="recheck", **extra)
            line = {"kind": "recheck", "text": "결과에 없는 수치: " + ", ".join(piece.numbers)}
        elif isinstance(piece, Escalate):
            yield _event(type="tool", name="escalate", args={}, **extra)
            line = {"kind": "escalate", "text": f"{piece.reason} → {piece.model}이 이어받음"}
        elif isinstance(piece, Thought):
            line = {"kind": "thought", "text": piece.text}
        elif isinstance(piece, gemini.Fallback):
            line = {"kind": "fallback", "text": f"{piece.failed} {piece.reason} → {piece.model}이 대신"}
        elif isinstance(piece, ToolResult):
            line = {"kind": "result", "name": piece.name, "ok": piece.ok and (piece.result or {}).get("ok", True), "text": piece.preview}
            if piece.ok and piece.name in SQUAD_TOOLS and piece.result and piece.result.get("ok", True):  # 짜는 중인 스쿼드도 왼쪽 칸에 바로 그린다
                try:
                    yield _event(type="squad", **squad_view(piece.result), **extra)
                except FeedError:
                    pass  # 자리를 다 못 채운 중간 스쿼드는 건너뛴다
        else:
            if not started:
                started = True
                keep.add("first", text="답이 나오기 시작")
            yield _event(type="delta", text=piece, **extra)
        if line:
            keep.add(**line)
            if trace and line["kind"] == "result":  # 도구는 이름·성공 여부만 (인자·결과는 /admin에만)
                yield _event(type="trace", kind="result", name=line["name"], ok=line["ok"], **extra)
            elif trace and line["kind"] == "thought":  # 모델의 생각은 그대로
                yield _event(type="trace", kind="thought", text=line["text"], **extra)


def _squad_call(lines: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """The answer's squad: its last squad tool call that ran without error (each tool line is followed by its result)."""
    found = pending = None
    for line in lines:
        if line["kind"] == "tool":
            pending = (line["name"], line["args"]) if line["name"] in SQUAD_TOOLS else None
        elif line["kind"] == "result" and pending:
            found, pending = (pending if line["ok"] else found), None
    return found


def _visitor(request: Request) -> str:
    """The visitor's IP as the tunnel (CF-Connecting-IP) or proxy (first X-Forwarded-For) passes it."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return request.headers.get("cf-connecting-ip") or forwarded or (request.client.host if request.client else "?")


def _billed(bots: Iterable[Any]) -> Counter[tuple[str, str]]:
    """Tokens billed so far by these chats, keyed (model, input|output)."""
    total: Counter[tuple[str, str]] = Counter()
    for bot in bots:
        for model, used in getattr(bot, "billed", {}).items():
            total.update({(model, kind): n for kind, n in used.items()})
    return total


def _cost(billed: Counter[tuple[str, str]]) -> dict[str, Any]:
    """{'tokens': 입력+출력, 'cost': 달러 추정} for log records (models without a known price count tokens only)."""
    usd = 0.0
    for (model, kind), n in billed.items():
        price = next((p for name, p in PRICES.items() if name in model), None)
        usd += n * price[kind == "output"] / 1e6 if price else 0
    return {"tokens": sum(billed.values()), "cost": round(usd, 5)} if billed else {}


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


class Feedback(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{12}$")  # 답과 함께 받은 그 질문의 기록 id
    vote: Literal["up", "down"]


class FeedRequest(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{12}$")  # 스쿼드를 답한 질문의 기록 id


class TeamRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=30)
    session_id: str | None = Field(default=None, max_length=64)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, max_length=64)
    # auto(/model 없이: GPT-6 Luna 생각 끔, OpenRouter 키가 없으면 키워드로 고른 Gemini) · gemini(모델 선택창의 Gemini 3.5 Flash, 안 되면
    # 다른 Gemini) · deep(DeepSeek 생각 끔) · deep_r(DeepSeek + think) · luna(GPT-6 Luna 생각 끔) · luna_r(GPT-6 Luna + think)
    mode: Literal["auto", "gemini", "deep", "deep_r", "luna", "luna_r"] = "auto"
    think: bool = False  # Gemini + think: 생각 깊이 HIGH (기본은 모델이 정함). gpt·deepseek의 think는 mode luna_r·deep_r
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
    team_limit = HourlyLimit(TEAM_PER_HOUR)
    feedback_limit = HourlyLimit(FEEDBACK_PER_HOUR)
    teams: dict[str, tuple[float, Team]] = {}  # 닉네임 → (가져온 시각, 팀)
    team_lock = threading.Lock()  # 넥슨 API 클라이언트는 한 번에 하나씩
    squads: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()  # 답 id → 그 답의 스쿼드 도구 호출 (최근 것만)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # 서버가 꺼질 때(배포·Render가 잠재울 때) 남은 기록을 올린다. uvicorn.run이 끝난 뒤에 하면 uvicorn이 SIGTERM을
        # 다시 일으켜 프로세스가 먼저 끝나 버린다 (2026-11-15 배포 사이에 한 대화의 기록이 사라짐)
        chat_log.flush()

    app = FastAPI(title="FCO 랭커 메타", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
    app.state.conn = conn
    app.state.chat_log = chat_log
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(StarletteHTTPException)
    async def not_found_page(request: Request, exc: StarletteHTTPException):
        """Pages that do not exist get the 404 page (home / guide buttons); /api and /static keep their plain errors."""
        if exc.status_code == 404 and request.method in ("GET", "HEAD") and not request.url.path.startswith(("/api/", "/static/")):
            return FileResponse(STATIC_DIR / "404.html", status_code=404)
        return await http_exception_handler(request, exc)

    @app.middleware("http")
    async def revalidate(request, call_next):
        # 브라우저가 옛 화면을 저장해 두고 쓰지 않도록 매번 확인하게 한다 (바뀌지 않았으면 304로 가볍게)
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-cache")
        response.headers.setdefault("X-Frame-Options", "DENY")  # 다른 사이트가 iframe으로 끼워 넣지 못하게 (/admin 등)
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
            colors = tool("query_rankers", {"group_by": "team_color", "limit": 30})["rows"]
            # 왼쪽 패널에서 누를 수 있는 순위까지 (더보기 포함). 하나씩 잠금을 잡았다 놓아 그동안 온 질문도 처리된다
            for f in formations[:WARM_FORMATIONS]:
                tool("get_formation_overview", {"formation": f["formation"]})
            for c in colors[:WARM_TEAM_COLORS]:
                tool("get_team_color_overview", {"team_color": c["team_color"]})
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

    def make_bot(key: str, session: _Session | None = None) -> Callable[[], Any]:
        tools = _SessionTools(gemini_tools, session) if session else gemini_tools
        if key == "gemini":
            return lambda: _gemini_chat(tools, gemini_model)
        if key in LUNA_LABEL:
            return lambda: _luna_chat(tools, reasoning=key == "luna_r")
        return lambda: _deepseek_chat(tools, reasoning=key == "deep_r")

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
            entry: dict[str, Any] = {"id": uuid.uuid4().hex[:12], "q": req.message, "session": session_id[:8], "outcome": "cancelled"}
            ask, team = session.take(req.message)
            if team:
                entry["team"] = team.mark + team.nickname
            keep = _TraceLog()  # 관리자 페이지의 답변 기록에서 줄을 누르면 보이는 trace·답변
            try:
                yield from answer_events(entry, keep, ask, team)
            finally:
                if "billed" in entry:  # 모델을 부른 질문: 그 사이 이 세션의 모델들이 쓴 토큰 (실패·정지도 청구된다)
                    entry.update(_cost(_billed(e[0] for e in session.bots.values()) - entry.pop("billed")))
                chat_log.record(**entry, ms=round((time.monotonic() - started) * 1000), detail=keep.detail())

        def answer_events(entry: dict[str, Any], keep: _TraceLog, ask: str, team: Team | None):
            yield _event(type="start", session_id=session_id)
            # 모델을 기다리는 동안 DB 잠금을 잡지 않는다 (도구 실행 때만 gemini_tools가 잡는다).
            # 같은 대화의 질문은 차례로. 사용자가 정지하면 이 생성기가 닫히고 그 질문은 기록에서 빠진다
            with session.lock:
                first = not session.history  # 대화의 첫 질문만 캐시한다
                route = _jev_route(req.message, session.last)
                if route and route.blocked:  # 범위 밖·프롬프트 공격 → LLM 없이 거절 문구
                    session.answered(None, req.message, REFUSAL)
                    entry.update(outcome="blocked", route=f"{route.scope} ({route.describe()})")
                    keep.add("route", text=f"{route.describe()} → 거절 문구 (모델 호출 없음)")
                    keep.answer = REFUSAL
                    if req.trace:
                        yield _event(type="trace", kind="blocked")
                    yield _event(type="delta", text=REFUSAL)
                    yield _event(type="done", tool_calls=[], blocked=True)
                    return
                if req.mode in ("auto", "gemini"):  # auto: GPT-6 Luna (키가 없는 서버만 키워드로 Flash-Lite·3.5 Flash). gemini: 3.5 Flash
                    cached = answers.get(req.message) if req.mode == "auto" and first and not team else None
                    if cached is not None:
                        session.answered(None, req.message, cached)  # 이어지는 질문이 이 답을 맥락으로 쓰도록
                        entry.update(outcome="cached", chars=len(cached))
                        keep.add("route", text="같은 첫 질문의 저장된 답 (모델 호출 없음)")
                        keep.answer = cached
                        yield _event(type="delta", text=cached)
                        yield _event(type="done", tool_calls=[], cached=True, id=entry["id"])
                        return
                    key = "gemini"
                    options, why = gemini_pick(req.message)
                    if req.mode == "gemini":  # 모델 선택창의 Gemini 3.5 Flash (안 되면 다른 Flash 먼저, 그다음 Lite)
                        entry["mode"] = req.mode
                        options, why = {"model": FLASH_MODEL}, f"/model gemini → {GEMINI_LABEL} (안 되면 다른 Gemini 모델)"
                    elif _deepseek_ready():  # 기본은 GPT-6 Luna 생각 켬 하나 — 실패해도 다른 모델로 넘기지 않는다 (OpenRouter 키가 없는 서버만 Gemini)
                        # 2026-11-15 DeepSeek과 같은 하네스로 비교: 품질은 비슷하고 비용은 약 2.5분의 1 (DeepSeek은 한글이 안 깨지는 제공사가 비싸다)
                        # 2026-11-16 생각 켬: 불필요한 도구 호출이 줄어 비용은 크게 늘지 않고 의도 구분이 나아짐 (reports/luna-thinking-20261116)
                        # 생각 끔은 세션 첫 질문 · 팀(@·#) 없음 · Jev가 단순 조회(light)라고 확신할 때만. 나머지는 모두 생각 켬
                        light = bool(route and route.light and first and not team)
                        key, options = ("luna" if light else "luna_r"), {"deadline": LONG_DEADLINE}
                        why = f"{route.describe() if route else 'Jev 없음'} → {LUNA_LABEL[key]}"
                    entry["route"] = why
                else:  # deep·luna (OpenRouter)
                    if not _deepseek_ready():
                        entry.update(outcome="error", error="OPENROUTER_API_KEY 없음")
                        yield _event(type="delta", text="지금은 이 모델을 쓸 수 없어요 (서버에 OpenRouter 키가 없습니다).")
                        yield _event(type="done", tool_calls=[], error="unavailable")
                        return
                    label = {**DEEP_LABEL, **LUNA_LABEL}[req.mode]
                    name = {"deep": "deepseek", "luna": "gpt"}[req.mode.removesuffix("_r")]
                    key, options, why = req.mode, {"deadline": LONG_DEADLINE}, f"/model {name} → {label}"
                    entry["mode"] = req.mode
                options["thoughts"] = True  # 관리자 페이지에 남기려고 /trace를 안 켜도 생각을 받는다
                if req.think and key == "gemini":
                    why += " · think: 생각 깊게 (HIGH)"
                    entry["route"] = why
                keep.add("route", text=why)
                entry["billed"] = _billed(e[0] for e in session.bots.values())  # 잠금 안에서: 같은 대화의 앞 질문은 끝난 뒤
                bot = session.bot(key, make_bot(key, session))
                if key == "gemini" and req.mode == "gemini":  # 3.5 Flash가 안 되면 다른 Flash 먼저, 그다음 Lite
                    options["fallbacks"] = gemini_fallbacks(getattr(bot, "fallback_models", ()))
                if key == "gemini" and req.think:  # think: Gemini가 가장 깊게 생각
                    options["think"] = entry["think"] = True
                stream = bot.ask_stream(ask, **options)
                try:
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
                session.answered(key, ask, turn.text, req.message, turn.tool_calls)  # 팀 블록째 남겨 이어지는 질문도 그 팀을 안다
            # 근거 줄과 대체 모델은 서버 로그에만 남기고 사용자 답에는 넣지 않는다
            log.info("answered with %s; evidence: %s", bot.last_model, turn.evidence)
            drawn = any(n == "recommend_squad" and not a.get("slots") and not (a.get("team_color") and a.get("formation")) for n, a in turn.tool_calls)
            # 잘리거나 도구 한도에 걸린 답, 팀컬러·포메이션을 무작위로 뽑은 스쿼드는 캐시하지 않는다 (같은 질문에 늘 같은 팀이 나오지 않게)
            if first and req.mode == "auto" and not entry.get("team") and turn.finish_reason == "STOP" and not drawn:
                answers.put(req.message, turn.text)
            entry.update(outcome="unverified_squad" if turn.finish_reason == "unverified_squad" else "ok", model=bot.last_model, provider=getattr(bot, "last_provider", None), tools=[n for n, _ in turn.tool_calls],
                         finish=turn.finish_reason, chars=len(turn.text))  # fmt: skip
            if unverified := getattr(bot, "unverified", None):  # 다시 쓰기 뒤에도 도구 결과로 확인되지 않은 수치 (관리자 페이지에서 모아 본다)
                entry["unverified"] = unverified
            keep.add("done", text=f"모델 {bot.last_model}" + (f" · 제공사 {p}" if (p := getattr(bot, "last_provider", None)) else ""))
            keep.answer = turn.text
            squad = None if turn.finish_reason == "unverified_squad" else _squad_call(keep.lines)
            if squad:  # 답 아래 "스쿼드메이커로 내보내기": 누르면 같은 호출을 다시 돌려(결과는 pages에 캐시) 링크를 만든다
                with lock:
                    squads[entry["id"]] = squad
                    while len(squads) > MAX_SESSIONS:
                        squads.popitem(last=False)
            yield _event(type="done", tool_calls=[n for n, _ in turn.tool_calls], model=bot.last_model, id=entry["id"], squad=bool(squad))

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/api/team")
    def attach_team(req: TeamRequest, request: Request) -> dict[str, Any]:
        """@닉네임: that user's latest starting XI, attached to the conversation (sent ahead of each question).
        "#피드ID" instead: the starting XI of that squad maker feed."""
        nickname = req.nickname.strip()
        feed = nickname.startswith("#")
        if not feed and not os.environ.get("NEXON_API_KEY"):
            raise HTTPException(status_code=503, detail="지금은 팀을 불러올 수 없어요 (서버에 넥슨 API 키가 없습니다).")
        cached = teams.get(nickname)
        if cached and time.time() - cached[0] < TEAM_CACHE_SECONDS:
            team = cached[1]
        else:
            if not team_limit.check(_visitor(request)):
                raise HTTPException(status_code=429, detail=f"팀 불러오기는 1시간에 {TEAM_PER_HOUR}번까지 할 수 있어요.")
            try:
                with team_lock, closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as team_db:
                    args = {"catalog": toolbox.catalog, "team_color_info": toolbox.team_color_info}
                    team = fetch_feed(team_db, nickname[1:], **args) if feed else fetch_team(_nexon(), team_db, nickname, **args)
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

    @app.post("/api/squad-link")
    def squad_maker_link(req: FeedRequest) -> dict[str, Any]:
        """스쿼드메이커로 내보내기 (/send 페이지): the answer's squad as a squad maker link for the FCLM bookmarklet,
        with the players on their slots for the preview."""
        with lock:
            found = squads.get(req.id)
        if not found:
            raise HTTPException(status_code=404, detail="이 답의 스쿼드는 스쿼드메이커로 보낼 수 없어요.")
        try:
            view = squad_view(tool(*found))
            return {"url": squad_link(view["players"], view["formation"], view["title"]), **view}
        except FeedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.post("/api/feedback")
    def feedback(req: Feedback, request: Request) -> dict[str, bool]:
        """👍/👎 under an answer (shown only now and then): kept as its own record, joined to the question by id."""
        if not feedback_limit.check(_visitor(request)):
            raise HTTPException(status_code=429, detail="잠시 후 다시 눌러 주세요.")
        chat_log.record(outcome="feedback", ref=req.id, vote=req.vote)
        return {"ok": True}

    # --- 관리자: 답변 기록 (ADMIN_KEY가 없으면 없는 페이지, 5회 틀린 IP는 막힘) ---
    @app.get("/guide", include_in_schema=False)
    def guide_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "guide.html")  # 사용법 목록 (주제마다 움직이는 화면이 있는 개별 페이지로)

    @app.get("/guide/{topic}", include_in_schema=False)
    def guide_topic(topic: str) -> FileResponse:
        if topic not in GUIDE_TOPICS:
            raise HTTPException(status_code=404, detail="없는 가이드입니다.")
        return FileResponse(STATIC_DIR / f"guide-{topic}.html")

    @app.get("/send", include_in_schema=False)
    def send_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "send.html")  # 스쿼드메이커로 내보내기 안내 (?id=답 기록 id)

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


class _SessionTools(_LockedTools):
    """One conversation's tools: a squad result also says which slots changed from the squad answered before
    ("벤제마 UC로" → the tool also swapped a CB to fit the salary cap — the answer should say so)."""

    def __init__(self, inner: _LockedTools, session: _Session):
        super().__init__(inner.toolbox, inner.lock)
        self.session = session

    def run(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        content, is_error = super().run(name, tool_input)
        if name == "squad_candidates" or name in SQUAD_TOOLS:
            self.session.turn_lineup = None
        if name == "recommend_squad":
            self.session.turn_squad = None
        if is_error or name not in SQUAD_TOOLS:
            return content, is_error
        result = json.loads(content)
        if result.get("ok") is False:
            return content, is_error
        if lineup := result.get("lineup"):
            if changes := _lineup_changes(self.session.lineup, lineup):
                result["changed_from_previous"] = {
                    "slots": changes,
                    "note": "앞서 답한 스쿼드와 비교해 바뀐 자리. 사용자가 바꾸라고 한 자리 말고도 바뀐 자리가 있으면 답에 한 줄씩 알릴 것"
                            " (급여·예산을 맞추려고 바뀐 것이면 그렇다고)",
                }  # fmt: skip
                content = json.dumps(result, ensure_ascii=False)
            self.session.turn_lineup = [
                {"role": p["role"], "player": p.get("player"), "season": p.get("season"),
                 "card": {k: v for k, v in p.get("card", {}).items() if k in ("most_used_grade", "salary")}, **({"owned": True} if p.get("owned") else {})}
                for p in lineup
            ]
            conditions = self.session.turn_squad or self.session.squad or {}
            preferences = {k: v for k, v in conditions.items() if k in ("prefer", "save", "invest", "high_salary", "spend")}
            args = {k: v for k, v in tool_input.items() if k != "picks"}
            self.session.turn_squad = {**preferences, **args} if name == "check_squad" else args
            for k in ("team_color", "formation"):  # 도구가 무작위로 고른 팀컬러·포메이션도 다음 질문의 조건으로 (다시 뽑히지 않게)
                if not tool_input.get(k) and result.get(k) and not tool_input.get("slots"):
                    self.session.turn_squad[k] = result[k]
        return content, is_error


def _lineup_changes(before: list[dict[str, Any]] | None, after: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Per role, the cards that left and came in (same layout only): [{role, from, to}] with season and salary."""
    def card(e: dict[str, Any]) -> tuple[str, str]:
        c = e.get("card") or {}
        return (e.get("player") or "").replace("★", "").strip(), str(e.get("season") or c.get("season") or "").split(" (")[0]

    def label(e: dict[str, Any]) -> str:
        salary = (e.get("card") or {}).get("salary", e.get("salary"))
        return " ".join(card(e)) + (f" (급여 {salary})" if salary is not None else "")

    filled = lambda xs: [e for e in xs or [] if e.get("player")]  # noqa: E731
    before, after = filled(before), filled(after)
    if not before or sorted(e["role"] for e in before) != sorted(e["role"] for e in after):
        return []  # 앞 스쿼드가 없거나 포메이션(자리 구성)이 다르면 자리별로 비교하지 않는다
    out = []
    for role in dict.fromkeys(e["role"] for e in after):
        old = [e for e in before if e["role"] == role]
        new = [e for e in after if e["role"] == role]
        gone = [e for e in old if card(e) not in {card(x) for x in new}]
        came = [e for e in new if card(e) not in {card(x) for x in old}]
        out += [{"role": role, "from": label(a), "to": label(b)} for a, b in zip(gone, came)]
    return out




def _gemini_chat(toolbox: Toolbox | _LockedTools, model: str | None):
    from google import genai

    from ..chatbot.gemini import GeminiChat, models_from_env

    primary, fallbacks = models_from_env(model)
    return GeminiChat(genai.Client(), toolbox, model=primary, fallback_models=fallbacks)


_HTTP: Any = None


def _http() -> Any:
    import httpx

    global _HTTP
    _HTTP = _HTTP or httpx.Client()
    return _HTTP


def _jev_route(question: str, previous: str | None) -> Route | None:
    """Jev's routing for this question, or None (no OPENROUTER_API_KEY, error or timeout → nothing is blocked)."""
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


def _deepseek_ready() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_COMPAT_API_KEY"))


def _deepseek_chat(toolbox: Toolbox | _LockedTools, *, reasoning: bool):
    from ..chatbot.llm import OpenAIChat

    # fp4 제공사(Sail Research 등)는 도구 인자를 빠뜨린다 (2026-11-15: 같은 질문 5/5에서 팀컬러·포메이션 누락, DeepInfra는 5/5 정상)
    # DeepInfra는 긴 답에서 한글이 깨진다 (2026-11-15: temperature 기본 72개 중 6개, 0.3에서도 71개 중 2개 — "사요률", "포메숀").
    # 같은 측정에서 Nebius·Fireworks는 0.3으로 48개씩 깨짐 0. 다른 제공사로 넘기지 않는다
    provider = {"only": ["nebius", "fireworks"]}
    return OpenAIChat(_http(), toolbox, model=DEEPSEEK_MODEL,
                      extra={"reasoning": {"enabled": reasoning}, "provider": provider, "temperature": 0.3})


def _luna_chat(toolbox: Toolbox | _LockedTools, *, reasoning: bool):
    from ..chatbot.llm import OpenAIChat

    bot = OpenAIChat(_http(), toolbox, model=LUNA_MODEL, extra={"reasoning": {"enabled": reasoning}, "provider": {"only": ["openai"]}})
    return bot
