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
    assert text.startswith("전체 랭커 4-2-3-1 볼란치(DM) 추천 — 랭킹 상위 20명 중 랭커")
    assert "매일 수집: 랭킹 상위 20명, 그중 스쿼드 10명" in bot.ask("어떤 데이터 있어?").text

    formations = bot.ask("랭커 포메이션 알려줘")
    assert formations.tool == "list_formations" and "랭킹 상위 20명 기준" in formations.text
