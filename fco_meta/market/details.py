"""Card details (신체·개인기·주발·특성·능력치) for the cards top rankers use.

The datacenter card popup (`POST /datacenter/PlayerPreView`, one request per card) shows the
profile and all 34 stats. Stats barely change, so a card is fetched once and refreshed only after
MAX_AGE; each run fetches at most `limit` cards (most used first) and the rest follow next run.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..crawler.client import DatacenterClient
from .parser import parse_player_preview
from .storage import SCHEMA as MARKET_SCHEMA
from .storage import MarketStorage

log = logging.getLogger(__name__)

PREVIEW_PATH = "/datacenter/PlayerPreView"
DEFAULT_CARDS = 600  # 한 번 실행에 받는 최대 카드 수 (카드당 약 2초 → 20분)
MAX_AGE = timedelta(days=30)
GRADE = 1  # 능력치 기준 강화 (강화 효과는 더하지 않은 기본값)


@dataclass
class DetailResult:
    targets: int = 0  # 상세가 없거나 오래된 카드
    fetched: int = 0
    failed: int = 0
    remaining: int = 0  # 이번 실행 한도로 남긴 카드 (다음 실행에서 이어서)


def cards_needing_details(conn: sqlite3.Connection, now: datetime) -> list[int]:
    """Cards started by rankers in the latest all-rankers stats without fresh details, most used first."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "usage_stats" not in tables:
        return []
    conn.executescript(MARKET_SCHEMA)  # card_detail이 아직 없는 DB
    row = conn.execute("SELECT MAX(data_as_of) FROM usage_stats WHERE team_color_id = 0").fetchone()
    if row[0] is None:
        return []
    cutoff = (now - MAX_AGE).isoformat(timespec="seconds")
    return [
        sp_id for (sp_id,) in conn.execute(
            "SELECT u.sp_id FROM usage_stats u LEFT JOIN card_detail d ON d.spid = u.sp_id"
            " WHERE u.team_color_id = 0 AND u.formation = '*' AND u.strict = 0 AND u.data_as_of = ?"
            " AND (d.spid IS NULL OR d.fetched_at < ?)"
            " GROUP BY u.sp_id ORDER BY SUM(u.ranker_count) DESC, u.sp_id",
            (row[0], cutoff),
        )
    ]  # fmt: skip


def refresh_card_details(
    client: DatacenterClient, db_path, *, limit: int = DEFAULT_CARDS, now: datetime | None = None
) -> DetailResult:
    now = now or datetime.now(timezone.utc)
    storage = MarketStorage(db_path)
    result = DetailResult()
    try:
        targets = cards_needing_details(storage.conn, now)
        result.targets = len(targets)
        result.remaining = max(len(targets) - limit, 0)
        for sp_id in targets[:limit]:
            try:
                html = client.post(PREVIEW_PATH, {"spid": str(sp_id), "n1strong": str(GRADE), "n1Grow": "0", "n1TeamColor": "0"},
                                   referer="/datacenter")  # fmt: skip
                storage.save_card_detail(parse_player_preview(html, sp_id, GRADE), now.isoformat(timespec="seconds"))
            except Exception as exc:  # 한 카드 실패로 전체를 멈추지 않음
                log.warning("card detail failed for %d: %s", sp_id, exc)
                result.failed += 1
                continue
            result.fetched += 1
        return result
    finally:
        storage.close()
