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
        return httpx.Response(200, text=fixture_html("../datacenter/player_list_tc1004_dm_first5.html"))

    http = httpx.Client(base_url="https://fconline.nexon.com", transport=httpx.MockTransport(handler))
    return DatacenterClient(http, min_interval=0, max_retries=0, sleep=lambda s: None)


def test_used_players_ordered_by_usage(tmp_path, fixture_html):
    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    with_names(storage)
    players = used_players(storage.conn, limit=3)
    # 10명 모두 GK(101000001)와 RCB(101000004)를 씀 → 사용 랭커 수 10으로 맨 앞
    assert [(p.name, p.rankers) for p in players[:2]] == [("골키퍼", 10), ("센터백R", 10)]
    assert len(players) == 3 and players[0].sp_ids == (101000001,)


def test_refresh_saves_prices_and_skips_fresh(tmp_path, fixture_html):
    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    with_names(storage)
    db = tmp_path / "db.sqlite"
    searched = []
    now = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)

    first = refresh_used_prices(datacenter(fixture_html, searched), db, limit=2, now=now)
    assert (first.players, first.requests, first.skipped_fresh) == (2, 2, 0)
    assert searched == ["골키퍼", "센터백R"] and first.cards == 10  # 픽스처는 카드 5장
    assert storage.conn.execute("SELECT COUNT(*) FROM card_price WHERE fetched_at = ?", (now.isoformat(),)).fetchone()[0] > 0

    # 픽스처 카드에 랭커가 쓴 카드(sp_id)가 없으면 '최근 갱신'으로 볼 수 없어 다시 받는다 → 실제 카드를 넣어 확인
    storage.conn.executemany(
        "INSERT OR REPLACE INTO card_price (spid, grade, price, fetched_at) VALUES (?, 5, 1000, ?)",
        [(101000001, now.isoformat()), (101000004, now.isoformat())],
    )
    storage.conn.commit()
    searched.clear()
    again = refresh_used_prices(datacenter(fixture_html, searched), db, limit=2, now=now + timedelta(hours=1))
    assert (again.requests, again.skipped_fresh) == (0, 2) and searched == []

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
