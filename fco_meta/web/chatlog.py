"""Chat log for the admin page: one record per /api/chat question (time, question, outcome, error, tools,
model, duration).

Render's disk is not kept across sleeps and deploys, so records are kept in memory and written to the private
Hugging Face dataset now and then (logs/YYYY-MM-DD/HHMMSS-xxxx.jsonl, a new file each time — nothing is
rewritten). Without a store (local runs) they stay in memory only. Visitor IPs are not recorded.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
FLUSH_EVERY = 600.0  # 초: 이만큼마다 모아 둔 기록을 데이터셋에 올린다 (서버가 꺼질 때도)
MAX_PENDING = 2000  # 올리지 못한 기록을 이 이상 쌓지 않는다 (오래된 것부터 버림)
RECENT = 300  # 관리자 페이지에 보여 줄 최근 기록 수
# 결과 종류: ok 답함 · cached 캐시된 답 · busy 혼잡 안내(Gemini·서버 오류) · limited 질문 수 제한 · cancelled 사용자가 정지
OUTCOMES = ("ok", "cached", "busy", "limited", "cancelled")


class HfLogStore:
    """Log files in the private dataset (the one holding the DB)."""

    def __init__(self, api: Any, repo: str):
        self.api, self.repo = api, repo
        self.read_files: dict[str, list[dict[str, Any]]] = {}  # 올린 파일은 바뀌지 않으므로 한 번만 받는다

    def write(self, records: list[dict[str, Any]], now: datetime) -> None:
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode()
        path = f"logs/{now:%Y-%m-%d}/{now:%H%M%S}-{uuid.uuid4().hex[:6]}.jsonl"
        self.api.upload_file(
            path_or_fileobj=io.BytesIO(body), path_in_repo=path, repo_id=self.repo, repo_type="dataset",
            commit_message=f"chat logs ({len(records)})",
        )  # fmt: skip

    def read(self, since: str) -> list[dict[str, Any]]:
        """Records in files dated `since` (YYYY-MM-DD) or later."""
        from huggingface_hub import hf_hub_download

        out = []
        for path in sorted(self.api.list_repo_files(self.repo, repo_type="dataset")):
            if not path.startswith("logs/") or path.split("/")[1] < since:
                continue
            if path not in self.read_files:
                with open(hf_hub_download(self.repo, path, repo_type="dataset"), encoding="utf-8") as f:
                    self.read_files[path] = [json.loads(line) for line in f if line.strip()]
            out += self.read_files[path]
        return out


class ChatLog:
    def __init__(self, store: HfLogStore | None = None, now: Callable[[], float] = time.time):
        self.store, self.now = store, now
        self.pending: list[dict[str, Any]] = []  # 아직 올리지 않은 기록 (저장소가 없으면 전부)
        self.lock = threading.Lock()

    def record(self, **fields: Any) -> None:
        entry = {"t": datetime.fromtimestamp(self.now(), KST).isoformat(timespec="seconds"), **fields}
        with self.lock:
            self.pending.append(entry)
            if self.store and len(self.pending) > MAX_PENDING:
                del self.pending[: len(self.pending) - MAX_PENDING]

    def flush(self) -> None:
        """Write what is pending to the store (kept for the next try if that fails)."""
        if self.store is None:
            return
        with self.lock:
            batch, self.pending = self.pending, []
        if not batch:
            return
        try:
            self.store.write(batch, datetime.fromtimestamp(self.now(), KST))
        except Exception:
            log.exception("writing %d chat log records failed; keeping them for the next try", len(batch))
            with self.lock:
                self.pending[:0] = batch

    def run_flusher(self, every: float = FLUSH_EVERY, sleep: Callable[[float], None] = time.sleep) -> None:
        """Background loop (daemon thread)."""
        while True:
            sleep(every)
            self.flush()

    def summary(self, days: int = 7) -> dict[str, Any]:
        """Counts by outcome and by day, errors grouped by message, and the latest records (newest first)."""
        since = (datetime.fromtimestamp(self.now(), KST) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        stored = self.store.read(since) if self.store else []
        with self.lock:
            pending = list(self.pending)
        records = sorted((r for r in stored + pending if r["t"][:10] >= since), key=lambda r: r["t"])
        by_day: dict[str, Counter[str]] = {}
        errors: dict[str, dict[str, Any]] = {}
        for r in records:
            by_day.setdefault(r["t"][:10], Counter())[r["outcome"]] += 1
            if r.get("error"):
                e = errors.setdefault(r["error"], {"error": r["error"], "count": 0, "last": r["t"]})
                e["count"] += 1
                e["last"] = r["t"]
        return {
            "days": days, "since": since, "total": len(records), "pending": len(pending) if self.store else 0,
            "by_outcome": {o: sum(1 for r in records if r["outcome"] == o) for o in OUTCOMES},
            "by_day": [{"date": d, **{o: c[o] for o in OUTCOMES}} for d, c in sorted(by_day.items(), reverse=True)],
            "errors": sorted(errors.values(), key=lambda e: (-e["count"], e["error"])),
            "recent": records[::-1][:RECENT],
        }  # fmt: skip
