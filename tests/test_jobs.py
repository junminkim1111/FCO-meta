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
