from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date

from ..crawler.client import DatacenterClient
from .models import Card, PriceHistory
from .parser import parse_player_list, parse_price_graph
from .roles import POSITION_GROUPS
from .search import RESULT_CAP, PlayerSearch

log = logging.getLogger(__name__)

SEARCH_PATH = "/datacenter/PlayerList"
PRICE_GRAPH_PATH = "/datacenter/PlayerPriceGraph"
SALARY_RANGE = (0, 99)
MAX_REQUESTS = 60


@dataclass
class SearchResult:
    cards: list[Card]
    requests: int
    truncated: bool  # 나눠 조회해도 200장 제한에 걸린 구간이 남았음


def fetch_cards(client: DatacenterClient, search: PlayerSearch) -> list[Card]:
    return parse_player_list(client.post(SEARCH_PATH, search.to_form(), referer="/datacenter"))


def search_all(client: DatacenterClient, search: PlayerSearch, *, max_requests: int = MAX_REQUESTS) -> SearchResult:
    """Run `search`, splitting it by salary (and position group) whenever a response hits the cap.

    Stops after `max_requests` requests (each response is ~1.8MB) and reports the result as truncated.
    """
    found: dict[int, Card] = {}
    stats = {"requests": 0, "truncated": False}

    def run(s: PlayerSearch) -> list[Card]:
        if stats["requests"] >= max_requests:
            stats["truncated"] = True
            return []
        stats["requests"] += 1
        cards = fetch_cards(client, s)
        for card in cards:
            found[card.spid] = card
        return cards

    def split_salary(s: PlayerSearch, lo: int, hi: int) -> None:
        cards = run(replace(s, salary=(lo, hi)))
        if len(cards) < RESULT_CAP:
            return
        if lo < hi:
            mid = (lo + hi) // 2
            split_salary(s, lo, mid)
            split_salary(s, mid + 1, hi)
        elif not s.positions:
            for group in POSITION_GROUPS.values():
                if len(run(replace(s, salary=(lo, hi), positions=group))) >= RESULT_CAP:
                    _mark_truncated(stats, s, lo)
        else:
            _mark_truncated(stats, s, lo)

    if len(run(search)) >= RESULT_CAP:
        if search.salary is not None:
            split_salary(search, *search.salary)
        else:
            split_salary(search, *SALARY_RANGE)
    return SearchResult(list(found.values()), stats["requests"], stats["truncated"])


def _mark_truncated(stats: dict, search: PlayerSearch, salary: int) -> None:
    stats["truncated"] = True
    log.warning("still %d+ cards at salary %d for %s; some cards may be missing", RESULT_CAP, salary, search)


def fetch_price_history(client: DatacenterClient, spid: int, grade: int, today: date) -> PriceHistory:
    html = client.post(
        PRICE_GRAPH_PATH,
        {"spid": str(spid), "n1strong": str(grade)},
        referer=f"/DataCenter/PlayerInfo?spid={spid}&n1Strong={grade}",
    )
    return parse_price_graph(html, spid, grade, today)
