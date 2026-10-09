"""Chat log for the admin page: one record per /api/chat question (time, question, outcome, error, tools,
model, duration), and per question its details — what /trace shows (route, thoughts, tools, results, times) and the
answer — kept for DETAIL_DAYS. A detail the admin saves is kept for good (saved/<id>.json) until unsaved.

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
DETAIL_DAYS = 7  # 질문마다의 trace·답변을 이만큼만 둔다 (지난 날짜 폴더는 저장소에서 지운다, 저장한 것은 빼고)
# 결과 종류: ok 답함 · cached 캐시된 답 · busy 혼잡 안내(Gemini·서버 오류) · limited 질문 수 제한 · cancelled 사용자가 정지
# · compare /compare (칸별 모델·시간·도구·답은 panes). 비교에서 고른 답은 compare_pick 기록으로 따로 남아 그 비교의 chosen이 된다
# · blocked 범위 밖·프롬프트 공격이라 모델 없이 거절 (Jev 판단, route에 이유)
OUTCOMES = ("ok", "cached", "busy", "limited", "cancelled", "compare", "blocked")
MAX_COMPARES = 50  # 관리자 페이지에 답까지 보여 줄 최근 비교 수


class HfLogStore:
    """Log files in the private dataset (the one holding the DB)."""

    def __init__(self, api: Any, repo: str):
        self.api, self.repo = api, repo
        self.read_files: dict[str, list[dict[str, Any]]] = {}  # 올린 파일은 바뀌지 않으므로 한 번만 받는다

    def write(self, records: list[dict[str, Any]], now: datetime, folder: str = "logs") -> None:
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode()
        path = f"{folder}/{now:%Y-%m-%d}/{now:%H%M%S}-{uuid.uuid4().hex[:6]}.jsonl"
        self.api.upload_file(
            path_or_fileobj=io.BytesIO(body), path_in_repo=path, repo_id=self.repo, repo_type="dataset",
            commit_message=f"chat {folder} ({len(records)})",
        )  # fmt: skip

    def read(self, since: str, folder: str = "logs") -> list[dict[str, Any]]:
        """Records in `folder` files dated `since` (YYYY-MM-DD) or later."""
        out = []
        for path in sorted(self.api.list_repo_files(self.repo, repo_type="dataset")):
            if path.startswith(folder + "/") and path.split("/")[1] >= since:
                out += self._load(path)
        return out

    def _load(self, path: str) -> list[dict[str, Any]]:
        from huggingface_hub import hf_hub_download

        if path not in self.read_files:
            with open(hf_hub_download(self.repo, path, repo_type="dataset"), encoding="utf-8") as f:
                self.read_files[path] = [json.loads(line) for line in f if line.strip()]
        return self.read_files[path]

    def delete_before(self, folder: str, before: str) -> None:
        """Delete `folder`'s date folders older than `before` (YYYY-MM-DD)."""
        dates = {p.split("/")[1] for p in self.api.list_repo_files(self.repo, repo_type="dataset") if p.startswith(folder + "/")}
        for date in sorted(d for d in dates if d < before):
            self.api.delete_folder(path_in_repo=f"{folder}/{date}", repo_id=self.repo, repo_type="dataset",
                                   commit_message=f"chat {folder}: drop {date}")  # fmt: skip
            self.read_files = {k: v for k, v in self.read_files.items() if not k.startswith(f"{folder}/{date}/")}

    def write_saved(self, item: dict[str, Any]) -> None:
        self.api.upload_file(
            path_or_fileobj=io.BytesIO(json.dumps(item, ensure_ascii=False).encode()), path_in_repo=f"saved/{item['id']}.json",
            repo_id=self.repo, repo_type="dataset", commit_message="chat detail saved",
        )  # fmt: skip

    def delete_saved(self, item_id: str) -> None:
        self.api.delete_file(path_in_repo=f"saved/{item_id}.json", repo_id=self.repo, repo_type="dataset",
                             commit_message="chat detail unsaved")  # fmt: skip
        self.read_files.pop(f"saved/{item_id}.json", None)

    def read_saved(self) -> list[dict[str, Any]]:
        return [r for p in self.api.list_repo_files(self.repo, repo_type="dataset")
                if p.startswith("saved/") and p.endswith(".json") for r in self._load(p)]  # fmt: skip


class ChatLog:
    def __init__(self, store: HfLogStore | None = None, now: Callable[[], float] = time.time):
        self.store, self.now = store, now
        self.pending: list[dict[str, Any]] = []  # 아직 올리지 않은 기록 (저장소가 없으면 전부)
        self.details_pending: list[dict[str, Any]] = []  # 그 trace·답변 (기록의 id로 잇는다)
        self.saved: dict[str, dict[str, Any]] | None = None  # 저장한 trace·답변 (처음 필요할 때 저장소에서 읽는다)
        self.cleaned: str | None = None  # 지난 trace·답변을 마지막으로 지운 날
        self.lock = threading.Lock()

    def record(self, detail: dict[str, Any] | None = None, **fields: Any) -> None:
        """One question. `detail` = {"trace": [...], "answer": "..."} — kept DETAIL_DAYS, reachable by the record's id."""
        entry = {"t": datetime.fromtimestamp(self.now(), KST).isoformat(timespec="seconds"), **fields}
        with self.lock:
            if detail is not None:
                entry["id"] = uuid.uuid4().hex[:12]
                self.details_pending.append({"id": entry["id"], "t": entry["t"], **detail})
            self.pending.append(entry)
            for pending in (self.pending, self.details_pending):
                if self.store and len(pending) > MAX_PENDING:
                    del pending[: len(pending) - MAX_PENDING]

    def flush(self) -> None:
        """Write what is pending to the store (kept for the next try if that fails); once a day, drop old details."""
        if self.store is None:
            return
        now = datetime.fromtimestamp(self.now(), KST)
        for name, folder in (("pending", "logs"), ("details_pending", "details")):
            with self.lock:
                batch = getattr(self, name)
                setattr(self, name, [])
            if not batch:
                continue
            try:
                self.store.write(batch, now, folder)
            except Exception:
                log.exception("writing %d chat %s failed; keeping them for the next try", len(batch), folder)
                with self.lock:
                    getattr(self, name)[:0] = batch
        if self.cleaned != now.strftime("%Y-%m-%d"):
            try:
                self.store.delete_before("details", self._detail_since().strftime("%Y-%m-%d"))
                self.cleaned = now.strftime("%Y-%m-%d")
            except Exception:
                log.exception("dropping old chat details failed")

    def _detail_since(self) -> datetime:
        return datetime.fromtimestamp(self.now(), KST) - timedelta(days=DETAIL_DAYS)

    def _find(self, folder: str, item_id: str) -> dict[str, Any] | None:
        since = self._detail_since()
        stored = self.store.read(since.strftime("%Y-%m-%d"), folder) if self.store else []
        with self.lock:
            pending = list(self.pending if folder == "logs" else self.details_pending)
        return next((r for r in stored + pending if r.get("id") == item_id and datetime.fromisoformat(r["t"]) >= since), None)

    def _saved(self) -> dict[str, dict[str, Any]]:
        if self.saved is None:
            self.saved = {r["id"]: r for r in (self.store.read_saved() if self.store else [])}
        return self.saved

    def detail(self, item_id: str) -> dict[str, Any] | None:
        """A question's trace and answer: saved, or within DETAIL_DAYS."""
        if item := self._saved().get(item_id):
            return {"trace": item["trace"], "answer": item["answer"], "saved": True}
        found = self._find("details", item_id)
        return {"trace": found["trace"], "answer": found["answer"], "saved": False} if found else None

    def save(self, item_id: str) -> dict[str, Any] | None:
        """Keep a question with its trace and answer for good (None when it is past DETAIL_DAYS or unknown)."""
        if item_id in self._saved():
            return self.saved[item_id]
        record, found = self._find("logs", item_id), self._find("details", item_id)
        if record is None or found is None:
            return None
        item = {"id": item_id, "saved_at": datetime.fromtimestamp(self.now(), KST).isoformat(timespec="seconds"),
                "record": record, "trace": found["trace"], "answer": found["answer"]}  # fmt: skip
        if self.store:
            self.store.write_saved(item)
        self.saved[item_id] = item
        return item

    def unsave(self, item_id: str) -> bool:
        if item_id not in self._saved():
            return False
        if self.store:
            self.store.delete_saved(item_id)
        del self.saved[item_id]
        return True

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
        everything = sorted((r for r in stored + pending if r["t"][:10] >= since), key=lambda r: r["t"])
        picks = {r["compare_id"]: r["pane"] for r in everything if r["outcome"] == "compare_pick"}
        records = [{**r, "chosen": picks.get(r["compare_id"])} if r["outcome"] == "compare" else r
                   for r in everything if r["outcome"] != "compare_pick"]  # fmt: skip
        compares = [r for r in records if r["outcome"] == "compare"]
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
            "recent": [{k: v for k, v in r.items() if k != "panes"} for r in records[::-1][:RECENT]],
            "compares": compares[::-1][:MAX_COMPARES],
            "detail_since": self._detail_since().isoformat(timespec="seconds"),
            "saved": sorted(self._saved().values(), key=lambda r: r["record"]["t"], reverse=True),
        }  # fmt: skip
