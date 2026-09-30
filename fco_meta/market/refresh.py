"""Keep prices (and salaries) fresh for the players top rankers actually use.

The datacenter search can't look up a card by spid, but a full player name returns every season
card of that player with its 1~13강 prices and salary. So: take the used players from `usage_stats`
(all rankers, all formations), search each by name (one request per player), keep only the season
cards rankers actually used, and skip players refreshed recently.

Refreshed in tiers so the run stays about the same length as the ranker count grows (10,000 rankers use
thousands of players at ~2 s each): the HOT_PLAYERS most used every day, the rest once a week (never-priced
first, then the oldest), at most `max_requests` searches per run — the rest wait for the next run.
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

DEFAULT_PLAYERS = 10_000  # 살펴볼 '많이 쓰는 선수' 수 = 사실상 쓰인 선수 전부
HOT_PLAYERS = 500  # 이만큼 많이 쓰이는 선수는 매일 갱신
MAX_AGE = timedelta(hours=20)  # 많이 쓰는 선수: 이보다 최근에 받았으면 다시 받지 않음 (매일 실행 기준)
REST_MAX_AGE = timedelta(days=7)  # 나머지 선수: 일주일에 한 번
MAX_REQUESTS = 1500  # 한 번 실행에 보낼 최대 검색 수 (선수당 약 2초 → 약 50분), 남으면 다음 실행으로


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
    cards: int = 0  # 저장한 카드 (랭커가 쓴 시즌)
    cards_seen: int = 0  # 검색 응답에 온 카드 (그 선수의 모든 시즌)
    skipped_fresh: int = 0
    failed: int = 0
    deferred: int = 0  # 갱신할 때가 됐지만 max_requests를 넘어 다음 실행으로 넘긴 선수


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


def _oldest_price(conn: sqlite3.Connection, player: UsedPlayer) -> datetime | None:
    """When the stalest of the player's used cards was priced; None if some card has no price yet."""
    marks = ",".join("?" * len(player.sp_ids))
    count, oldest = conn.execute(
        f"SELECT COUNT(DISTINCT spid), MIN(last) FROM (SELECT spid, MAX(fetched_at) AS last FROM card_price"
        f" WHERE spid IN ({marks}) GROUP BY spid)",
        player.sp_ids,
    ).fetchone()
    return datetime.fromisoformat(oldest) if count == len(player.sp_ids) and oldest else None


def refresh_used_prices(
    client: DatacenterClient,
    db_path,
    *,
    limit: int = DEFAULT_PLAYERS,
    max_requests: int = MAX_REQUESTS,
    now: datetime | None = None,
) -> RefreshResult:
    """Fetch current prices of the most used players that are due (one datacenter request each)."""
    now = now or datetime.now(timezone.utc)
    storage = MarketStorage(db_path)
    result = RefreshResult()
    try:
        storage.conn.executescript(MARKET_SCHEMA)
        players = used_players(storage.conn, limit)
        result.players = len(players)
        due = []  # (순서 키, 선수): 많이 쓰는 선수(사용 순) → 시세가 없는 선수 → 가장 오래된 선수
        for i, p in enumerate(players):
            hot = i < HOT_PLAYERS
            oldest = _oldest_price(storage.conn, p)
            if oldest is not None and oldest >= now - (MAX_AGE if hot else REST_MAX_AGE):
                result.skipped_fresh += 1
            else:
                due.append(((0, 0, "", i) if hot else (1, oldest is not None, oldest.isoformat() if oldest else "", i), p))
        due.sort(key=lambda d: d[0])
        result.deferred = max(len(due) - max_requests, 0)
        for _, p in due[:max_requests]:
            try:
                cards = fetch_cards(client, PlayerSearch(name=p.name))
            except Exception as exc:  # 한 선수 실패로 전체를 멈추지 않음
                log.warning("price search failed for pid %d: %s", p.pid, exc)
                result.failed += 1
                continue
            result.requests += 1
            # 응답에는 그 선수의 모든 시즌 카드가 오지만, 랭커들이 실제로 쓴 시즌 카드만 저장한다 (강화 1~13은 모두)
            used = [c for c in cards if c.spid in p.sp_ids]
            storage.save_cards(used, fetched_at=now.isoformat(timespec="seconds"))
            result.cards += len(used)
            result.cards_seen += len(cards)
            if len(used) < len(p.sp_ids):
                log.warning("prices: %s — %d of %d used cards not in search result", p.name, len(p.sp_ids) - len(used), len(p.sp_ids))
            log.info("prices: %s → %d/%d cards kept", p.name, len(used), len(cards))
        return result
    finally:
        storage.close()
