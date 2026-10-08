from datetime import date

import httpx
import pytest

from fco_meta.crawler import DatacenterClient
from fco_meta.market import (
    RESULT_CAP,
    MarketStorage,
    PlayerSearch,
    fetch_price_history,
    format_bp,
    parse_bp,
    parse_player_list,
    parse_price_graph,
    resolve_role,
    search_all,
)
from fco_meta.market.models import Card


def test_parse_player_list(fixture_html):
    cards = parse_player_list(fixture_html("player_list_tc1004_dm_first5.html"))

    assert len(cards) == 5
    rice = cards[0]
    assert (rice.spid, rice.name, rice.season_code) == (854234378, "데클런 라이스", "26TOTY")
    assert (rice.main_position, rice.ovr, rice.salary) == ("CM", 125, 32)
    assert (rice.rating, rice.rating_count) == (7.8, 111)
    assert sorted(rice.prices) == list(range(1, 14))
    assert rice.prices[1] == 1_990_000
    assert rice.prices[8] == 506_000_000
    assert (rice.season_id, rice.pid) == (854, 234378)
    assert cards[3].main_position == "CDM" and cards[3].name == "파트리크 비에이라"


def test_zero_price_means_unknown():
    html = """
    <div id="area_playerunit_101000001"><div class="info_top"><div class="name">테스트</div></div>
      <div class="td td_ar_bp"><span class="span_bp0">-</span>
        <span class="span_bp1" title="0">0</span><span class="span_bp2" title="1,000">1,000</span></div>
    </div>"""
    card = parse_player_list(html)[0]
    assert card.prices == {1: None, 2: 1000}
    assert card.rating is None and card.season_code is None


def test_parse_price_graph_assigns_years(fixture_html):
    history = parse_price_graph(fixture_html("player_price_graph_100005471_1.html"), 100005471, 1, date(2026, 9, 28))

    assert history.current_price == 3_170_000
    assert len(history.points) == 365
    assert history.points[0] == (date(2025, 9, 28), 129_200)
    assert history.points[-1] == (date(2026, 9, 27), 3_198_200)
    dates = [d for d, _ in history.points]
    assert dates == sorted(dates) and len(set(dates)) == 365


def test_price_graph_year_rollover():
    html = 'var json1 = { "time": [ "12.30", "12.31", "1.01", ], "value": [ "10", "20", "30", ] };'
    points = parse_price_graph(html, 1, 1, date(2027, 1, 2)).points
    assert points == [(date(2026, 12, 30), 10), (date(2026, 12, 31), 20), (date(2027, 1, 1), 30)]


def test_search_form():
    form = PlayerSearch(team_color_id=1004, positions=(9, 10, 11), salary=(10, 20)).to_form()
    assert form == {
        "n4PageNo": "1",
        "teamcolorid": "1004",
        "strPosition": ",9,10,11,",
        "n4SalaryMin": "10",
        "n4SalaryMax": "20",
    }


@pytest.mark.parametrize("text,role", [("볼란치", "DM"), ("CDM", "DM"), ("cdm", "DM"), ("센터백", "CB"), ("ST", "ST")])
def test_resolve_role(text, role):
    assert resolve_role(text) == role


def test_resolve_unknown_role():
    assert resolve_role("리베로") is None
    assert resolve_role("윙어") is None  # 여러 역할 → resolve_roles


@pytest.mark.parametrize(
    "text,roles",
    [("윙어", ("RW", "LW", "RM", "LM", "RAM", "LAM")), ("공격수", ("ST", "CF", "RW", "LW")), ("볼란치", ("DM",)), ("RW, LW", ("RW", "LW")),
     ("rw+lw+rw", ("RW", "LW")), ("리베로", None), ("RW,리베로", None)],
)  # fmt: skip
def test_resolve_roles(text, roles):
    from fco_meta.market.roles import resolve_roles

    assert resolve_roles(text) == roles


@pytest.mark.parametrize(
    "text,value",
    [("5억", 500_000_000), ("1억 2,000만", 120_000_000), ("1.5억", 150_000_000), ("3,500,000", 3_500_000), ("300만 BP", 3_000_000)],
)
def test_parse_bp(text, value):
    assert parse_bp(text) == value


def test_parse_bp_rejects_garbage():
    with pytest.raises(ValueError):
        parse_bp("많이")


def test_format_bp():
    assert format_bp(150_000_000) == "1억 5,000만"
    assert format_bp(12_000_000_000) == "120억"
    assert format_bp(1_250) == "1,250"
    assert format_bp(None) == "-"


def _unit(spid: int) -> str:
    return (
        f'<div id="area_playerunit_{spid}"><div class="info_top"><div class="name">p{spid}</div></div>'
        f'<div class="td td_ar_bp"><span class="span_bp1" title="{spid}">x</span></div></div>'
    )


def _client(handler):
    http = httpx.Client(base_url="https://fconline.nexon.com", transport=httpx.MockTransport(handler))
    return DatacenterClient(http, min_interval=0, sleep=lambda s: None)


def test_search_all_splits_by_salary_when_capped():
    """급여 0~99 중 30~39에만 250장이 있는 상황: 200장 제한을 넘는 구간만 계속 나눈다."""
    cards_by_salary = {s: [s * 1000 + i for i in range(25)] for s in range(30, 40)}  # 250장
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        requests.append(form)
        lo, hi = int(form.get("n4SalaryMin", 0)), int(form.get("n4SalaryMax", 99))
        spids = [spid for s, ids in cards_by_salary.items() if lo <= s <= hi for spid in ids]
        return httpx.Response(200, text="".join(_unit(s) for s in spids[:RESULT_CAP]))

    result = search_all(_client(handler), PlayerSearch(team_color_id=1004))

    assert len(result.cards) == 250
    assert not result.truncated
    assert requests[0]["teamcolorid"] == "1004" and "n4SalaryMin" not in requests[0]
    assert all(r["teamcolorid"] == "1004" for r in requests)


def test_search_all_single_request_when_under_cap():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=_unit(1) + _unit(2))

    result = search_all(_client(handler), PlayerSearch(name="사카"))
    assert (len(result.cards), result.requests, len(calls)) == (2, 1, 1)


def test_search_all_marks_truncation():
    result = search_all(
        _client(lambda r: httpx.Response(200, text="".join(_unit(i) for i in range(RESULT_CAP)))),
        PlayerSearch(positions=(9, 10, 11)),
    )
    assert result.truncated


def test_search_all_respects_request_limit():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="".join(_unit(i) for i in range(RESULT_CAP)))

    result = search_all(_client(handler), PlayerSearch(team_color_id=1004), max_requests=5)
    assert result.truncated
    assert len(calls) == result.requests == 5


def test_fetch_price_history_posts_form(fixture_html):
    seen = {}

    def handler(request):
        seen["form"] = dict(httpx.QueryParams(request.content.decode()))
        seen["method"] = request.method
        return httpx.Response(200, text=fixture_html("player_price_graph_100005471_1.html"))

    history = fetch_price_history(_client(handler), 100005471, 1, date(2026, 9, 28))
    assert seen == {"method": "POST", "form": {"spid": "100005471", "n1strong": "1"}}
    assert history.current_price == 3_170_000


def test_storage_find_cards(tmp_path):
    storage = MarketStorage(tmp_path / "db.sqlite")
    cheap = Card(101000001, "싼 볼란치", "SEA", "CDM", 110, 20, 8.0, 10, {5: 30_000_000})
    pricey = Card(102000001, "비싼 볼란치", "ICON", "CDM", 125, 35, 9.0, 50, {5: 900_000_000})
    other = Card(103000001, "다른 팀", "SEA", "CDM", 115, 25, 8.0, 10, {5: 10_000_000})
    storage.save_cards([cheap, pricey], team_color_id=1004, role="DM", fetched_at="2026-09-28T00:00:00+00:00")
    storage.save_cards([other], team_color_id=1016, role="DM", fetched_at="2026-09-28T00:00:00+00:00")
    # 나중에 다시 수집된 가격이 최신 가격으로 쓰인다
    storage.save_cards([Card(**{**cheap.__dict__, "prices": {5: 40_000_000}})], fetched_at="2026-09-29T00:00:00+00:00")

    rows = storage.find_cards(grade=5, team_color_id=1004, role="DM")
    assert [(r["name"], r["price"]) for r in rows] == [("싼 볼란치", 40_000_000), ("비싼 볼란치", 900_000_000)]
    assert [r["name"] for r in storage.find_cards(grade=5, team_color_id=1004, max_price=100_000_000)] == ["싼 볼란치"]
    assert storage.find_cards(grade=8, team_color_id=1004) == []
    assert [r["name"] for r in storage.find_cards(grade=5, role="DM", sort="ovr")] == ["비싼 볼란치", "다른 팀", "싼 볼란치"]
