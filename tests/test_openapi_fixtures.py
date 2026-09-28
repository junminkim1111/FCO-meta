"""Real Open API responses (nicknames, ouids and matchIds anonymized) through the client and pipeline."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from fco_meta.openapi import CallBudget, NexonOpenApiClient
from fco_meta.pipeline import PipelineStore, SquadCollector, line_shape, select_targets
from fco_meta.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures" / "openapi"
RANKER = "0" * 31 + "1"
OPPONENT = "0" * 31 + "2"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def handler(request):
    path = request.url.path
    if path == "/fconline/v1/id":
        return httpx.Response(200, json=load("id.json"))
    if path == "/fconline/v1/user/match":
        return httpx.Response(200, json=load("user_match.json"))
    if path == "/fconline/v1/match-detail" and request.url.params["matchid"] == load("user_match.json")[0]:
        return httpx.Response(200, json=load("match_detail.json"))
    raise AssertionError(f"unexpected request {request.url}")


def client(budget=None):
    http = httpx.Client(base_url="https://open.api.nexon.com", transport=httpx.MockTransport(handler))
    return NexonOpenApiClient("k", http, budget=budget, sleep=lambda s: None)


def test_client_parses_real_responses():
    api = client()
    assert api.get_ouid("랭커1") == RANKER
    ids = api.user_matches(RANKER, limit=5)
    assert len(ids) == 5 and all(len(i) == 24 for i in ids)
    detail = api.match_detail(ids[0])
    assert detail["matchType"] == 50
    assert {i["ouid"] for i in detail["matchInfo"]} == {RANKER, OPPONENT}
    for info in detail["matchInfo"]:
        assert len(info["player"]) == 18
        assert sum(p["spPosition"] != 28 for p in info["player"]) == 11


def test_pipeline_stores_real_match(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    storage.conn.execute("INSERT INTO crawl_run (id, started_at, mode, query_json) VALUES (1, 'x', '1vs1', '{}')")
    storage.conn.execute(
        "INSERT INTO ranker_snapshot (data_as_of, mode, rank, run_id, nickname, formation)"
        " VALUES ('2026-09-28T21:00:00+09:00', '1vs1', 1, 1, '랭커1', '4-2-3-1')"
    )
    storage.conn.execute(
        "INSERT INTO ranker_team_color VALUES ('2026-09-28T21:00:00+09:00', '1vs1', 1, 1004, 1)"
    )
    store = PipelineStore(storage.conn)
    api = client(CallBudget(storage.conn))
    later = datetime(2026, 9, 29, tzinfo=timezone.utc)

    result = SquadCollector(api, store, now=later).run(select_targets(storage.conn, 1004, "4-2-3-1"))

    assert result.statuses == {"ok": 1} and result.api_calls == 3
    squad = storage.conn.execute("SELECT match_order, ouid, inferred_formation, formation_match FROM ranker_squad").fetchall()
    assert [tuple(r) for r in squad] == [(0, RANKER, "4-2-3-1", 1)]

    match = storage.conn.execute("SELECT match_date, match_type FROM match").fetchone()
    assert tuple(match) == ("2026-09-26T10:12:40+00:00", 50)
    results = dict(storage.conn.execute("SELECT ouid, match_result FROM match_team").fetchall())
    assert results == {RANKER: "승", OPPONENT: "패"}

    players = storage.conn.execute("SELECT * FROM match_player WHERE ouid = ?", (RANKER,)).fetchall()
    assert len(players) == 18 and sum(p["starter"] for p in players) == 11
    for p in players:
        assert p["season_id"] == p["sp_id"] // 1_000_000 and 100 <= p["season_id"] <= 999
        assert p["pid"] == p["sp_id"] % 1_000_000
        assert p["sp_grade"] >= 1
    assert sorted(p["sp_position"] for p in players if p["sp_position"] in (9, 10, 11)) == [9, 11]

    opp = [p["sp_position"] for p in storage.conn.execute("SELECT sp_position FROM match_player WHERE ouid = ?", (OPPONENT,))]
    assert line_shape(opp) == "4-2-1-3"
    assert storage.conn.execute("SELECT COUNT(*) FROM account").fetchone()[0] == 1  # 상대 닉네임은 저장하지 않음
