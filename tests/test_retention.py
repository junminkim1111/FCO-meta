"""Old raw rows are dropped; the latest snapshot, the latest price per card and the aggregates stay."""

from datetime import datetime, timezone

from fco_meta.market.storage import MarketStorage
from fco_meta.pipeline.store import PipelineStore
from fco_meta.retention import prune
from fco_meta.storage import Storage

NOW = datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)  # 2026-10-30 12:00 KST
OLD, RECENT, LATEST = "2026-09-01T08:00:00+09:00", "2026-10-25T08:00:00+09:00", "2026-10-30T08:00:00+09:00"


def build(path):
    Storage(path).close()
    market = MarketStorage(path)
    conn = market.conn
    PipelineStore(conn)
    for i, as_of in enumerate((OLD, RECENT, LATEST)):
        conn.execute("INSERT INTO ranker_snapshot (data_as_of, mode, rank, run_id, nickname) VALUES (?, '1vs1', 1, 1, 'x')", (as_of,))
        conn.execute("INSERT INTO ranker_team_color (data_as_of, mode, rank, team_color_id, run_id) VALUES (?, '1vs1', 1, 7, 1)", (as_of,))
        conn.execute(
            "INSERT INTO ranker_squad (data_as_of, mode, rank, match_order, ouid, match_id, formation_match, accepted)"
            " VALUES (?, '1vs1', 1, 0, 'o', ?, 1, 1)", (as_of, f"m{i}"),
        )  # fmt: skip
        conn.execute("INSERT INTO ranker_squad_status (data_as_of, mode, rank, status, updated_at) VALUES (?, '1vs1', 1, 'ok', ?)", (as_of, as_of))
    # 스쿼드가 가리키는 경기 m0~m2, 가리키지 않는 경기 x-old(오래됨)·x-new(최근, 상대 전적에 쓰임)
    for match_id, played in (("m0", "2026-09-01"), ("m1", "2026-10-25"), ("m2", "2026-10-30"), ("x-old", "2026-09-01"), ("x-new", "2026-10-28")):
        conn.execute("INSERT INTO match (match_id, match_date, match_type, fetched_at) VALUES (?, ?, 50, ?)",
                     (match_id, f"{played}T00:00:00+00:00", f"{played}T00:00:00+00:00"))  # fmt: skip
        conn.execute(
            "INSERT INTO match_player (match_id, ouid, sp_id, season_id, pid, sp_position, starter) VALUES (?, 'o', 1, 1, 1, 25, 1)", (match_id,)
        )  # fmt: skip
        conn.execute("INSERT INTO match_team (match_id, ouid) VALUES (?, 'o')", (match_id,))
    prices = [(1, "2026-09-01T00:00:00+00:00"), (1, "2026-10-29T00:00:00+00:00"), (2, "2026-09-01T00:00:00+00:00")]
    conn.executemany("INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (?, 5, 100, ?)", prices)
    conn.commit()
    return conn


def dates(conn, table):
    return sorted(r[0] for r in conn.execute(f"SELECT data_as_of FROM {table}"))


def test_prune_keeps_recent_latest_and_last_prices(tmp_path):
    conn = build(tmp_path / "db.sqlite")
    deleted = prune(conn, NOW)

    assert dates(conn, "ranker_squad") == [RECENT, LATEST]  # 경기 원본 14일 (9월 1일은 지움)
    assert dates(conn, "ranker_snapshot") == [RECENT, LATEST]  # 랭킹 30일
    # 남은 스쿼드의 경기와 최근 경기는 남고, 오래된 경기(스쿼드와 함께 지운 m0, 연결 없는 x-old)만 지운다
    assert sorted(r[0] for r in conn.execute("SELECT match_id FROM match_player")) == ["m1", "m2", "x-new"]
    assert sorted(r[0] for r in conn.execute("SELECT match_id FROM match")) == ["m1", "m2", "x-new"]
    # 카드 1: 오래된 기록은 지우고 최신만, 카드 2: 오래됐어도 그게 유일한 최신 시세라 남긴다
    assert sorted(tuple(r) for r in conn.execute("SELECT spid, fetched_at FROM card_price")) == [
        (1, "2026-10-29T00:00:00+00:00"), (2, "2026-09-01T00:00:00+00:00"),
    ]  # fmt: skip
    assert deleted["ranker_squad"] == 1 and deleted["card_price"] == 1


def test_latest_snapshot_is_kept_even_when_old(tmp_path):
    conn = build(tmp_path / "db.sqlite")
    conn.execute("DELETE FROM ranker_squad WHERE data_as_of != ?", (OLD,))  # 수집이 한 달 넘게 멈춘 경우
    conn.commit()
    prune(conn, NOW)
    assert dates(conn, "ranker_squad") == [OLD]
    assert prune(conn, NOW) == {}  # 다시 돌리면 지울 것이 없다


def test_old_style_match_records_are_slimmed(tmp_path):
    import json

    conn = build(tmp_path / "db.sqlite")
    full = {"matchDetail": {"possession": 55}, "shoot": {"goalTotal": 3, "shootTotal": 9}, "pass": {"passTry": 400}}
    conn.execute("UPDATE match_team SET detail = ?, shots = '[{\"x\": 0.1}]'", (json.dumps(full),))
    conn.execute(
        "INSERT INTO match_player (match_id, ouid, sp_id, season_id, pid, sp_position, starter, stats) VALUES ('m2', 'o', 2, 1, 2, 28, 0, '{\"goal\": 0}')"
    )  # fmt: skip
    conn.execute("UPDATE match_player SET stats = '{\"goal\": 1}' WHERE starter = 1")
    conn.commit()
    deleted = prune(conn, NOW)
    assert all(json.loads(d) == {"shoot": {"goalTotal": 3}} for (d,) in conn.execute("SELECT detail FROM match_team"))
    assert {s for (s,) in conn.execute("SELECT shots FROM match_team")} == {None}
    assert conn.execute("SELECT stats FROM match_player WHERE starter = 0").fetchone()[0] is None  # 교체는 포지션만
    assert conn.execute("SELECT COUNT(*) FROM match_player WHERE starter = 1 AND stats IS NOT NULL").fetchone()[0] > 0  # 선발 기록은 유지
    assert deleted["교체 선수 기록 비움"] == 1
    assert "팀 상세를 스코어만 남김" not in prune(conn, NOW)  # 한 번 줄이면 다시 건드리지 않는다
