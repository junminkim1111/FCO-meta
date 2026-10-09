"""@닉네임 in the chat: a user's team — the starting XI of their latest official or friendly 1vs1 match (NEXON Open
API), or of the latest squad we collected if they are a ranker and that is newer. Names, seasons, roles, salaries and
prices come from our DB. The team is sent to the model ahead of each question until the visitor removes it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..market.money import format_bp
from ..market.roles import ROLES
from ..openapi.errors import NotFoundError
from ..pipeline.formation import SUB, FormationTable
from ..pipeline.squads import match_id_time
from ..pipeline.store import parse_match_date

KST = timezone(timedelta(hours=9))
MATCH_TYPES = {50: "공식경기", 60: "공식 친선", 30: "리그 친선"}  # 1vs1에서 자기 스쿼드로 하는 경기 (클래식 1on1은 고정 스쿼드라 뺌)
OLD_AFTER = timedelta(days=30)  # 이보다 오래된 경기면 그 날짜를 분명히 밝힌다
ROLE_OF = {code: role for role, codes in ROLES.items() for code in codes}
ROLE_ORDER = {role: i for i, role in enumerate(("GK", "RB", "RWB", "CB", "LB", "LWB", "DM", "RM", "CM", "LM", "RAM",
                                                 "CAM", "LAM", "RW", "CF", "ST", "LW"))}  # fmt: skip


class TeamNotFound(Exception):
    """No team for this nickname (the message is shown to the visitor)."""


@dataclass
class Team:
    nickname: str
    source: str  # "공식경기"·"공식 친선"·"리그 친선" 또는 "랭커 수집"
    played_at: datetime
    formation: str | None
    lineup: list[dict[str, Any]]
    old: bool = False
    totals: dict[str, Any] = field(default_factory=dict)

    def label(self) -> str:
        """For the chip tooltip: '10-09 공식경기 · 4-2-3-1 · 선발 11명'."""
        when = self.played_at.astimezone(KST).strftime("%m-%d")
        return " · ".join(filter(None, [f"{when} {self.source}", self.formation, f"선발 {len(self.lineup)}명"]))

    def for_model(self) -> str:
        """The block sent ahead of the question."""
        when = self.played_at.astimezone(KST).strftime("%Y-%m-%d")
        head = [f"@{self.nickname}", f"{self.source} {when}" + (" (30일 넘은 경기)" if self.old else ""), self.formation,
                f"총 급여 {self.totals['salary']}", f"총액 {self.totals['price']}"]  # fmt: skip
        rows = [
            f"{p['role']} {p['player']} ({p['season']}, {p['grade']}강, 급여 {p['salary'] if p['salary'] is not None else '미수집'}, "
            f"{p['price'] or '시세 미수집'})"
            for p in self.lineup
        ]
        return "[사용자 팀: " + " · ".join(filter(None, head)) + "]\n" + "\n".join(rows) + "\n[/사용자 팀]"

    def to_json(self) -> dict[str, Any]:
        return {"nickname": self.nickname, "label": self.label(), "source": self.source, "formation": self.formation,
                "played_at": self.played_at.astimezone(KST).isoformat(timespec="minutes"), "old": self.old,
                "lineup": self.lineup, **self.totals}  # fmt: skip


def fetch_team(api: Any, conn: sqlite3.Connection, nickname: str, now: datetime | None = None) -> Team:
    """Latest starting XI of `nickname`: their newest official/friendly match, or our ranker squad if newer."""
    now = now or datetime.now(timezone.utc)
    try:
        ouid = api.get_ouid(nickname)
    except NotFoundError:
        raise TeamNotFound("이 닉네임의 유저를 찾지 못했어요.") from None
    found: list[tuple[datetime, str, str, list[tuple[int, int, int]]]] = []  # (경기 시각, 출처, 경기 id, 선발)
    latest = None
    for match_type in MATCH_TYPES:
        for match_id in api.user_matches(ouid, match_type, limit=1):
            started = match_id_time(match_id)
            if started and (latest is None or started > latest[0]):
                latest = (started, match_type, match_id)
    if latest:
        detail = api.match_detail(latest[2])
        info = next((i for i in detail.get("matchInfo") or [] if i.get("ouid") == ouid), None)
        players = [(int(p["spId"]), int(p["spPosition"]), int(p.get("spGrade") or 1)) for p in (info or {}).get("player") or []]
        if players:
            found.append((parse_match_date(detail["matchDate"]), MATCH_TYPES[latest[1]], latest[2], players))
    if ranker := _ranker_squad(conn, ouid):
        found.append(ranker)
    if not found:
        raise TeamNotFound("최근 공식·친선경기 기록이 없어 팀을 가져올 수 없어요.")
    played_at, source, _, players = max(found, key=lambda f: f[0])
    starters = [(sp_id, pos, grade) for sp_id, pos, grade in players if pos != SUB]
    lineup = sorted((_card(conn, sp_id, pos, grade) for sp_id, pos, grade in starters), key=lambda p: ROLE_ORDER.get(p["role"], 99))
    salaries = [p["salary"] for p in lineup if p["salary"] is not None]
    prices = [p["price_bp"] for p in lineup if p["price_bp"] is not None]
    return Team(
        nickname=nickname, source=source, played_at=played_at, formation=FormationTable.load().infer(pos for _, pos, _ in starters),
        lineup=lineup, old=now - played_at > OLD_AFTER,
        totals={"salary": sum(salaries), "price": format_bp(sum(prices)), "price_bp": sum(prices),
                "unpriced": len(lineup) - len(prices)},
    )  # fmt: skip


def _ranker_squad(conn: sqlite3.Connection, ouid: str) -> tuple[datetime, str, str, list[tuple[int, int, int]]] | None:
    """The newest squad we collected for this ouid as a ranker (daily collection), if any."""
    row = conn.execute(
        "SELECT q.match_id, m.match_date FROM ranker_squad q JOIN match m ON m.match_id = q.match_id"
        " WHERE q.ouid = ? AND q.match_order = 0 ORDER BY m.match_date DESC LIMIT 1",
        (ouid,),
    ).fetchone()
    if row is None:
        return None
    players = conn.execute("SELECT sp_id, sp_position, sp_grade FROM match_player WHERE match_id = ? AND ouid = ?", (row[0], ouid)).fetchall()
    return (datetime.fromisoformat(row[1]), "랭커 수집", row[0], [(a, b, c or 1) for a, b, c in players]) if players else None


def _card(conn: sqlite3.Connection, sp_id: int, position: int, grade: int) -> dict[str, Any]:
    name = conn.execute("SELECT name FROM meta_spid WHERE sp_id = ?", (sp_id,)).fetchone()
    season = conn.execute("SELECT class_name FROM meta_season WHERE season_id = ?", (sp_id // 1_000_000,)).fetchone()
    salary = conn.execute("SELECT salary FROM card WHERE spid = ?", (sp_id,)).fetchone()
    price = conn.execute("SELECT price FROM card_price_latest WHERE spid = ? AND grade = ?", (sp_id, grade)).fetchone()
    price_bp = price[0] if price and price[0] is not None else None
    return {
        "role": ROLE_OF.get(position, "?"), "player": name[0].strip() if name else str(sp_id),
        "season": (season[0] or "").split(" (")[0] if season else "?", "grade": grade,
        "salary": salary[0] if salary else None, "price_bp": price_bp, "price": format_bp(price_bp) if price_bp else None,
    }  # fmt: skip
