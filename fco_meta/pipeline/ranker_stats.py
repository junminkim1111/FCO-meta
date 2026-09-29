"""Average match stats of the cards top rankers use, over TOP 10,000 rankers' recent matches.

The Open API `ranker-stats` returns, per (spid, spposition), the average shots / goals / assists /
passes / dribbles / blocks / tackles over the last 20 official matches of TOP 10,000 rankers who
used that card there, plus how many such matches there were. One call takes up to 50 pairs, so the
pairs our collected squads use (most used first) cost a few dozen calls; each pair is refreshed
after MAX_AGE and a run spends at most `max_calls`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..openapi import NexonOpenApiClient
from ..openapi.client import RANKER_STATS_MAX_PLAYERS
from ..openapi.errors import BudgetExceededError, MaintenanceError, OpenApiError, RateLimitError
from .store import PipelineStore

log = logging.getLogger(__name__)

SCHEMA = """
-- (카드, 포지션)별 TOP 10,000 랭커 최근 20경기 평균 스탯 (Open API ranker-stats)
CREATE TABLE IF NOT EXISTS ranker_stats (
    sp_id       INTEGER NOT NULL,
    sp_position INTEGER NOT NULL,
    match_count INTEGER,            -- 그 랭커들이 이 카드를 이 포지션으로 뛴 경기 수 (0/NULL = 데이터 없음)
    stats       TEXT,               -- status JSON: 경기당 평균 shoot·effectiveShoot·assist·goal·dribble·passTry …
    created_at  TEXT,               -- 넥슨 집계 시각 (UTC)
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (sp_id, sp_position)
);
"""

DEFAULT_CALLS = 30  # 한 번 실행에 쓸 최대 호출 (1회 50쌍 → 1,500쌍)
MAX_AGE = timedelta(days=7)


@dataclass
class RankerStatsResult:
    pairs: int = 0  # 새로 받아야 할 (카드, 포지션) 쌍
    calls: int = 0
    saved: int = 0  # 데이터가 온 쌍
    empty: int = 0  # 응답에 없던 쌍 (랭커들이 그 포지션으로 쓴 기록 없음)
    remaining: int = 0  # 호출 한도로 다음 실행에 넘긴 쌍
    stopped: str | None = None  # budget | maintenance | rate_limit


def pairs_needing_stats(conn: sqlite3.Connection, now: datetime, mode: str = "1vs1") -> list[tuple[int, int]]:
    """(sp_id, sp_position) started by rankers in the latest snapshot's base squads, most used first,
    without stats fetched within MAX_AGE."""
    PipelineStore(conn)
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT MAX(data_as_of) FROM ranker_squad WHERE match_order = 0 AND mode = ?", (mode,)).fetchone()
    if row[0] is None:
        return []
    used = Counter(
        (sp_id, pos) for sp_id, pos in conn.execute(
            "SELECT p.sp_id, p.sp_position FROM ranker_squad q"
            " JOIN match_player p ON p.match_id = q.match_id AND p.ouid = q.ouid AND p.starter = 1"
            " WHERE q.match_order = 0 AND q.accepted = 1 AND q.data_as_of = ? AND q.mode = ?",
            (row[0], mode),
        )
    )  # fmt: skip
    cutoff = (now - MAX_AGE).isoformat(timespec="seconds")
    fresh = {(sp, po) for sp, po in conn.execute("SELECT sp_id, sp_position FROM ranker_stats WHERE fetched_at >= ?", (cutoff,))}
    return [pair for pair, _ in sorted(used.items(), key=lambda kv: (-kv[1], kv[0])) if pair not in fresh]


def collect_ranker_stats(
    api: NexonOpenApiClient, conn: sqlite3.Connection, *, max_calls: int = DEFAULT_CALLS, now: datetime | None = None
) -> RankerStatsResult:
    now = now or datetime.now(timezone.utc)
    pairs = pairs_needing_stats(conn, now)
    result = RankerStatsResult(pairs=len(pairs))
    chunks = [pairs[i : i + RANKER_STATS_MAX_PLAYERS] for i in range(0, len(pairs), RANKER_STATS_MAX_PLAYERS)]
    fetched_at = now.isoformat(timespec="seconds")
    for n, chunk in enumerate(chunks):
        if n >= max_calls:
            result.remaining = sum(len(c) for c in chunks[n:])
            break
        try:
            rows = api.ranker_stats(chunk)
        except BudgetExceededError:
            result.stopped, result.remaining = "budget", sum(len(c) for c in chunks[n:])
            break
        except MaintenanceError:
            result.stopped, result.remaining = "maintenance", sum(len(c) for c in chunks[n:])
            break
        except RateLimitError:
            result.stopped, result.remaining = "rate_limit", sum(len(c) for c in chunks[n:])
            break
        except OpenApiError as exc:  # 이 묶음만 건너뛰고 다음 실행에서 다시
            log.warning("ranker-stats failed for %d pairs: %s", len(chunk), exc)
            result.calls += 1
            continue
        result.calls += 1
        returned = {(int(r["spId"]), int(r["spPosition"])): r for r in rows or []}
        for pair in chunk:
            r = returned.get(pair)
            status = (r or {}).get("status") or {}
            if r is None or not status.get("matchCount"):
                result.empty += 1
            else:
                result.saved += 1
            conn.execute(
                "INSERT OR REPLACE INTO ranker_stats (sp_id, sp_position, match_count, stats, created_at, fetched_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (*pair, status.get("matchCount"), json.dumps(status) if status else None, (r or {}).get("createDate"), fetched_at),
            )
        conn.commit()
    return result
