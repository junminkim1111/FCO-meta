"""Keep prices fresh for the players top rankers actually use.

The datacenter search can't look up a card by spid, but a full player name returns every season
card of that player with its 1~13강 prices. So: take the most used players from `usage_stats`
(all rankers, all formations), search each by name, skip players refreshed recently.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..crawler.client import DatacenterClient
from .collector import fetch_cards
from .search import PlayerSearch
from .storage import SCHEMA as MARKET_SCHEMA
from .storage import MarketStorage

log = logging.getLogger(__name__)

DEFAULT_PLAYERS = 150
MAX_AGE = timedelta(hours=20)  # 이보다 최근에 받은 선수는 다시 받지 않음 (매일 실행 기준)


@dataclass(frozen=True)
class UsedPlayer:
    pid: int
    name: str
    rankers: int  # 이 선수를 선발로 쓴 랭커 수 (역할 합산)
    sp_ids: tuple[int, ...]  # 랭커들이 실제로 쓴 카드


@dataclass
class RefreshResult:
    players: int = 0
    requests: int = 0
    cards: int = 0
    skipped_fresh: int = 0
    failed: int = 0


def used_players(conn: sqlite3.Connection, limit: int = DEFAULT_PLAYERS, data_as_of: str | None = None) -> list[UsedPlayer]:
    """Most used players (by rankers who start them) in the latest all-rankers stats."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not {"usage_stats", "meta_spid"} <= tables:  # 아직 집계 전
        return []
    if data_as_of is None:
        row = conn.execute("SELECT MAX(data_as_of) FROM usage_stats WHERE team_color_id = 0").fetchone()
        data_as_of = row[0] if row else None
        if data_as_of is None:
            return []
    rows = conn.execute(
        "SELECT u.pid, MAX(sp.name), SUM(u.ranker_count), GROUP_CONCAT(DISTINCT u.sp_id)"
        " FROM usage_stats u JOIN meta_spid sp ON sp.sp_id = u.sp_id"
        " WHERE u.team_color_id = 0 AND u.formation = '*' AND u.strict = 0 AND u.data_as_of = ?"
        " GROUP BY u.pid ORDER BY SUM(u.ranker_count) DESC, u.pid LIMIT ?",
        (data_as_of, limit),
    ).fetchall()
    return [UsedPlayer(pid, name, n, tuple(int(x) for x in ids.split(","))) for pid, name, n, ids in rows]


def _fresh(conn: sqlite3.Connection, player: UsedPlayer, now: datetime) -> bool:
    """All cards rankers used already have a price fetched within MAX_AGE."""
    marks = ",".join("?" * len(player.sp_ids))
    row = conn.execute(
        f"SELECT COUNT(DISTINCT spid), MIN(last) FROM (SELECT spid, MAX(fetched_at) AS last FROM card_price"
        f" WHERE spid IN ({marks}) GROUP BY spid)",
        player.sp_ids,
    ).fetchone()
    count, oldest = row
    return count == len(player.sp_ids) and oldest is not None and datetime.fromisoformat(oldest) >= now - MAX_AGE


def refresh_used_prices(
    client: DatacenterClient,
    db_path,
    *,
    limit: int = DEFAULT_PLAYERS,
    now: datetime | None = None,
) -> RefreshResult:
    """Fetch current prices of the `limit` most used players (one datacenter request each)."""
    now = now or datetime.now(timezone.utc)
    storage = MarketStorage(db_path)
    result = RefreshResult()
    try:
        storage.conn.executescript(MARKET_SCHEMA)
        players = used_players(storage.conn, limit)
        result.players = len(players)
        for p in players:
            if _fresh(storage.conn, p, now):
                result.skipped_fresh += 1
                continue
            try:
                cards = fetch_cards(client, PlayerSearch(name=p.name))
            except Exception as exc:  # 한 선수 실패로 전체를 멈추지 않음
                log.warning("price search failed for pid %d: %s", p.pid, exc)
                result.failed += 1
                continue
            result.requests += 1
            # 이름이 다른 동명이인·부분 일치 카드는 버리지 않고 함께 저장 (시세 정보로는 유효)
            storage.save_cards(cards, fetched_at=now.isoformat(timespec="seconds"))
            result.cards += len(cards)
            log.info("prices: %s → %d cards", p.name, len(cards))
        return result
    finally:
        storage.close()
