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
    assert (top["squad_value"], top["unpriced"]) == (None, 11)  # 시세 전

    # 구단가치 = 선발 중 시세를 아는 선수(카드·강화)의 최신 시세 합
    a, b = top["players"][:2]
    storage.conn.executemany(
        "INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (?, ?, ?, ?)",
        [(a["sp_id"], a["grade"], 1, "2026-09-27"), (a["sp_id"], a["grade"], 100_000_000, "2026-09-28"),
         (b["sp_id"], b["grade"], 50_000_000, "2026-09-28"), (b["sp_id"], b["grade"] + 1, 9, "2026-09-28")],
    )  # fmt: skip
    content, _ = Toolbox(storage.conn).run("get_formation_overview", {"formation": "4231"})
    top = json.loads(content)["top_ranker_squad"]
    assert (top["squad_value"], top["unpriced"]) == ("1억 5,000만", 9)

    best = d["best_eleven"]
    squads = sum(t.formation == "4-2-3-1" for t in targets)
    assert best["squads"] == squads and len(best["players"]) == 11
    st = next(p for p in best["players"] if p["position"] == 25)
    assert (st["name"], st["rankers"]) == ("스트라이커", squads)
    assert all("grade" not in p for p in best["players"])  # 베스트 11은 강화와 상관없이 선수·카드로만 센다

    # 모든 테스트 경기: 랭커 4-2-3-1(승) vs 상대 4-4-2(패)
    (vs,) = d["matchups"]["opponents"]
    assert (vs["opponent"], vs["games"], vs["wins"], vs["losses"]) == ("4-4-2", squads, squads, 0)
    content, _ = Toolbox(storage.conn).run("get_formation_overview", {"formation": "4-4-2"})
    losses = next(o for o in json.loads(content)["matchups"]["opponents"] if o["opponent"] == "4-2-3-1")
    assert losses["losses"] >= squads  # 같은 경기를 상대 쪽에서 본 결과


def test_team_color_overview_uses_that_team_colors_rankers(tmp_path, fixture_html):
    storage, as_of, targets = setup(tmp_path, fixture_html, top=10)
    tb = Toolbox(storage.conn)
    marks = ",".join(str(t.rank) for t in targets)  # 스쿼드를 받은 랭커가 가장 많은 팀컬러
    (tc_id,) = storage.conn.execute(
        f"SELECT team_color_id FROM ranker_team_color WHERE data_as_of = ? AND rank IN ({marks}) GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1",
        (as_of,),
    ).fetchone()
    (n,) = storage.conn.execute("SELECT COUNT(*) FROM ranker_team_color WHERE data_as_of = ? AND team_color_id = ?", (as_of, tc_id)).fetchone()
    name = tb._scope_name(tc_id)
    d = json.loads(tb.run("get_team_color_overview", {"team_color": name})[0])
    assert d["team_color"] == name and d["ranking"]["rankers"] == n and d["ranking"]["usage_rank"] >= 1
    assert d["formations"] and d["rank_bands"][0]["rank_band"].startswith("1~") and d["all_rankers_win_rate"] is not None

    members = {r for (r,) in storage.conn.execute("SELECT rank FROM ranker_team_color WHERE data_as_of = ? AND team_color_id = ?", (as_of, tc_id))}
    with_squads = members & {t.rank for t in targets}
    top = d["top_ranker_squad"]  # 이 팀컬러에서 가장 높은 순위 (포메이션과 무관)
    assert top["rank"] == min(with_squads) and len(top["players"]) == 11
    assert d["best_eleven"]["squads"] == len(with_squads) and len(d["best_eleven"]["players"]) == 11

    _, is_error = tb.run("get_team_color_overview", {"team_color": "전체 랭커"})
    assert is_error  # 팀컬러를 골라야 한다
