"""New chatbot tools: squad, value sort, player summary, meta trends, evidence lines."""

import json
import sqlite3

import pytest
from test_analytics import db  # noqa: F401  (fixture: 아스널 4명 랭커)
from test_chatbot import toolbox  # noqa: F401  (볼란치 카드 시세 포함)
from test_top_rankers import setup

from fco_meta.analytics import role_slots
from fco_meta.chatbot import RuleBot, Toolbox
from fco_meta.chatbot.tools import _choose, normalize_formation

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
    assert role_slots(db.conn, "4-2-3-1") == ({"GK": 1, "CB": 2, "RB": 1, "LB": 1, "DM": 2, "CAM": 1, "RAM": 1, "LAM": 1, "ST": 1}, 2)
    assert role_slots(db.conn, "3-4-3") == ({}, 0)


def test_recommend_squad_fills_every_slot_once(toolbox):  # noqa: F811
    r = run(toolbox, "recommend_squad", {"team_color": "아스날", "formation": "4231"})
    assert r["formation"] == "4-2-3-1" and r["formation_source"] == "requested" and r["sample_size"] == 3
    assert [s["role"] for s in r["lineup"]] == ["GK", "CB", "CB", "RB", "LB", "DM", "DM", "CAM", "RAM", "LAM", "ST"]
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
    assert [p["season"] for p in run(toolbox, "recommend_players", args)["players"]] == ["ICON", "S250", "S300"]
    r = run(toolbox, "recommend_players", {**args, "sort": "price"})  # 싼 순: ICON 1,000만 < S300 5,000만 < S250 3억
    assert [p["season"] for p in r["players"]] == ["ICON", "S300", "S250"] and "가성비" in r["definitions"]["sort"]
    assert [p["season"] for p in run(toolbox, "recommend_players", {**args, "sort": "price", "min_usage_rate": 0.5})["players"]] == ["ICON"]
    assert run(toolbox, "recommend_players", {**args, "sort": "price", "min_usage_rate": 0.9})["players"] == []
    assert toolbox.run("recommend_players", {**args, "sort": "cheap"})[1] is True


def test_recommend_players_lowers_the_grade_to_fit_the_budget(toolbox):  # noqa: F811
    # 볼란치L ICON: 랭커들은 11강(30억)을 쓴다. 10억이면 같은 카드를 예산 안의 가장 높은 강화로 (9강 8억), 8강 아래로는 안 내린다
    toolbox.conn.execute("""UPDATE usage_stats SET grade_dist = '{"11": 2}' WHERE sp_id = 101000011""")
    toolbox.conn.executemany(
        "INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (101000011, ?, ?, '2026-09-28T12:00:00+00:00')",
        [(7, 200_000_000), (8, 400_000_000), (9, 800_000_000), (10, 1_500_000_000), (11, 3_000_000_000)],
    )
    toolbox.conn.commit()
    args = {"team_color": "아스널", "formation": "4-2-3-1", "role": "DM"}
    top = run(toolbox, "recommend_players", {**args, "max_price_bp": 1_000_000_000})["players"][0]
    card = top["cards"][0]
    assert (top["season"], card["most_used_grade"], card["ranker_grade"], card["price_at_most_used_grade"]["price"]) == ("ICON", 9, 11, "8억")
    # 3억: 8강(4억)도 넘으면 7강으로 내리지 않고 그 카드는 빠진다
    assert "ICON" not in [p["season"] for p in run(toolbox, "recommend_players", {**args, "max_price_bp": 300_000_000})["players"]]
    assert run(toolbox, "recommend_players", {**args, "max_price_bp": "10억"})["players"][0]["cards"][0]["most_used_grade"] == 9  # 금액 표기 그대로
    # 예산이 없으면 랭커들이 쓰는 11강 그대로
    card = run(toolbox, "recommend_players", args)["players"][0]["cards"][0]
    assert card["most_used_grade"] == 11 and "ranker_grade" not in card


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

    # 이전 스냅샷의 스쿼드 표본이 지금의 절반도 안 되면 선수 변화는 비교하지 않는다 (모두 급상승으로 보이므로)
    conn.execute("UPDATE usage_sample SET squads = 1 WHERE data_as_of = ? AND formation = '*'", (OLDER,))
    conn.commit()
    r = run(tb, "get_meta_trends", {})
    assert "rising" not in r and "1명만 집계" in r["note"] and r["formations"][0].get("share_change") is not None

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

    # 쉼표 있는 수를 잘라 읽지 않는다 ("9,996명" ≠ "996명"), 이미 %인 값을 반올림한 것도 결과에 있는 값
    results = [{"squads": 9996, "rows": [{"rankers": 1718, "pass_success_rate": 94.88}]}]
    assert unsupported_numbers("9,996명 중 1,718명, 패스 성공률 94.9%", results) == []
    assert unsupported_numbers("9,996명 중 2,718명", results) == ["2,718명"]
    assert unsupported_numbers("9,996명 중 2,718명\n\n**이어서 무어볼 만한 질분**\n- 100억으로 다시 짜줘", results) == ["2,718명"]  # 제안 질문의 금액은 빼고

    # 편의상 반올림한 값은 통과 (쓴 자리 한 단위까지), 결과에 없는 합계·새 비율은 그대로 걸린다
    results = [{"total_price": "81억 6,600만", "price": "3억 6,800만", "usage_rate": 0.328}]
    assert unsupported_numbers("총액 약 81억, 시세 3.7억, 사용률 33%", results) == []
    assert unsupported_numbers("총액 81.7억, 사용률 32.8%", results) == []
    assert unsupported_numbers("두 선수 합 85억 4,600만, 사용률 41%, 시세 3.5억", results) == ["41%", "85억 4,600만", "3.5억"]


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
    assert [(p["pid"], p["rankers"]) for p in r["players"]] == [(101000012, 2), (101000016, 2)]  # C·D의 4-4-2 (pid = 그 시즌 카드)
    assert RuleBot(toolbox).parse("아스널 윙어 추천").role == "RW+LW+RM+LM+RAM+LAM"  # 측면 미드·공미도 윙어


def test_recommend_players_salary(toolbox):  # noqa: F811
    add_salaries(toolbox.conn)
    args = {"team_color": "아스널", "formation": "4-2-3-1", "role": "DM"}
    r = run(toolbox, "recommend_players", {**args, "max_salary": 25})
    assert [(p["name"], [c["salary"] for c in p["cards"]]) for p in r["players"]] == [("볼란치R", [20]), ("볼란치R", [15])]
    r = run(toolbox, "recommend_players", {**args, "sort": "salary"})
    assert [p["season"] for p in r["players"]] == ["S300", "S250", "ICON"] and "급여" in r["definitions"]["sort"]


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


def test_full_squad_keeps_the_game_salary_cap(toolbox):  # noqa: F811
    # 이 픽스처의 아스널 4-2-3-1은 볼란치 두 자리만 채워진다. 가장 많이 쓴 카드로는 200 + 150 = 350 > 315,
    # 볼란치R은 급여 100짜리 다른 시즌 카드도 쓰였다
    toolbox.conn.executemany(
        "INSERT INTO card (spid, pid, season_id, name, salary, updated_at) VALUES (?, ?, ?, ?, ?, 'x')",
        [(250000009, 9, 250, "볼란치R", 200), (300000009, 9, 300, "볼란치R", 100), (101000011, 11, 101, "볼란치L", 150)],
    )
    toolbox.conn.commit()
    args = {"team_color": "아스널", "formation": "4-2-3-1"}
    r = run(toolbox, "recommend_squad", args)  # 급여를 말하지 않아도 게임 상한 315
    assert r["total_salary"] == 250 and r["budget"]["within_budget"] and r["budget"]["salary_cap"] == 315
    assert [(s["player"], s["card"]["season"]) for s in r["lineup"] if s["player"]] == [("볼란치R", "S300"), ("볼란치L", "ICON")]
    # 사용자가 급여 합을 더 크게 말하면 그 값을 따르고, 게임 상한을 넘으면 경고한다
    r = run(toolbox, "recommend_squad", {**args, "max_total_salary": 400})
    assert r["budget"]["max_total_salary"] == 400 and r["total_salary"] == 350 and "315 초과" in r["warnings"][0]
    # 일부 자리만 고를 때는 팀 전체 상한이 아니므로 걸지 않는다
    assert "budget" not in run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM"})


def test_choose_finds_best_pair_within_limit():
    from fco_meta.chatbot.tools import _choose

    def opt(pid, usage, salary):
        return {"entry": {"pid": pid, "usage_rate": usage}, "price": None, "salary": salary}

    options = [opt(1, 0.6, 32), opt(2, 0.5, 30), opt(3, 0.3, 20), opt(4, 0.2, 15)]
    chosen = _choose([("DM", options), ("DM", options)], {"salary": 53})
    assert sorted(c["entry"]["pid"] for c in chosen) == [1, 3]  # 사용률 합 0.9, 급여 52
    assert [c["entry"]["pid"] for c in _choose([("DM", options), ("DM", options)], {})] == [1, 2]


def test_choose_fills_chemistry_with_least_usage_loss():
    from fco_meta.chatbot.tools import _choose

    def opt(pid, usage, salary=0, chem=False):
        return {"entry": {"pid": pid, "usage_rate": usage}, "price": None, "salary": salary, "tags": {"chem"} if chem else set()}

    # 자리 3개, 케미 명단에서 2명: 사용률 손해가 가장 적은 자리부터 명단 선수로
    slots = [("ST", [opt(1, 0.9), opt(3, 0.2, chem=True)]), ("CAM", [opt(2, 0.8), opt(5, 0.7, chem=True)]), ("GK", [opt(4, 0.9)])]
    chosen = _choose(slots, {}, {"chem": 2})
    assert [c["entry"]["pid"] for c in chosen] == [3, 5, 4]
    # 급여 상한도 같이 지킨다: 명단 선수(급여 40)를 넣고 한도 안으로
    slots = [("ST", [opt(1, 0.9, 30), opt(3, 0.5, 40, chem=True)]), ("CAM", [opt(2, 0.8, 30), opt(6, 0.1, 10)])]
    chosen = _choose(slots, {"salary": 55}, {"chem": 1})
    assert [c["entry"]["pid"] for c in chosen] == [3, 6]
    # 급여 상한을 넘겨야만 채울 수 있으면 상한이 먼저 (명단 선수를 넣지 않음)
    slots = [("ST", [opt(1, 0.9, 30), opt(3, 0.5, 40, chem=True)]), ("CAM", [opt(2, 0.8, 30)])]
    assert [c["entry"]["pid"] for c in _choose(slots, {"salary": 60}, {"chem": 1})] == [1, 2]


def test_recommend_squad_chemistry(toolbox):  # noqa: F811
    toolbox.team_color_info = [{"name": "19-20 아스널", "type": "relation", "description": "",
                                "levels": [{"level": 1, "players": 2, "effects": ["짧은 패스 +3"]}],
                                "players": [{"pid": 9, "name": "볼란치R"}, {"pid": 11, "name": "볼란치L"}], "players_complete": True}]  # fmt: skip
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "formation": "4-2-3-1", "chemistry": "19-20 아스널"})
    assert r["chemistry"]["active"] and sorted(r["chemistry"]["players_in_lineup"]) == ["볼란치L", "볼란치R"]
    assert sorted(x["player"] for x in r["lineup"] if (x["player"] or "").endswith(" ★")) == ["볼란치L ★", "볼란치R ★"]  # 표에 그대로 옮기면 ★가 남는다
    assert run(toolbox, "recommend_squad", {"team_color": "아스널", "formation": "4-2-3-1", "chemistry": "현역 아스널"})["chemistry"]["team_color"] == "19-20 아스널"
    content, is_error = toolbox.run("recommend_squad", {"team_color": "아스널", "chemistry": "없는 케미"})
    assert is_error and "하나로 정할 수 없음" in json.loads(content)["error"]


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
    toolbox.conn.execute("DELETE FROM card_detail WHERE spid = 300000009")  # S300은 상세 미수집
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


def test_unexpected_tool_failures_come_back_as_tool_errors(toolbox, monkeypatch):
    import json as _json

    # 잘못된 조합(analytics의 ValueError): 모델이 고쳐 다시 부를 수 있게 그 이유를 돌려준다
    content, is_error = toolbox.run("query_squads", {"group_by": "role", "sort_by": "price"})
    assert is_error and "price" in _json.loads(content)["error"]

    # 예상 못 한 오류(DB 등): 답 전체를 실패시키지 않고 도구 오류로
    def broken(**kwargs):
        raise sqlite3.OperationalError("database disk image is malformed")

    monkeypatch.setitem(toolbox._handlers, "list_formations", broken)
    content, is_error = toolbox.run("list_formations", {})
    assert is_error and "OperationalError" in _json.loads(content)["error"]


def test_model_view_formats_rates_decimals_and_times():
    from fco_meta.chatbot.tools import model_view

    raw = {
        "data_as_of": "2026-10-01T09:00:00+09:00",
        "rows": [{"usage_rate": 0.528, "season_win_rate": 0.5568, "share_change": -0.0117, "avg_elo": 2769.9251,
                  "avg_rating": 6.2118, "avg_salary": 31.001, "rankers": 9996, "match_stat": 1.59, "pass_success_rate": 0.8976, "avg_season_win_rate_of_users": 0.5523}],
    }  # fmt: skip
    assert model_view(raw) == {
        "data_as_of": "2026-10-01 09:00",
        "rows": [{"usage_rate": "52.8%", "season_win_rate": "55.7%", "share_change": "-1.2%p", "avg_elo": 2769.9,
                  "avg_rating": 6.21, "avg_salary": 31.0, "rankers": 9996, "match_stat": 1.59, "pass_success_rate": "89.8%", "avg_season_win_rate_of_users": "55.2%"}],
    }  # fmt: skip


def test_team_color_rules(toolbox):  # noqa: F811
    from fco_meta.crawler.models import TeamColor

    # 클럽 팀컬러: 11명 모두 그 클럽 경력 카드 — 첼시 경력뿐인 볼란치L 카드는 아스널 스쿼드에 못 들어간다
    toolbox.conn.execute("UPDATE card_detail SET clubs = '[{\"club\": \"첼시\"}]' WHERE spid = 101000011")
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "formation": "4-2-3-1"})
    assert "볼란치L" not in [s["player"] for s in r["lineup"]] and "11명 모두" in r["team_color_rule"]

    # 시즌 단일(스페셜) 팀컬러 = 그 시즌 카드: "Winning Streak" → WS, "TOTY" → 19 TOTY
    toolbox.conn.executemany("INSERT OR REPLACE INTO meta_season (season_id, class_name) VALUES (?, ?)",
                             [(848, "WS (Winning Streak)"), (211, "19 TOTY (19 Team Of The Year)")])  # fmt: skip
    fits = toolbox._team_color_cards(TeamColor(id=1, name="Winning Streak", category="special", group_id=1))
    assert fits(848000009) and not fits(211000009)
    assert toolbox._team_color_cards(TeamColor(id=2, name="TOTY", category="special", group_id=2))(211000009)


def test_season_chemistry_alone_keeps_its_club_team_color(toolbox):  # noqa: F811
    # "21-22 롬바르디아 짜줘"처럼 케미만 오면 나머지 선수도 그 클럽 경력 카드로 (예전엔 다른 팀 선수가 섞였다)
    toolbox.team_color_info = [{"name": "19-20 아스널", "type": "relation", "description": "",
                                "levels": [{"level": 1, "players": 2, "effects": ["짧은 패스 +3"]}],
                                "players": [{"pid": 9, "name": "볼란치R"}, {"pid": 11, "name": "볼란치L"}], "players_complete": True}]  # fmt: skip
    r = run(toolbox, "recommend_squad", {"formation": "4-2-3-1", "chemistry": "19-20 아스널"})
    assert r["team_color"] == "아스널" and "아스널 팀컬러도 지킴" in r["team_color_from_chemistry"]
    assert r["chemistry"]["active"]
    toolbox.team_color_info[0]["name"] = "아스널 중원 듀오"  # 시즌 없이 클럽 이름으로 시작해도 그 클럽
    assert run(toolbox, "recommend_squad", {"formation": "4-2-3-1", "chemistry": "아스널 중원 듀오"})["team_color"] == "아스널"
    toolbox.team_color_info[0]["name"] = "아버지와 아들"  # 클럽·국가 이름이 없으면 팀컬러를 추측하지 않는다
    assert run(toolbox, "recommend_squad", {"formation": "4-2-3-1", "chemistry": "아버지와 아들"})["team_color"] == "전체 랭커"


def test_usage_is_rebuilt_once_when_roles_change(db, monkeypatch):  # noqa: F811
    import json as _json

    import fco_meta.analytics.usage as usage

    store = usage.UsageStore(db.conn)
    calls = []
    monkeypatch.setattr(store, "build_all", lambda *a: calls.append(a) or [])
    assert store.rebuild_if_roles_changed() and not store.rebuild_if_roles_changed()  # 처음 한 번만 (지난 스냅샷 포함 전부)
    db.conn.execute("UPDATE usage_meta SET value = ? WHERE key = 'roles'", (_json.dumps({"CAM": [17, 18, 19]}),))
    assert store.rebuild_if_roles_changed() and calls == [(), ()]  # 역할 구분이 바뀌면 다시


def test_recommend_squad_includes_requested_players(toolbox):  # noqa: F811
    add_salaries(toolbox.conn)
    # 급여 합 30이면 원래는 볼란치R(S300, 15)+...; 볼란치L(급여 30)을 꼭 넣으라면 넣고 나머지를 다시 고른다
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM", "include": "볼란치 L"})
    assert [s["player"] for s in r["lineup"]] == ["볼란치L"] and r["include"] == {"볼란치L": "볼란치L"}
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM", "include": "없는선수"})
    assert r["include"] == {"없는선수": None} and "'없는선수'를 넣지 못함" in r["note"]
    # '이름:시즌'이면 그 시즌 카드로, 그 시즌 카드가 없으면 넣지 못한다 (자리와 함께 써도 된다)
    for season in ("S250", "S300"):
        r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM", "include": f"볼란치R:DM:{season}"})
        assert r["lineup"][0]["player"] == "볼란치R" and r["lineup"][0]["season"] == season
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM", "include": "볼란치R:ICON"})  # 있는 시즌이지만 그 선수 카드가 없음
    assert r["include"] == {"볼란치R": None} and "ICON 카드를 넣지 못함" in r["note"]
    # 시즌·자리·강화 어느 것도 아닌 값은 조용히 시즌으로 받지 않고 형식을 알려 주는 오류
    content, is_error = toolbox.run("recommend_squad", {"team_color": "아스널", "slots": "DM", "include": "볼란치R:S999"})
    assert is_error and "'이름:11강'" in content
    content, is_error = toolbox.run("recommend_squad", {"team_color": "아스널", "exclude": "없는선수"})
    assert is_error and "없는선수" in content  # 없는 선수를 뺐다고 하지 않는다


def test_judge_tools_show_candidates_and_check_the_models_picks(toolbox):  # noqa: F811
    from fco_meta.chatbot.prompt import JUDGE_PROMPT, judges
    from fco_meta.chatbot.tools import tools_for

    toolbox.conn.executemany(
        "INSERT INTO card (spid, pid, season_id, name, salary, updated_at) VALUES (?, ?, ?, ?, ?, 'x')",
        [(250000009, 9, 250, "볼란치R", 200), (300000009, 9, 300, "볼란치R", 100), (101000011, 11, 101, "볼란치L", 150)],
    )
    toolbox.conn.commit()
    args = {"team_color": "아스널", "slots": "DM,DM", "max_total_salary": 310}
    c = run(toolbox, "squad_candidates", args)
    assert [p["player"] for p in c["candidates"]["DM"]] == ["볼란치L", "볼란치R"] and "lineup" not in c  # 가장 많이 쓰인 카드 순, 조합은 숨김
    assert [(card["season"], card["rankers"]) for card in c["candidates"]["DM"][1]["cards"]] == [("S250", 1), ("S300", 1)]  # 카드마다 사용 수

    def check(*picks):
        return run(toolbox, "check_squad", {**args, "picks": [{"role": r, "player": n, "season": s} for r, n, s in picks]})

    r = check(("DM", "볼란치R", "S250"), ("DM", "볼란치L", None))
    assert not r["ok"] and r["problems"] == ["총 급여 350 > 상한 310"]
    r = check(("DM", "볼란치R", "S300"), ("DM", "볼란치L", None))
    assert r["ok"] and r["total_salary"] == 250 and "alternatives" not in r
    assert "같은 선수를 두 자리에: 볼란치R" in check(("DM", "볼란치R", "S300"), ("DM", "볼란치R", "S300"))["problems"]
    problems = check(("ST", "볼란치R", None), ("DM", "볼란치L", "S999"))["problems"]
    assert "자리가 아님" in problems[0] and "'S999' 카드는" in problems[1] and problems[2] == "DM 2자리가 비었음"
    assert toolbox.run("check_squad", {**args, "picks": "볼란치R"})[1] is True

    # 큰 모델만 직접 고른다: 도구와 지시문이 갈린다
    names = lambda judge: {t["name"] for t in tools_for(judge)}  # noqa: E731
    assert names(True) - names(False) == {"squad_candidates", "check_squad"} and "recommend_squad" in names(True)
    assert judges("gemini-3.5-flash") and judges("deepseek/deepseek-v4.1-flash-1017") and not judges("gemini-3.5-flash-lite")
    assert "초안" in JUDGE_PROMPT and "check_squad" in JUDGE_PROMPT


def test_squad_draft_shows_leftover_upgrades_and_owned_cards(toolbox):  # noqa: F811
    toolbox.conn.executemany(
        "INSERT INTO card (spid, pid, season_id, name, salary, updated_at) VALUES (?, ?, ?, ?, ?, 'x')",
        [(250000009, 9, 250, "볼란치R", 20), (300000009, 9, 300, "볼란치R", 10), (101000011, 11, 101, "볼란치L", 15)],
    )
    # 볼란치L ICON은 6·7·8강 시세도 있다 (5강 9억 → 6강 +3억, 7강 +10억, 8강 +30억)
    toolbox.conn.executemany(
        "INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (101000011, ?, ?, '2026-09-28T12:00:00+00:00')",
        [(6, 1_200_000_000), (7, 1_900_000_000), (8, 3_900_000_000)],
    )
    toolbox.conn.commit()
    args = {"team_color": "아스널", "slots": "DM,DM", "max_total_price_bp": 3_000_000_000}
    # 남은 예산을 쓰면: 사용률 높은 자리부터 한 단계씩 (볼란치L 5→6→7강, 8강은 +20억이라 못 감)
    r = run(toolbox, "recommend_squad", args)
    assert r["total_price_bp"] == 2_200_000_000 and r["leftover"]["price"] == "8억"
    assert [(u["player"], u["from"], u["to"]) for u in r["upgraded"]] == [("볼란치L", "ICON 5강", "ICON 7강")]
    # spend=false면 초안 그대로, 대신 올릴 수 있는 선택지를 보여 준다
    r = run(toolbox, "recommend_squad", {**args, "spend": False})
    assert r["total_price_bp"] == 1_200_000_000 and r["leftover"]["price"] == "18억"  # 남은 예산
    # 남은 18억으로 올릴 수 있는 강화만 (8강은 +30억이라 빠짐)
    assert r["upgrades"]["DM 볼란치L"]["grade_up"] == [
        {"grade": 6, "price": "12억", "extra": "3억"}, {"grade": 7, "price": "19억", "extra": "10억"},
    ]  # fmt: skip
    # 예산이 없으면 업그레이드 선택지도 없다 (토큰 절약)
    assert "upgrades" not in run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM"})

    # 이미 가진 카드는 총액에서 뺀다
    picks = [{"role": "DM", "player": "볼란치R", "season": "S250", "owned": True}, {"role": "DM", "player": "볼란치L"}]
    c = run(toolbox, "check_squad", {**args, "picks": picks, "spend": False})
    assert c["ok"] and c["owned"]["players"] == ["볼란치R"] and c["total_price_bp"] < r["total_price_bp"]
    # 구단가치(total_value)는 가진 카드 시세까지: 추천 스쿼드 패널의 총 금액은 이 값 (총액은 살 카드만)
    assert c["total_value_bp"] == r["total_price_bp"] and "total_value" not in r
    # 모델이 고른 강화로 예산이 남으면 check_squad도 남는 돈으로 올린다 (가진 카드는 그대로) — 예전엔 27억을 남긴 채 답했다
    c = run(toolbox, "check_squad", {**args, "picks": picks})
    assert c["ok"] and [(x["player"], x["to"]) for x in c["raised_grades"]] == [("볼란치L", "7강")]

    # 후보는 바꿀 자리만
    cand = run(toolbox, "squad_candidates", {"team_color": "아스널", "formation": "4-2-3-1", "roles": "DM"})
    assert list(cand["candidates"]) == ["DM"]
    # 신규특성 조건도 recommend_squad처럼 받는다 (예전엔 traits에 '예상 못 한 인자' 오류)
    assert not toolbox.run("squad_candidates", {"team_color": "아스널", "formation": "4-2-3-1", "roles": "DM", "traits": "DM:커맨더"})[1]


def test_choose_keeps_the_card_most_rankers_use():
    def option(pid, usage, share, price, salary=10):
        score = usage * (1 + share) / 2  # _squad_setup이 매기는 점수: 선수 사용률 × 시즌 카드 비중
        return {"entry": {"pid": pid, "usage_rate": usage}, "score": score, "price": price, "salary": salary, "tags": set()}

    meta, rare = option(1, 0.5, 1.0, 10), option(1, 0.5, 0.2, 5)  # 같은 선수: 많이 쓰는 카드(비쌈)와 드문 카드(쌈)
    star, backup = option(2, 0.5, 1.0, 10), option(3, 0.45, 1.0, 3)
    # 예산 15: 같은 선수의 드문 카드로 내리는 것(공짜가 아님)보다 사용률이 조금 낮은 다른 선수가 낫다
    assert _choose([("ST", [meta, rare]), ("CAM", [star, backup])], {"price": 15}) == [meta, backup]


def test_squad_options_score_the_card_rankers_use_more(toolbox):  # noqa: F811
    # 점수는 그 시즌 카드를 쓴 랭커 비율: S300을 2명, S250을 1명이 쓰면 (표본 3명... 이 경우 4명) 2:1
    toolbox.conn.execute("UPDATE usage_stats SET ranker_count = 2 WHERE sp_id = 300000009")
    toolbox.conn.commit()
    sq = toolbox._squad_setup("아스널", None, False, 10**10, None, "DM,DM", None, None)
    cards = {o["card"]["season"]: o for o in sq.slot_options[0][1] if o["entry"]["name"] == "볼란치R"}
    sample = sq.first.sample_size
    assert cards["S300"]["score"] == pytest.approx(2 / sample, abs=1e-4) and cards["S250"]["score"] == pytest.approx(1 / sample, abs=1e-4)


def test_budget_sets_the_grade_never_below_8(toolbox):  # noqa: F811
    # 볼란치L ICON: 랭커들은 10강(20억)을 쓴다. 7강 2억, 8강 4억, 9강 8억
    toolbox.conn.execute("""UPDATE usage_stats SET grade_dist = '{"10": 2}' WHERE sp_id = 101000011""")
    toolbox.conn.executemany(
        "INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (101000011, ?, ?, '2026-09-28T12:00:00+00:00')",
        [(6, 100_000_000), (7, 200_000_000), (8, 400_000_000), (9, 800_000_000), (10, 2_000_000_000)],
    )
    toolbox.conn.commit()

    def squad(budget):
        r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM", "max_total_price_bp": budget})
        left = next(s for s in r["lineup"] if s["player"] == "볼란치L")
        return r, (left["player"], left["card"]["season"], left["card"]["most_used_grade"])

    # 10억: 다른 카드로 바꾸지 않고 강화를 낮춘다 — 8강(4억)에서 시작하고, 9강은 +4억이라 넘어서 그대로
    r, left = squad(1_000_000_000)
    assert left == ("볼란치L", "ICON", 8) and r["budget"]["within_budget"] and r["total_price_bp"] == 700_000_000
    assert [(x["player"], x["from"], x["to"]) for x in r["lowered"]] == [("볼란치L", "ICON 10강", "ICON 8강")]
    assert "upgraded" not in r
    # 30억: 랭커들이 쓰는 10강까지 — 낮춘 것도 올린 것도 없다
    r, left = squad(3_000_000_000)
    assert left == ("볼란치L", "ICON", 10) and "lowered" not in r and "upgraded" not in r
    # 3억: 8강(4억)이 안 되지만 그 아래 7강(2억)으로는 내리지 않는다 — 예산을 못 맞췄다고 알린다
    r, left = squad(300_000_000)
    assert left == ("볼란치L", "ICON", 8) and not r["budget"]["within_budget"]
    # 예산이 없으면 랭커들이 쓰는 강화 그대로
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM"})
    assert next(s for s in r["lineup"] if s["player"] == "볼란치L")["card"]["most_used_grade"] == 10 and "lowered" not in r


def test_budget_stretches_for_a_much_better_pick(toolbox):  # noqa: F811
    # 볼란치R: S250(3억)을 셋이 쓰고 S300(5,000만)은 하나만 쓴다 → 싼 카드로 내리면 점수가 크게 깎인다
    toolbox.conn.execute("UPDATE usage_stats SET ranker_count = 3 WHERE sp_id = 250000009")
    toolbox.conn.commit()

    def squad(budget):
        r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM", "max_total_price_bp": budget})
        return r, [s["card"]["season"] for s in r["lineup"]]

    # 예산 안의 본안과 예산 초과 대안을 분리한다.
    r, seasons = squad(1_150_000_000)
    assert seasons == ["S300", "ICON"] and r["budget"]["within_budget"]
    alternative = r["budget"]["over_budget_alternative"]
    assert alternative["over_by"] == "5,000만"
    assert [p["card"]["season"] for p in alternative["lineup"]] == ["S250", "ICON"]
    # 10억: 12억은 10%를 넘으므로 예산 안의 스쿼드
    r, seasons = squad(1_000_000_000)
    assert seasons == ["S300", "ICON"] and r["budget"]["within_budget"] and "over_by" not in r["budget"]


def test_squad_shows_where_salary_differs_from_rankers(toolbox):  # noqa: F811
    toolbox.conn.executemany(
        "INSERT INTO card (spid, pid, season_id, name, salary, updated_at) VALUES (?, ?, ?, ?, ?, 'x')",
        [(250000009, 9, 250, "볼란치R", 24), (300000009, 9, 300, "볼란치R", 10), (101000011, 11, 101, "볼란치L", 16)],
    )
    toolbox.conn.commit()
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM"})
    # 랭커들의 DM 급여 평균(사용 수로 가중)은 16.5, 이 스쿼드의 DM은 24·16 → 평균 20
    assert r["salary_vs_rankers"] == [{"role": "DM", "rankers_avg": 16.5, "squad_avg": 20.0}]
    # 차이가 2 미만이면 알리지 않는다
    toolbox.conn.execute("UPDATE card SET salary = 17 WHERE spid = 250000009")
    toolbox.conn.commit()
    assert "salary_vs_rankers" not in run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM"})


def test_squad_never_uses_grades_above_11(toolbox):  # noqa: F811
    # 볼란치L ICON: 랭커들이 13강을 쓴다 — 12·13강은 돈으로 살 수 있는 카드가 아니라 11강으로 셈
    toolbox.conn.execute("""UPDATE usage_stats SET grade_dist = '{"13": 2}' WHERE sp_id = 101000011""")
    toolbox.conn.executemany(
        "INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (101000011, ?, ?, '2026-09-28T12:00:00+00:00')",
        [(8, 400_000_000), (9, 800_000_000), (10, 2_000_000_000), (11, 5_000_000_000), (12, 20_000_000_000), (13, 90_000_000_000)],
    )
    toolbox.conn.commit()

    def grade(args):
        r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM", **args})
        return r, next(s for s in r["lineup"] if s["player"] == "볼란치L")["card"]["most_used_grade"]

    r, g = grade({})
    assert g == 11 and next(s for s in r["lineup"] if s["player"] == "볼란치L")["card"]["price_at_most_used_grade"]["price_bp"] == 5_000_000_000
    # 예산이 넉넉해도 11강 위로는 올리지 않고, 업그레이드 선택지에도 12·13강은 없다
    r, g = grade({"max_total_price_bp": 1_000_000_000_000})
    assert g == 11 and "lowered" not in r
    r, g = grade({"max_total_price_bp": 1_000_000_000_000, "spend": False})
    assert all(u["grade"] <= 11 for v in r.get("upgrades", {}).values() if isinstance(v, dict) for u in v.get("grade_up", []))


def test_squad_skips_rarely_used_cards_unless_it_must():
    from fco_meta.chatbot.tools import _card_floor

    def option(pid, rankers, salary, tags=()):
        return {"entry": {"pid": pid, "rankers": rankers, "usage_rate": rankers / 100}, "card": {"rankers": rankers},
                "price": 0, "salary": salary, "tags": set(tags)}

    dehea, romero, kept = option(1, 13, 23), option(2, 1, 9), option(3, 1, 9, ("include:x",))
    # 하한: 4명 이상 그리고 그 자리 1위(13명)의 10% 이상 — 1명이 쓴 로메로는 빠지고, 꼭 넣을 선수(tags)는 남는다
    assert _card_floor([("GK", [dehea, romero, kept])]) == [("GK", [dehea, kept])]
    # 어느 카드도 하한을 못 넘는 자리는 그대로 둔다 (랭커가 적은 팀컬러)
    assert _card_floor([("GK", [romero])]) == [("GK", [romero])]

    from fco_meta.chatbot.tools import _choose_floored

    st, cheap = option(4, 40, 30), option(5, 5, 10)
    slots = [("GK", [dehea, romero]), ("ST", [st, cheap])]
    # 급여 40: 하한 안에서 맞출 수 있다 (데헤아 23 + 싼 ST 10) — 점수가 더 높은 로메로+1위 ST(39)보다 랭커가 쓰는 카드로
    assert _choose_floored(slots, {"salary": 40}, {}) == ([dehea, cheap], False)
    # 급여 32: 하한 안에서는 못 맞춘다 → 하한 없이 다시 (로메로 9 + 싼 ST 10), 하한을 풀었다고 알린다
    assert _choose_floored(slots, {"salary": 32}, {}) == ([romero, cheap], True)


def test_squad_knobs_turn_wishes_into_the_search(toolbox):  # noqa: F811
    toolbox.conn.executemany(
        "INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (?, ?, ?, '2026-09-28T12:00:00+00:00')",
        [(101000011, 8, 2_000_000_000), (250000009, 8, 600_000_000)],
    )
    toolbox.conn.commit()
    args = {"team_color": "아스널", "slots": "DM,DM"}
    base = run(toolbox, "recommend_squad", args)
    assert sorted(s["player"] for s in base["lineup"]) == ["볼란치L", "볼란치R"]
    # 뺄 선수: 그 자리를 다른 선수로
    r = run(toolbox, "recommend_squad", {**args, "exclude": "볼란치R"})
    assert "볼란치R" not in [s["player"] for s in r["lineup"]] and r["applied"]["exclude"] == ["볼란치R"]
    # 가진 카드: 그 카드로 고정, 시세는 0
    r = run(toolbox, "recommend_squad", {**args, "owned": "볼란치L:ICON:5"})
    mine = next(s for s in r["lineup"] if s.get("owned"))
    assert mine["player"] == "볼란치L" and r["total_price_bp"] == base["total_price_bp"] - 900_000_000
    # 강화 하한: 덜 강화해 쓴 카드는 그 강화 시세로
    r = run(toolbox, "recommend_squad", {**args, "min_grade": 8})
    assert {s["card"]["most_used_grade"] for s in r["lineup"]} == {8}
    # 모르는 능력치는 도구 오류 (모델이 고쳐 다시 부른다)
    assert toolbox.run("recommend_squad", {**args, "prefer": "DM:없는능력치"})[1] is True


def test_high_salary_knob_picks_the_higher_salary_card_not_the_pricier_one(toolbox):  # noqa: F811
    # "고급여 키퍼"는 돈(invest)이 아니라 카드 급여: 볼란치L ICON(급여 15, 많이 씀) 대신 볼란치R(급여 20)
    toolbox.conn.executemany(
        "INSERT INTO card (spid, pid, season_id, name, salary, updated_at) VALUES (?, ?, ?, ?, ?, 'x')",
        [(250000009, 9, 250, "볼란치R", 20), (101000011, 11, 101, "볼란치L", 15)],
    )
    toolbox.conn.commit()
    args = {"team_color": "아스널", "slots": "DM"}
    pick = lambda r: (r["lineup"][0]["player"], r["lineup"][0]["card"]["salary"])  # noqa: E731
    assert pick(run(toolbox, "recommend_squad", args)) == ("볼란치L", 15)
    r = run(toolbox, "recommend_squad", {**args, "high_salary": "DM"})
    assert pick(r) == ("볼란치R", 20) and r["applied"]["high_salary"] == {"DM": "급여 높은 카드 우선"}
    r = run(toolbox, "recommend_squad", {**args, "high_salary": "DM:18"})  # 하한: 그 급여 이상 카드만
    assert pick(r) == ("볼란치R", 20) and r["applied"]["high_salary"] == {"DM": "급여 18 이상"}
    r = run(toolbox, "recommend_squad", {**args, "high_salary": "DM:30"})  # 그런 카드가 없으면 알리고 가장 높은 급여로
    assert pick(r) == ("볼란치R", 20) and "급여 30 이상 카드가" in r["note"]
    assert toolbox.run("recommend_squad", {**args, "high_salary": "DM:많이"})[1] is True  # 형식 오류는 도구 오류


def test_invest_pins_a_dearer_card_within_the_ceiling_and_rebuilds_the_rest(toolbox):  # noqa: F811
    # 볼란치L ICON 5강 9억(많이 씀, 더 높은 강화 시세 없음) / 볼란치R S250 5강 3억, 8강 10억 5천만
    from fco_meta.chatbot.tools import INVEST_CEILING, INVEST_FIRST_GAP

    assert [round(INVEST_CEILING - INVEST_FIRST_GAP * 0.5 ** (n - 1), 3) for n in (1, 2, 3, 4)] == [0.5, 0.7, 0.8, 0.85]
    toolbox.conn.execute("INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (250000009, 8, 1050000000, '2026-09-28T12:00:00+00:00')")
    toolbox.conn.commit()
    args = {"team_color": "아스널", "slots": "DM", "max_total_price_bp": 2_100_000_000}
    base = run(toolbox, "recommend_squad", args)
    assert (base["lineup"][0]["player"], base["total_price_bp"]) == ("볼란치L", 900_000_000) and "invest" not in base
    # DM 한 자리: 한도 50% = 10억 5천만 → 지금(9억)보다 비싸고 한도 안인 카드 (볼란치R 8강)로 고정
    r = run(toolbox, "recommend_squad", {**args, "invest": "DM"})
    assert r["invest"]["ceiling"] == "10억 5,000만" and len(r["invest"]["changed"]) == 1
    assert (r["lineup"][0]["player"], r["lineup"][0]["card"]["most_used_grade"], r["total_price_bp"]) == ("볼란치R", 8, 1_050_000_000)
    # 한도 안에 더 비싼 카드가 없으면 그대로 두고 알린다
    r = run(toolbox, "recommend_squad", {**args, "max_total_price_bp": 1_200_000_000, "invest": "DM"})
    assert not r["invest"]["changed"] and "그대로 둠" in r["invest"]["note"]
    # 예산이 없으면 기본 스쿼드 총액을 기준으로 옮긴다 (답에 예산을 쓰지 말라는 안내와 함께)
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM", "invest": "DM"})
    assert "기본 스쿼드 총액" in r["invest"]["budget_from_base"]


def test_squad_trait_knob_keeps_only_cards_with_the_traits(toolbox):  # noqa: F811
    # 볼란치L ICON만 트릭스터가 달려 있다. 라브까지 "달 수 있는"이면 8강 이상에서 하나를 새로 단다
    toolbox.conn.execute("UPDATE card_detail SET traits = '[\"트릭스터\"]' WHERE spid = 101000011")
    toolbox.conn.execute("INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (101000011, 8, 2000000000, 'x')")
    toolbox.conn.commit()
    args = {"team_color": "아스널", "slots": "DM"}
    r = run(toolbox, "recommend_squad", {**args, "traits": "DM:트릭"})
    assert [(s["player"], s["card"]["new_traits"]) for s in r["lineup"]] == [("볼란치L", ["트릭스터"])]
    r = run(toolbox, "recommend_squad", {**args, "traits": "DM:트릭,라브", "can_add_trait": True})
    card = r["lineup"][0]["card"]
    assert card["added_trait"] == "라인 브레이커" and card["most_used_grade"] == 8 and r["total_price"] == "20억"
    # 달 수 없으면 그 자리를 채우지 못한다
    assert run(toolbox, "recommend_squad", {**args, "traits": "DM:트릭,라브"})["lineup"][0]["player"] is None


def test_diagnose_team_ranks_each_starter_and_shows_who_rankers_use_more(toolbox):  # noqa: F811
    picks = [{"role": "DM", "player": "볼란치 L", "season": "S101", "grade": 8}, {"role": "DM", "player": "없는선수"}]
    r = run(toolbox, "diagnose_team", {"picks": picks, "team_color": "아스널"})
    low, missing = r["slots"]
    # 순위는 선수가 아니라 시즌 카드 기준: 사용자의 S101 카드는 랭커가 안 써서 순위가 없고, 같은 선수의 ICON 카드는 쓰였다고만 알린다
    assert "usage_rank" not in low and "이 시즌 카드를 쓴 기록이 없음" in low["note"] and "ICON 카드로 씀" in low["note"]
    assert "usage_rank" not in missing and "기록이 없음" in missing["note"]
    r = run(toolbox, "diagnose_team", {"picks": [{"role": "DM", "player": "볼란치 L", "season": "ICON"}], "team_color": "아스널"})
    assert r["slots"][0]["usage_rank"] == 1 and r["slots"][0]["rankers_card"]["season"] == "ICON" and r["slots"][0]["used_more"] == []
    # 이미 팀에 있는 선수는 '더 많이 쓰는 선수'로 권하지 않는다
    r = run(toolbox, "diagnose_team", {"picks": [*picks[:1], {"role": "DM", "player": "볼란치R"}], "team_color": "아스널"})
    assert r["slots"][0]["used_more"] == [] and r["slots"][1]["usage_rank"] == 2  # 볼란치R의 카드는 ICON 다음
    assert toolbox.run("diagnose_team", {"picks": [{"role": "윙어", "player": "x"}]})[1] is True  # 자리 여럿을 뜻하는 말은 거절


def test_amounts_in_the_question_count_as_known():
    from fco_meta.chatbot.numbers import unsupported_numbers

    block = "[사용자 팀: @예시 · 4-2-2-2 · 총 급여 309 · 총액 37억 1,778만]\nGK 골키퍼 (HG, 12강, 급여 21, 9억 1,500만)\n[/사용자 팀]\n내 팀 평가해줘"
    assert unsupported_numbers("총액 37억 1,778만, 골키퍼는 9억 1,500만입니다", [], block) == []  # 팀 블록의 값은 근거
    assert unsupported_numbers("총액 40억입니다", [], block) == ["40억"]


def test_sums_and_differences_of_real_amounts_count_as_backed():
    from fco_meta.chatbot.numbers import unsupported_numbers

    r = {"card": {"sp_id": 283192448, "price_at_most_used_grade": {"price_bp": 915000000, "price": "9억 1,500만"}},
         "upgrade": {"price": "12억 2,000만"}, "win": 0.528, "other": 0.505}  # fmt: skip
    assert unsupported_numbers("올리는 데 3억 500만 더, 약 3.1억, 승률 2.3%p 높아요", [r]) == []  # 차이
    assert unsupported_numbers("둘이 21억 3,500만", [r]) == []  # 합
    assert unsupported_numbers("5억 3,370만입니다", [r]) == ["5억 3,370만"]  # 카드 id끼리의 합·차로는 맞추지 않는다
    assert unsupported_numbers("TOP 10,000명 기준", [r]) == []  # 시스템 프롬프트의 사실


def test_check_squad_takes_the_users_own_card_and_candidates_take_exclude(toolbox):  # noqa: F811
    toolbox.conn.execute("INSERT OR IGNORE INTO meta_season (season_id, class_name) VALUES (777, 'S777 (Rare)')")
    toolbox.conn.execute("INSERT INTO card (spid, pid, season_id, name, salary, updated_at) VALUES (777000009, 9, 777, '볼란치R', 12, 'x')")
    toolbox.conn.execute("INSERT INTO card_detail (spid, grade, positions, traits, summary, stats, clubs, fetched_at, nation)"
                         " VALUES (777000009, 1, '{}', '[]', '{}', '{}', '[{\"club\": \"아스널\"}]', 'x', '잉글랜드')")  # 아스널 경력 카드
    toolbox.conn.commit()
    args = {"team_color": "아스널", "slots": "DM,DM"}
    picks = [{"role": "DM", "player": "볼란치R", "season": "S777"}, {"role": "DM", "player": "볼란치L"}]
    r = run(toolbox, "check_squad", {**args, "picks": picks})
    assert not r["ok"] and "owned: true" in r["problems"][0]  # 랭커가 이 자리에 안 쓴 시즌
    picks[0]["owned"] = True  # 사용자가 가진 카드면 받아 준다 (시세는 총액에서 뺌)
    r = run(toolbox, "check_squad", {**args, "picks": picks})
    assert r["ok"] and r["owned"]["players"] == ["볼란치R"]
    c = run(toolbox, "squad_candidates", {**args, "exclude": "볼란치R"})
    assert "볼란치R" not in [p["player"] for p in c["candidates"]["DM"]] and c["candidates"]["DM"]


def test_include_alone_picks_the_players_club(toolbox):  # noqa: F811
    # "볼란치R 포함 스쿼드"처럼 팀컬러 없이 선수만 오면 그 선수 클럽 경력 중 랭커가 그 선수를 쓴 클럽 (예전엔 레알·전체 랭커로 짰다)
    r = run(toolbox, "recommend_squad", {"formation": "4-2-3-1", "include": "볼란치R"})
    assert r["team_color"] == "아스널" and r["team_color_from_include"]["team_color"] == "아스널"
    assert r["include"] == {"볼란치R": "볼란치R"} and "11명 모두" in r["team_color_rule"]
    # 팀컬러를 말했으면 그대로 (include로 바꾸지 않음)
    assert "team_color_from_include" not in run(toolbox, "recommend_squad", {"team_color": "아스널", "formation": "4-2-3-1", "include": "볼란치R"})


def test_unknown_team_color_says_ask_back(toolbox):  # noqa: F811
    content, is_error = toolbox.run("recommend_squad", {"team_color": "첨시", "formation": "4-1-2-3"})
    assert is_error and "되물을 것" in json.loads(content)["error"]


def test_include_without_shared_club_falls_back_to_nation(toolbox):  # noqa: F811
    from fco_meta.crawler.models import TeamColor

    # 함께 뛴 클럽이 없으면 같은 국적 팀컬러 (픽스처 카드는 모두 잉글랜드)
    toolbox.conn.execute("UPDATE card_detail SET clubs = '[{\"club\": \"첼시\"}]' WHERE spid = 101000011")
    toolbox.catalog.entries.append(TeamColor(id=1004, name="잉글랜드", category="nationality", group_id=9))
    toolbox.catalog.entries[:] = [t for t in toolbox.catalog.entries if not (t.id == 1004 and t.category == "club")]
    clubs = toolbox._include_clubs(["볼란치R", "볼란치L"])
    assert [(t.name, t.category) for t in clubs] == [("잉글랜드", "nationality")]


def test_player_detail_forgives_a_one_letter_typo(toolbox):  # noqa: F811
    from fco_meta.chatbot.tools import _typo_close

    assert _typo_close("손흥미", "손흥민") and _typo_close("반페르쉬", "반페르시") and _typo_close("베켄바우어", "베켄바워")
    assert _typo_close("룸바르디이", "롬바르디아") and _typo_close("챌시", "첼시")
    assert not _typo_close("손흥민", "손흥민") and not _typo_close("첨시", "첼시") and not _typo_close("메시", "메시아스")
    r = run(toolbox, "get_player_detail", {"name": "볼란치", "team_color": "4231아스날"})  # 팀컬러 오타·붙여 쓴 포메이션은 고쳐 찾는다
    assert {p["name"] for p in r["players"]} == {"볼란치L", "볼란치R"}
    r = run(toolbox, "get_player_detail", {"name": "볼란추R"})  # 볼란치L·볼란치R 둘 다 비슷하고 사용 차이가 작으면 고치지 않는다
    assert r["usage"] == [] and "typo" not in r


def test_player_detail_season_card_even_if_rankers_never_used_it(toolbox):  # noqa: F811
    toolbox.conn.execute("INSERT INTO card (spid, pid, season_id, season_code, name, main_position, ovr, salary, updated_at)"
                         " VALUES (210000009, 9, 210, 'GR', '볼란치R', 'CDM', 90, 14, 'x')")
    toolbox.conn.commit()
    r = run(toolbox, "get_player_detail", {"name": "볼란치R", "season": "gr"})
    assert (r["season_card"]["season"], r["season_card"]["salary"], r["season_card"]["ovr"]) == ("GR", 14, 90)
    assert "찾지 못함" in run(toolbox, "get_player_detail", {"name": "볼란치R", "season": "TOTY"})["season_card"]["note"]


def test_recommended_cards_show_their_new_traits(toolbox):  # noqa: F811
    toolbox.conn.execute("UPDATE card_detail SET traits = '[\"트릭스터\", \"테크니컬 드리블러\"]' WHERE spid = 101000011")
    toolbox.conn.commit()
    r = run(toolbox, "recommend_players", {"team_color": "아스널", "role": "DM"})
    cards = {c["sp_id"]: c for p in r["players"] for c in p["cards"]}
    assert cards[101000011]["new_traits"] == ["트릭스터"]  # 신규특성만, 조건 없이도


def test_leftover_after_11_swaps_to_better_cards_then_ranker_owned_top_grades_in_order(toolbox):  # noqa: F811
    from types import SimpleNamespace

    def opt(pid, sp, grade, price, usage, salary=25):
        return {"entry": {"pid": pid, "name": f"p{pid}", "usage_rate": usage}, "card": {"sp_id": sp, "most_used_grade": grade},
                "price": price, "salary": salary, "tags": set()}  # fmt: skip

    prices = {(1, 11): 10, (2, 11): 50, (3, 11): 10, (3, 12): 30, (3, 13): 60, (4, 11): 10, (4, 12): 20, (4, 13): 25}
    ovr = {1: 150, 2: 160, 3: 140, 4: 140}
    toolbox._price = lambda sp, g: {"grade": g, "price_bp": prices[(sp, g)]} if (sp, g) in prices else None
    toolbox._ovr = lambda sp, role, g: ovr[sp] + g
    st_now, st_better, rb, gk = opt(1, 1, 11, 10, 0.5), opt(2, 2, 11, 50, 0.1), opt(3, 3, 11, 10, 0.4), opt(4, 4, 11, 10, 0.4)
    sq = SimpleNamespace(slot_options=[("ST", [st_now, st_better]), ("RB", [rb]), ("GK", [gk])], limits={"price": 125, "salary": 315}, reqs={})
    chosen = toolbox._better_cards(sq, [st_now, rb, gk], {})  # 다 11강이고 남는다 → ST를 OVR 높은 카드로 (+40)
    assert [(c["entry"]["pid"], c["card"]["most_used_grade"]) for c in chosen] == [(2, 11), (3, 11), (4, 11)]
    # 남은 55: 키퍼 먼저 — 13강은 아무도 안 써서 12강(+10, 랭커 4명) → 풀백 13강(+50)은 안 돼 12강(+20, 랭커 2명)
    toolbox._ranker_top_grades = lambda: {(4, 12): 4, (3, 12): 2, (3, 13): 2}
    chosen = toolbox._top_grades(sq, chosen, {})
    assert [c["card"]["most_used_grade"] for c in chosen] == [11, 12, 12] and chosen[2]["top_grade_rankers"] == 4
    toolbox._ranker_top_grades = lambda: {(4, 12): 1, (3, 12): 1}  # 랭커 1명뿐인 12·13강은 매물이 거의 없어 올리지 않는다
    assert [c["card"]["most_used_grade"] for c in toolbox._top_grades(sq, [st_better, rb, gk], {})] == [11, 11, 11]
    toolbox._ranker_top_grades = lambda: {}  # 그 강화로 쓰는 랭커가 없으면 올리지 않는다
    assert [c["card"]["most_used_grade"] for c in toolbox._top_grades(sq, [st_better, rb, gk], {})] == [11, 11, 11]
    lower = {**st_now, "card": {"sp_id": 1, "most_used_grade": 10}}
    prices[(1, 11)] = 1000  # 11강이 너무 비싸 못 올린 카드가 있을 때
    tight = SimpleNamespace(slot_options=sq.slot_options, limits={"price": 31}, reqs={})  # 남은 돈 1 (예산의 10% 미만): 카드를 바꾸지 않는다
    assert toolbox._better_cards(tight, [lower, rb, gk], {})[0] is lower
    roomy = SimpleNamespace(slot_options=sq.slot_options, limits={"price": 125, "salary": 315}, reqs={})  # 많이 남으면 다른 자리는 더 좋게
    assert toolbox._better_cards(roomy, [lower, rb, gk], {})[0]["entry"]["pid"] == 2
    assert toolbox._not_raised(tight, [lower, rb, gk], {})[0]["card"] == "p1  10강"

def test_include_can_fix_a_players_grade(toolbox):  # noqa: F811
    toolbox.conn.executemany(
        "INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (101000011, ?, ?, '2026-09-28T12:00:00+00:00')",
        [(6, 1_200_000_000), (7, 1_900_000_000), (8, 3_900_000_000)],
    )
    toolbox.conn.commit()
    args = {"team_color": "아스널", "slots": "DM,DM", "max_total_price_bp": "30억"}
    assert run(toolbox, "recommend_squad", args)["lineup"][1]["card"]["most_used_grade"] == 7  # 남은 돈으로 7강까지 올린다
    r = run(toolbox, "recommend_squad", {**args, "include": "볼란치L:6강"})  # "볼란치L 6카": 그 강화 그대로 (올리지도 내리지도 않음)
    mine = next(s for s in r["lineup"] if s["player"] == "볼란치L")
    assert mine["card"]["most_used_grade"] == 6 and r["include"] == {"볼란치L": "볼란치L"}


def test_include_a_player_rankers_did_not_use_there(toolbox):  # noqa: F811
    # "호날두를 톱에": 랭커가 그 자리에 쓴 기록이 없어도 그 선수의 카드(시세가 있는 것)로 넣고, 기록이 없다고 알린다
    toolbox.conn.execute("INSERT INTO card (spid, pid, season_id, name, salary, ovr, updated_at) VALUES (300000077, 77, 300, '새선수', 20, 90, 'x')")
    toolbox.conn.execute("INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (300000077, 8, 50000000, '2026-09-28T12:00:00+00:00')")
    toolbox.conn.execute("INSERT INTO card_detail (spid, grade, positions, traits, summary, stats, clubs, fetched_at, nation)"
                         " VALUES (300000077, 1, '{}', '[]', '{}', '{}', '[{\"club\": \"아스널\"}]', 'x', '잉글랜드')")  # 아스널 경력 카드
    toolbox.conn.commit()
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM", "include": "새선수:DM", "max_total_price_bp": "10억"})
    assert [s["player"] for s in r["lineup"]] == ["새선수"] and r["include"] == {"새선수": "새선수"}
    assert r["include_without_ranker_data"]["players"] == ["새선수 S300"]
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM", "include": "볼란치L", "max_total_price_bp": "10억"})
    assert "include_without_ranker_data" not in r  # 랭커가 쓴 카드가 맞으면 그 카드
    assert "와일드 태클러" in __import__("fco_meta.chatbot.tools", fromlist=["x"]).NEW_TRAITS  # 블랑 26FSL의 신규특성이 표에서 빠졌다


def test_over_the_cap_with_owned_cards_comes_with_fixes():
    from fco_meta.chatbot.tools import Toolbox

    def opt(pid, name, salary, usage, owned=False):
        return {"entry": {"pid": pid, "name": name, "usage_rate": usage, "rankers": 10}, "salary": salary, "price": 0 if owned else 1,
                "card": {"season": "TK (Top Korea)", "rankers": 10, "price_at_most_used_grade": {"price": "1억"}}, **({"owned": True} if owned else {})}  # fmt: skip

    park, hong, ki = opt(1, "박지성", 31, 0.14), opt(2, "홍명보", 32, 0.3, True), opt(3, "기성용", 31, 0.2, True)
    kim, ko = opt(4, "김정우", 30, 0.25), opt(5, "고승범", 28, 0.24)
    slots = [("LM", [park]), ("DM", [hong, kim, ko]), ("DM", [ki, kim, ko])]
    fixes = Toolbox._owned_fixes(slots, [park, hong, ki], {"salary": 93})  # 31+32+31 = 94, 한도 93
    assert [(f["from"], f["to"], f["total_salary_after"]) for f in fixes] == [
        ("홍명보 TK (급여 32)", "김정우 TK (급여 30)", 92), ("기성용 TK (급여 31)", "김정우 TK (급여 30)", 93), ("홍명보 TK (급여 32)", "고승범 TK (급여 28)", 90),
    ]  # 사용률 높은 카드로 바꾸는 안부터, 넣고 싶은 박지성은 그대로
    assert Toolbox._owned_fixes(slots, [park, hong, ki], {"salary": 80}) == []  # 한 자리로는 안 맞으면 없음


def test_included_players_get_a_slot_of_their_own():
    # 넣을 선수는 한 자리를 그 선수 카드로 고정한다: 한 번에 한 자리씩 바꾸는 탐색이 비싼 다른 자리를 싸게 바꾸는 대신 그 선수를 빼지 않게
    from fco_meta.chatbot.tools import _pin_includes

    def opt(pid, usage, tags=()):
        return {"entry": {"pid": pid, "usage_rate": usage, "rankers": 1}, "card": {"rankers": 1}, "tags": set(tags), "salary": 0, "price": 0}

    ron, ron_lm, other, other2 = opt(1, 0.1, ["include:호나우두"]), opt(1, 0.3, ["include:호나우두"]), opt(2, 0.5), opt(3, 0.4)
    st = [ron, other, other2]
    slots = [("ST", st), ("ST", st), ("LM", [ron_lm, other])]
    out = _pin_includes(slots, ["호나우두", "없는선수"])
    assert out == [("ST", [other, other2]), ("ST", [other, other2]), ("LM", [ron_lm])]  # 가장 잘 쓰이는 자리 하나만, 다른 자리에서는 빠진다
    out = _pin_includes([("ST", st), ("ST", st)], ["호나우두"])
    assert out == [("ST", [ron]), ("ST", [other, other2])]  # 같은 역할의 두 자리 중 앞 자리 (먼저 고르는 자리가 가져가 이 자리가 비지 않게)


def test_prefer_moves_to_the_formations_role_and_shows_the_stat(toolbox):  # noqa: F811
    # "RW:크로스"인데 그 포메이션에 RW가 없으면 조용히 버리지 않는다: 없는 자리는 note로, 반영한 자리는 그 능력치 값을 함께
    r = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM", "prefer": "ST:키,DM:키"})
    assert "prefer의 ST는 이 포메이션에 없는 자리" in r["note"] and "키" in r["lineup"][0]["preferred_stat"]
    from fco_meta.chatbot.tools import PREFER_FAMILIES

    assert ("RW", "RM", "RAM") in PREFER_FAMILIES  # 4-2-2-2의 윙은 RM, 4-2-3-1은 RAM


def test_diagnose_team_lists_swaps_that_free_salary(toolbox):  # noqa: F811
    picks = [{"role": "ST", "player": "공격수A", "season": "ICON"}, {"role": "DM", "player": "볼란치L", "season": "ICON"}]
    r = run(toolbox, "diagnose_team", {"picks": picks, "team_color": "아스널", "free_salary": 1})
    slots = {s["role"]: s for s in r["slots"]}
    for s in r["salary_savers"]:
        assert s["saves"] >= 1 and s["salary"] == slots[s["role"]]["rankers_card"]["salary"] - s["saves"]
    assert "salary_savers" not in run(toolbox, "diagnose_team", {"picks": picks, "team_color": "아스널"})



def test_player_detail_keeps_scope_definitions_in_model_view(toolbox):
    from fco_meta.chatbot.tools import for_model

    result = run(toolbox, "get_player_detail", {"name": "볼란치R", "team_color": "아스널"})
    visible = for_model("get_player_detail", result)
    assert visible["players"][0]["scope"] == for_model("scope", result["players"][0]["scope"])
    assert {"season_card", "players_scope", "usage_scope"} <= visible["definitions"].keys()
    missing = run(toolbox, "get_player_detail", {"name": "없는선수xyz", "season": "없는시즌"})
    assert "usage_scope" in missing["definitions"] and not missing["usage"]


def test_diagnose_team_reuses_role_candidates_without_duplicate_cards(toolbox, monkeypatch):  # noqa: F811
    from collections import Counter

    sq = toolbox._squad_setup("아스널", None, False, None, None, "DM,DM", None, None)
    options = sq.slot_options[0][1]
    options.extend(list(options))  # 같은 카드 후보가 여러 경로로 들어와도 순위는 한 번만 센다.
    fits, calls = sq.fits, Counter()

    def counted(sp_id):
        calls[sp_id] += 1
        return fits(sp_id)

    sq.fits = counted
    monkeypatch.setattr(toolbox, "_squad_setup", lambda *args, **kwargs: sq)
    pick = {"role": "DM", "player": "볼란치L", "season": "ICON"}
    single = toolbox.diagnose_team([pick], team_color="아스널")
    assert single["slots"][0]["usage_rank"] == 1
    expected_calls = calls.copy()
    calls.clear()
    double = toolbox.diagnose_team([pick, pick], team_color="아스널")
    assert double["slots"] == single["slots"] * 2
    assert calls == expected_calls and all(n == 1 for n in calls.values())


def test_owned_grade_keeps_market_price_but_costs_nothing(toolbox):  # noqa: F811
    result = run(toolbox, "recommend_squad", {"team_color": "아스널", "slots": "DM,DM", "owned": "볼란치L:ICON:5"})
    owned = next(p for p in result["lineup"] if p["player"] == "볼란치L")
    assert owned["card"]["most_used_grade"] == 5
    assert owned["card"]["price_at_most_used_grade"]["price_bp"] == 900_000_000
    assert result["total_price_bp"] == 300_000_000  # 보유 카드의 시세는 남기되 구매 비용에는 나머지 카드만.
