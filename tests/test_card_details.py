"""Card details from the datacenter card popup (daily step 8)."""

import json
from datetime import datetime, timedelta, timezone

import httpx
from test_top_rankers import setup

from fco_meta.crawler import DatacenterClient
from fco_meta.market.details import MAX_AGE, cards_needing_details, refresh_card_details
from fco_meta.market.parser import parse_player_preview

NOW = datetime(2026, 9, 30, 3, 0, tzinfo=timezone.utc)


def test_parse_player_preview(fixture_html):
    d = parse_player_preview(fixture_html("player_preview_854234378_1.html"), 854234378, 1)
    assert d.positions == {"CM": 125, "CDM": 125}
    assert (d.birth, d.height, d.weight, d.body_type, d.skill_moves) == ("1999-01-14", 188, 80, "보통", 3)
    assert (d.left_foot, d.right_foot, d.reputation) == (3, 5, "월드클래스")
    assert d.traits == ["커맨더", "예리한 감아차기", "장거리 스로잉", "긴 패스 선호", "강철몸", "플레이 메이커"]
    assert d.summary == {"스피드": 121, "슛": 116, "패스": 128, "드리블": 121, "수비": 124, "피지컬": 125}
    assert len(d.stats) == 34 and d.stats["속력"] == 124 and d.stats["GK 위치 선정"] == 22
    assert d.clubs[0] == {"years": "2023 ~", "club": "아스널", "loan": ""}


def datacenter(fixture_html, requested, fail=()):
    def handler(request):
        form = dict(httpx.QueryParams(request.content.decode()))
        assert request.url.path == "/datacenter/PlayerPreView" and form["n1strong"] == "1"
        requested.append(int(form["spid"]))
        if int(form["spid"]) in fail:
            return httpx.Response(500)
        return httpx.Response(200, text=fixture_html("player_preview_854234378_1.html"))

    http = httpx.Client(base_url="https://fconline.nexon.com", transport=httpx.MockTransport(handler))
    return DatacenterClient(http, min_interval=0, max_retries=0, sleep=lambda s: None)


def test_refresh_fetches_most_used_first_and_resumes(tmp_path, fixture_html):
    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    targets = cards_needing_details(storage.conn, NOW)
    assert targets[:5] == [101000001, 101000003, 101000004, 101000006, 101000007]  # 10명 모두 쓰는 GK·수비 4명이 먼저 (같으면 spid 순)
    storage.close()

    requested = []
    r = refresh_card_details(datacenter(fixture_html, requested, fail={targets[1]}), tmp_path / "db.sqlite", limit=3, now=NOW)
    assert (r.targets, r.fetched, r.failed, r.remaining) == (len(targets), 2, 1, len(targets) - 3)
    assert requested == targets[:3]

    # 다음 실행: 받은 카드는 건너뛰고 나머지부터 (실패한 카드는 다시 시도)
    requested.clear()
    r = refresh_card_details(datacenter(fixture_html, requested), tmp_path / "db.sqlite", limit=100, now=NOW)
    assert requested[0] == targets[1] and targets[0] not in requested and r.remaining == 0

    # 30일이 지나면 다시 받는다
    later = NOW + MAX_AGE + timedelta(days=1)
    storage2 = type(storage)(tmp_path / "db.sqlite")
    assert len(cards_needing_details(storage2.conn, later)) == len(targets)
    row = storage2.conn.execute("SELECT stats, traits FROM card_detail WHERE spid = 101000001").fetchone()
    assert json.loads(row[0])["속력"] == 124 and "커맨더" in json.loads(row[1])
    storage2.close()
