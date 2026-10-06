"""query_rankers: ranking-page aggregates over the whole crawled ranking (no squads)."""

import json

from test_top_rankers import setup

from fco_meta.chatbot import Toolbox


def run(tb, args):
    content, is_error = tb.run("query_rankers", args)
    assert not is_error, content
    return json.loads(content)


def test_formation_distribution_and_win_rate(tmp_path, fixture_html):
    storage, as_of, _ = setup(tmp_path, fixture_html, top=5)  # 랭킹 20명, 스쿼드 5명 — 랭커 조회는 20명 전부
    tb = Toolbox(storage.conn)
    r = run(tb, {"group_by": "formation"})
    assert r["rankers"] == 20 and sum(x["rankers"] for x in r["rows"]) == 20
    assert r["scope"]["description"] == "랭킹 상위 20명"
    wins, games = storage.conn.execute(
        "SELECT SUM(wins), SUM(wins + draws + losses) FROM ranker_snapshot WHERE formation = ?", (r["rows"][0]["formation"],)
    ).fetchone()
    assert r["rows"][0]["season_win_rate"] == round(wins / games, 4) and r["rows"][0]["season_games"] == games
    assert r["rows"][0]["avg_squad_value"].endswith("억") or "만" in r["rows"][0]["avg_squad_value"]

    # 승률 정렬은 기본 최소 30명 → 20명 규모에서는 비고, min_rankers로 낮출 수 있다
    assert run(tb, {"group_by": "formation", "sort_by": "season_win_rate"})["rows"] == []
    rates = [x["season_win_rate"] for x in run(tb, {"group_by": "formation", "sort_by": "season_win_rate", "min_rankers": 1})["rows"]]
    assert rates == sorted(rates, reverse=True)


def test_rank_bands_filters_and_team_colors(tmp_path, fixture_html):
    storage, _, _ = setup(tmp_path, fixture_html, top=5)
    tb = Toolbox(storage.conn)
    r = run(tb, {"group_by": "rank_band", "band": 10})
    assert [(x["rank_band"], x["rankers"]) for x in r["rows"]] == [("1~10위", 10), ("11~20위", 10)]
    assert [x["share_of_band"] for x in r["rows"]] == [1.0, 1.0] and "share_of_band" in r["definitions"]
    # 포메이션 하나: share는 그 포메이션 랭커 중 구간 비율, share_of_band는 구간 랭커 중 그 포메이션 비율
    top = run(tb, {"group_by": "formation"})["rows"][0]
    r = run(tb, {"group_by": "rank_band", "band": 10, "formation": top["formation"], "rank_min": 6})
    first = r["rows"][0]
    assert first["share_of_band"] == round(first["rankers"] / 5, 4)  # 6~10위 다섯 명 중
    r = run(tb, {"group_by": "formation", "rank_min": 11, "rank_max": 20})
    assert r["rankers"] == 10 and "순위 11~20위" in r["scope"]["description"]

    r = run(tb, {"group_by": "team_color", "limit": 30})
    assert r["rows"] and "표시된 팀컬러" in r["note"]
    assert tb.evidence("query_rankers", r).startswith("랭킹 상위 20명 — 랭커 20명, 랭킹 페이지 기준")

    content, is_error = tb.run("query_rankers", {"group_by": "player"})
    assert is_error and "group_by는" in json.loads(content)["error"]
