import sqlite3

import httpx

from fco_meta.crawler import DatacenterClient, RankQuery, crawl_rankings
from fco_meta.crawler.membership import record_displayed_membership
from fco_meta.storage import Storage


def crawl(storage, fixture_html, name, query=RankQuery()):
    http = httpx.Client(
        base_url="https://fconline.nexon.com",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=fixture_html(name))),
    )
    client = DatacenterClient(http, min_interval=0, sleep=lambda s: None)
    crawl_rankings(client, storage, query, max_pages=1)
    return storage.conn.execute("SELECT MAX(data_as_of) FROM ranker_snapshot").fetchone()[0]


def members(storage):
    return {
        (rank, tc): source
        for rank, tc, source in storage.conn.execute("SELECT rank, team_color_id, source FROM ranker_team_color")
    }


def test_clubs_and_nations_from_unfiltered_page(tmp_path, fixture_html):
    storage = Storage(tmp_path / "db.sqlite")
    as_of = crawl(storage, fixture_html, "rank_inner_1vs1_p1.html")
    assert members(storage) == {}  # 필터 없는 조회는 소속을 기록하지 않음

    result = record_displayed_membership(storage.conn, as_of, "1vs1")

    assert (result.rows, result.recorded, result.unresolved) == (20, 20, 0)
    m = members(storage)
    assert m[(1, 1016)] == "display"  # FC 바르셀로나 (엠블럼 l241 → 클럽)
    assert (8, 2001) in m and (8, 974) not in m  # 대한민국: 엠블럼 없음 → 국가 팀컬러 (같은 이름의 클럽 아님)
    assert (14, 2002) in m  # 프랑스 → 국가
    assert (11, 1004) in m  # 아스널


def test_special_team_color_maps_to_club_by_crest(tmp_path, fixture_html):
    storage = Storage(tmp_path / "db.sqlite")
    as_of = crawl(storage, fixture_html, "rank_inner_1vs1_arsenal_4231_boost.html")
    storage.conn.execute("DELETE FROM ranker_team_color")  # 필터 조회 결과를 지우고 표시 팀컬러만으로 추정

    record_displayed_membership(storage.conn, as_of, "1vs1")

    m = members(storage)
    # "Winning Streak"(엠블럼 l1) → 특수 팀컬러 + 같은 엠블럼의 클럽(아스널)
    assert (5808, 40663) in m and (5808, 1004) in m
    assert all(tc in (1004, 40663) for _, tc in m)


def test_filter_membership_is_kept(tmp_path, fixture_html):
    storage = Storage(tmp_path / "db.sqlite")
    as_of = crawl(storage, fixture_html, "rank_inner_1vs1_arsenal_4231_boost.html", RankQuery(team_color_id=1004))
    record_displayed_membership(storage.conn, as_of, "1vs1")
    m = members(storage)
    assert m[(5808, 1004)] == "filter" and m[(5808, 40663)] == "display"


def test_old_database_gets_source_column(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ranker_team_color (data_as_of TEXT NOT NULL, mode TEXT NOT NULL, rank INTEGER NOT NULL,"
        " team_color_id INTEGER NOT NULL, run_id INTEGER NOT NULL, PRIMARY KEY (data_as_of, mode, rank, team_color_id))"
    )
    conn.execute("INSERT INTO ranker_team_color VALUES ('t', '1vs1', 1, 1004, 1)")
    conn.commit()
    conn.close()

    storage = Storage(path)
    assert storage.conn.execute("SELECT source FROM ranker_team_color").fetchone()[0] == "filter"
