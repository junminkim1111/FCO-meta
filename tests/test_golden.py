"""Golden-set checks catch a broken squad and a wrong route, without any model."""

import json
import sqlite3

from fco_meta.chatbot.golden import check


class FakeToolbox:
    def __init__(self, result):
        self.result = result
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE card_detail (spid INTEGER, clubs TEXT, nation TEXT)")
        self.conn.execute("INSERT INTO card_detail VALUES (1, '[{\"club\": \"FC 바르셀로나\"}]', '스페인'), (2, '[{\"club\": \"레알 마드리드\"}]', '스페인')")

    def run(self, name, args):
        return json.dumps(self.result, ensure_ascii=False), False


def test_squad_outside_the_team_color_over_budget_and_light_route_fail():
    squad = {"team_color": "FC 바르셀로나", "total_salary": 300, "total_price_bp": 20_000_000_000, "total_price": "200억",
             "lineup": [{"pid": 10, "player": "페드리", "card": {"sp_id": 1}}, {"pid": 20, "player": "벨링엄", "card": {"sp_id": 2}}]}
    expect = {"route": "heavy", "squad": {"team_color": "FC 바르셀로나", "players": 2, "max_price": 15_000_000_000}}
    record = {"outcome": "ok", "route": "Jev → 기본 Flash-Lite", "unverified": ["99%"]}
    results = {name: ok for name, ok, _ in check(expect, record, [("recommend_squad", {})], "총액 200억, 총 급여 300", FakeToolbox(squad))}
    assert results["2명 · 중복 없음"] and results["답에 총액·총 급여"] and results["급여 ≤ 315"]
    assert not results["전원 팀컬러 소속 (카드 상세)"]  # 벨링엄은 바르샤 경력이 없다
    assert not results["총액 ≤ 15,000,000,000"] and not results["라우팅 heavy"] and not results["수치 확인"]


def test_blocked_question_only_checks_the_refusal():
    assert check({"blocked": True}, {"outcome": "ok"}, [], "답", FakeToolbox({})) == [("거절", False, "ok")]


def test_oracle_keeps_missing_cases_unverified():
    from fco_meta.chatbot.golden import report, run_oracle

    rows = run_oracle([(1, {"q": "안녕", "expect": {"no_tools": True}})], FakeToolbox({}))
    assert len(rows) == 1 and rows[0]["checks"] == []
    rendered = report(rows, oracle=True)
    assert "통과 0/1 · 실패 0 · 미검증 1" in rendered
    assert "라우팅·답변은 미검증" in rendered


def test_oracle_cli_returns_failure_and_writes_json(tmp_path):
    from fco_meta.chatbot.golden import main
    from fco_meta.chatbot.tools import Toolbox

    db = tmp_path / "test.sqlite"
    conn = sqlite3.connect(db)
    Toolbox(conn)
    conn.commit()
    conn.close()
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"cases": [{"q": "test", "oracle": [["unused", {}]],
                                           "expect": {"no_tools": True}}]}))
    result = tmp_path / "result.json"
    args = ["--oracle", "--db", str(db), "--cases", str(cases),
            "--out", str(tmp_path / "report.md"), "--json-out", str(result)]
    assert main(args) == 1
    assert json.loads(result.read_text())["rows"][0]["checks"][0][1] is False
    cases.write_text(json.dumps({"cases": [{"q": "test", "oracle": [["unused", {}]],
                                           "expect": {"tools": ["unused"]}}]}))
    assert main(args) == 0
    assert main(args + ["--only", "999"]) == 2
    cases.write_text(json.dumps({"cases": [{"q": "no oracle", "expect": {"no_tools": True}}]}))
    assert main(args) == 2


def test_budget_is_not_relaxed_and_semantic_constraints_are_checked():
    from fco_meta.chatbot.golden import _squad

    result = {"total_salary": 20, "total_price_bp": 105, "budget": {"over_by": "5"},
              "lineup": [{"pid": 1, "role": "GK", "player": "키퍼", "card": {"salary": 20}}],
              "chemistry": {"team_color": "현역", "active": False}}
    checks = _squad({"players": 1, "max_price": 100, "chemistry": "현역", "min_role_salary": {"GK": 25}}, result, None, None)
    failed = [name for name, ok, _ in checks if not ok]
    assert failed == ["총액 ≤ 100", "케미 현역 발동", "GK 급여 ≥ 25"]
