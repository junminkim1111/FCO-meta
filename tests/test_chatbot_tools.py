"""New chatbot tools: squad, value sort, player summary, meta trends, evidence lines."""

import json
import sqlite3

import pytest
from test_analytics import db  # noqa: F401  (fixture: 아스널 4명 랭커)
from test_chatbot import toolbox  # noqa: F401  (볼란치 카드 시세 포함)
from test_top_rankers import setup

from fco_meta.analytics import role_slots
from fco_meta.chatbot import RuleBot, Toolbox
from fco_meta.chatbot.tools import normalize_formation

OLDER = "2026-09-21T20:00:00+09:00"


def run(tb, name, args):
    content, is_error = tb.run(name, args)
    assert not is_error, content
    return json.loads(content)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("4231", "4-2-3-1"), ("4-2-3-1", "4-2-3-1"), ("442", "4-4-2"), ("4 4 2 (2)", "4-4-2(2)"), ("4422", "4-4-2(2)"),
     ("전체", None), (None, None), ("9-9", "9-9")],
)  # fmt: skip
def test_normalize_formation(text, expected):
    assert normalize_formation(text) == expected


def test_role_slots_from_matching_squads(db):  # noqa: F811
    # A·B만 실제 배치가 4-2-3-1 (D는 스냅샷만 4-2-3-1이고 4-4-2로 경기)
    assert role_slots(db.conn, "4-2-3-1") == ({"GK": 1, "CB": 2, "RB": 1, "LB": 1, "DM": 2, "CAM": 3, "ST": 1}, 2)
    assert role_slots(db.conn, "3-4-3") == ({}, 0)


def test_recommend_squad_fills_every_slot_once(toolbox):  # noqa: F811
    r = run(toolbox, "recommend_squad", {"team_color": "아스날", "formation": "4231"})
    assert r["formation"] == "4-2-3-1" and r["formation_source"] == "requested" and r["sample_size"] == 3
    assert [s["role"] for s in r["lineup"]] == ["GK", "CB", "CB", "RB", "LB", "DM", "DM", "CAM", "CAM", "CAM", "ST"]
    pids = [s["pid"] for s in r["lineup"]]
    assert len(set(pids)) == 11  # 같은 선수를 두 자리에 넣지 않음
    dms = [s for s in r["lineup"] if s["role"] == "DM"]
    assert [(s["player"], s["card"]["season"]) for s in dms] == [("볼란치R", "S250"), ("볼란치L", "ICON")]
    assert r["total_price"] == "12억" and len(r["unpriced_players"]) == 9  # 볼란치 카드만 시세 있음

    # 포메이션을 말하지 않으면 가장 많이 쓰인 포메이션
    assert run(toolbox, "recommend_squad", {"team_color": "아스널"})["formation_source"] == "most_used"


def test_recommend_squad_budget_swaps_cheapest_loss_first(toolbox):  # noqa: F811
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "formation": "4-2-3-1", "max_total_price_bp": 1_000_000_000})
    dms = [(s["player"], s["card"]["season"]) for s in r["lineup"] if s["role"] == "DM"]
    # 같은 선수(볼란치R)의 싼 시즌 카드로 바꾸면 사용률 손실 없이 2억 5,000만 절약
    assert dms == [("볼란치R", "S300"), ("볼란치L", "ICON")]
    assert r["budget"]["within_budget"] and r["total_price_bp"] == 950_000_000

    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "formation": "4-2-3-1", "max_total_price_bp": 500_000_000})
    assert not r["budget"]["within_budget"] and "한도를 넘음" in r["note"]  # 볼란치L은 더 싼 대안이 없음


def test_recommend_squad_without_matching_squads(toolbox):  # noqa: F811
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "formation": "3-4-3"})
    assert r["lineup"] == [] and "포지션 구성을 알 수 없음" in r["note"]


def test_recommend_players_price_sort(toolbox):  # noqa: F811
    toolbox.conn.execute("UPDATE card_price SET price = 10000000 WHERE spid = 101000011")
    toolbox.conn.commit()
    args = {"team_color": "아스널", "formation": "4-2-3-1", "role": "DM"}
    assert [p["name"] for p in run(toolbox, "recommend_players", args)["players"]] == ["볼란치R", "볼란치L"]
    r = run(toolbox, "recommend_players", {**args, "sort": "price"})
    assert [p["name"] for p in r["players"]] == ["볼란치L", "볼란치R"] and "가성비" in r["definitions"]["sort"]
    assert run(toolbox, "recommend_players", {**args, "sort": "price", "min_usage_rate": 0.9})["players"] == []
    assert toolbox.run("recommend_players", {**args, "sort": "cheap"})[1] is True


def test_player_detail_summary_in_scope(toolbox):  # noqa: F811
    r = run(toolbox, "get_player_detail", {"name": "볼란치R", "team_color": "아스널"})
    (p,) = r["players"]
    assert p["scope"] == {"team_color": "아스널", "formation": "전체", "sample_size": 4, "data_as_of": "2026-09-28T20:00:00+09:00"}
    (role,) = p["roles"]
    assert (role["role"], role["rank_in_role"], role["players_in_role"], role["usage_rate"]) == ("DM", 1, 2, 0.5)
    assert [c["price_at_most_used_grade"]["price"] for c in role["cards"]] == ["3억", "5,000만"]
    assert p["usage_history"] is None  # 스냅샷 1개
    assert r["usage"] and r["usage"][0]["team_color"] == "아스널"  # 웹 화면이 쓰는 기존 필드 유지

    # 범위 안에 데이터가 없으면 요약은 비지만 찾지 못했다고 하지는 않음
    r = run(toolbox, "get_player_detail", {"name": "볼란치R", "formation": "4-2-3-1"})
    assert r["players"] == [] and r["usage"]


def copy_snapshot(conn, as_of, older):
    """Duplicate every row of one snapshot under an older data_as_of."""
    conn.row_factory = sqlite3.Row
    for table in ("crawl_run", "ranker_snapshot", "ranker_team_color", "usage_sample", "usage_stats"):
        rows = conn.execute(f"SELECT * FROM {table} WHERE data_as_of = ?", (as_of,)).fetchall()
        cols = [c for c in rows[0].keys() if c != "id"]
        conn.executemany(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [[older if c == "data_as_of" else r[c] for c in cols] for r in rows],
        )
    conn.commit()


def test_meta_trends(tmp_path, fixture_html):
    storage, as_of, targets = setup(tmp_path, fixture_html, top=10)
    conn = storage.conn
    conn.execute("INSERT INTO meta_spid VALUES (101000011, '볼란치L'), (101000025, '스트라이커')")
    copy_snapshot(conn, as_of, OLDER)
    # 일주일 전에는 볼란치L을 아무도 쓰지 않았고, 1위 랭커의 포메이션이 달랐다
    conn.execute("DELETE FROM usage_stats WHERE data_as_of = ? AND pid = 11", (OLDER,))
    old_formation = conn.execute("SELECT formation FROM ranker_snapshot WHERE data_as_of = ? AND rank = 1", (OLDER,)).fetchone()[0]
    conn.execute("UPDATE ranker_snapshot SET formation = '5-3-2' WHERE data_as_of = ? AND rank = 1", (OLDER,))
    conn.commit()
    tb = Toolbox(conn, min_sample=1)

    r = run(tb, "get_meta_trends", {})
    assert (r["scope"], r["data_as_of"], r["compared_with"], r["ranking_scope"]) == ("전체 랭커", as_of, OLDER, 20)
    assert r["team_colors"] and all(t["share_change"] == 0 for t in r["team_colors"])  # 소속은 그대로 복사
    changed = {f["formation"]: f["share_change"] for f in r["formations"]}
    assert changed[old_formation] == pytest.approx(0.05)  # 20명 중 1명
    assert r["top_players"][0]["usage_rate"] > 0 and r["player_sample_size"] == 10
    (rise,) = r["rising"]
    assert (rise["player"], rise["main_role"], rise["previous_usage_rate"]) == ("볼란치L", "DM", 0.0)
    assert r["falling"] == []

    evidence = tb.evidence("get_meta_trends", r)
    assert evidence.startswith("전체 랭커 메타 — 랭킹 상위 20명") and "과 비교" in evidence

    # 스냅샷이 하나뿐이면 변화량 없이 안내
    conn.execute("DELETE FROM crawl_run WHERE data_as_of = ?", (OLDER,))
    r = run(tb, "get_meta_trends", {})
    assert r["compared_with"] is None and "rising" not in r and "이전 스냅샷이 없어" in r["note"]
    assert "share_change" not in r["formations"][0]


def test_player_detail_history_across_snapshots(tmp_path, fixture_html):
    storage, as_of, _ = setup(tmp_path, fixture_html, top=10)
    storage.conn.execute("INSERT INTO meta_spid VALUES (101000011, '볼란치L')")
    copy_snapshot(storage.conn, as_of, OLDER)
    storage.conn.execute("DELETE FROM usage_stats WHERE data_as_of = ? AND pid = 11", (OLDER,))
    r = run(Toolbox(storage.conn, min_sample=1), "get_player_detail", {"name": "볼란치L"})
    history = r["players"][0]["usage_history"]
    assert [(h["data_as_of"], h["rankers"]) for h in history][0] == (OLDER, 0) and history[-1]["rankers"] > 0


def test_evidence_lines(toolbox):  # noqa: F811
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "formation": "4-2-3-1"})
    assert toolbox.evidence("recommend_squad", r) == "아스널 4-2-3-1 스쿼드 — 랭커 3명 스쿼드 (2026-09-28 20:00 기준)"
    r = run(toolbox, "get_player_detail", {"name": "볼란치R", "team_color": "아스널"})
    assert toolbox.evidence("get_player_detail", r) == "볼란치R — 아스널 전체 포메이션 랭커 4명 스쿼드 (2026-09-28 20:00 기준)"
    assert toolbox.evidence("resolve_terms", {}) is None
    assert toolbox.evidence("recommend_players", {"players": [], "sample_size": 0}) is None


def test_unsupported_numbers_flags_made_up_figures():
    from fco_meta.chatbot.numbers import unsupported_numbers

    results = [{"players": [{"usage_rate": 0.5641, "rankers": 53, "price": "3억 2,000만", "change": -0.021}], "sample_size": 94}]
    answer = (
        "라이스 56.4% (94명 중 53명), 시세 3.2억, 2.1%p 하락. 2명 추천합니다.\n"
        "수비멘디 35.1%, 시세 7억, 12명\n"
        "[근거] 99.9% 무시되는 줄"
    )
    assert unsupported_numbers(answer, results, "볼란치 2명 추천") == ["35.1%", "7억", "12명"]


def test_evaluate_cli_rules(db, tmp_path, capsys):  # noqa: F811
    from fco_meta.chatbot.evaluate import main

    questions = tmp_path / "q.txt"
    questions.write_text("# 주석\n아스날 4-2-3-1 볼란치 1명 추천 || recommend_players\n아스널 포메이션\n", encoding="utf-8")
    out = tmp_path / "report.md"
    assert main(["--db", str(tmp_path / "db.sqlite"), "--backend", "rules", "--questions", str(questions), "--out", str(out)]) == 0
    report = out.read_text(encoding="utf-8")
    assert "## 1. 아스날 4-2-3-1 볼란치 1명 추천" in report and "도구 `list_formations`" in report
    assert "질문 2개" in report and "- 기대: recommend_players" in report

    assert main(["--db", str(tmp_path / "db.sqlite"), "--backend", "rules", "--questions", str(questions), "--out", str(out), "--only", "2"]) == 0
    report = out.read_text(encoding="utf-8")
    assert "## 2. 아스널 포메이션" in report and "## 1." not in report


def test_default_question_file_parses():
    from fco_meta.chatbot.evaluate import DEFAULT_QUESTIONS, load_questions

    questions = load_questions(DEFAULT_QUESTIONS)
    assert len(questions) >= 30 and all(q and expected for q, expected in questions)


def test_partial_team_color_name_resolves_when_unique(toolbox):  # noqa: F811
    r = run(toolbox, "resolve_terms", {"team_color": "롬바르디아"})
    assert r["team_color"]["name"] == "롬바르디아 FC"
    r = run(toolbox, "resolve_terms", {"team_color": "맨체스터"})  # 시티·유나이티드 둘 다 → 후보만
    assert r["team_color"] is None and r["team_color_candidates"]


def test_every_alias_points_to_a_real_team_color():
    from fco_meta.chatbot.tools import TEAM_COLOR_ALIASES
    from fco_meta.crawler.teamcolors import TeamColorCatalog

    catalog = TeamColorCatalog.load()
    missing = {alias: name for alias, name in TEAM_COLOR_ALIASES.items() if not catalog.find(name)}
    assert missing == {}


@pytest.mark.parametrize(
    ("alias", "name"),
    [("AC밀란", "밀라노 FC"), ("ac 밀란", "밀라노 FC"), ("레알", "레알 마드리드"), ("돌문", "보루시아 도르트문트"), ("PSG", "파리 생제르맹"), ("인테르", "롬바르디아 FC"),
     ("인터 마이애미", "인터 마이애미")],
)
def test_alias_resolution(toolbox, alias, name):  # noqa: F811
    assert run(toolbox, "resolve_terms", {"team_color": alias})["team_color"]["name"] == name


def add_salaries(conn):
    conn.executemany(
        "INSERT INTO card (spid, pid, season_id, name, salary, updated_at) VALUES (?, ?, ?, ?, ?, 'x')",
        [(250000009, 9, 250, "볼란치R", 20), (300000009, 9, 300, "볼란치R", 15), (101000011, 11, 101, "볼란치L", 30)],
    )
    conn.commit()


def test_recommend_players_role_group(toolbox):  # noqa: F811
    r = run(toolbox, "recommend_players", {"team_color": "아스널", "role": "측면미드필더"})
    assert (r["role"], r["positions"]) == ("RM+LM", [12, 16])
    assert [(p["pid"], p["rankers"]) for p in r["players"]] == [(12, 2), (16, 2)]  # C·D의 4-4-2
    assert RuleBot(toolbox).parse("아스널 윙어 추천").role == "RW+LW"


def test_recommend_players_salary(toolbox):  # noqa: F811
    add_salaries(toolbox.conn)
    args = {"team_color": "아스널", "formation": "4-2-3-1", "role": "DM"}
    r = run(toolbox, "recommend_players", {**args, "max_salary": 25})
    assert [(p["name"], [c["salary"] for c in p["cards"]]) for p in r["players"]] == [("볼란치R", [20, 15])]
    r = run(toolbox, "recommend_players", {**args, "sort": "salary"})
    assert [p["name"] for p in r["players"]] == ["볼란치R", "볼란치L"] and "급여" in r["definitions"]["sort"]


def test_recommend_squad_slots_with_salary_cap(toolbox):  # noqa: F811
    add_salaries(toolbox.conn)
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM", "max_total_salary": 45})
    assert r["formation_source"] == "slots" and r["layout"] == {"DM": 2}
    assert [(s["player"], s["card"]["season"], s["card"]["salary"]) for s in r["lineup"]] == [
        ("볼란치R", "S300", 15), ("볼란치L", "ICON", 30),
    ]  # fmt: skip
    assert r["total_salary"] == 45 and r["budget"]["within_budget"]

    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM", "max_total_salary": 40})
    assert not r["budget"]["within_budget"] and r["total_salary"] == 45  # 가장 가까운 조합
    assert toolbox.run("recommend_squad", {"slots": "윙어"})[1] is True  # 자리마다 역할 하나


def test_choose_finds_best_pair_within_limit():
    from fco_meta.chatbot.tools import _choose

    def opt(pid, usage, salary):
        return {"entry": {"pid": pid, "usage_rate": usage}, "price": None, "salary": salary}

    options = [opt(1, 0.6, 32), opt(2, 0.5, 30), opt(3, 0.3, 20), opt(4, 0.2, 15)]
    chosen = _choose([("DM", options), ("DM", options)], {"salary": 53})
    assert sorted(c["entry"]["pid"] for c in chosen) == [1, 3]  # 사용률 합 0.9, 급여 52
    assert [c["entry"]["pid"] for c in _choose([("DM", options), ("DM", options)], {})] == [1, 2]


def test_query_season_win_rate_and_costs(toolbox):  # noqa: F811
    add_salaries(toolbox.conn)
    # A·B·D = 스냅샷 4-2-3-1, C = 4-4-2
    for rank, (w, d, l) in {1: (60, 0, 40), 2: (50, 10, 40), 3: (30, 0, 70), 4: (10, 0, 10)}.items():
        toolbox.conn.execute("UPDATE ranker_snapshot SET wins = ?, draws = ?, losses = ? WHERE rank = ?", (w, d, l, rank))
    toolbox.conn.commit()
    r = run(toolbox, "query_squads", {"group_by": "formation", "sort_by": "season_win_rate", "min_rankers": 1})
    rows = {x["formation"]: x for x in r["rows"]}
    assert rows["4-2-3-1"]["season_win_rate"] == round(120 / 220, 4) and rows["4-2-3-1"]["season_games"] == 220
    assert [x["formation"] for x in r["rows"]] == ["4-2-3-1", "4-4-2"] and "season_win_rate" in r["definitions"]

    r = run(toolbox, "query_squads", {"group_by": "role", "sort_by": "avg_salary", "min_rankers": 1})
    dm = next(x for x in r["rows"] if x["role"] == "DM")
    assert r["rows"][0]["role"] == "DM" and dm["avg_salary"] == 23.75 and dm["salary_coverage"] == 1.0  # (20+30+15+30)/4
    assert dm["avg_price"] == "5억 3,750만"  # (3억 + 9억 + 5,000만 + 9억) / 4


def add_details(conn, fixture_html):
    """볼란치R S250(속력 124)·볼란치L ICON(속력 100, 키 175) 상세."""
    from dataclasses import replace

    from fco_meta.market.parser import parse_player_preview
    from fco_meta.market.storage import MarketStorage

    base = parse_player_preview(fixture_html("player_preview_854234378_1.html"), 250000009, 1)
    store = MarketStorage.__new__(MarketStorage)
    store.conn = conn
    store.save_card_detail(base)
    store.save_card_detail(replace(base, spid=101000011, height=175, stats={**base.stats, "속력": 100}))


def test_query_by_stat(toolbox, fixture_html):  # noqa: F811
    add_details(toolbox.conn, fixture_html)
    r = run(toolbox, "query_squads", {"group_by": "card", "role": "DM", "stat": "속 력", "sort_by": "stat", "min_rankers": 1})
    got = [(x["sp_id"], x["stat"], x["stat_coverage"]) for x in r["rows"]]
    assert got == [(250000009, 124.0, 1.0), (101000011, 100.0, 1.0), (300000009, None, 0.0)]  # 상세 없는 카드는 뒤로

    r = run(toolbox, "query_squads", {"group_by": "player", "role": "DM", "stat": "속력", "stat_min": 120})
    assert [(x["player"], x["rankers"]) for x in r["rows"]] == [("볼란치R", 1)]  # S250을 쓴 A만
    assert "속력 120 이상 카드만" in r["scope"]["description"]
    r = run(toolbox, "query_squads", {"group_by": "player", "role": "DM", "stat": "키", "sort_by": "stat", "min_rankers": 1})
    assert [x["stat"] for x in r["rows"]] == [188.0, 175.0]

    for args, message in [({"group_by": "player", "stat": "순발력"}, "알 수 없는 능력치"),
                          ({"group_by": "player", "sort_by": "stat"}, "stat(능력치 이름)이 필요")]:  # fmt: skip
        content, is_error = toolbox.run("query_squads", args)
        assert is_error and message in json.loads(content)["error"]


def test_card_profiles_in_detail(toolbox, fixture_html):  # noqa: F811
    add_details(toolbox.conn, fixture_html)
    r = run(toolbox, "get_player_detail", {"name": "볼란치R", "team_color": "아스널"})
    (profile,) = r["players"][0]["card_profiles"]  # S300은 상세 미수집
    assert profile["season"] == "S250" and profile["foot"] == "L3-R5" and profile["stats"]["속력"] == 124
    assert profile["traits"][0] == "커맨더" and profile["stats_grade"] == 1 and profile["height"] == 188


def add_match_stats(conn):
    """볼란치 카드들의 경기 기록(우리 랭커)과 TOP 10,000 랭커 스탯."""
    for sp_id, tackle, intercept in [(250000009, 4, 3), (300000009, 2, 1), (101000011, 1, 5)]:
        conn.execute(
            "UPDATE match_player SET stats = ? WHERE sp_id = ?",
            (json.dumps({"tackle": tackle, "intercept": intercept, "passTry": 40, "passSuccess": 36}), sp_id),
        )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ranker_stats (sp_id INTEGER, sp_position INTEGER, match_count INTEGER, stats TEXT,"
        " created_at TEXT, fetched_at TEXT, PRIMARY KEY (sp_id, sp_position))"
    )
    conn.executemany(
        "INSERT INTO ranker_stats VALUES (?, ?, ?, ?, 'x', 'x')",
        [(250000009, 9, 100, json.dumps({"tackle": 3.0, "passTry": 50, "passSuccess": 45})),
         (300000009, 9, 300, json.dumps({"tackle": 1.0, "passTry": 50, "passSuccess": 40})),
         (101000011, 11, 50, json.dumps({"tackle": 2.5, "passTry": 20, "passSuccess": 19})),
         (101000011, 18, 999, json.dumps({"tackle": 9.9}))],  # CAM 자리 기록 — DM 역할에는 안 들어감
    )  # fmt: skip
    conn.commit()


def test_query_match_stats_both_sources(toolbox):  # noqa: F811
    add_match_stats(toolbox.conn)
    base = {"group_by": "player", "role": "DM", "sort_by": "match_stat", "min_rankers": 1}
    r = run(toolbox, "query_squads", {**base, "match_stat": "tackle"})
    rows = {x["player"]: x for x in r["rows"]}
    assert (rows["볼란치R"]["match_stat"], rows["볼란치R"]["match_stat_matches"]) == (2.0, 400)  # (3.0 + 1.0) / 2 출전
    assert (rows["볼란치L"]["match_stat"], rows["볼란치L"]["match_stat_matches"]) == (2.5, 50)
    assert [x["player"] for x in r["rows"]] == ["볼란치L", "볼란치R"]

    r = run(toolbox, "query_squads", {**base, "match_stat": "intercept", "match_stat_source": "rankers"})
    assert [(x["player"], x["match_stat"]) for x in r["rows"]] == [("볼란치L", 5.0), ("볼란치R", 2.0)]
    r = run(toolbox, "query_squads", {**base, "match_stat": "pass_success_rate", "match_stat_source": "rankers"})
    assert r["rows"][0]["match_stat"] == 0.9  # 성공/시도 합

    for args, message in [({**base, "match_stat": "intercept"}, "match_stat_source='rankers'"),
                          ({**base, "match_stat": None}, "match_stat이 필요"),
                          ({**base, "match_stat": "slide"}, "알 수 없는 match_stat")]:  # fmt: skip
        content, is_error = toolbox.run("query_squads", {k: v for k, v in args.items() if v is not None})
        assert is_error and message in json.loads(content)["error"], args


def test_player_detail_top10000_stats(toolbox):  # noqa: F811
    add_match_stats(toolbox.conn)
    r = run(toolbox, "get_player_detail", {"name": "볼란치R", "team_color": "아스널"})
    stats = r["players"][0]["roles"][0]["top10000_stats"]
    # 경기 수 가중: 태클 (3.0×100 + 1.0×300) / 400, 패스 성공률 (45×100 + 40×300) / (50×400)
    assert stats["matches"] == 400 and stats["tackle"] == 1.5 and stats["pass_success_rate"] == 0.825
    assert "top10000_stats" in r["definitions"]
