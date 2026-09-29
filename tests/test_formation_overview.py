"""get_formation_overview: what the web page shows when a formation bar is clicked."""

import json

from test_top_rankers import setup

from fco_meta.chatbot import Toolbox


def test_overview_ranking_squads_and_matchups(tmp_path, fixture_html):
    storage, as_of, targets = setup(tmp_path, fixture_html, top=10)
    storage.conn.execute("INSERT INTO meta_spid VALUES (101000025, '스트라이커')")
    storage.conn.commit()
    content, is_error = Toolbox(storage.conn).run("get_formation_overview", {"formation": "4231"})
    d = json.loads(content)
    assert not is_error and d["formation"] == "4-2-3-1"

    n4231 = storage.conn.execute("SELECT COUNT(*) FROM ranker_snapshot WHERE formation = '4-2-3-1'").fetchone()[0]
    r = d["ranking"]
    assert r["rankers"] == n4231 and r["share"] == round(n4231 / 20, 4) and r["best_rank"] >= 1
    assert d["team_colors"] and d["rank_bands"][0]["rank_band"].startswith("1~")

    top = d["top_ranker_squad"]  # 스쿼드를 받은 상위 10명 중 4-2-3-1을 쓰는 가장 높은 순위
    first = min(t.rank for t in targets if t.formation == "4-2-3-1")
    assert top["rank"] == first and top["formation_match"] and len(top["players"]) == 11
    assert {p["position"] for p in top["players"]} == {0, 3, 4, 6, 7, 9, 11, 17, 18, 19, 25}

    best = d["best_eleven"]
    squads = sum(t.formation == "4-2-3-1" for t in targets)
    assert best["squads"] == squads and len(best["players"]) == 11
    st = next(p for p in best["players"] if p["position"] == 25)
    assert (st["name"], st["rankers"]) == ("스트라이커", squads)

    # 모든 테스트 경기: 랭커 4-2-3-1(승) vs 상대 4-4-2(패)
    (vs,) = d["matchups"]["opponents"]
    assert (vs["opponent"], vs["games"], vs["wins"], vs["losses"]) == ("4-4-2", squads, squads, 0)
    content, _ = Toolbox(storage.conn).run("get_formation_overview", {"formation": "4-4-2"})
    losses = next(o for o in json.loads(content)["matchups"]["opponents"] if o["opponent"] == "4-2-3-1")
    assert losses["losses"] >= squads  # 같은 경기를 상대 쪽에서 본 결과
