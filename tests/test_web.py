import pytest
from test_analytics import db  # noqa: F401  (아스널 4명 랭커, usage_stats 빌드 완료)

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fco_meta.chatbot import Toolbox  # noqa: E402
from fco_meta.web import create_app  # noqa: E402


@pytest.fixture
def client(db, tmp_path):  # noqa: F811
    Toolbox(db.conn)  # 시세 테이블 생성
    db.conn.executemany(
        "INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (?, 5, ?, '2026-09-28T12:00:00+00:00')",
        [(101000011, 900_000_000), (250000009, 300_000_000), (300000009, 50_000_000)],
    )
    db.conn.commit()
    return TestClient(create_app(tmp_path / "db.sqlite"))


def test_index_and_static(client):
    res = client.get("/")
    assert res.status_code == 200 and "<title>FCLM</title>" in res.text and "/api/formation?" in res.text
    assert res.headers["cache-control"] == "no-cache"  # 수정한 화면이 바로 보이게
    assert client.get("/static/orb.js").headers["cache-control"] == "no-cache"


def test_meta(client):
    meta = client.get("/api/meta").json()
    assert meta["backend"] == "rules"
    assert set(meta) == {"backend", "daily_scope"}


def test_formations_and_errors(client):
    f = client.get("/api/formations", params={"team_color": "아스널"}).json()
    assert {x["formation"]: x["rankers"] for x in f["formations"]} == {"4-2-3-1": 3, "4-4-2": 1}
    bad = client.get("/api/formations", params={"team_color": "없는팀"})
    assert bad.status_code == 400 and "알 수 없는 팀컬러" in bad.json()["detail"]


def test_formation_overview(client):
    d = client.get("/api/formation", params={"name": "4231"}).json()
    assert d["formation"] == "4-2-3-1"
    # 테스트 DB는 필터 조회(아스널)만 있고 필터 없는 랭킹이 없음 → 랭킹 통계 없이 안내
    assert "note" in d
    assert client.get("/api/formation", params={"name": ""}).status_code == 422


def test_chat_rules(client):
    r = client.post("/api/chat", json={"message": "아스날 볼란치 1명 추천"}).json()
    assert r["tool_calls"] == ["recommend_players"] and "볼란치R" in r["answer"]
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 501}).status_code == 422


def test_database_is_opened_read_only(client):
    import sqlite3

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        client.app.state.conn.execute("CREATE TABLE x (a)")


def test_missing_db(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_app(tmp_path / "missing.sqlite")


def test_gemini_errors_are_shown_and_answered_by_rules(db, tmp_path, monkeypatch):  # noqa: F811
    from google.genai import errors

    import fco_meta.web.app as web_app

    class Broken:
        def ask(self, message):
            raise errors.ClientError(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})

    monkeypatch.setattr(web_app, "_gemini_chat", lambda toolbox, model: Broken())
    client = TestClient(create_app(tmp_path / "db.sqlite", backend="gemini"))
    r = client.post("/api/chat", json={"message": "아스날 볼란치 1명 추천"})
    assert r.status_code == 200
    body = r.json()
    assert "Gemini API 오류 404" in body["answer"] and "이 키로 쓸 수 없는 모델" in body["answer"]
    assert "볼란치R" in body["answer"]  # 규칙 기반 답변이 이어서 나옴
    assert body["error"].startswith("Gemini API 오류 404")


def test_gemini_answer_hides_evidence_and_fallback_model(db, tmp_path, monkeypatch):  # noqa: F811
    import fco_meta.web.app as web_app
    from fco_meta.chatbot.gemini import GeminiTurn

    class Bot:
        model, last_model = "gemini-a", "gemini-b"  # 대체 모델로 답한 경우

        def ask(self, message):
            return GeminiTurn("라이스가 1순위입니다.", [("recommend_players", {})], "STOP", ["아스널 DM — 랭커 3명"])

    monkeypatch.setattr(web_app, "_gemini_chat", lambda toolbox, model: Bot())
    client = TestClient(create_app(tmp_path / "db.sqlite", backend="gemini"))
    body = client.post("/api/chat", json={"message": "아스날 볼란치"}).json()
    assert body["answer"] == "라이스가 1순위입니다."
    assert body["model"] == "gemini-b"
