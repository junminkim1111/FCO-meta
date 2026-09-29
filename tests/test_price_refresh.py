"""Prices for the players top rankers use (daily step 7)."""

from datetime import datetime, timedelta, timezone

import httpx
from test_top_rankers import setup

from fco_meta.crawler import DatacenterClient
from fco_meta.market.refresh import MAX_AGE, refresh_used_prices, used_players

NAMES = {101000011: "볼란치L", 250000009: "볼란치R", 101000001: "골키퍼", 101000004: "센터백R"}


def with_names(storage):
    storage.conn.executemany("INSERT OR REPLACE INTO meta_spid VALUES (?, ?)", list(NAMES.items()))
    storage.conn.commit()


def datacenter(fixture_html, searched, fail=()):
    def handler(request):
        form = dict(httpx.QueryParams(request.content.decode()))
        name = form.get("strPlayerName", "")
        searched.append(name)
        if name in fail:
            return httpx.Response(500)
        # 픽스처 카드 5장 중 1장을 테스트 랭커들이 쓰는 골키퍼 카드(101000001)로 바꿔 둔다
        html = fixture_html("../datacenter/player_list_tc1004_dm_first5.html")
        return httpx.Response(200, text=html.replace("area_playerunit_856234378", "area_playerunit_101000001"))

    http = httpx.Client(base_url="https://fconline.nexon.com", transport=httpx.MockTransport(handler))
    return DatacenterClient(http, min_interval=0, max_retries=0, sleep=lambda s: None)


def test_used_players_ordered_by_usage(tmp_path, fixture_html):
    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    with_names(storage)
    players = used_players(storage.conn, limit=3)
    # 10명 모두 GK(101000001)와 RCB(101000004)를 씀 → 사용 랭커 수 10으로 맨 앞
    assert [(p.name, p.rankers) for p in players[:2]] == [("골키퍼", 10), ("센터백R", 10)]
    assert len(players) == 3 and players[0].sp_ids == (101000001,)


def test_refresh_keeps_only_used_seasons_and_skips_fresh(tmp_path, fixture_html):
    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    with_names(storage)
    db = tmp_path / "db.sqlite"
    searched = []
    now = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)

    first = refresh_used_prices(datacenter(fixture_html, searched), db, limit=2, now=now)
    assert (first.players, first.requests, first.skipped_fresh) == (2, 2, 0)
    assert searched == ["골키퍼", "센터백R"]
    # 검색 응답은 선수당 5장씩(10장)이지만 랭커가 쓴 시즌 카드(골키퍼 101000001)만 저장
    assert (first.cards_seen, first.cards) == (10, 1)
    stored = {r[0] for r in storage.conn.execute("SELECT DISTINCT spid FROM card_price WHERE fetched_at = ?", (now.isoformat(),))}
    assert stored == {101000001}
    grades = storage.conn.execute("SELECT COUNT(*) FROM card_price WHERE spid = 101000001").fetchone()[0]
    assert grades == 13  # 강화 1~13은 모두 저장

    # 골키퍼는 방금 받았으니 건너뛰고, 사용 카드가 검색에 없던 센터백R은 다시 시도
    searched.clear()
    again = refresh_used_prices(datacenter(fixture_html, searched), db, limit=2, now=now + timedelta(hours=1))
    assert (again.requests, again.skipped_fresh) == (1, 1) and searched == ["센터백R"]

    later = refresh_used_prices(datacenter(fixture_html, searched), db, limit=2, now=now + MAX_AGE + timedelta(hours=1))
    assert later.requests == 2  # 오래되면 다시 받음


def test_refresh_continues_after_a_failure(tmp_path, fixture_html):
    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    with_names(storage)
    searched = []
    r = refresh_used_prices(datacenter(fixture_html, searched, fail={"골키퍼"}), tmp_path / "db.sqlite", limit=2)
    assert (r.failed, r.requests) == (1, 1) and searched == ["골키퍼", "센터백R"]


def test_no_stats_means_nothing_to_refresh(tmp_path, fixture_html):
    searched = []
    r = refresh_used_prices(datacenter(fixture_html, searched), tmp_path / "empty.sqlite")
    assert r.players == 0 and searched == []
