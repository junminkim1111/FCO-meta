from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from .errors import BudgetExceededError

KST = timezone(timedelta(hours=9))

SCHEMA = """
-- Open API 호출 수 (KST 날짜별·엔드포인트별). 재시도·실패 요청도 모두 센다.
CREATE TABLE IF NOT EXISTS api_usage (
    day      TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    calls    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, endpoint)
);
"""


def kst_today() -> str:
    return datetime.now(KST).date().isoformat()


class CallBudget:
    """Daily call budget shared by every run on the same database, plus an optional per-run cap.

    `spend()` is called right before each HTTP request; it raises `BudgetExceededError`
    instead of letting the request go out once either limit is reached.
    """

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        daily_limit: int = 1000,
        run_limit: int | None = None,
        today: Callable[[], str] = kst_today,
    ):
        self._conn = conn or sqlite3.connect(":memory:")
        self._conn.executescript(SCHEMA)
        self.daily_limit = daily_limit
        self.run_limit = run_limit
        self._today = today
        self.used_this_run = 0

    def used_today(self) -> int:
        row = self._conn.execute("SELECT COALESCE(SUM(calls), 0) FROM api_usage WHERE day = ?", (self._today(),)).fetchone()
        return int(row[0])

    def remaining(self) -> int:
        left = self.daily_limit - self.used_today()
        if self.run_limit is not None:
            left = min(left, self.run_limit - self.used_this_run)
        return max(left, 0)

    def spend(self, endpoint: str) -> None:
        if self.run_limit is not None and self.used_this_run >= self.run_limit:
            raise BudgetExceededError(f"run budget exhausted ({self.used_this_run}/{self.run_limit})")
        used = self.used_today()
        if used >= self.daily_limit:
            raise BudgetExceededError(f"daily budget exhausted ({used}/{self.daily_limit}, {self._today()} KST)")
        self._conn.execute(
            "INSERT INTO api_usage (day, endpoint, calls) VALUES (?, ?, 1)"
            " ON CONFLICT (day, endpoint) DO UPDATE SET calls = calls + 1",
            (self._today(), endpoint),
        )
        self._conn.commit()
        self.used_this_run += 1
