from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from ..storage import Storage
from .client import DatacenterClient
from .models import RankerRow
from .query import RankQuery

log = logging.getLogger(__name__)

# 정각 갱신은 서버마다 조금씩 늦게 퍼져서, 갱신 직후 몇 분은 이전 기준 시각 페이지가 섞여 온다
STALE_RETRIES = 5
STALE_WAIT = 10.0  # 초
# 남은 순위가 있는데 빈 페이지가 오면(사이트 일시 오류) 끝으로 보지 않고 잠시 뒤 다시 받는다
EMPTY_RETRIES = 4
EMPTY_WAIT = 30.0  # 초


def _untie(rows: list[RankerRow], tie: tuple[int | None, int]) -> tuple[list[RankerRow], tuple[int | None, int]]:
    """Tied rankers share the displayed rank (2581, 2581, 2583) and the snapshot is keyed by rank, so the
    second would overwrite the first. Number them by position instead (2581, 2582, 2583): the ranking skips
    exactly as many numbers as the tie has. `tie` = (displayed rank, how many before with it), carried
    across pages."""
    shown, n = tie
    out = []
    for r in rows:
        n = n + 1 if r.rank == shown else 0
        shown = r.rank
        out.append(replace(r, rank=r.rank + n) if n else r)
    return out, (shown, n)


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
    restarts: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> CrawlResult:
    """Fetch ranking pages for `query` and upsert them into `storage`.

    The ranking data is refreshed hourly. With `restarts` > 0 a crawl that sees the refresh
    part-way (long crawls such as TOP 10,000 ≈ 17 min) starts over from page 1 on the new data,
    up to that many times; otherwise it finishes as `mixed_as_of`. A page older than the snapshot
    being crawled (a server not yet refreshed) is fetched again after STALE_WAIT seconds.
    """
    for _ in range(restarts):
        result = _crawl_once(client, storage, query, max_pages, stop_on_refresh=True, sleep=sleep)
        if result.status != "refreshed":
            return result
        log.warning("ranking data was refreshed during the crawl; starting over from page 1")
    return _crawl_once(client, storage, query, max_pages, stop_on_refresh=False, sleep=sleep)


def _crawl_once(
    client: DatacenterClient, storage: Storage, query: RankQuery, max_pages: int | None, *,
    stop_on_refresh: bool, sleep: Callable[[float], None],
) -> CrawlResult:  # fmt: skip
    run_id = storage.start_run(query.mode, query)
    member_of = _single_team_color(query)
    pages = rows = 0
    total = None
    as_of_seen = set()
    status = "ok"
    target = None  # 이번 수집의 기준 시각 (첫 페이지)
    tie: tuple[int | None, int] = (None, 0)  # 동점 순위 번호 매기기 (페이지를 넘어 이어진다)
    try:
        n = 1
        while max_pages is None or n <= max_pages:
            page = client.fetch_rank_page(query, n)
            for _ in range(STALE_RETRIES):  # 아직 갱신 안 된 서버의 이전 데이터 → 잠시 뒤 다시
                if target is None or not page.rows or page.data_as_of is None or page.data_as_of >= target:
                    break
                log.info("page %d is older (%s) than %s; retrying", n, page.data_as_of, target)
                sleep(STALE_WAIT)
                page = client.fetch_rank_page(query, n)
            for _ in range(EMPTY_RETRIES if total is not None and rows < total else 0):
                if page.rows:
                    break
                log.warning("page %d came back empty at %d of %d rows; retrying in %.0fs", n, rows, total, EMPTY_WAIT)
                sleep(EMPTY_WAIT)
                page = client.fetch_rank_page(query, n)
            if not page.rows:
                if total is not None and rows < total:  # 다시 받아도 비면 끝이 아니라 불완전한 수집
                    status = "partial"
                    log.warning("page %d still empty: stopping at %d of %d rows", n, rows, total)
                break
            if page.data_as_of is None:
                raise ValueError(f"page {n}: data_as_of not found")
            target = target or page.data_as_of
            if stop_on_refresh and page.data_as_of > target:
                storage.finish_run(run_id, total, status="refreshed")  # 불완전한 수집 — 범위 계산에서 빠진다
                return CrawlResult(run_id, total, pages, rows, "refreshed")
            as_of_seen.add(page.data_as_of)
            total = page.total_count
            untied, tie = _untie(page.rows, tie)
            storage.save_page(run_id, query.mode, page.data_as_of, untied, member_of)
            pages += 1
            rows += len(page.rows)
            log.info("page %d: %d rows (total %s, as of %s)", pages, len(page.rows), total, page.data_as_of)
            if page.total_pages is not None and n >= page.total_pages:
                break
            n += 1
    except Exception:
        storage.finish_run(run_id, total, status="failed")
        raise
    if status == "ok" and len(as_of_seen) > 1:
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
