import json
import sqlite3
from urllib.parse import unquote

import httpx
import pytest

from fco_meta.openapi import (
    BudgetExceededError,
    CallBudget,
    DataNotReadyError,
    MaintenanceError,
    NexonOpenApiClient,
    NotFoundError,
    OpenApiError,
    RateLimitError,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def error(status, code, message="error"):
    return httpx.Response(status, json={"error": {"name": code, "message": message}})


def make_client(handler, clock=None, budget=None, **kw):
    clock = clock or FakeClock()
    http = httpx.Client(base_url="https://open.api.nexon.com", transport=httpx.MockTransport(handler))
    return NexonOpenApiClient("test-key", http, budget=budget, sleep=clock.sleep, clock=clock.time, **kw)


def test_sends_key_header_and_params():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/fconline/v1/id":
            return httpx.Response(200, json={"ouid": "abc"})
        if request.url.path == "/fconline/v1/user/match":
            return httpx.Response(200, json=["m2", "m1"])
        return httpx.Response(200, json={"matchId": "m1"})

    client = make_client(handler)
    assert client.get_ouid("닉네임") == "abc"
    assert client.user_matches("abc", limit=3) == ["m2", "m1"]
    assert client.match_detail("m1") == {"matchId": "m1"}

    assert all(r.headers["x-nxopen-api-key"] == "test-key" for r in seen)
    assert seen[0].url.params["nickname"] == "닉네임"
    assert dict(seen[1].url.params) == {"ouid": "abc", "matchtype": "50", "offset": "0", "limit": "3"}
    assert seen[2].url.params["matchid"] == "m1"


def test_ranker_stats_encodes_players_as_json():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[])

    client = make_client(handler)
    client.ranker_stats([(100167680, 18), (101000001, 10)])
    players = seen[0].url.params["players"]
    assert json.loads(players) == [{"id": 100167680, "po": 18}, {"id": 101000001, "po": 10}]
    assert "%5B%7B%22id%22%3A100167680" in str(seen[0].url)  # URL 인코딩된 JSON 배열

    with pytest.raises(ValueError):
        client.ranker_stats([(1, 1)] * 51)
    assert client.ranker_stats([]) == [] and len(seen) == 1


def test_metadata_paths():
    paths = []

    def handler(request):
        paths.append(unquote(request.url.path))
        return httpx.Response(200, json=[{"spposition": 0, "desc": "GK"}])

    client = make_client(handler)
    assert client.metadata("spposition")[0]["desc"] == "GK"
    assert paths == ["/static/fconline/meta/spposition.json"]
    with pytest.raises(ValueError):
        client.metadata("unknown")


def test_rate_limit_allows_five_per_second():
    clock = FakeClock()
    client = make_client(lambda r: httpx.Response(200, json=[]), clock)
    for _ in range(11):
        client.user_matches("o")
    # 5건은 바로, 6번째와 11번째에서 1초 창이 찰 때까지 대기
    assert clock.sleeps == [1.0, 1.0]


def test_429_is_retried_with_backoff():
    responses = iter([error(429, "OPENAPI00007"), error(429, "OPENAPI00007"), httpx.Response(200, json=["m"])])
    clock = FakeClock()
    client = make_client(lambda r: next(responses), clock)

    assert client.user_matches("o") == ["m"]
    assert clock.sleeps == [1.0, 2.0]
    assert client.budget.used_this_run == 3  # 재시도도 호출 수에 포함


def test_429_gives_up_after_max_retries():
    client = make_client(lambda r: error(429, "OPENAPI00007"), max_retries=2)
    with pytest.raises(RateLimitError):
        client.user_matches("o")
    assert client.budget.used_this_run == 3


def test_server_errors_are_retried():
    responses = iter([httpx.Response(500), httpx.Response(200, json=["m"])])
    assert make_client(lambda r: next(responses)).user_matches("o") == ["m"]


@pytest.mark.parametrize(
    ("response", "exc"),
    [
        (error(400, "OPENAPI00009", "Data being prepared"), DataNotReadyError),
        (error(400, "OPENAPI00010", "Game maintenance"), MaintenanceError),
        (error(503, "OPENAPI00011", "API maintenance"), MaintenanceError),
        (error(400, "OPENAPI00004", "Please input valid parameter"), NotFoundError),
        (error(403, "OPENAPI00002", "Forbidden"), OpenApiError),
    ],
)
def test_errors_are_not_retried(response, exc):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    with pytest.raises(exc) as info:
        make_client(handler).get_ouid("x")
    assert len(calls) == 1
    assert info.value.code == response.json()["error"]["name"]


def test_missing_key(monkeypatch):
    monkeypatch.delenv("NEXON_API_KEY", raising=False)
    with pytest.raises(ValueError):
        NexonOpenApiClient()


def test_daily_budget_blocks_requests_before_sending():
    conn = sqlite3.connect(":memory:")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[])

    day = {"value": "2026-09-28"}
    client = make_client(handler, budget=CallBudget(conn, daily_limit=3, today=lambda: day["value"]))
    for _ in range(3):
        client.user_matches("o")
    with pytest.raises(BudgetExceededError):
        client.user_matches("o")
    assert len(calls) == 3

    # 같은 DB를 쓰는 다음 실행도 오늘 사용량을 이어받는다
    again = make_client(handler, budget=CallBudget(conn, daily_limit=3, today=lambda: day["value"]))
    with pytest.raises(BudgetExceededError):
        again.user_matches("o")

    day["value"] = "2026-09-29"
    again.user_matches("o")
    rows = conn.execute("SELECT day, endpoint, calls FROM api_usage ORDER BY day").fetchall()
    assert rows == [("2026-09-28", "/fconline/v1/user/match", 3), ("2026-09-29", "/fconline/v1/user/match", 1)]


def test_run_budget():
    budget = CallBudget(daily_limit=1000, run_limit=2)
    client = make_client(lambda r: httpx.Response(200, json=[]), budget=budget)
    client.user_matches("o")
    client.user_matches("o")
    assert budget.remaining() == 0
    with pytest.raises(BudgetExceededError):
        client.user_matches("o")
