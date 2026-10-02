"""DB sync for the Render host, with a fake HfApi and hook (no network)."""

import sqlite3

import pytest

pytest.importorskip("huggingface_hub")

from fco_meta import cloud  # noqa: E402


class FakeApi:
    def __init__(self):
        self.calls, self.uploaded = [], []

    def whoami(self):
        return {"name": "me"}

    def upload_file(self, **kw):  # 임시 사본이 지워지기 전에 내용을 확인한다
        self.uploaded.append(sqlite3.connect(kw["path_or_fileobj"]).execute("SELECT a FROM t").fetchall())
        self.calls.append(("upload_file", (), kw))

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.calls.append((name, args, kwargs))


class Posted:
    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return self

    def raise_for_status(self):
        pass


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (a)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    return path


def test_push_db_uploads_a_private_copy_and_asks_render_to_redeploy(db, monkeypatch):
    monkeypatch.delenv("HF_DATA_REPO", raising=False)
    monkeypatch.setenv("RENDER_DEPLOY_HOOK", "https://api.render.com/deploy/srv-x?key=y")
    api, post = FakeApi(), Posted()
    cloud.push_db(api, post, db=db)
    assert [c[0] for c in api.calls] == ["create_repo", "upload_file", "super_squash_history"]
    create = api.calls[0]
    assert create[1] == ("me/fclm-data",) and create[2]["private"] is True  # 랭커 데이터는 비공개
    kw = api.calls[1][2]
    assert (kw["repo_id"], kw["path_in_repo"], kw["repo_type"]) == ("me/fclm-data", "fco_meta.sqlite", "dataset")
    assert api.uploaded == [[(1,)]]
    assert post.urls == ["https://api.render.com/deploy/srv-x?key=y"]


def test_push_db_without_a_hook_only_uploads(db, monkeypatch):
    monkeypatch.setenv("HF_DATA_REPO", "someone/data")
    monkeypatch.delenv("RENDER_DEPLOY_HOOK", raising=False)
    api, post = FakeApi(), Posted()
    cloud.push_db(api, post, db=db)
    assert api.calls[1][2]["repo_id"] == "someone/data" and post.urls == []


def test_pull_db_downloads_into_data(monkeypatch):
    import huggingface_hub

    calls = []
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda *a, **kw: calls.append((a, kw)) or "data/fco_meta.sqlite")
    assert cloud.pull_db("me/fclm-data") == "data/fco_meta.sqlite"
    assert calls == [(("me/fclm-data", "fco_meta.sqlite"), {"repo_type": "dataset", "local_dir": "data"})]


def test_serve_downloads_in_a_child_process(monkeypatch):
    import fco_meta.web.__main__ as web_main

    calls = []
    monkeypatch.setenv("HF_DATA_REPO", "me/fclm-data")
    monkeypatch.setattr(cloud.subprocess, "run", lambda cmd, check: calls.append(cmd))
    monkeypatch.setattr(web_main, "main", lambda argv: calls.append(argv) or 0)
    assert cloud.serve(1234) == 0
    download, web = calls  # 받기가 끝난 뒤 웹 (받기 메모리는 자식 프로세스와 함께 반납)
    assert download[-1] == "from fco_meta.cloud import pull_db; pull_db('me/fclm-data')"
    assert web == ["--db", "data/fco_meta.sqlite", "--host", "0.0.0.0", "--port", "1234", "--log-to-dataset", "me/fclm-data"]
