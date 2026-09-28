from __future__ import annotations

import logging
from dataclasses import dataclass

from ..storage import Storage
from .client import DatacenterClient
from .query import RankQuery

log = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    run_id: int
    total_count: int | None
    pages: int
    rows: int
    status: str


def crawl_rankings(
    client: DatacenterClient,
    storage: Storage,
    query: RankQuery,
    *,
    max_pages: int | None = None,
) -> CrawlResult:
    """Fetch ranking pages for `query` and upsert them into `storage`."""
    run_id = storage.start_run(query.mode, query)
    member_of = _single_team_color(query)
    pages = rows = 0
    total = None
    as_of_seen = set()
    status = "ok"
    try:
        for page in client.iter_rank_pages(query, max_pages=max_pages):
            if page.data_as_of is None:
                raise ValueError(f"page {pages + 1}: data_as_of not found")
            as_of_seen.add(page.data_as_of)
            total = page.total_count
            storage.save_page(run_id, query.mode, page.data_as_of, page.rows, member_of)
            pages += 1
            rows += len(page.rows)
            log.info("page %d: %d rows (total %s, as of %s)", pages, len(page.rows), total, page.data_as_of)
    except Exception:
        storage.finish_run(run_id, total, status="failed")
        raise
    if len(as_of_seen) > 1:
        # 수집 도중 정각 갱신이 일어난 경우: 순위가 밀려 누락/중복이 있을 수 있음
        status = "mixed_as_of"
        log.warning("ranking data was refreshed during the crawl: %s", sorted(as_of_seen))
    storage.finish_run(run_id, total, status=status)
    return CrawlResult(run_id, total, pages, rows, status)


def _single_team_color(query: RankQuery) -> int | None:
    """Team color id when the query filters on exactly one team color with the default count range."""
    others = (
        query.team_color_id_2,
        query.team_color_league_id,
        query.team_color_league_id_2,
        query.team_color_continent_id,
        query.team_color_continent_id_2,
    )
    if query.team_color_id and not any(others) and query.team_color_count == (1, 11):
        return query.team_color_id
    return None
