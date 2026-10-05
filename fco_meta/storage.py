from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 런타임 import 시 crawler → jobs → storage 순환 방지
    from .crawler.models import RankerRow

SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    mode          TEXT NOT NULL,
    query_json    TEXT NOT NULL,
    data_as_of    TEXT,
    total_count   INTEGER,
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    rows_saved    INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'running'
);

-- 같은 기준 시각(data_as_of)의 같은 순위는 한 행: 전체 수집과 조합 수집이 겹쳐도 중복 없음
CREATE TABLE IF NOT EXISTS ranker_snapshot (
    data_as_of       TEXT NOT NULL,
    mode             TEXT NOT NULL,
    rank             INTEGER NOT NULL,
    run_id           INTEGER NOT NULL REFERENCES crawl_run(id),
    nickname         TEXT NOT NULL,
    nexon_sn         INTEGER,
    level            INTEGER,
    tier_icon        INTEGER,
    squad_value      INTEGER,
    elo              REAL,
    win_rate         REAL,
    wins             INTEGER,
    draws            INTEGER,
    losses           INTEGER,
    team_color_name  TEXT,
    team_color_count INTEGER,
    team_color_crest TEXT,
    team_color_boost TEXT,
    formation        TEXT,
    best_tier_icon   INTEGER,
    prev_tier_icon   INTEGER,
    team_color_flag  TEXT,
    PRIMARY KEY (data_as_of, mode, rank)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_formation
    ON ranker_snapshot (data_as_of, mode, formation);

-- 랭커의 팀컬러 소속. 화면에는 팀컬러가 하나만 표시되므로(특수 팀컬러 우선) 소속 판단은 이 테이블을 기준으로 한다.
--   source='filter'  : 팀컬러 필터(tc_01)로 조회한 결과에 포함 (정확)
--   source='display' : 필터 없이 조회한 행의 표시 팀컬러·엠블럼으로 추정 (crawler/membership.py)
CREATE TABLE IF NOT EXISTS ranker_team_color (
    data_as_of    TEXT NOT NULL,
    mode          TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    team_color_id INTEGER NOT NULL,
    run_id        INTEGER NOT NULL REFERENCES crawl_run(id),
    source        TEXT NOT NULL DEFAULT 'filter',
    PRIMARY KEY (data_as_of, mode, rank, team_color_id)
);
"""

_ROW_COLUMNS = [
    "nickname", "nexon_sn", "level", "tier_icon", "squad_value", "elo", "win_rate",
    "wins", "draws", "losses", "team_color_name", "team_color_count", "team_color_crest",
    "team_color_boost", "formation", "best_tier_icon", "prev_tier_icon", "team_color_flag",
]  # fmt: skip


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(ranker_team_color)")}
        if "source" not in cols:
            self.conn.execute("ALTER TABLE ranker_team_color ADD COLUMN source TEXT NOT NULL DEFAULT 'filter'")
            self.conn.commit()
        if "team_color_flag" not in {r[1] for r in self.conn.execute("PRAGMA table_info(ranker_snapshot)")}:
            self.conn.execute("ALTER TABLE ranker_snapshot ADD COLUMN team_color_flag TEXT")
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def start_run(self, mode: str, query: object) -> int:
        cur = self.conn.execute(
            "INSERT INTO crawl_run (started_at, mode, query_json) VALUES (?, ?, ?)",
            (_now(), mode, json.dumps(asdict(query), ensure_ascii=False)),  # type: ignore[call-overload]
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def save_page(
        self,
        run_id: int,
        mode: str,
        data_as_of: datetime,
        rows: list[RankerRow],
        team_color_id: int | None = None,
    ) -> None:
        """Upsert one page of rows.

        `team_color_id`: the page came from a query filtered on exactly this team color,
        so every row is recorded as a member of it.
        """
        cols = ["data_as_of", "mode", "rank", "run_id", *_ROW_COLUMNS]
        sql = (
            f"INSERT OR REPLACE INTO ranker_snapshot ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})"
        )
        as_of = data_as_of.isoformat()
        self.conn.executemany(
            sql,
            [
                (as_of, mode, r.rank, run_id, *(getattr(r, c) for c in _ROW_COLUMNS))
                for r in rows
            ],
        )
        if team_color_id:
            self.conn.executemany(
                "INSERT OR REPLACE INTO ranker_team_color (data_as_of, mode, rank, team_color_id, run_id, source)"
                " VALUES (?, ?, ?, ?, ?, 'filter')",
                [(as_of, mode, r.rank, team_color_id, run_id) for r in rows],
            )
        self.conn.execute(
            "UPDATE crawl_run SET pages_fetched = pages_fetched + 1, rows_saved = rows_saved + ?,"
            " data_as_of = ? WHERE id = ?",
            (len(rows), as_of, run_id),
        )
        self.conn.commit()

    def finish_run(self, run_id: int, total_count: int | None, status: str = "ok") -> None:
        self.conn.execute(
            "UPDATE crawl_run SET finished_at = ?, total_count = ?, status = ? WHERE id = ?",
            (_now(), total_count, status, run_id),
        )
        self.conn.commit()


def _is_unfiltered(query_json: str) -> bool:
    from .crawler.query import RankQuery

    raw = json.loads(query_json)
    raw = {k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()}
    try:
        return not RankQuery(**raw).is_filtered
    except TypeError:
        return False


def unfiltered_coverage(conn: sqlite3.Connection, data_as_of: str, mode: str = "1vs1") -> int:
    """How many top ranks (1..N) an unfiltered crawl saved for this snapshot (0 if none).

    Only such a crawl covers *every* ranker in a rank range; filtered crawls cover one combo.
    """
    best = 0
    for query_json, rows in conn.execute(
        "SELECT query_json, rows_saved FROM crawl_run WHERE data_as_of = ? AND mode = ? AND status = 'ok'",
        (data_as_of, mode),
    ):
        if _is_unfiltered(query_json):
            best = max(best, rows or 0)
    return best


def latest_unfiltered_snapshot(conn: sqlite3.Connection, mode: str = "1vs1") -> tuple[str, int] | None:
    """(data_as_of, covered ranks) of the most recent unfiltered crawl."""
    for (as_of,) in conn.execute(
        "SELECT DISTINCT data_as_of FROM crawl_run WHERE mode = ? AND status = 'ok' AND data_as_of IS NOT NULL"
        " ORDER BY data_as_of DESC",
        (mode,),
    ):
        covered = unfiltered_coverage(conn, as_of, mode)
        if covered:
            return as_of, covered
    return None
