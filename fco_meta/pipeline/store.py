"""SQLite tables for the Open API pipeline (same database as the ranking crawler)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .formation import SUB

SCHEMA = """
-- 닉네임 → ouid 조회 결과 캐시 (실패도 기록해 같은 닉네임을 반복 조회하지 않음)
CREATE TABLE IF NOT EXISTS nickname_lookup (
    nickname     TEXT PRIMARY KEY,
    ouid         TEXT,
    status       TEXT NOT NULL,             -- ok | not_found | error
    error_code   TEXT,
    looked_up_at TEXT NOT NULL
);

-- 계정(ouid 기준)과 닉네임 이력
CREATE TABLE IF NOT EXISTS account (
    ouid          TEXT PRIMARY KEY,
    nickname      TEXT NOT NULL,            -- 가장 최근에 확인한 닉네임
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_nickname (
    ouid          TEXT NOT NULL,
    nickname      TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (ouid, nickname)
);

-- user/match 결과 (최신순 matchId 목록)
CREATE TABLE IF NOT EXISTS user_match_list (
    ouid       TEXT NOT NULL,
    matchtype  INTEGER NOT NULL,
    fetched_at TEXT NOT NULL,
    match_ids  TEXT NOT NULL,               -- JSON 배열
    PRIMARY KEY (ouid, matchtype)
);

-- match-detail: 한 번 받은 matchId는 다시 호출하지 않음
CREATE TABLE IF NOT EXISTS match (
    match_id   TEXT PRIMARY KEY,
    match_date TEXT NOT NULL,               -- UTC (ISO 8601, +00:00)
    match_type INTEGER NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_team (
    match_id       TEXT NOT NULL,
    ouid           TEXT NOT NULL,
    match_result   TEXT,                    -- 승 | 무 | 패
    match_end_type INTEGER,
    PRIMARY KEY (match_id, ouid)
);
CREATE TABLE IF NOT EXISTS match_player (
    match_id    TEXT NOT NULL,
    ouid        TEXT NOT NULL,
    sp_id       INTEGER NOT NULL,
    season_id   INTEGER NOT NULL,           -- spId 앞 3자리
    pid         INTEGER NOT NULL,           -- spId 뒤 6자리
    sp_position INTEGER NOT NULL,           -- 28 = 교체
    sp_grade    INTEGER,                    -- 강화 등급
    sp_rating   REAL,
    starter     INTEGER NOT NULL,
    PRIMARY KEY (match_id, ouid, sp_id)
);
CREATE INDEX IF NOT EXISTS idx_match_player_pos ON match_player (sp_position, sp_id);

-- 랭커 스냅샷 행 ↔ 스쿼드로 쓰는 경기. match_order 0 = 기준 시각 직전 경기(기본 스쿼드)
CREATE TABLE IF NOT EXISTS ranker_squad (
    data_as_of         TEXT NOT NULL,
    mode               TEXT NOT NULL,
    rank               INTEGER NOT NULL,
    match_order        INTEGER NOT NULL,
    ouid               TEXT NOT NULL,
    match_id           TEXT NOT NULL,
    inferred_formation TEXT,
    formation_match    INTEGER NOT NULL,    -- 추론 포메이션 = 스냅샷 포메이션
    accepted           INTEGER NOT NULL,    -- 집계에 반영하는지
    run_id             INTEGER,
    PRIMARY KEY (data_as_of, mode, rank, match_order)
);

-- 랭커별 수집 상태 (재실행 시 완료된 랭커는 건너뜀)
CREATE TABLE IF NOT EXISTS ranker_squad_status (
    data_as_of TEXT NOT NULL,
    mode       TEXT NOT NULL,
    rank       INTEGER NOT NULL,
    ouid       TEXT,
    status     TEXT NOT NULL,               -- ok | provisional | nickname_not_found | no_match_before_snapshot | data_not_ready | error
    detail     TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (data_as_of, mode, rank)
);

CREATE TABLE IF NOT EXISTS pipeline_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    params_json TEXT NOT NULL,
    api_calls   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'running',
    summary     TEXT
);

-- 메타데이터 (/static/fconline/meta/*.json)
CREATE TABLE IF NOT EXISTS meta_spid (sp_id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta_season (season_id INTEGER PRIMARY KEY, class_name TEXT NOT NULL, season_img TEXT);
CREATE TABLE IF NOT EXISTS meta_position (sp_position INTEGER PRIMARY KEY, name TEXT NOT NULL);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_match_date(value: str) -> datetime:
    """`matchDate` is UTC without an offset (e.g. "2023-10-29T12:22:48")."""
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class MatchPlayer:
    sp_id: int
    sp_position: int
    sp_grade: int | None
    sp_rating: float | None


class PipelineStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.executescript(SCHEMA)

    # --- runs --------------------------------------------------------------

    def start_run(self, params: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO pipeline_run (started_at, params_json) VALUES (?, ?)",
            (now_utc(), json.dumps(params, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, api_calls: int, status: str, summary: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE pipeline_run SET finished_at = ?, api_calls = ?, status = ?, summary = ? WHERE id = ?",
            (now_utc(), api_calls, status, json.dumps(summary, ensure_ascii=False), run_id),
        )
        self.conn.commit()

    # --- nickname → ouid ---------------------------------------------------

    def cached_lookup(self, nickname: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM nickname_lookup WHERE nickname = ?", (nickname,)).fetchone()

    def save_lookup(self, nickname: str, ouid: str | None, status: str, error_code: str | None = None) -> None:
        now = now_utc()
        self.conn.execute(
            "INSERT OR REPLACE INTO nickname_lookup (nickname, ouid, status, error_code, looked_up_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (nickname, ouid, status, error_code, now),
        )
        if ouid:
            self.conn.execute(
                "INSERT INTO account (ouid, nickname, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (ouid) DO UPDATE SET nickname = excluded.nickname, last_seen_at = excluded.last_seen_at",
                (ouid, nickname, now, now),
            )
            self.conn.execute(
                "INSERT INTO account_nickname (ouid, nickname, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (ouid, nickname) DO UPDATE SET last_seen_at = excluded.last_seen_at",
                (ouid, nickname, now, now),
            )
        self.conn.commit()

    def forget_lookup(self, nickname: str) -> None:
        self.conn.execute("DELETE FROM nickname_lookup WHERE nickname = ?", (nickname,))
        self.conn.commit()

    # --- user/match --------------------------------------------------------

    def cached_match_list(self, ouid: str, matchtype: int, fetched_after: datetime) -> list[str] | None:
        """Cached match ids if they were fetched after `fetched_after` (i.e. they cover everything before it)."""
        row = self.conn.execute(
            "SELECT fetched_at, match_ids FROM user_match_list WHERE ouid = ? AND matchtype = ?", (ouid, matchtype)
        ).fetchone()
        if row is None or datetime.fromisoformat(row["fetched_at"]) < fetched_after:
            return None
        return list(json.loads(row["match_ids"]))

    def save_match_list(self, ouid: str, matchtype: int, match_ids: list[str], fetched_at: datetime | None = None) -> None:
        fetched = fetched_at.isoformat(timespec="seconds") if fetched_at else now_utc()
        self.conn.execute(
            "INSERT OR REPLACE INTO user_match_list (ouid, matchtype, fetched_at, match_ids) VALUES (?, ?, ?, ?)",
            (ouid, matchtype, fetched, json.dumps(match_ids)),
        )
        self.conn.commit()

    # --- match-detail ------------------------------------------------------

    def has_match(self, match_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM match WHERE match_id = ?", (match_id,)).fetchone() is not None

    def match_date(self, match_id: str) -> datetime | None:
        row = self.conn.execute("SELECT match_date FROM match WHERE match_id = ?", (match_id,)).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def save_match(self, detail: dict[str, Any]) -> None:
        """Store match header plus result and players of every participant (nicknames are not kept)."""
        match_id = detail["matchId"]
        self.conn.execute(
            "INSERT OR REPLACE INTO match (match_id, match_date, match_type, fetched_at) VALUES (?, ?, ?, ?)",
            (match_id, parse_match_date(detail["matchDate"]).isoformat(), detail.get("matchType"), now_utc()),
        )
        for info in detail.get("matchInfo") or []:
            ouid = info["ouid"]
            md = info.get("matchDetail") or {}
            self.conn.execute(
                "INSERT OR REPLACE INTO match_team (match_id, ouid, match_result, match_end_type) VALUES (?, ?, ?, ?)",
                (match_id, ouid, md.get("matchResult"), md.get("matchEndType")),
            )
            rows = []
            for p in info.get("player") or []:
                sp_id, pos = int(p["spId"]), int(p["spPosition"])
                rating = (p.get("status") or {}).get("spRating")
                rows.append(
                    (match_id, ouid, sp_id, sp_id // 1_000_000, sp_id % 1_000_000, pos, p.get("spGrade"), rating, int(pos != SUB))
                )
            self.conn.executemany(
                "INSERT OR REPLACE INTO match_player (match_id, ouid, sp_id, season_id, pid, sp_position, sp_grade,"
                " sp_rating, starter) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        self.conn.commit()

    def players(self, match_id: str, ouid: str) -> list[MatchPlayer]:
        rows = self.conn.execute(
            "SELECT sp_id, sp_position, sp_grade, sp_rating FROM match_player WHERE match_id = ? AND ouid = ?",
            (match_id, ouid),
        ).fetchall()
        return [MatchPlayer(r["sp_id"], r["sp_position"], r["sp_grade"], r["sp_rating"]) for r in rows]

    # --- squads ------------------------------------------------------------

    def squad_status(self, data_as_of: str, mode: str, rank: int) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM ranker_squad_status WHERE data_as_of = ? AND mode = ? AND rank = ?",
            (data_as_of, mode, rank),
        ).fetchone()
        return row[0] if row else None

    def save_squad_status(
        self, data_as_of: str, mode: str, rank: int, ouid: str | None, status: str, detail: str | None = None
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO ranker_squad_status (data_as_of, mode, rank, ouid, status, detail, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (data_as_of, mode, rank, ouid, status, detail, now_utc()),
        )
        self.conn.commit()

    def save_squad(
        self,
        data_as_of: str,
        mode: str,
        rank: int,
        rows: list[tuple[int, str, str, str | None, bool, bool]],
        run_id: int | None,
    ) -> None:
        """`rows`: (match_order, ouid, match_id, inferred_formation, formation_match, accepted)."""
        self.conn.execute(
            "DELETE FROM ranker_squad WHERE data_as_of = ? AND mode = ? AND rank = ?", (data_as_of, mode, rank)
        )
        self.conn.executemany(
            "INSERT INTO ranker_squad (data_as_of, mode, rank, match_order, ouid, match_id, inferred_formation,"
            " formation_match, accepted, run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(data_as_of, mode, rank, o, ouid, m, f, int(fm), int(a), run_id) for o, ouid, m, f, fm, a in rows],
        )
        self.conn.commit()

    # --- metadata ----------------------------------------------------------

    def save_metadata(self, name: str, items: list[dict[str, Any]]) -> int:
        if name == "spid":
            sql, rows = "INSERT OR REPLACE INTO meta_spid VALUES (?, ?)", [(int(i["id"]), i["name"]) for i in items]
        elif name == "seasonid":
            sql = "INSERT OR REPLACE INTO meta_season VALUES (?, ?, ?)"
            rows = [(int(i["seasonId"]), i["className"], i.get("seasonImg")) for i in items]
        elif name == "spposition":
            sql = "INSERT OR REPLACE INTO meta_position VALUES (?, ?)"
            rows = [(int(i["spposition"]), i["desc"]) for i in items]
        else:
            raise ValueError(f"metadata {name} is not stored")
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)
