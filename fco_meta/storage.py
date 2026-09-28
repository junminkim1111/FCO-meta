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
    PRIMARY KEY (data_as_of, mode, rank)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_formation
    ON ranker_snapshot (data_as_of, mode, formation);

-- 팀컬러 필터(tc_01)로 조회했을 때 결과에 포함된 랭커 = 그 팀컬러를 적용 중인 랭커.
-- 화면에는 팀컬러가 하나만 표시되므로(특수 팀컬러 우선) 소속 판단은 이 테이블을 기준으로 한다.
CREATE TABLE IF NOT EXISTS ranker_team_color (
    data_as_of    TEXT NOT NULL,
    mode          TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    team_color_id INTEGER NOT NULL,
    run_id        INTEGER NOT NULL REFERENCES crawl_run(id),
    PRIMARY KEY (data_as_of, mode, rank, team_color_id)
);
"""

_ROW_COLUMNS = [
    "nickname", "nexon_sn", "level", "tier_icon", "squad_value", "elo", "win_rate",
    "wins", "draws", "losses", "team_color_name", "team_color_count", "team_color_crest",
    "team_color_boost", "formation", "best_tier_icon", "prev_tier_icon",
]  # fmt: skip


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

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
                "INSERT OR REPLACE INTO ranker_team_color (data_as_of, mode, rank, team_color_id, run_id)"
                " VALUES (?, ?, ?, ?, ?)",
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
