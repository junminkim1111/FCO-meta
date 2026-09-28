from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx

from .models import RankPage
from .parser import parse_rank_inner
from .query import RankQuery

log = logging.getLogger(__name__)

BASE_URL = "https://fconline.nexon.com"
USER_AGENT = "Mozilla/5.0 (compatible; fco-meta-crawler/0.1; +https://github.com/junminkim1111/FCO-meta)"


class DatacenterClient:
    """Polite client for the FC Online datacenter ranking pages.

    - one request at a time, at least `min_interval` seconds apart
    - retries transport errors and 5xx with exponential backoff
    - optionally keeps every raw response under `raw_dir` for re-parsing
    """

    def __init__(
        self,
        http: httpx.Client | None = None,
        *,
        min_interval: float = 2.0,
        max_retries: int = 3,
        raw_dir: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=20.0)
        self._http.headers.update(
            {
                "User-Agent": USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{BASE_URL}/datacenter/rank",
            }
        )
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._raw_dir = raw_dir
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DatacenterClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch_shell(self, mode: str = "1vs1") -> str:
        """`/datacenter/rank` page: team color catalog, formation list, top-10 pick rates."""
        return self._get("/datacenter/rank", {"rt": mode})

    def fetch_rank_page(self, query: RankQuery, page: int) -> RankPage:
        return parse_rank_inner(self._get("/datacenter/rank_inner", query.to_params(page)))

    def iter_rank_pages(self, query: RankQuery, max_pages: int | None = None) -> Iterator[RankPage]:
        page = 1
        while max_pages is None or page <= max_pages:
            result = self.fetch_rank_page(query, page)
            if not result.rows:
                return
            yield result
            if result.total_pages is not None and page >= result.total_pages:
                return
            page += 1

    def _get(self, path: str, params: dict[str, str]) -> str:
        for attempt in range(self._max_retries + 1):
            self._throttle()
            try:
                resp = self._http.get(path, params=params)
                if resp.status_code < 500:
                    resp.raise_for_status()
                    self._save_raw(path, params, resp.text)
                    return resp.text
                error: Exception = httpx.HTTPStatusError(
                    f"server error {resp.status_code}", request=resp.request, response=resp
                )
            except httpx.TransportError as exc:
                error = exc
            if attempt == self._max_retries:
                raise error
            backoff = 2.0 * 2**attempt
            log.warning("request %s failed (%s), retrying in %.0fs", path, error, backoff)
            self._sleep(backoff)
        raise AssertionError("unreachable")

    def _throttle(self) -> None:
        now = self._clock()
        if self._last_request is not None:
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_request = now

    def _save_raw(self, path: str, params: dict[str, str], text: str) -> None:
        if self._raw_dir is None:
            return
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        name = f"{path.strip('/').replace('/', '_')}_{hashlib.sha1(key.encode()).hexdigest()[:12]}.html"
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        (self._raw_dir / name).write_text(text, encoding="utf-8")
