"""Daily path: unfiltered TOP N crawl → displayed team color membership → squads → all-rankers stats."""

import httpx
from test_pipeline import F442, F4231, LATER, FakeApi, detail

from fco_meta.analytics import ALL_RANKERS, UsageStore, top_players
from fco_meta.chatbot import RuleBot, Toolbox
from fco_meta.crawler import DatacenterClient, RankQuery, crawl_rankings
from fco_meta.crawler.membership import record_displayed_membership
from fco_meta.openapi import CallBudget, NexonOpenApiClient
from fco_meta.pipeline import PipelineStore, SquadCollector, select_top_targets
from fco_meta.storage import Storage, latest_unfiltered_snapshot, unfiltered_coverage


def setup(tmp_path, fixture_html, top=10):
    storage = Storage(tmp_path / "db.sqlite")
    page = httpx.Client(
        base_url="https://fconline.nexon.com",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=fixture_html("rank_inner_1vs1_p1.html"))),
    )
    crawl_rankings(DatacenterClient(page, min_interval=0, sleep=lambda s: None), storage, RankQuery(), max_pages=1)
    as_of, covered = latest_unfiltered_snapshot(storage.conn)
    record_displayed_membership(storage.conn, as_of, "1vs1")

    targets = select_top_targets(storage.conn, top)
    fake = FakeApi({}, {}, {})
    for t in targets:
        ouid = f"ouid-{t.rank}"
        fake.users[t.nickname] = ouid
        fake.matches[ouid] = [f"m{t.rank}"]
        squad = F4231 if t.formation == "4-2-3-1" else F442
        fake.details[f"m{t.rank}"] = detail(f"m{t.rank}", "2026-09-28T09:00:00", ouid, squad)
    http = httpx.Client(base_url="https://open.api.nexon.com", transport=httpx.MockTransport(fake))
    api = NexonOpenApiClient("k", http, budget=CallBudget(storage.conn), sleep=lambda s: None)
    SquadCollector(api, PipelineStore(storage.conn), now=LATER).run(targets)
    UsageStore(storage.conn).build_all(as_of)
    return storage, as_of, targets


def test_top_targets_limited_to_unfiltered_coverage(tmp_path, fixture_html):
    storage, as_of, targets = setup(tmp_path, fixture_html, top=10)
    assert unfiltered_coverage(storage.conn, as_of) == 20
    assert [t.rank for t in targets] == list(range(1, 11))
    assert len(select_top_targets(storage.conn, 330)) == 20  # 크롤링한 20위까지만


def test_all_rankers_and_team_color_stats(tmp_path, fixture_html):
    storage, as_of, targets = setup(tmp_path, fixture_html, top=10)
    n4231 = sum(t.formation == "4-2-3-1" for t in targets)

    all_dm = top_players(storage.conn, ALL_RANKERS, "4-2-3-1", "DM", min_sample=1)
    crawled_4231 = storage.conn.execute("SELECT COUNT(*) FROM ranker_snapshot WHERE formation = '4-2-3-1'").fetchone()[0]
    # 표본 = 스쿼드를 받은 상위 10명 중 4-2-3-1, 조합 랭커 = 크롤링한 20명 중 4-2-3-1
    assert (all_dm.sample_size, all_dm.combo_rankers) == (n4231, crawled_4231) and crawled_4231 > n4231
    assert all_dm.players[0].usage_rate == 1.0  # 테스트 스쿼드의 볼란치는 모두 같음

    everyone = top_players(storage.conn, ALL_RANKERS, "*", "ST", min_sample=1)
    assert everyone.sample_size == 10  # 포메이션 무관 상위 10명

    # 표시 팀컬러로 추정한 소속: 유벤투스(2, 10위) → 표본 2
    juve = top_players(storage.conn, 1021, "*", "GK", min_sample=1)
    assert juve.sample_size == 2


def test_chatbot_without_team_color_uses_all_rankers(tmp_path, fixture_html):
    storage, as_of, _ = setup(tmp_path, fixture_html, top=10)
    bot = RuleBot(Toolbox(storage.conn, min_sample=1))

    text = bot.ask("4-2-3-1 볼란치 추천").text
    assert text.startswith("전체 랭커 4-2-3-1 볼란치(DM) 추천 — 랭킹 상위 10명 중 랭커")  # 랭킹은 20명, 스쿼드는 상위 10명
    assert "매일 수집: 랭킹 상위 20명, 그중 스쿼드 10명" in bot.ask("어떤 데이터 있어?").text

    formations = bot.ask("랭커 포메이션 알려줘")
    assert formations.tool == "list_formations" and "랭킹 상위 20명 기준" in formations.text


def test_every_listed_combo_is_accepted_by_recommend(tmp_path, fixture_html):
    """list_available_data가 돌려준 이름(예: '전체 랭커')을 그대로 recommend_players에 넘겨도 동작해야 한다."""
    import json

    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    tb = Toolbox(storage.conn, min_sample=1)
    combos = json.loads(tb.run("list_available_data", {})[0])["combos"]
    assert any(c["team_color"] == "전체 랭커" for c in combos)
    for c in combos:
        formation = None if c["formation"] == "전체" else c["formation"]
        content, is_error = tb.run("recommend_players", {"team_color": c["team_color"], "formation": formation, "role": "GK"})
        assert not is_error, (c, content)
        assert json.loads(content)["sample_size"] > 0


def test_all_ranker_aliases(tmp_path, fixture_html):
    import json

    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    tb = Toolbox(storage.conn, min_sample=1)
    for alias in (None, "", "전체", "전체 랭커", "전체랭커", "상위 랭커", "ALL"):
        r = json.loads(tb.run("recommend_players", {"team_color": alias, "role": "GK"})[0])
        assert r["team_color"] == "전체 랭커", alias
    bad = json.loads(tb.run("recommend_players", {"team_color": "없는팀", "role": "GK"})[0])
    assert "team_color를 생략" in bad["error"]


def test_same_name_club_and_nation_resolves_to_the_one_with_data(tmp_path, fixture_html):
    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    bot = RuleBot(Toolbox(storage.conn, min_sample=1))
    # "대한민국"은 클럽(974)·국가(2001) 둘 다 있음. 랭커 소속은 국가 팀컬러(엠블럼 없음)
    text = bot.ask("대한민국 골키퍼 추천").text
    assert text.startswith("대한민국(국가) 전체 포메이션 골키퍼(GK) 추천"), text
    assert "1. " in text


def test_empty_role_reports_sample(tmp_path, fixture_html):
    import json

    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    tb = Toolbox(storage.conn, min_sample=1)
    # 테스트 스쿼드(4-2-3-1 / 4-4-2)에는 LWB가 없음 → 표본 수와 함께 "선수 없음"
    r = json.loads(tb.run("recommend_players", {"role": "LWB"})[0])
    assert r["players"] == [] and r["sample_size"] == 10 and "표본 10명" in r["note"]
    # 3-4-3은 데이터가 없어도 전체 포메이션으로 대체되므로, 대체할 데이터도 없는 팀컬러(첼시)로 확인
    r = json.loads(tb.run("recommend_players", {"role": "DM", "team_color": "첼시"})[0])
    assert r["players"] == [] and r["sample_size"] == 0 and "수집·집계되지 않음" in r["note"]


def test_team_color_icons(tmp_path, fixture_html):
    storage, _, _ = setup(tmp_path, fixture_html, top=5)
    icons = Toolbox(storage.conn).team_color_icons()
    assert icons["FC 바르셀로나"] == "crests/light/medium/l241.png"  # 클럽 엠블럼
    assert icons["대한민국(국가)"] == "countries/largeflags/f_167.png"  # 국가는 국기 (이름은 분포 화면과 같은 표기)
    assert icons["프랑스(국가)"] == "countries/largeflags/f_18.png"

    # 국기 칸이 생기기 전의 DB: 열을 다시 만들고, 그 전에도 클럽 엠블럼은 그대로 나온다
    storage.conn.execute("ALTER TABLE ranker_snapshot DROP COLUMN team_color_flag")
    old = Toolbox(storage.conn).team_color_icons()
    assert "대한민국(국가)" not in old and old["FC 바르셀로나"] == "crests/light/medium/l241.png"
    storage.close()
    reopened = Storage(tmp_path / "db.sqlite")
    assert "team_color_flag" in {r[1] for r in reopened.conn.execute("PRAGMA table_info(ranker_snapshot)")}
