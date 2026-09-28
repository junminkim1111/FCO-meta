import httpx
import pytest

from fco_meta.crawler import DatacenterClient, RankQuery


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(handler, clock, **kw):
    http = httpx.Client(base_url="https://fconline.nexon.com", transport=httpx.MockTransport(handler))
    return DatacenterClient(http, sleep=clock.sleep, clock=clock.time, **kw)


def test_iterates_until_last_page(fixture_html):
    pages = {"1": "rank_inner_1vs1_arsenal_4231_p1.html", "5": "rank_inner_1vs1_arsenal_4231_p5.html"}
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["n4pageno"]
        requested.append(page)
        return httpx.Response(200, text=fixture_html(pages.get(page, "rank_inner_1vs1_arsenal_4231_p1.html")))

    clock = FakeClock()
    client = make_client(handler, clock)
    result = list(client.iter_rank_pages(RankQuery(team_color_id=1004, formation="4-2-3-1")))

    assert requested == ["1", "2", "3", "4", "5"]  # total 94명 → 5페이지에서 멈춤
    assert len(result) == 5
    assert clock.sleeps == [2.0] * 4  # 요청 간격 유지


def test_stops_on_empty_page_and_respects_max_pages(fixture_html):
    def handler(request):
        return httpx.Response(200, text=fixture_html("rank_inner_empty.html"))

    client = make_client(handler, FakeClock())
    assert list(client.iter_rank_pages(RankQuery())) == []

    calls = []

    def full(request):
        calls.append(request)
        return httpx.Response(200, text=fixture_html("rank_inner_1vs1_p1.html"))

    client = make_client(full, FakeClock())
    assert len(list(client.iter_rank_pages(RankQuery(), max_pages=3))) == 3
    assert len(calls) == 3


def test_sends_ajax_headers(fixture_html):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, text=fixture_html("rank_inner_empty.html"))

    make_client(handler, FakeClock()).fetch_rank_page(RankQuery(), 1)
    assert seen["x-requested-with"] == "XMLHttpRequest"
    assert "fco-meta-crawler" in seen["user-agent"]


def test_retries_server_errors_then_succeeds(fixture_html):
    responses = iter([httpx.Response(503), httpx.Response(502), httpx.Response(200, text=fixture_html("rank_inner_empty.html"))])
    clock = FakeClock()
    client = make_client(lambda r: next(responses), clock)

    assert client.fetch_rank_page(RankQuery(), 1).total_count == 0
    assert clock.sleeps == [2.0, 4.0]


def test_gives_up_after_max_retries():
    client = make_client(lambda r: httpx.Response(500), FakeClock(), max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch_rank_page(RankQuery(), 1)


def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403)

    with pytest.raises(httpx.HTTPStatusError):
        make_client(handler, FakeClock()).fetch_rank_page(RankQuery(), 1)
    assert len(calls) == 1


def test_saves_raw_responses(tmp_path, fixture_html):
    client = make_client(lambda r: httpx.Response(200, text=fixture_html("rank_inner_empty.html")), FakeClock(), raw_dir=tmp_path)
    client.fetch_rank_page(RankQuery(), 1)
    client.fetch_rank_page(RankQuery(), 2)
    assert len(list(tmp_path.glob("datacenter_rank_inner_*.html"))) == 2
