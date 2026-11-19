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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from ..config import KST

log = logging.getLogger(__name__)

FLUSH_EVERY = 600.0  # 초: 이만큼마다 모아 둔 기록을 데이터셋에 올린다 (서버가 꺼질 때도)
MAX_PENDING = 2000  # 올리지 못한 기록을 이 이상 쌓지 않는다 (오래된 것부터 버림)
RECENT = 300  # 관리자 페이지에 보여 줄 최근 기록 수
DETAIL_DAYS = 7  # 질문마다의 trace·답변을 이만큼만 둔다 (지난 날짜 폴더는 저장소에서 지운다, 저장한 것은 빼고)
# 결과 종류: ok 답함 · cached 캐시된 답 · busy 혼잡 안내(Gemini·서버 오류) · limited 질문 수 제한 · cancelled 사용자가 정지
# · compare /compare (칸별 모델·시간·도구·답은 panes). 비교에서 고른 답은 compare_pick 기록으로 따로 남아 그 비교의 chosen이 된다
# · blocked 범위 밖·프롬프트 공격이라 모델 없이 거절 (Jev 판단, route에 이유)
# 모델을 부른 질문은 tokens·cost(유료 등급 기준 달러 추정), 다시 쓰기 뒤에도 확인되지 않은 수치가 남은 답은 unverified
# 답 아래 👍/👎(가끔만 보임)는 feedback 기록(ref = 그 질문의 id, vote = up·down)으로 따로 남아 그 질문의 vote가 된다
# visit(페이지 방문 — path·ref·device·v·key)와 beta_check(베타 키 입력 성공·실패 — ok)는 질문이 아니라서 OUTCOMES에 없고,
# 질문 집계·Traces·키별 하루 제한에서 빼고 summary()["visits"]로만 센다 (NON_QUESTIONS)
OUTCOMES = ("ok", "cached", "busy", "limited", "cancelled", "compare", "blocked")
NON_QUESTIONS = ("visit", "beta_check")


def _key_label(key: str) -> str:
    return "체험" if key.startswith("trial-") else key
MAX_COMPARES = 50  # 관리자 페이지에 답까지 보여 줄 최근 비교 수
DOWNLOADS = 16  # 기록 파일을 이만큼 한꺼번에 받는다 (서버가 깨어난 뒤 첫 관리자 페이지가 파일마다 기다리지 않게)


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
        paths = [p for p in sorted(self.api.list_repo_files(self.repo, repo_type="dataset"))
                 if p.startswith(folder + "/") and p.split("/")[1] >= since]  # fmt: skip
        return self._load_all(paths)

    def _load_all(self, paths: list[str]) -> list[dict[str, Any]]:
        with ThreadPoolExecutor(DOWNLOADS) as pool:
            return [r for records in pool.map(self._load, paths) for r in records]

    def compact(self, folder: str, before: str) -> None:
        """Merge each of `folder`'s date folders older than `before` (YYYY-MM-DD) that holds several files into one
        day.jsonl. One commit per date adds it and deletes the rest, so a failure leaves the files as they were."""
        from huggingface_hub import CommitOperationAdd, CommitOperationDelete

        by_date: dict[str, list[str]] = {}
        for p in sorted(self.api.list_repo_files(self.repo, repo_type="dataset")):
            if p.startswith(folder + "/") and p.endswith(".jsonl"):
                by_date.setdefault(p.split("/")[1], []).append(p)
        for date, paths in sorted(by_date.items()):
            if date >= before or len(paths) < 2:
                continue
            records, target = self._load_all(paths), f"{folder}/{date}/day.jsonl"
            body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode()
            self.api.create_commit(
                repo_id=self.repo, repo_type="dataset", commit_message=f"chat {folder}: merge {date} ({len(paths)} files)",
                operations=[CommitOperationAdd(path_in_repo=target, path_or_fileobj=body),
                            *(CommitOperationDelete(path_in_repo=p) for p in paths if p != target)],
            )  # fmt: skip
            for p in paths:
                self.read_files.pop(p, None)
            self.read_files[target] = records

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
                entry.setdefault("id", uuid.uuid4().hex[:12])  # 답과 함께 미리 보낸 id가 있으면 그것 (👍/👎가 이 id로 온다)
                self.details_pending.append({"id": entry["id"], "t": entry["t"], **detail})
            self.pending.append(entry)
            for pending in (self.pending, self.details_pending):
                if self.store and len(pending) > MAX_PENDING:
                    del pending[: len(pending) - MAX_PENDING]

    def flush(self) -> None:
        """Write what is pending to the store (kept for the next try if that fails); once a day, drop old details and
        merge each past day's files into one."""
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
        today = now.strftime("%Y-%m-%d")
        if self.cleaned != today:
            try:
                self.store.delete_before("details", self._detail_since().strftime("%Y-%m-%d"))
                for folder in ("logs", "details"):  # 지난 날짜는 파일 하나로 (관리자 페이지가 받을 파일 수를 줄인다)
                    self.store.compact(folder, today)
                self.cleaned = today
            except Exception:
                log.exception("dropping or merging old chat files failed")

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

    def today_counts(self) -> Counter[str]:
        """Questions per beta key so far today (KST) that counted against its daily limit — to refill the counters after a restart."""
        today = datetime.fromtimestamp(self.now(), KST).strftime("%Y-%m-%d")
        stored = self.store.read(today) if self.store else []
        with self.lock:
            pending = list(self.pending)
        return Counter(r["key"] for r in stored + pending
                       if r.get("key") and r["t"][:10] == today and r["outcome"] not in ("limited", "feedback", *NON_QUESTIONS))  # fmt: skip

    @staticmethod
    def _visits(raw: list[dict[str, Any]], questions: list[dict[str, Any]]) -> dict[str, Any]:
        """Page views / unique visitors (v differs per day, so uv is per day) and beta-key entries; `questions` are the question records."""
        days: dict[str, dict[str, Any]] = {}

        def day(r: dict[str, Any]) -> dict[str, Any]:
            return days.setdefault(r["t"][:10], {"date": r["t"][:10], "pv": 0, "vs": set(), "questions": 0, "keys": set(), "beta_ok": 0, "beta_fail": 0})

        for r in raw:
            if r["outcome"] == "visit":
                d = day(r)
                d["pv"] += 1
                d["vs"].add(r.get("v"))
            elif r["outcome"] == "beta_check":
                day(r)["beta_ok" if r.get("ok") else "beta_fail"] += 1
        for r in questions:
            d = day(r)
            d["questions"] += 1
            if r.get("key"):
                d["keys"].add(_key_label(r["key"]))
        visits = [r for r in raw if r["outcome"] == "visit"]
        return {
            "by_day": [{**{k: v for k, v in d.items() if k not in ("vs", "keys")}, "uv": len(d["vs"]), "keys": len(d["keys"])}
                       for _, d in sorted(days.items(), reverse=True)],
            "refs": [{"ref": k, "count": n} for k, n in Counter(r.get("ref", "") for r in visits).most_common(10)],
            "devices": dict(Counter(r.get("device", "desktop") for r in visits)),
            "paths": [{"path": k, "pv": n} for k, n in Counter(r.get("path", "") for r in visits).most_common()],
        }  # fmt: skip

    def summary(self, days: int = 7) -> dict[str, Any]:
        """Counts by outcome and by day, errors grouped by message, and the latest records (newest first)."""
        since = (datetime.fromtimestamp(self.now(), KST) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        stored = self.store.read(since) if self.store else []
        with self.lock:
            pending = list(self.pending)
        everything = sorted((r for r in stored + pending if r["t"][:10] >= since), key=lambda r: r["t"])
        raw, everything = everything, [r for r in everything if r["outcome"] not in NON_QUESTIONS]
        picks = {r["compare_id"]: r["pane"] for r in everything if r["outcome"] == "compare_pick"}
        votes = {r["ref"]: r["vote"] for r in everything if r["outcome"] == "feedback"}  # 다시 누르면 나중 것
        records = [{**r, "chosen": picks.get(r["compare_id"])} if r["outcome"] == "compare"
                   else {**r, "vote": votes[r["id"]]} if r.get("id") in votes else r
                   for r in everything if r["outcome"] not in ("compare_pick", "feedback")]  # fmt: skip
        compares = [r for r in records if r["outcome"] == "compare"]
        today = datetime.fromtimestamp(self.now(), KST).strftime("%Y-%m-%d")
        by_key: dict[str, dict[str, Any]] = {}  # 베타 키별 (키 해시 앞 12자): 질문(제한에 걸린 것 빼고)·오늘·비용·👍👎·마지막
        for r in records:
            if r.get("key") and r["outcome"] != "limited":
                label = _key_label(r["key"])  # 키 없는 방문자의 체험 질문(trial-…)은 하나로 묶는다
                k = by_key.setdefault(label, {"key": label, "questions": 0, "today": 0, "cost": 0.0, "up": 0, "down": 0, "last": r["t"]})
                k["questions"] += 1
                k["today"] += r["t"][:10] == today
                k["cost"] += r.get("cost", 0)
                k["last"] = max(k["last"], r["t"])
                if r.get("vote"):
                    k[r["vote"]] += 1
        by_day: dict[str, Counter[str]] = {}
        errors: dict[str, dict[str, Any]] = {}
        cost_by_day: Counter[str] = Counter()
        for r in records:
            by_day.setdefault(r["t"][:10], Counter())[r["outcome"]] += 1
            cost_by_day[r["t"][:10]] += r.get("cost", 0)
            if r.get("error"):
                e = errors.setdefault(r["error"], {"error": r["error"], "count": 0, "last": r["t"]})
                e["count"] += 1
                e["last"] = r["t"]
        # 모델 API 사용량: 질문 하나의 모델과 /compare 칸마다의 모델을 모델별로 (호출·오류·토큰·비용·걸린 시간)
        calls = [r for r in records if r.get("model") and r["model"] != "rules"] + [p for c in compares for p in c.get("panes", {}).values() if p.get("model")]
        by_model: dict[str, dict[str, Any]] = {}
        for c in calls:
            m = by_model.setdefault(c["model"], {"model": c["model"], "calls": 0, "errors": 0, "tokens": 0, "cost": 0.0, "ms": []})
            m["calls"] += 1
            m["errors"] += bool(c.get("error"))
            m["tokens"] += c.get("tokens", 0)
            m["cost"] += c.get("cost", 0)
            if c.get("ms") is not None:
                m["ms"].append(c["ms"])
        for m in by_model.values():
            ms = sorted(m.pop("ms"))
            m["cost"] = round(m["cost"], 4)
            m["p50_ms"] = ms[len(ms) // 2] if ms else None
            m["p95_ms"] = ms[min(len(ms) - 1, int(len(ms) * 0.95))] if ms else None
        return {
            "days": days, "since": since,
            "by_model": sorted(by_model.values(), key=lambda m: -m["calls"]),
            "tokens": sum(m["tokens"] for m in by_model.values()), "total": len(records), "pending": len(pending) if self.store else 0,
            "by_outcome": {o: sum(1 for r in records if r["outcome"] == o) for o in OUTCOMES},
            "votes": dict(Counter(r["vote"] for r in records if r.get("vote"))),
            "by_day": [{"date": d, **{o: c[o] for o in OUTCOMES}, "cost": round(cost_by_day[d], 4)} for d, c in sorted(by_day.items(), reverse=True)],
            "cost": round(sum(cost_by_day.values()), 4),
            "by_key": sorted(({**k, "cost": round(k["cost"], 4)} for k in by_key.values()), key=lambda k: (k["key"] == "admin", -k["questions"], k["key"])),
            "visits": self._visits(raw, records),
            "unverified": sum(1 for r in records if r.get("unverified")),
            "errors": sorted(errors.values(), key=lambda e: (-e["count"], e["error"])),
            "recent": [{k: v for k, v in r.items() if k != "panes"} for r in records[::-1][:RECENT]],
            "compares": compares[::-1][:MAX_COMPARES],
            "detail_since": self._detail_since().isoformat(timespec="seconds"),
            "saved": sorted(self._saved().values(), key=lambda r: r["record"]["t"], reverse=True),
        }  # fmt: skip
