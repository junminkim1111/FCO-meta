import httpx

from fco_meta.crawler import DatacenterClient, RankQuery, crawl_rankings
from fco_meta.storage import Storage


def client_for(handler):
    http = httpx.Client(base_url="https://fconline.nexon.com", transport=httpx.MockTransport(handler))
    return DatacenterClient(http, min_interval=0, sleep=lambda s: None)


def test_crawl_records_team_color_membership(tmp_path, fixture_html):
    pages = {"1": "rank_inner_1vs1_arsenal_4231_p1.html", "2": "rank_inner_1vs1_arsenal_4231_p5.html"}
    client = client_for(lambda r: httpx.Response(200, text=fixture_html(pages[r.url.params["n4pageno"]])))
    storage = Storage(tmp_path / "db.sqlite")

    result = crawl_rankings(client, storage, RankQuery(team_color_id=1004, formation="4-2-3-1"), max_pages=2)

    assert (result.pages, result.rows, result.total_count, result.status) == (2, 34, 94, "ok")
    rows = storage.conn.execute("SELECT * FROM ranker_snapshot ORDER BY rank").fetchall()
    assert len(rows) == 34
    assert {(r["team_color_name"], r["formation"]) for r in rows} == {("아스널", "4-2-3-1")}
    members = storage.conn.execute("SELECT DISTINCT team_color_id, COUNT(*) FROM ranker_team_color").fetchall()
    assert [tuple(m) for m in members] == [(1004, 34)]
    assert rows[0]["data_as_of"] == "2026-09-28T20:00:00+09:00"
    run = storage.conn.execute("SELECT * FROM crawl_run").fetchone()
    assert (run["pages_fetched"], run["rows_saved"], run["status"]) == (2, 34, "ok")


def test_recrawl_of_same_snapshot_does_not_duplicate(tmp_path, fixture_html):
    client = client_for(lambda r: httpx.Response(200, text=fixture_html("rank_inner_1vs1_p1.html")))
    storage = Storage(tmp_path / "db.sqlite")

    crawl_rankings(client, storage, RankQuery(), max_pages=1)
    crawl_rankings(client, storage, RankQuery(), max_pages=1)

    assert storage.conn.execute("SELECT COUNT(*) FROM ranker_snapshot").fetchone()[0] == 20
    assert storage.conn.execute("SELECT COUNT(*) FROM crawl_run").fetchone()[0] == 2


def test_failed_crawl_is_marked(tmp_path):
    client = client_for(lambda r: httpx.Response(200, text="<html>점검 중</html>"))
    storage = Storage(tmp_path / "db.sqlite")

    try:
        crawl_rankings(client, storage, RankQuery(), max_pages=1)
    except ValueError:
        pass
    assert storage.conn.execute("SELECT status FROM crawl_run").fetchone()[0] == "failed"


def test_membership_counts_boosted_rankers(tmp_path, fixture_html):
    """특수 팀컬러가 표시된 랭커도 필터 결과에 있으면 해당 클럽 팀컬러 소속으로 집계된다."""
    client = client_for(lambda r: httpx.Response(200, text=fixture_html("rank_inner_1vs1_arsenal_4231_boost.html")))
    storage = Storage(tmp_path / "db.sqlite")

    crawl_rankings(client, storage, RankQuery(team_color_id=1004, formation="4-2-3-1"), max_pages=1)

    shown_as_other = storage.conn.execute(
        "SELECT s.team_color_name FROM ranker_snapshot s JOIN ranker_team_color m USING (data_as_of, mode, rank)"
        " WHERE m.team_color_id = 1004 AND s.team_color_name != '아스널'"
    ).fetchall()
    assert [r[0] for r in shown_as_other] == ["Winning Streak"]


def test_unfiltered_crawl_records_no_membership(tmp_path, fixture_html):
    client = client_for(lambda r: httpx.Response(200, text=fixture_html("rank_inner_1vs1_p1.html")))
    storage = Storage(tmp_path / "db.sqlite")

    crawl_rankings(client, storage, RankQuery(formation="4-2-3-1"), max_pages=1)
    crawl_rankings(client, storage, RankQuery(team_color_id=1004, team_color_id_2=1016), max_pages=1)

    assert storage.conn.execute("SELECT COUNT(*) FROM ranker_team_color").fetchone()[0] == 0


def test_long_crawl_restarts_when_the_ranking_refreshes(tmp_path, fixture_html):
    html = fixture_html("rank_inner_1vs1_p1.html")
    newer = html.replace("2026-09-28 20:00:00", "2026-09-28 21:00:00")
    served = []

    def handler(request):  # 첫 페이지 이후 정각 갱신
        served.append(request.url.params["n4pageno"])
        return httpx.Response(200, text=html if len(served) == 1 else newer)

    storage = Storage(tmp_path / "db.sqlite")
    result = crawl_rankings(client_for(handler), storage, RankQuery(), max_pages=2, restarts=1)
    assert result.status == "ok" and served == ["1", "2", "1", "2"]
    runs = [tuple(r) for r in storage.conn.execute("SELECT data_as_of, status FROM crawl_run ORDER BY id")]
    assert runs == [("2026-09-28T20:00:00+09:00", "refreshed"), ("2026-09-28T21:00:00+09:00", "ok")]

    served.clear()
    assert crawl_rankings(client_for(handler), storage, RankQuery(), max_pages=2).status == "mixed_as_of"  # 재시작 없음


def test_stale_page_after_a_refresh_is_fetched_again(tmp_path, fixture_html):
    html = fixture_html("rank_inner_1vs1_p1.html")
    newer = html.replace("2026-09-28 20:00:00", "2026-09-28 21:00:00")
    replies = iter([newer, html, html, newer])  # 2페이지가 두 번 이전 데이터로 왔다가 갱신됨
    slept = []
    storage = Storage(tmp_path / "db.sqlite")
    client = client_for(lambda r: httpx.Response(200, text=next(replies)))
    result = crawl_rankings(client, storage, RankQuery(), max_pages=2, restarts=1, sleep=slept.append)
    assert result.status == "ok" and result.pages == 2 and len(slept) == 2
    assert [tuple(r) for r in storage.conn.execute("SELECT data_as_of, status FROM crawl_run")] == [
        ("2026-09-28T21:00:00+09:00", "ok")
    ]


def test_tied_ranks_keep_every_ranker(tmp_path, fixture_html):
    """동점은 같은 순위로 표시된다 (2581, 2581, 2583) — 순위가 키라 둘째가 첫째를 덮지 않게 자리 순으로 번호를 매긴다."""
    from dataclasses import replace

    from fco_meta.crawler.jobs import _untie
    from fco_meta.crawler.parser import parse_rank_inner

    rows = parse_rank_inner(fixture_html("rank_inner_1vs1_p1.html")).rows[:5]
    shown = [10, 10, 12, 13, 13]  # 13위 동점이 다음 페이지까지 이어진다
    page1, tie = _untie([replace(r, rank=k) for r, k in zip(rows, shown)], (None, 0))
    page2, tie = _untie([replace(rows[0], rank=13), replace(rows[1], rank=16)], tie)
    assert [r.rank for r in page1 + page2] == [10, 11, 12, 13, 14, 15, 16]
    assert [r.nickname for r in page1] == [r.nickname for r in rows]  # 순서와 내용은 그대로


def test_empty_page_before_the_end_is_retried_then_marked_partial(tmp_path, fixture_html):
    """남은 순위가 있는데 빈 페이지(사이트 일시 오류) → 끝으로 보지 않고 다시 받는다. 계속 비면 partial."""
    from fco_meta.crawler import jobs

    def run(empty_times):
        served = {"2": 0}

        def handler(request):
            page = request.url.params["n4pageno"]
            if page == "2" and served["2"] < empty_times:
                served["2"] += 1
                return httpx.Response(200, text=fixture_html("rank_inner_empty.html"))
            return httpx.Response(200, text=fixture_html("rank_inner_1vs1_p1.html"))  # 총 10,000명 중 20명씩

        storage = Storage(tmp_path / f"db{empty_times}.sqlite")
        return crawl_rankings(client_for(handler), storage, RankQuery(), max_pages=3, sleep=lambda s: None)

    recovered = run(empty_times=2)  # 두 번 비었다가 받아짐
    assert (recovered.status, recovered.pages) == ("ok", 3)
    stopped = run(empty_times=jobs.EMPTY_RETRIES + 1)  # 다시 받아도 계속 빔
    assert (stopped.status, stopped.pages, stopped.rows) == ("partial", 1, 20)
