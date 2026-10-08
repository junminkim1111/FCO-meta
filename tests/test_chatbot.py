import json

import pytest
from test_analytics import db  # noqa: F401  (fixture: 아스널 4명 랭커, usage_stats 빌드 완료)

from fco_meta.chatbot import TOOLS, RuleBot, Toolbox
from fco_meta.chatbot.__main__ import main


@pytest.fixture
def toolbox(db):  # noqa: F811
    tb = Toolbox(db.conn, min_sample=1)
    # 볼란치 카드 시세 (강화 5 = 테스트 스쿼드의 강화)
    db.conn.executemany(
        "INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (?, 5, ?, '2026-09-28T12:00:00+00:00')",
        [(101000011, 900_000_000), (250000009, 300_000_000), (300000009, 50_000_000)],
    )
    # 카드 상세: 테스트 랭커들이 쓴 카드는 모두 아스널 경력 (스쿼드 추천은 클럽 팀컬러면 그 경력 카드만 쓴다)
    db.conn.executemany(
        "INSERT INTO card_detail (spid, grade, positions, traits, summary, stats, clubs, fetched_at, nation)"
        " VALUES (?, 1, '{}', '[]', '{}', '{}', ?, 'x', '잉글랜드')",
        [(sp_id, '[{"years": "2020 ~", "club": "아스널", "loan": ""}]')
         for (sp_id,) in db.conn.execute("SELECT DISTINCT sp_id FROM usage_stats")],
    )  # fmt: skip
    db.conn.commit()
    return tb


def test_tool_schemas_are_plain_json_schema():
    for tool in TOOLS:
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert set(schema["required"]) <= set(schema["properties"])
        for prop in schema["properties"].values():
            assert isinstance(prop["type"], str)  # null 유니언 없음 (Gemini 호환)


def test_recommend_players_with_prices_and_budget(toolbox):
    content, is_error = toolbox.run("recommend_players", {"team_color": "아스날", "formation": "4-2-3-1", "role": "볼란치"})
    r = json.loads(content)
    assert not is_error and r["team_color"] == "아스널" and r["role"] == "DM" and r["sample_size"] == 3
    assert [(p["name"], p["rankers"]) for p in r["players"]] == [("볼란치R", 2), ("볼란치L", 2)]
    cards = r["players"][0]["cards"]
    assert [(c["sp_id"], c["most_used_grade"], c["price_at_most_used_grade"]["price"]) for c in cards] == [
        (250000009, 5, "3억"), (300000009, 5, "5,000만"),
    ]  # fmt: skip
    assert all(c["season_img"] is None for c in cards)  # 메타데이터에 이미지 없음

    r = json.loads(toolbox.run("recommend_players", {"team_color": "아스널", "formation": "4-2-3-1", "role": "DM", "max_price_bp": 1e8})[0])
    # 1억 이하 = S300 카드만 → 볼란치R(그 카드 사용 1명)만 남음, 볼란치L(9억)은 제외
    assert [(p["name"], [c["sp_id"] for c in p["cards"]]) for p in r["players"]] == [("볼란치R", [300000009])]
    assert r["budget"]["max_price"] == "1억"


def test_tool_errors_are_reported(toolbox):
    content, is_error = toolbox.run("recommend_players", {"team_color": "없는팀", "role": "DM"})
    assert is_error and "알 수 없는 팀컬러" in json.loads(content)["error"]
    content, is_error = toolbox.run("recommend_players", {"team_color": "아스널", "role": "리베로"})
    assert is_error and "알 수 없는 역할" in json.loads(content)["error"]
    assert toolbox.run("nope", {})[1] is True
    assert toolbox.run("recommend_players", {"bad_arg": 1})[1] is True


def test_resolve_terms_aliases(toolbox):
    r = json.loads(toolbox.run("resolve_terms", {"team_color": "맨유", "role": "수미"})[0])
    assert r["team_color"]["name"] == "맨체스터 유나이티드" and r["role"]["code"] == "DM"
    r = json.loads(toolbox.run("resolve_terms", {"team_color": "맨체스터"})[0])
    assert r["team_color"] is None and any("맨체스터 시티" in c for c in r["team_color_candidates"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("아스날 4-2-3-1 볼란치 2명 추천해줘", dict(team_color="아스널", formation="4-2-3-1", role="DM", top_n=2)),
        ("레알 마드리드 4-4-2(2) 공미 3명", dict(team_color="레알 마드리드", formation="4-4-2(2)", role="CAM", top_n=3)),
        ("아스널 CDM 1억 5,000만 이하", dict(team_color="아스널", role="DM", max_price_bp=150_000_000)),
        ("아스널 볼란치 5억 이하 엄격하게", dict(role="DM", max_price_bp=500_000_000, strict=True)),
        ("안녕하세요", dict(team_color=None, role=None, formation=None, top_n=None, max_price_bp=None)),
    ],
)
def test_parse(toolbox, text, expected):
    q = RuleBot(toolbox).parse(text)
    for key, value in expected.items():
        assert getattr(q, key) == value, key


def test_rule_bot_recommend(toolbox):
    answer = RuleBot(toolbox).ask("아스날 4-2-3-1 볼란치 1명 추천해줘")
    assert answer.tool == "recommend_players" and answer.tool_input["top_n"] == 3  # 다음 후보용으로 여유 있게
    assert "아스널 4-2-3-1 볼란치(DM) 추천 — 랭커 3명 스쿼드 기준 (2026-09-28 20:00 기준)" in answer.text
    assert "1. 볼란치R — 2명 사용 (66.7%)" in answer.text
    assert "S250 1명 (주로 5강, 시세 3억)" in answer.text
    assert "다음 후보: 볼란치L (66.7%)" in answer.text
    assert "표본이 3명으로 적어 참고용" in answer.text and "전체 포메이션" not in answer.text


def test_rule_bot_budget_and_fallback(toolbox):
    toolbox.min_sample = 10
    text = RuleBot(toolbox).ask("아스널 4-2-3-1 볼란치 1억 이하").text
    # 4-2-3-1 표본 3명 < 10명 → 전체 포메이션(4명)으로 폴백
    assert "전체 포메이션으로 집계" in text and "예산 1억 이하" in text
    assert "볼란치R — 2명 사용 (50.0%) · 예산 내 카드 사용 1명" in text and "볼란치L" not in text


def test_rule_bot_other_intents(toolbox):
    bot = RuleBot(toolbox)
    detail = bot.ask("볼란치L 사용률 알려줘")
    # "볼란치"가 역할로도 잡히므로 역할 추천이 우선 — 팀컬러가 없으면 전체 랭커 기준 (여기선 필터 없는 수집이 없어 데이터 없음)
    assert detail.tool == "recommend_players" and detail.tool_input["team_color"] is None
    assert "전체 랭커 데이터가 없습니다" in detail.text

    assert bot.ask("아스널 포메이션 보여줘").tool == "list_formations"
    assert "표본이 많은 조합" in bot.ask("어떤 데이터 있어?").text
    assert "이렇게 물어보세요" in bot.ask("안녕").text
    assert "데이터가 없습니다" in bot.ask("맨유 공미 추천").text


def test_cli_rules_and_tool(db, tmp_path, capsys):  # noqa: F811
    path = str(tmp_path / "db.sqlite")
    assert main(["--db", path, "--backend", "rules", "--ask", "아스널 4-2-3-1 볼란치 1명 추천"]) == 0
    assert "볼란치R" in capsys.readouterr().out
    assert main(["--db", path, "--tool", "resolve_terms", '{"role": "공미"}']) == 0
    assert json.loads(capsys.readouterr().out)["role"]["code"] == "CAM"


def test_rule_bot_player_detail(toolbox):
    toolbox.conn.execute("INSERT INTO meta_spid VALUES (101000004, '가브리엘 마갈량이스')")
    answer = RuleBot(toolbox).ask("마갈량이스 사용률 알려줘")  # 이름 일부로도 찾는다
    assert answer.tool == "get_player_detail" and answer.tool_input == {"name": "마갈량이스"}
    assert "가브리엘 마갈량이스 — 랭커 사용 현황" in answer.text
    assert "아스널 4-2-3-1 CB · ICON: 3/3명 (100.0%)" in answer.text  # 모든 테스트 스쿼드의 RCB


def test_recommend_cards_carry_season_icon(db):  # noqa: F811
    db.conn.execute("UPDATE meta_season SET season_img = 'https://example.test/s250.png' WHERE season_id = 250")
    db.conn.commit()
    r = json.loads(Toolbox(db.conn, min_sample=1).run("recommend_players", {"team_color": "아스널", "formation": "4-2-3-1", "role": "DM"})[0])
    imgs = {c["sp_id"]: c["season_img"] for p in r["players"] for c in p["cards"]}
    assert imgs[250000009] == "https://example.test/s250.png" and imgs[300000009] is None


def test_recommend_players_new_traits(toolbox):
    conn = toolbox.conn
    conn.execute("UPDATE card_detail SET traits = '[\"라인 브레이커\", \"긴 패스 선호\"]' WHERE spid = 250000009")
    conn.execute("UPDATE card_detail SET traits = '[\"트릭스터\", \"라인 브레이커\"]' WHERE spid = 101000011")
    conn.execute("INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (250000009, 8, 2000000000, 'x')")
    args = {"team_color": "아스널", "formation": "4-2-3-1", "role": "DM"}
    cards = lambda r: [(p["name"], [c["sp_id"] for c in p["cards"]]) for p in r["players"]]  # noqa: E731

    r = json.loads(toolbox.run("recommend_players", {**args, "traits": "라브"})[0])  # 달린 = 원래 가진 카드만
    assert cards(r) == [("볼란치R", [250000009]), ("볼란치L", [101000011])]
    r = json.loads(toolbox.run("recommend_players", {**args, "traits": "라브 트릭"})[0])
    assert cards(r) == [("볼란치L", [101000011])]
    # 달 수 있는 = 하나 모자라면 8강 이상 기준 → 그 강화 시세로 예산 비교 (S250 8강 20억)
    r = json.loads(toolbox.run("recommend_players", {**args, "traits": "라인브레이커,트릭스터", "can_add_trait": True})[0])
    card = r["players"][0]["cards"][0]
    assert card["added_trait"] == "트릭스터" and card["price_for_traits"]["grade"] == 8
    r = json.loads(toolbox.run("recommend_players", {**args, "traits": "라브,트릭", "can_add_trait": True, "max_price_bp": 1e9})[0])
    assert cards(r) == [("볼란치L", [101000011])]
    assert toolbox.run("recommend_players", {**args, "traits": "없는특성"})[1] is True


def test_common_cards_drop_rarely_used_cards():
    from fco_meta.chatbot.tools import _common_cards

    cards = [{"sp_id": 1, "rankers": 300}, {"sp_id": 2, "rankers": 36}, {"sp_id": 3, "rankers": 1}]
    assert [c["sp_id"] for c in _common_cards(cards, 337)] == [1, 2]  # 1명만 쓴 카드(UP 3강 같은)는 빠짐
    assert [c["sp_id"] for c in _common_cards(cards, 337, keep=lambda c: c["sp_id"] == 3)] == [1, 2, 3]  # 시즌 단일 카드는 남김
    assert [c["sp_id"] for c in _common_cards(cards[2:], 337)] == [3]  # 가장 많이 쓴 카드는 항상 남김


def test_tool_args_with_markup_debris(toolbox):
    # DeepSeek이 보낸 실제 인자 이름: 백틱·「 string=」 찌꺼기, 대문자
    args = {"team_color`": "아스널", "Role": "DM", "top_n」 string=": 1, "max_price_bp_rankers": 10**10}
    content, is_error = toolbox.run("recommend_players", args)
    assert not is_error and len(json.loads(content)["players"]) == 1


def test_squad_spends_salary_left_under_the_cap():
    from fco_meta.chatbot.tools import _choose

    def opt(pid, usage, salary):
        return {"entry": {"pid": pid, "usage_rate": usage}, "card": {}, "price": None, "salary": salary, "tags": set()}

    # 11자리, 자리마다 주전 선수의 급여 20·28 카드 + 덜 쓰인 선수 둘 → 조합이 많아 탐욕 교체 경로
    slots = [("ST", [opt(i, 0.5, 20), opt(i, 0.5, 28), opt(100 + i, 0.1, 25), opt(200 + i, 0.05, 25)]) for i in range(11)]
    chosen = _choose(slots, {"salary": 300})
    assert [c["entry"]["pid"] for c in chosen] == list(range(11))  # 주전은 그대로
    assert sum(c["salary"] for c in chosen) == 300  # 남는 급여로 같은 선수의 급여 높은 카드로 올림 (220 → 300)
