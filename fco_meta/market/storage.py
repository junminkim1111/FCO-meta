from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Card, CardDetail, PriceHistory

SCHEMA = """
CREATE TABLE IF NOT EXISTS card (
    spid          INTEGER PRIMARY KEY,
    pid           INTEGER NOT NULL,
    season_id     INTEGER NOT NULL,
    season_code   TEXT,
    name          TEXT NOT NULL,
    main_position TEXT,
    ovr           INTEGER,
    salary        INTEGER,
    rating        REAL,
    rating_count  INTEGER,
    updated_at    TEXT NOT NULL
);

-- 수집 시각별 강화 단계 현재가 (시세 변화 추적용으로 누적)
CREATE TABLE IF NOT EXISTS card_price (
    spid       INTEGER NOT NULL,
    grade      INTEGER NOT NULL,
    price      INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (spid, grade, fetched_at)
);

-- 데이터센터 선수 검색의 팀컬러 필터에 걸린 카드 = 해당 팀컬러 적격 카드
CREATE TABLE IF NOT EXISTS card_team_color (
    spid          INTEGER NOT NULL,
    team_color_id INTEGER NOT NULL,
    seen_at       TEXT NOT NULL,
    PRIMARY KEY (spid, team_color_id)
);

-- 포지션 필터(roles.ROLES)에 걸린 카드 = 해당 역할로 기용 가능한 카드
CREATE TABLE IF NOT EXISTS card_role (
    spid    INTEGER NOT NULL,
    role    TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (spid, role)
);

CREATE TABLE IF NOT EXISTS price_history (
    spid  INTEGER NOT NULL,
    grade INTEGER NOT NULL,
    date  TEXT NOT NULL,
    price INTEGER NOT NULL,
    PRIMARY KEY (spid, grade, date)
);

-- 카드·강화별 가장 최근 수집 가격
-- 카드 상세 (데이터센터 카드 팝업, 1강 기준): 신체·개인기·주발·특성·능력치. 능력치는 거의 바뀌지 않아 드물게 갱신
CREATE TABLE IF NOT EXISTS card_detail (
    spid        INTEGER PRIMARY KEY,
    grade       INTEGER NOT NULL,       -- 능력치 기준 강화
    positions   TEXT NOT NULL,          -- {"CM": 125, "CDM": 125}
    birth       TEXT,
    height      INTEGER,
    weight      INTEGER,
    body_type   TEXT,
    skill_moves INTEGER,
    left_foot   INTEGER,
    right_foot  INTEGER,
    reputation  TEXT,
    traits      TEXT NOT NULL,          -- ["커맨더", …]
    summary     TEXT NOT NULL,          -- {"스피드": 121, …}
    stats       TEXT NOT NULL,          -- {"속력": 124, … 34개}
    clubs       TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);

CREATE VIEW IF NOT EXISTS card_price_latest AS
SELECT p.spid, p.grade, p.price, p.fetched_at
FROM card_price p
JOIN (SELECT spid, grade, MAX(fetched_at) AS fetched_at FROM card_price GROUP BY spid, grade) last
  USING (spid, grade, fetched_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MarketStorage:
    """Market tables; can share the SQLite file used by the ranking crawler."""

    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def save_cards(
        self,
        cards: list[Card],
        *,
        team_color_id: int | None = None,
        role: str | None = None,
        fetched_at: str | None = None,
    ) -> None:
        now = fetched_at or _now()
        self.conn.executemany(
            "INSERT OR REPLACE INTO card (spid, pid, season_id, season_code, name, main_position, ovr,"
            " salary, rating, rating_count, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (c.spid, c.pid, c.season_id, c.season_code, c.name, c.main_position, c.ovr,
                 c.salary, c.rating, c.rating_count, now)  # fmt: skip
                for c in cards
            ],
        )
        self.conn.executemany(
            "INSERT OR REPLACE INTO card_price (spid, grade, price, fetched_at) VALUES (?, ?, ?, ?)",
            [(c.spid, grade, price, now) for c in cards for grade, price in c.prices.items()],
        )
        if team_color_id:
            self.conn.executemany(
                "INSERT OR REPLACE INTO card_team_color (spid, team_color_id, seen_at) VALUES (?, ?, ?)",
                [(c.spid, team_color_id, now) for c in cards],
            )
        if role:
            self.conn.executemany(
                "INSERT OR REPLACE INTO card_role (spid, role, seen_at) VALUES (?, ?, ?)",
                [(c.spid, role, now) for c in cards],
            )
        self.conn.commit()

    def save_card_detail(self, d: CardDetail, fetched_at: str | None = None) -> None:
        dump = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
        self.conn.execute(
            "INSERT OR REPLACE INTO card_detail VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d.spid, d.grade, dump(d.positions), d.birth, d.height, d.weight, d.body_type, d.skill_moves,
             d.left_foot, d.right_foot, d.reputation, dump(d.traits), dump(d.summary), dump(d.stats), dump(d.clubs),
             fetched_at or _now()),
        )  # fmt: skip
        self.conn.commit()

    def save_price_history(self, history: PriceHistory) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO price_history (spid, grade, date, price) VALUES (?, ?, ?, ?)",
            [(history.spid, history.grade, d.isoformat(), price) for d, price in history.points],
        )
        self.conn.commit()

    def find_cards(
        self,
        *,
        grade: int,
        team_color_id: int | None = None,
        role: str | None = None,
        max_price: int | None = None,
        sort: str = "price",
        limit: int = 20,
    ) -> list[sqlite3.Row]:
        """Cards with a known price at `grade`. `sort`: "price" (cheapest first) or "ovr" (highest first)."""
        sql = [
            "SELECT c.*, p.price, p.fetched_at FROM card c",
            "JOIN card_price_latest p ON p.spid = c.spid AND p.grade = ?",
        ]
        args: list[object] = [grade]
        if team_color_id:
            sql.append("JOIN card_team_color t ON t.spid = c.spid AND t.team_color_id = ?")
            args.append(team_color_id)
        if role:
            sql.append("JOIN card_role r ON r.spid = c.spid AND r.role = ?")
            args.append(role)
        sql.append("WHERE p.price IS NOT NULL")
        if max_price is not None:
            sql.append("AND p.price <= ?")
            args.append(max_price)
        order = {"price": "p.price, c.ovr DESC", "ovr": "c.ovr DESC, p.price"}[sort]
        sql.append(f"ORDER BY {order} LIMIT ?")
        args.append(limit)
        return self.conn.execute(" ".join(sql), args).fetchall()
