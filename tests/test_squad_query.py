"""query_squads: ad-hoc grouping of the base squads (범용 조회)."""

import json

import pytest
from test_analytics import db  # noqa: F401  (A·B 4-2-3-1, C 4-4-2, D 스냅샷 4-2-3-1·경기 4-4-2)
from test_chatbot import toolbox  # noqa: F401  (볼란치 카드 시세: S250 3억, S300 5,000만, ICON 9억)

from fco_meta.analytics import SquadQuery, query_squads


def run(tb, args):
    content, is_error = tb.run("query_squads", args)
    assert not is_error, content
    return json.loads(content)


def test_group_players_in_role(db):  # noqa: F811
    res = query_squads(db.conn, SquadQuery(roles=("DM",)), "player")
    assert res["squads"] == 4 and res["covered"] == 0  # 필터 없는 수집이 없으면 수집된 스쿼드 전체
    assert [(r["key"], r["rankers"], r["usage_rate"]) for r in res["rows"]] == [(9, 2, 0.5), (11, 2, 0.5)]
    assert res["rows"][0]["win_rate"] == 0.5 and res["rows"][0]["avg_elo"] == 3550.0  # A 승, B 패


def test_filters_rank_elo_formation(db):  # noqa: F811
    assert query_squads(db.conn, SquadQuery(rank_max=1), "role")["squads"] == 1
    assert query_squads(db.conn, SquadQuery(rank_min=2, elo_min=3400), "role")["squads"] == 2  # B, C
    assert query_squads(db.conn, SquadQuery(formation="4-2-3-1", strict=True), "role")["squads"] == 2  # D는 배치 불일치
    assert query_squads(db.conn, SquadQuery(team_color_id=9999), "role")["rows"] == []


def test_with_player_finds_partners_and_excludes_self(db):  # noqa: F811
    res = query_squads(db.conn, SquadQuery(with_pid=11, roles=("DM",)), "player")
    # 볼란치L(pid 11)을 쓴 A·B의 다른 볼란치 = 볼란치R, 자신은 빠짐
    assert res["squads"] == 2 and [(r["key"], r["usage_rate"]) for r in res["rows"]] == [(9, 1.0)]


def test_ranker_level_groups(db):  # noqa: F811
    by_formation = {r["key"]: r for r in query_squads(db.conn, SquadQuery(), "formation")["rows"]}
    assert by_formation["4-2-3-1"]["rankers"] == 3 and by_formation["4-2-3-1"]["win_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert by_formation["4-4-2"]["rankers"] == 1
    (tc,) = query_squads(db.conn, SquadQuery(), "team_color")["rows"]
    assert (tc["key"], tc["usage_rate"]) == (1004, 1.0)
    (grade,) = query_squads(db.conn, SquadQuery(roles=("ST",)), "grade")["rows"]
    assert (grade["key"], grade["rankers"]) == (5, 4)


def test_metric_sort_respects_min_rankers(db):  # noqa: F811
    db.conn.execute("UPDATE match_player SET sp_rating = 9.0 WHERE pid = 11 AND starter = 1")
    res = query_squads(db.conn, SquadQuery(roles=("DM",)), "player", sort_by="avg_rating")
    assert [r["key"] for r in res["rows"]] == [11, 9]
    res = query_squads(db.conn, SquadQuery(roles=("DM",)), "player", sort_by="avg_rating", min_rankers=3)
    assert res["rows"] == [] and res["total_groups"] == 2


def test_invalid_arguments(db):  # noqa: F811
    with pytest.raises(ValueError):
        query_squads(db.conn, SquadQuery(), "nation")
    with pytest.raises(ValueError):
        query_squads(db.conn, SquadQuery(), "grade", sort_by="price")


def test_tool_price_sort_and_labels(toolbox):  # noqa: F811
    r = run(toolbox, {"group_by": "card", "role": "볼란치", "sort_by": "price"})
    assert r["min_rankers"] == 3 and r["rows"] == []  # 가격 정렬도 기본 최소 표본 3명 (각 카드 1~2명)
    r = run(toolbox, {"group_by": "card", "role": "볼란치", "sort_by": "price", "min_rankers": 1})
    assert [(x["player"], x["most_used_card"]["season"], x["most_used_card"]["price"]) for x in r["rows"]] == [
        ("볼란치R", "S300", "5,000만"), ("볼란치R", "S250", "3억"), ("볼란치L", "ICON", "9억"),
    ]  # fmt: skip


def test_tool_defaults_and_notes(toolbox):  # noqa: F811
    r = run(toolbox, {"group_by": "player", "role": "DM", "sort_by": "avg_rating"})
    assert r["min_rankers"] == 3 and r["rows"] == [] and "3명 미만 항목은 뺌 (전체 2개 항목)" in r["note"]

    r = run(toolbox, {"group_by": "player", "role": "DM", "with_player": "볼란치L", "rank_max": 2})
    assert r["scope"]["description"] == "수집된 전체 랭커 · 순위 1~2위 · 볼란치L을(를) 선발로 쓴 랭커만 · DM 자리만"
    assert [(x["player"], x["usage_rate"]) for x in r["rows"]] == [("볼란치R", 1.0)]
    assert "기준 선수(볼란치L) 자신은 결과에서 뺌" in r["note"] and "표본 2개로 작아 참고용" in r["note"]
    assert toolbox.evidence("query_squads", r) == (
        "수집된 전체 랭커 · 순위 1~2위 · 볼란치L을(를) 선발로 쓴 랭커만 · DM 자리만 — 스쿼드 2개 (2026-09-28 20:00 기준)"
    )

    r = run(toolbox, {"group_by": "team_color", "team_color": "아스날"})
    assert r["rows"][0]["team_color"] == "아스널" and "100%를 넘을 수 있음" in r["note"]


def test_tool_errors(toolbox):  # noqa: F811
    for args, message in [
        ({"group_by": "nation"}, "group_by는"),
        ({"group_by": "grade", "sort_by": "price"}, "player 또는 card"),
        ({"group_by": "player", "with_player": "없는선수"}, "찾지 못함"),
        ({"group_by": "player", "date": "2020-01-01"}, "수집된 스쿼드가 없음"),
        ({"group_by": "player", "role": "리베로"}, "알 수 없는 역할"),
    ]:
        content, is_error = toolbox.run("query_squads", args)
        assert is_error and message in json.loads(content)["error"], args
