from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from .budget import CallBudget
from .errors import (
    API_MAINTENANCE,
    DATA_NOT_READY,
    GAME_MAINTENANCE,
    NOT_FOUND_CODES,
    RATE_LIMIT,
    DataNotReadyError,
    MaintenanceError,
    NotFoundError,
    OpenApiError,
    RateLimitError,
)

log = logging.getLogger(__name__)

BASE_URL = "https://open.api.nexon.com"
API_KEY_HEADER = "x-nxopen-api-key"
OFFICIAL_MATCH = 50  # 공식경기 (1vs1 랭킹 모드)
RANKER_STATS_MAX_PLAYERS = 50
METADATA = ("spid", "seasonid", "spposition", "matchtype", "division")


class NexonOpenApiClient:
    """Client for the FC Online part of the NEXON Open API.

    - at most `per_second` requests in any 1-second window (dev key: 5/s)
    - every request is charged to `budget` first (dev key: 1,000/day)
    - 429 (OPENAPI00007), 5xx and transport errors are retried with exponential backoff
    - 400 OPENAPI00009 (data not ready) / 00010 (maintenance) raise without retrying
    """

    def __init__(
        self,
        api_key: str | None = None,
        http: httpx.Client | None = None,
        *,
        budget: CallBudget | None = None,
        per_second: int = 5,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        api_key = api_key if api_key is not None else os.environ.get("NEXON_API_KEY", "")
        if not api_key:
            raise ValueError("NEXON_API_KEY is not set")
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=20.0)
        self._http.headers[API_KEY_HEADER] = api_key
        self.budget = budget or CallBudget()
        self._per_second = per_second
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._sent: deque[float] = deque()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> NexonOpenApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- endpoints ---------------------------------------------------------

    def get_ouid(self, nickname: str) -> str:
        """Nickname → ouid. Raises `NotFoundError` for unknown nicknames."""
        return str(self._get("/fconline/v1/id", {"nickname": nickname})["ouid"])

    def user_matches(self, ouid: str, matchtype: int = OFFICIAL_MATCH, offset: int = 0, limit: int = 10) -> list[str]:
        """Match ids of a user, newest first (limit ≤ 100)."""
        params = {"ouid": ouid, "matchtype": matchtype, "offset": offset, "limit": limit}
        return [str(m) for m in self._get("/fconline/v1/user/match", params)]

    def match_detail(self, match_id: str) -> dict[str, Any]:
        return self._get("/fconline/v1/match-detail", {"matchid": match_id})

    def ranker_stats(self, players: Sequence[tuple[int, int]], matchtype: int = OFFICIAL_MATCH) -> list[dict[str, Any]]:
        """Average stats of `(spid, spposition)` pairs over TOP 10,000 rankers' matches (≤ 50 pairs per call)."""
        if not players:
            return []
        if len(players) > RANKER_STATS_MAX_PLAYERS:
            raise ValueError(f"ranker-stats accepts at most {RANKER_STATS_MAX_PLAYERS} players per call")
        payload = json.dumps([{"id": sp, "po": po} for sp, po in players], separators=(",", ":"))
        return self._get("/fconline/v1/ranker-stats", {"matchtype": matchtype, "players": payload})

    def metadata(self, name: str) -> list[dict[str, Any]]:
        if name not in METADATA:
            raise ValueError(f"unknown metadata: {name}")
        return self._get(f"/static/fconline/meta/{name}.json", {})

    # --- transport ---------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        for attempt in range(self._max_retries + 1):
            self.budget.spend(path)
            self._throttle()
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                error: Exception = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                error = _to_error(resp, path)
                retryable = isinstance(error, RateLimitError) or (
                    resp.status_code >= 500 and not isinstance(error, MaintenanceError)
                )
                if not retryable:
                    raise error
            if attempt == self._max_retries:
                raise error
            backoff = 1.0 * 2**attempt
            log.warning("%s failed (%s), retrying in %.0fs", path, error, backoff)
            self._sleep(backoff)
        raise AssertionError("unreachable")

    def _throttle(self) -> None:
        """Sliding 1-second window: wait until fewer than `per_second` requests were sent in the last second."""
        now = self._clock()
        while self._sent and now - self._sent[0] >= 1.0:
            self._sent.popleft()
        if len(self._sent) >= self._per_second:
            self._sleep(1.0 - (now - self._sent[0]))
            now = self._clock()
            self._sent.popleft()
        self._sent.append(now)


def _to_error(resp: httpx.Response, path: str) -> OpenApiError:
    try:
        body = resp.json().get("error") or {}
    except (ValueError, AttributeError):
        body = {}
    code, message = body.get("name"), body.get("message")
    if resp.status_code == 429 or code == RATE_LIMIT:
        cls: type[OpenApiError] = RateLimitError
    elif code == DATA_NOT_READY:
        cls = DataNotReadyError
    elif code in (GAME_MAINTENANCE, API_MAINTENANCE):
        cls = MaintenanceError
    elif code in NOT_FOUND_CODES:
        cls = NotFoundError
    else:
        cls = OpenApiError
    return cls(resp.status_code, code, message, path)
