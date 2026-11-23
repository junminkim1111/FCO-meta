"""FastAPI app: JSON API over the chatbot tools + a single static page."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from collections import Counter, OrderedDict, deque
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager, closing
from datetime import datetime
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

from ..beta_keys import key_hash
from ..chatbot.llm import LUNA_MODEL, RESET, Recheck, Thought, ToolCall, ToolResult, describe_error
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
SITE = "https://fclm.kr"  # 검색엔진에 알리는 대표 주소 (fclm.onrender.com도 같은 앱이라 canonical은 이쪽으로)
MAX_SESSIONS = 200
MAX_CACHED = 500  # 캐시에 두는 첫 질문 답 수
BUSY_MESSAGE = "지금 서버가 혼잡해 답을 드리지 못했어요. 잠시 후 다시 물어봐 주세요."
DEEPSEEK_MODEL = "deepseek/deepseek-v4.1-flash-eco"  # /model deepseek (OpenGateway, 서버 환경 변수 OPENGATEWAY_API_KEY)
DEEPSEEK_BASE_URL = "https://apis.opengateway.ai/v1"
# 기본(/model 없을 때)·/model gpt는 llm.LUNA_MODEL (OpenRouter, OpenAI 제공사로 고정 — 2026-11-12 평가와 같은 설정)
LONG_DEADLINE = 360.0  # 모델로 답하는 질문의 대기 상한(초)
WRAP_UP_AFTER = 300.0  # 이만큼 지나면 더 부르지 않고 생각도 끈 채 지금까지의 결과로 답을 쓴다
WARM_FORMATIONS, WARM_TEAM_COLORS = 15, 20  # 서버가 켜질 때 미리 계산할 상세 (화면의 더보기 범위와 같게)
# 100만 토큰당 달러 (입력, 출력 — 생각 포함). 모델 이름에 들어 있는 것으로 찾는다.
# deepseek: OpenGateway 상시 할인가 (2026-11-20, 정가 0.30/1.20, 별도 공지로 끝날 수 있음)
# ponytail: 캐시 할인은 빼고 셈 (실제보다 조금 비싸게 나옴)
PRICES = {"deepseek": (0.03, 0.12), "luna": (0.10, 0.50)}
FEEDBACK_PER_HOUR = 60  # 👍/👎: 한 사람이 1시간에
VISIT_PER_HOUR = 120  # 방문 기록: 한 IP가 1시간에 (UA를 바꿔 가며 기록을 늘리는 것을 막는다)
TEAM_PER_HOUR = 20  # @닉네임 팀 불러오기: 한 사람이 1시간에 (넥슨 API 4~5회씩)
TEAM_CACHE_SECONDS = 600  # 같은 닉네임은 이 동안 다시 부르지 않는다
SQUAD_TOOLS = ("recommend_squad", "check_squad")
DEEP_LABEL = {"deep": "DeepSeek V4.1 Flash Eco 생각 끔", "deep_r": "DeepSeek V4.1 Flash Eco 생각 켬"}
LUNA_LABEL = {"luna": "GPT-6 Luna 생각 끔", "luna_r": "GPT-6 Luna 생각 켬"}


TRIAL_PER_VISITOR = 10  # 베타 키 없는 방문자의 하루 체험 질문 수
TRIAL_USED = "체험 질문을 다 썼어요. 베타 키를 받으면 계속 물어볼 수 있어요."


class RateLimit:
    """Questions per person per minute and per day, and for the whole service per day — the model budget
    is shared by everyone. A person is the visitor's IP as the tunnel or proxy passes it (see chat())."""

    def __init__(self, per_minute: int = 6, per_day: int = 60, total_per_day: int = 300, now: Callable[[], float] = time.time):
        self.per_minute, self.per_day, self.total_per_day, self.now = per_minute, per_day, total_per_day, now
        self.recent: dict[str, deque[float]] = {}
        self.today: dict[str, int] = {}
        self.keys: dict[str, int] = {}  # 베타 키(해시 앞 12자)별 오늘 질문 수
        self.total, self.day = 0, None
        self.lock = threading.Lock()

    def seed(self, counts: dict[str, int]) -> None:
        """Refill today's beta-key counters (and the service total) from the log after a restart."""
        with self.lock:
            self.day = datetime.fromtimestamp(self.now(), KST).date()
            for k, n in counts.items():
                self.keys[k] = self.keys.get(k, 0) + n
                self.total += n

    def check(self, who: str, key: str | None = None, key_per_day: int = 0, trial_per_day: int = 0) -> str | None:
        """Counts one question; returns why it is refused instead (nothing is counted then).
        With a beta `key` the day's limit is the key's (key_per_day) instead of the visitor's; the per-minute limit stays per IP.
        A "trial-…" key (a visitor without a beta key) is also refused (TRIAL_USED) once all trial questions today reach trial_per_day."""
        with self.lock:
            now = self.now()
            day = datetime.fromtimestamp(now, KST).date()
            if day != self.day:  # 한국 시간 자정에 하루 횟수를 비운다
                self.today.clear()
                self.keys.clear()
                self.total, self.day = 0, day
            recent = self.recent.setdefault(who, deque())
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if self.total >= self.total_per_day:
                return "오늘 서비스 전체 질문 수가 다 찼어요. 내일 다시 물어봐 주세요."
            if key and key.startswith("trial-") and sum(n for k, n in self.keys.items() if k.startswith("trial-")) >= trial_per_day:
                return TRIAL_USED
            if key and self.keys.get(key, 0) >= key_per_day:
                if key.startswith("trial-"):
                    return TRIAL_USED
                return f"오늘 질문 수({key_per_day}개)를 다 썼어요. 내일 다시 물어봐 주세요."
            if not key and self.today.get(who, 0) >= self.per_day:
                return f"하루 질문 수({self.per_day}개)를 다 썼어요. 내일 다시 물어봐 주세요."
            if len(recent) >= self.per_minute:
                return f"질문이 너무 빨라요. 1분에 {self.per_minute}개까지 물어볼 수 있어요. 잠시 후 다시 물어봐 주세요."
            recent.append(now)
            if key:
                self.keys[key] = self.keys.get(key, 0) + 1
            else:
                self.today[who] = self.today.get(who, 0) + 1
            self.total += 1
            return None


class BetaGate:
    """Closed beta: a request needs a key (X-Beta-Key) whose SHA-256 is in `hashes`; no hashes = beta off, nothing is checked.
    Wrong keys are counted per visitor (IP): from MAX_FAILS wrong ones in an hour that visitor is blocked for the rest of the hour,
    even with a right key. In memory, like AdminGuard — a restart clears it."""

    MAX_FAILS = 10

    def __init__(self, hashes: Iterable[str], per_day: int = 30, closed: bool = False, now: Callable[[], float] = time.time, admin_key: str = "", trial_per_day: int = 0):
        self.admin_key, self.trial_per_day = admin_key, trial_per_day
        self.hashes = [h.strip().lower() for h in hashes if h.strip()]
        self.per_day, self.closed, self.now = per_day, closed, now
        self.fails: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.hashes)

    def peek(self, key: str | None) -> str | None:
        """The key's id if it is right, else None — counts nothing (for visit logging)."""
        if not key:
            return None
        if self.admin_key and hmac.compare_digest(key.encode(), self.admin_key.encode()):
            return "admin"
        given = key_hash(key)
        return given[:12] if any(hmac.compare_digest(given, h) for h in self.hashes) else None

    def identify(self, who: str, key: str | None) -> str:
        """The key's id (first 12 chars of its hash; "admin" for the admin password) or raises 401 (missing/wrong key) / 429 (too many wrong ones)."""
        with self.lock:
            fails = self.fails.setdefault(who, deque())
            while fails and fails[0] <= self.now() - 3600:
                fails.popleft()
            if len(fails) >= self.MAX_FAILS:
                raise HTTPException(status_code=429, detail="틀린 키를 너무 많이 입력해 1시간 동안 막혔어요. 잠시 후 다시 해 주세요.")
            if not key:
                raise HTTPException(status_code=401, detail="베타 키가 필요해요")
            if found := self.peek(key):
                return found
            fails.append(self.now())
        log.warning("wrong beta key from %s", who)
        raise HTTPException(status_code=401, detail="베타 키가 맞지 않아요")


VISIT_PATHS = {"/", "/guide", "/send", *(f"/guide/{t}" for t in GUIDE_TOPICS)}
VISIT_DEDUPE = 1800  # 초: 같은 방문자·페이지는 이만큼에 한 번만 기록
MOBILE_UA = re.compile(r"Mobi|Android|iPhone|iPad", re.I)


class VisitTracker:
    """Visit ids and the 30-minute duplicate filter. The id is the first 12 hex of HMAC(day key, IP + User-Agent); the day key is derived
    from `salt` and the KST date, so it is the same after a restart and different every day. IP and User-Agent are never stored."""

    def __init__(self, salt: str, now: Callable[[], float] = time.time):
        self.salt, self.now = salt or uuid.uuid4().hex, now
        self.seen: dict[tuple[str, str], float] = {}
        self.lock = threading.Lock()

    def visitor_id(self, ip: str, ua: str) -> str:
        day = datetime.fromtimestamp(self.now(), KST).strftime("%Y-%m-%d")
        day_key = hmac.new(self.salt.encode(), day.encode(), hashlib.sha256).digest()
        return hmac.new(day_key, f"{ip}\n{ua}".encode(), hashlib.sha256).hexdigest()[:12]

    def first(self, v: str, path: str) -> bool:
        """True (and remembers it) unless this visitor already opened this path within VISIT_DEDUPE."""
        with self.lock:
            now = self.now()
            if len(self.seen) > 10000:
                self.seen = {k: t for k, t in self.seen.items() if t > now - VISIT_DEDUPE}
            if self.seen.get((v, path), 0) > now - VISIT_DEDUPE:
                return False
            self.seen[(v, path)] = now
            return True


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
        self.attached: Team | None = None  # 붙인 팀 (떼기 전까지): 플레이스타일 분석이 그 구단주의 경기를 찾는다
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
            message += "\n\n[직전 답의 스쿼드: " + json.dumps(self.lineup, ensure_ascii=False) + "]"
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
        elif isinstance(piece, Thought):
            line = {"kind": "thought", "text": piece.text}
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
    changes (daily collection, price refresh) — repeated questions cost no model call. Follow-ups are
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


class BetaCheck(BaseModel):
    key: str = Field(max_length=64)


class Visit(BaseModel):
    path: str = Field(max_length=100)
    ref: str = Field(default="", max_length=300)  # document.referrer (서버가 호스트만 남긴다). 비콘의 Referer 헤더는 늘 이 사이트라서 쓸 수 없다


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, max_length=64)
    # auto(/model 없이: Jev 판단으로 GPT-6 Luna 생각 끔·켬) · deep(DeepSeek 생각 끔) · deep_r(DeepSeek + think)
    # · luna(GPT-6 Luna 생각 끔) · luna_r(GPT-6 Luna + think)
    mode: Literal["auto", "deep", "deep_r", "luna", "luna_r"] = "auto"
    trace: bool = False  # /trace: 모델의 생각·도구 결과·모델 전환도 보낸다


def _open_readonly(db_path: Path) -> sqlite3.Connection:
    # 웹은 조회만 한다. 테이블(CREATE IF NOT EXISTS)은 쓰기 연결로 한 번 보장한 뒤 읽기 전용으로 연다
    Toolbox(sqlite3.connect(str(db_path))).conn.close()
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)


def create_app(
    db_path: Path | str,
    *,
    backend: str = "rules",
    limit: RateLimit | None = None,
    chat_log: ChatLog | None = None,
    admin_key: str | None = None,
) -> FastAPI:
    """`admin_key` (기본: 환경 변수 ADMIN_KEY) 가 있으면 /admin 에서 답변 기록을 볼 수 있다.
    환경 변수 BETA_KEY_HASHES (쉼표로 구분한 키 SHA-256, 없으면 베타 꺼짐)·BETA_PER_DAY (키당 하루 질문, 30)·BETA_CLOSED (1이면 질문 거절)로
    클로즈드 베타, CHAT_TOTAL_PER_DAY로 서비스 전체 하루 질문 수(300)."""
    db_path = Path(db_path)
    limit = limit or RateLimit(total_per_day=int(os.environ.get("CHAT_TOTAL_PER_DAY", "300")))
    admin_key = admin_key if admin_key is not None else os.environ.get("ADMIN_KEY", "")
    beta = BetaGate(os.environ.get("BETA_KEY_HASHES", "").split(","), int(os.environ.get("BETA_PER_DAY", "30")), os.environ.get("BETA_CLOSED") == "1", admin_key=admin_key,
                    trial_per_day=int(os.environ.get("BETA_TRIAL_PER_DAY", "200")))  # 체험 질문 하루 합계 (방문자당 TRIAL_PER_VISITOR)
    chat_log = chat_log or ChatLog()
    visits = VisitTracker(os.environ.get("VISIT_SALT") or admin_key, chat_log.now)
    admin = AdminGuard(admin_key)
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    conn = _open_readonly(db_path)
    lock = threading.Lock()  # sqlite 연결 하나를 스레드풀 요청이 나눠 쓴다
    toolbox = Toolbox(conn)
    locked_tools = _LockedTools(toolbox, lock)
    rules = RuleBot(toolbox)
    answers = AnswerCache(db_path)
    # 화면 조회(포메이션·팀컬러 정보 등)도 DB가 바뀔 때까지 결과를 재사용한다 (Render 무료 CPU에선 한 번에 수 초)
    pages = AnswerCache(db_path, size=300)
    sessions: OrderedDict[str, _Session] = OrderedDict()
    team_limit = HourlyLimit(TEAM_PER_HOUR)
    feedback_limit = HourlyLimit(FEEDBACK_PER_HOUR)
    visit_limit = HourlyLimit(VISIT_PER_HOUR)
    teams: dict[str, tuple[float, Team]] = {}  # 닉네임 → (가져온 시각, 팀)
    team_lock = _NEXON_LOCK  # 넥슨 API 클라이언트는 한 번에 하나씩 (플레이스타일 분석과 같이 쓴다)
    squads: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()  # 답 id → 그 답의 스쿼드 도구 호출 (최근 것만)
    restored = threading.Lock()  # 첫 베타 질문에서 한 번만 오늘 기록으로 키별 카운터를 채운다 (잡은 채로 둠 = 끝)

    def beta_id(request: Request) -> str | None:
        """The beta key's id for a request that needs a key (None when the beta is off); raises 401/429 otherwise."""
        return beta.identify(_visitor(request), request.headers.get("x-beta-key")) if beta.enabled else None

    def trial_id(request: Request) -> str | None:
        """Only /api/chat: a request with no key at all gets a daily trial id (None when trials are off or a key was given — wrong keys are not trials)."""
        if beta.enabled and beta.trial_per_day > 0 and not request.headers.get("x-beta-key"):
            return "trial-" + visits.visitor_id(_visitor(request), request.headers.get("user-agent", ""))
        return None

    def restore_counts() -> None:
        # Render가 잠들었다 깨면 메모리 카운터가 비므로 오늘(KST) 기록에서 다시 센다. 실패해도 다시 시도하지 않는다 (느린 읽기가 질문마다 붙지 않게)
        if restored.acquire(blocking=False):
            try:
                limit.seed(chat_log.today_counts())
            except Exception:
                log.exception("restoring today's beta key counts failed")

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
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")  # https로만 (Cloudflare·Render 모두 https)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    # --- 검색엔진: 색인할 공개 페이지 목록 (/admin·/api는 막음. /send는 막지 않고 페이지의 noindex로 뺀다: 막으면 noindex를 못 읽음) ---
    # HEAD도 받는다: GET만 열면 HEAD가 405라 sitemap 검사기·일부 크롤러가 '가져올 수 없음'으로 본다
    @app.api_route("/robots.txt", methods=["GET", "HEAD"], include_in_schema=False)
    def robots() -> Response:
        body = f"User-agent: *\nDisallow: /admin\nDisallow: /api/\n\nSitemap: {SITE}/sitemap.xml\n"
        return Response(body, media_type="text/plain")

    @app.api_route("/ads.txt", methods=["GET", "HEAD"], include_in_schema=False)
    def ads_txt() -> Response:  # 구글 애드센스 판매자 확인 (각 페이지 head의 adsbygoogle.js와 같은 게시자 ID)
        return Response("google.com, pub-1315876984941536, DIRECT, f08c47fec0942fa0\n", media_type="text/plain")

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon-144.png", media_type="image/png")  # Google 검색 결과 아이콘: 48px 배수 정사각형

    @app.api_route("/sitemap.xml", methods=["GET", "HEAD"], include_in_schema=False)
    def sitemap() -> Response:
        urls = "".join(f"  <url>\n    <loc>{SITE}{p}</loc>\n  </url>\n" for p in ("/", "/guide", *(f"/guide/{t}" for t in GUIDE_TOPICS)))
        body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n'
        return Response(body, media_type="application/xml")

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

    @app.get("/api/beta")
    def beta_status() -> dict[str, bool]:
        return {"beta": beta.enabled, "closed": beta.enabled and beta.closed}

    @app.post("/api/beta/check")
    def beta_check(req: BetaCheck, request: Request) -> dict[str, bool]:
        if beta.enabled:
            try:
                beta.identify(_visitor(request), req.key)
            except HTTPException:
                chat_log.record(outcome="beta_check", ok=False)
                raise
            chat_log.record(outcome="beta_check", ok=True)
        return {"ok": True}

    @app.post("/api/visit", status_code=204, include_in_schema=False)
    def visit(req: Visit, request: Request) -> Response:
        """One page view (path, referrer host, device, daily visitor id, beta key id). Pages other than the site's own are ignored."""
        path = req.path.split("?")[0].split("#")[0].rstrip("/") or "/"
        ua = request.headers.get("user-agent", "")
        v = visits.visitor_id(_visitor(request), ua)
        if path in VISIT_PATHS and visits.first(v, path) and visit_limit.check(_visitor(request)):
            ref = (urlsplit(req.ref).hostname or "").removeprefix("www.")
            own = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(":")[0].removeprefix("www.")
            kid = beta.peek(request.headers.get("x-beta-key")) if beta.enabled else None
            chat_log.record(outcome="visit", path=path, ref="" if ref == own else ref, device="mobile" if MOBILE_UA.search(ua) else "desktop",
                            v=v, **({"key": kid} if kid else {}))  # fmt: skip
        return Response(status_code=204)

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
        tools = _SessionTools(locked_tools, session) if session else locked_tools
        if key in LUNA_LABEL:
            return lambda: _luna_chat(tools, reasoning=key == "luna_r")
        return lambda: _deepseek_chat(tools, reasoning=key == "deep_r")

    @app.post("/api/chat")
    def chat(req: ChatRequest, request: Request) -> StreamingResponse:
        """답을 한 줄에 하나씩 JSON 이벤트로 흘려보낸다: start(session_id) → delta(text)… → done.
        reset = 지금까지 보낸 글을 지운다 (도구를 부르기 전에 쓴 글이었음). tool = 지금 부르는 도구(name, args).
        trace = (/trace) 모델 선택·생각·도구 결과·모델 전환. 질문 수 제한에 걸리면 429 (detail = 안내 문구)."""
        who = _visitor(request)
        kid = trial_id(request) or beta_id(request)
        if beta.enabled and beta.closed and kid != "admin":  # 관리자는 종료 후에도 점검할 수 있다
            raise HTTPException(status_code=403, detail="베타가 종료됐어요")
        who_key = {"key": kid} if kid else {}  # 기록에는 키 해시 앞 12자만 (원문 키·IP는 남기지 않는다)
        if kid:
            restore_counts()
        trial = bool(kid) and kid.startswith("trial-")
        if refused := limit.check(who, kid, TRIAL_PER_VISITOR if trial else sys.maxsize if kid == "admin" else beta.per_day, beta.trial_per_day):  # 관리자는 키별 하루 제한 없음
            chat_log.record(q=req.message, outcome="limited", error=refused, **who_key)
            if refused == TRIAL_USED:  # 체험을 다 썼다 → 프론트가 401이면 키 입력창을 띄운다
                raise HTTPException(status_code=401, detail=refused)
            raise HTTPException(status_code=429, detail=refused)
        if backend != "llm":
            with lock:
                answer = rules.ask(req.message)
            chat_log.record(q=req.message, outcome="ok", model="rules", tools=[answer.tool] if answer.tool else [], **who_key)
            lines = [_event(type="start", session_id=req.session_id), _event(type="delta", text=answer.text),
                     _event(type="done", tool_calls=[answer.tool] if answer.tool else [])]  # fmt: skip
            return StreamingResponse(iter(lines), media_type="application/x-ndjson")

        session_id = req.session_id or uuid.uuid4().hex
        session = session_for(session_id)

        def events():
            # 관리자 페이지용 기록: 끝까지 가지 못하고 닫히면(사용자가 정지·연결 끊김) cancelled로 남는다
            started = time.monotonic()
            entry: dict[str, Any] = {"id": uuid.uuid4().hex[:12], "q": req.message, "session": session_id[:8], "outcome": "cancelled", **who_key}
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
            # 모델을 기다리는 동안 DB 잠금을 잡지 않는다 (도구 실행 때만 locked_tools가 잡는다).
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
                if req.mode == "auto":  # 기본: DeepSeek V4.1 Flash Eco (OpenGateway). 그 키가 없는 서버만 GPT-6 Luna (OpenRouter)
                    cached = answers.get(req.message) if first and not team else None
                    if cached is not None:
                        session.answered(None, req.message, cached)  # 이어지는 질문이 이 답을 맥락으로 쓰도록
                        entry.update(outcome="cached", chars=len(cached))
                        keep.add("route", text="같은 첫 질문의 저장된 답 (모델 호출 없음)")
                        keep.answer = cached
                        yield _event(type="delta", text=cached)
                        yield _event(type="done", tool_calls=[], cached=True, id=entry["id"])
                        return
                    deep = _deepseek_ready()
                    if not (deep or _luna_ready()):
                        entry.update(outcome="error", error="OPENGATEWAY_API_KEY·OPENROUTER_API_KEY 없음")
                        yield _event(type="delta", text="지금은 답할 모델을 쓸 수 없어요 (서버에 모델 키가 없습니다).")
                        yield _event(type="done", tool_calls=[], error="unavailable")
                        return
                    # 기본 모델 하나 — 실패해도 다른 모델로 넘기지 않는다
                    # 2026-11-21 기본을 GPT-6 Luna → DeepSeek V4.1 Flash Eco로: OpenGateway 상시 할인가로 질문당 비용이 약 1/3~1/4
                    # (할인이 끝나면 정가가 Luna보다 비싸다 — 그때는 OPENGATEWAY_API_KEY를 빼면 Luna로 돌아간다)
                    # 생각 끔은 세션 첫 질문 · 팀(@·#) 없음 · Jev가 단순 조회(light)라고 확신할 때만. 나머지는 모두 생각 켬
                    light = bool(route and route.light and first and not team)
                    key = ("deep" if light else "deep_r") if deep else ("luna" if light else "luna_r")
                    options = {"deadline": LONG_DEADLINE, "wrap_after": WRAP_UP_AFTER}
                    why = f"{route.describe() if route else 'Jev 없음'} → {(DEEP_LABEL if deep else LUNA_LABEL)[key]}"
                    entry["route"] = why
                else:  # deep(OpenGateway)·luna(OpenRouter)
                    deep = req.mode in DEEP_LABEL
                    if not (_deepseek_ready() if deep else _luna_ready()):
                        entry.update(outcome="error", error="OPENGATEWAY_API_KEY 없음" if deep else "OPENROUTER_API_KEY 없음")
                        yield _event(type="delta", text=f"지금은 이 모델을 쓸 수 없어요 (서버에 {'OpenGateway' if deep else 'OpenRouter'} 키가 없습니다).")
                        yield _event(type="done", tool_calls=[], error="unavailable")
                        return
                    label = {**DEEP_LABEL, **LUNA_LABEL}[req.mode]
                    name = "deepseek" if deep else "gpt"
                    key, options, why = req.mode, {"deadline": LONG_DEADLINE, "wrap_after": WRAP_UP_AFTER}, f"/model {name} → {label}"
                    entry["mode"] = req.mode
                options["thoughts"] = True  # 관리자 페이지에 남기려고 /trace를 안 켜도 생각을 받는다
                keep.add("route", text=why)
                entry["billed"] = _billed(e[0] for e in session.bots.values())  # 잠금 안에서: 같은 대화의 앞 질문은 끝난 뒤
                bot = session.bot(key, make_bot(key, session))
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
        "#피드ID" instead: the starting XI of that squad maker feed. Visitors on a trial (no key) may attach a team too."""
        if not trial_id(request):  # 체험 방문자도 @·# 팀을 붙일 수 있다 (넥슨 API는 TEAM_PER_HOUR로 따로 제한)
            beta_id(request)
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
        session = session_for(session_id)
        session.team = session.attached = team
        return {"session_id": session_id, **team.to_json()}

    @app.delete("/api/team")
    def detach_team(request: Request, session_id: str = Query(max_length=64)) -> dict[str, bool]:
        if not trial_id(request):
            beta_id(request)
        session = session_for(session_id)
        session.team = session.attached = None
        return {"ok": True}

    @app.post("/api/squad-link")
    def squad_maker_link(req: FeedRequest, request: Request) -> dict[str, Any]:
        """스쿼드메이커로 내보내기 (/send 페이지): the answer's squad as a squad maker link for the FCLM bookmarklet,
        with the players on their slots for the preview."""
        beta_id(request)
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
        kid = beta_id(request)
        if not feedback_limit.check(_visitor(request)):
            raise HTTPException(status_code=429, detail="잠시 후 다시 눌러 주세요.")
        chat_log.record(outcome="feedback", ref=req.id, vote=req.vote, **({"key": kid} if kid else {}))
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
    """The toolbox as the model sees it: the DB lock is held only while a tool runs."""

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
        if name == "analyze_playstyle":
            return self._playstyle()
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


    def _playstyle(self) -> tuple[str, bool]:
        """The attached @team's recent matches summarized (NEXON API, cached per ouid for PLAYSTYLE_CACHE_SECONDS)."""
        from .playstyle import player_names, recent_matches, summarize

        team = self.session.attached
        if team is None or not team.ouid:
            why = "스쿼드 피드(#)로 붙인 팀은 경기 기록이 없음" if team else "대화에 붙은 @팀이 없음"
            return json.dumps({"error": f"{why} — 사용자에게 @구단주명으로 팀을 불러오라고 할 것"}, ensure_ascii=False), True
        hit = _PLAYSTYLE.get(team.ouid)
        if hit and time.time() - hit[0] < PLAYSTYLE_CACHE_SECONDS:
            return hit[1], False
        if not os.environ.get("NEXON_API_KEY"):
            return json.dumps({"error": "서버에 넥슨 API 키가 없어 경기 기록을 가져올 수 없음"}, ensure_ascii=False), True
        try:
            with _NEXON_LOCK:  # 넥슨 클라이언트는 한 번에 하나씩 (@팀 불러오기와 같이)
                api = _nexon()
                details = [(kind, api.match_detail(m)) for m, kind in recent_matches(api, team.ouid)]
        except Exception as e:  # 예산 초과·넥슨 오류: 모델이 사용자에게 알린다
            log.warning("playstyle analysis failed: %s", e)
            return json.dumps({"error": "넥슨 경기 기록을 가져오지 못함 (잠시 후 다시)"}, ensure_ascii=False), True
        sp_ids = {int(p["spId"]) for _, d in details for i in d.get("matchInfo") or [] if i.get("ouid") == team.ouid for p in i.get("player") or []}
        with self.lock:
            names = player_names(self.toolbox.conn, sorted(sp_ids))
        result = {"owner": team.mark + team.nickname, **summarize(team.ouid, details, names),
                  "note": "넥슨 기록은 약 2시간 전 경기까지. 답에 분석한 경기 수·종류·기간을 밝힐 것"}  # fmt: skip
        content = json.dumps(result, ensure_ascii=False)
        _PLAYSTYLE[team.ouid] = (time.time(), content)
        while len(_PLAYSTYLE) > MAX_SESSIONS:
            _PLAYSTYLE.pop(next(iter(_PLAYSTYLE)))
        return content, False


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


_NEXON_LOCK = threading.Lock()  # 넥슨 Open API 클라이언트를 한 번에 하나씩 (@팀 불러오기·플레이스타일 분석)
PLAYSTYLE_CACHE_SECONDS = 3600  # 같은 구단주의 플레이스타일 분석은 이 동안 다시 부르지 않는다 (분석 1번 ≈ 넥슨 API 21회)
_PLAYSTYLE: dict[str, tuple[float, str]] = {}  # ouid → (분석한 시각, 결과 JSON)


def _nexon() -> Any:
    """One NEXON Open API client for @닉네임 lookups (its own daily call budget, in memory)."""
    global _NEXON
    if _NEXON is None:
        from ..openapi.budget import CallBudget
        from ..openapi.client import NexonOpenApiClient

        _NEXON = NexonOpenApiClient(budget=CallBudget(daily_limit=int(os.environ.get("NEXON_DAILY_LIMIT", "20000"))))
    return _NEXON


def _deepseek_ready() -> bool:
    return bool(os.environ.get("OPENGATEWAY_API_KEY"))


def _luna_ready() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_COMPAT_API_KEY"))


def _deepseek_chat(toolbox: Toolbox | _LockedTools, *, reasoning: bool):
    from ..chatbot.llm import OpenAIChat

    # OpenGateway: 생각은 기본이 켜져 있다 → 끌 때만 reasoning.enabled=false를 보낸다 (OpenRouter 제공사 고정은 쓰지 않는다)
    extra: dict[str, Any] = {"temperature": 0.3}
    if not reasoning:
        extra["reasoning"] = {"enabled": False}
    return OpenAIChat(_http(), toolbox, model=DEEPSEEK_MODEL, base_url=DEEPSEEK_BASE_URL, api_key=os.environ.get("OPENGATEWAY_API_KEY"), extra=extra)


def _luna_chat(toolbox: Toolbox | _LockedTools, *, reasoning: bool):
    from ..chatbot.llm import OpenAIChat

    bot = OpenAIChat(_http(), toolbox, model=LUNA_MODEL, extra={"reasoning": {"enabled": reasoning}, "provider": {"only": ["openai"]}})
    return bot
