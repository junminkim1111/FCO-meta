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
    assert res.status_code == 200 and "FCO 랭커 메타" in res.text and "/api/recommend" in res.text


def test_meta(client):
    meta = client.get("/api/meta").json()
    assert meta["backend"] == "rules"
    assert {(c["team_color"], c["formation"]) for c in meta["combos"]} >= {("아스널", "4-2-3-1"), ("아스널", "4-4-2")}
    assert {"code": "DM", "label": "볼란치(DM)", "positions": [9, 10, 11]} in meta["roles"]


def test_recommend(client):
    r = client.get("/api/recommend", params={"team_color": "아스날", "formation": "4-2-3-1", "role": "볼란치", "top_n": 2}).json()
    # 표본 3명 < 10명 → 전체 포메이션 폴백
    assert r["fallback_to_all_formations"] and r["sample_size"] == 4
    assert [p["name"] for p in r["players"]] == ["볼란치R", "볼란치L"]


def test_recommend_budget_and_errors(client):
    r = client.get("/api/recommend", params={"team_color": "아스널", "role": "DM", "max_price": "1억"}).json()
    assert r["budget"]["max_price"] == "1억"
    # 1억 이하 = S300(5,000만)만 → 볼란치R만 남고 볼란치L(9억)은 빠짐
    assert [(p["name"], [c["price_at_most_used_grade"]["price"] for c in p["cards"]]) for p in r["players"]] == [
        ("볼란치R", ["5,000만"])
    ]
    assert client.get("/api/recommend", params={"team_color": "아스널", "role": "DM", "max_price": "많이"}).status_code == 400
    bad = client.get("/api/recommend", params={"team_color": "없는팀", "role": "DM"})
    assert bad.status_code == 400 and "알 수 없는 팀컬러" in bad.json()["detail"]
    assert client.get("/api/recommend", params={"team_color": "아스널", "role": "DM", "top_n": 99}).status_code == 422


def test_formations_and_player(client):
    f = client.get("/api/formations", params={"team_color": "아스널"}).json()
    assert {x["formation"]: x["rankers"] for x in f["formations"]} == {"4-2-3-1": 3, "4-4-2": 1}
    p = client.get("/api/player", params={"name": "볼란치R"}).json()
    assert p["matched_names"] == ["볼란치R"] and p["usage"][0]["role"] == "DM"


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
