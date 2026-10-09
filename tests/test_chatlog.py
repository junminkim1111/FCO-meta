"""Chat log for the admin page (no network: a fake HfApi)."""

import json
from datetime import datetime

from fco_meta.web.chatlog import KST, ChatLog, HfLogStore

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=KST).timestamp()


class FakeApi:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.fail = False

    def upload_file(self, *, path_or_fileobj, path_in_repo, repo_id, repo_type, commit_message):
        if self.fail:
            raise OSError("network down")
        assert (repo_id, repo_type) == ("me/fclm-data", "dataset")
        self.files[path_in_repo] = path_or_fileobj.read()

    def list_repo_files(self, repo, repo_type):
        return [*self.files, "fco_meta.sqlite"]

    def delete_folder(self, *, path_in_repo, repo_id, repo_type, commit_message):
        self.files = {k: v for k, v in self.files.items() if not k.startswith(path_in_repo + "/")}

    def delete_file(self, *, path_in_repo, repo_id, repo_type, commit_message):
        del self.files[path_in_repo]


def store(api, tmp_path, monkeypatch):
    import huggingface_hub

    def download(repo, path, repo_type):
        out = tmp_path / path.replace("/", "_")
        out.write_bytes(api.files[path])
        return str(out)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    return HfLogStore(api, "me/fclm-data")


def test_records_are_written_as_new_files_and_summarized(tmp_path, monkeypatch):
    api = FakeApi()
    log = ChatLog(store(api, tmp_path, monkeypatch), now=lambda: NOW)
    log.record(q="레알 공격수", outcome="ok", tools=["recommend_players"], ms=4200)
    log.record(q="4-2-3-1 어때", outcome="busy", error="Gemini API 오류 503 UNAVAILABLE")
    log.flush()
    (path,) = api.files  # 데이터셋의 logs/날짜/ 아래 새 파일 하나
    assert path.startswith("logs/2026-10-02/") and path.endswith(".jsonl")
    assert [json.loads(line)["q"] for line in api.files[path].decode().splitlines()] == ["레알 공격수", "4-2-3-1 어때"]
    assert log.pending == []

    log.record(q="안녕", outcome="ok")  # 아직 올리지 않은 기록도 요약에 들어간다
    s = log.summary(days=7)
    assert s["total"] == 3 and s["pending"] == 1
    assert s["by_outcome"] == {"ok": 2, "cached": 0, "busy": 1, "limited": 0, "cancelled": 0, "compare": 0, "blocked": 0}
    assert s["by_day"] == [{"date": "2026-10-02", "ok": 2, "cached": 0, "busy": 1, "limited": 0, "cancelled": 0, "compare": 0, "blocked": 0}]
    assert s["errors"] == [{"error": "Gemini API 오류 503 UNAVAILABLE", "count": 1, "last": "2026-10-02T12:00:00+09:00"}]
    assert [r["q"] for r in s["recent"]] == ["안녕", "4-2-3-1 어때", "레알 공격수"]  # 최근 것부터


def test_failed_upload_keeps_records_for_the_next_try(tmp_path, monkeypatch):
    api = FakeApi()
    log = ChatLog(store(api, tmp_path, monkeypatch), now=lambda: NOW)
    log.record(q="q", outcome="ok")
    api.fail = True
    log.flush()
    assert len(log.pending) == 1 and not api.files
    api.fail = False
    log.flush()
    assert not log.pending and len(api.files) == 1


def test_without_a_store_records_stay_in_memory():
    log = ChatLog(now=lambda: NOW)
    log.record(q="q", outcome="cancelled")
    log.flush()  # 올릴 곳이 없으면 그대로 둔다
    assert log.summary(days=1)["by_outcome"]["cancelled"] == 1


def test_details_last_a_week_unless_saved(tmp_path, monkeypatch):
    api, clock = FakeApi(), [NOW]
    log = ChatLog(store(api, tmp_path, monkeypatch), now=lambda: clock[0])
    log.record(q="오래 둘 질문", outcome="ok", detail={"trace": [{"at": 0.0, "kind": "route", "text": "Flash-Lite"}], "answer": "답 A"})
    log.record(q="그냥 질문", outcome="ok", detail={"trace": [], "answer": "답 B"})
    keep_id, drop_id = (r["id"] for r in log.pending)
    log.flush()
    assert {p.split("/")[0] for p in api.files} == {"logs", "details"}
    assert log.detail(drop_id) == {"trace": [], "answer": "답 B", "saved": False}
    assert log.save(keep_id)["record"]["q"] == "오래 둘 질문" and f"saved/{keep_id}.json" in api.files

    clock[0] += 8 * 86400  # 일주일이 지나면: 저장 안 한 것은 안 보이고, 다음 저장 때 날짜 폴더도 지운다
    assert log.detail(drop_id) is None and log.save(drop_id) is None
    log.flush()
    assert not any(p.startswith("details/") for p in api.files)
    fresh = ChatLog(store(api, tmp_path, monkeypatch), now=lambda: clock[0])  # 서버가 다시 떠도 저장한 것은 남는다
    assert fresh.detail(keep_id)["answer"] == "답 A" and fresh.summary()["saved"][0]["id"] == keep_id
    assert fresh.unsave(keep_id) and fresh.detail(keep_id) is None and not fresh.unsave(keep_id)
