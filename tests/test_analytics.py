import httpx
import pytest
from test_pipeline import AS_OF, F442, F4231, F4231_OTHER_DM, LATER, FakeApi, detail

from fco_meta.analytics import ALL_FORMATIONS, UsageStore, top_players
from fco_meta.analytics.__main__ import main
from fco_meta.openapi import CallBudget, NexonOpenApiClient
from fco_meta.pipeline import PipelineStore, SquadCollector, select_targets
from fco_meta.storage import Storage

# 순위, 닉네임, 스냅샷 포메이션, ELO, ouid, 기본 스쿼드, 결과
RANKERS = [
    (1, "A", "4-2-3-1", 3600.0, "ouid-a", F4231, "승"),
    (2, "B", "4-2-3-1", 3500.0, "ouid-b", F4231_OTHER_DM, "패"),
    (3, "C", "4-4-2", 3400.0, "ouid-c", F442, "승"),
    (4, "D", "4-2-3-1", 3300.0, "ouid-d", F442, "무"),  # 스냅샷은 4-2-3-1인데 직전 경기는 4-4-2
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.sqlite"
    storage = Storage(path)
    conn = storage.conn
    store = PipelineStore(conn)
    conn.execute("INSERT INTO crawl_run (id, started_at, mode, query_json) VALUES (1, 'x', '1vs1', '{}')")
    fake = FakeApi({}, {}, {})
    for rank, nick, formation, elo, ouid, squad, result in RANKERS:
        conn.execute(
            "INSERT INTO ranker_snapshot (data_as_of, mode, rank, run_id, nickname, formation, elo, win_rate)"
            " VALUES (?, '1vs1', ?, 1, ?, ?, ?, 0.6)",
            (AS_OF, rank, nick, formation, elo),
        )
        conn.execute("INSERT INTO ranker_team_color VALUES (?, '1vs1', ?, 1004, 1)", (AS_OF, rank))
        fake.users[nick] = ouid
        fake.matches[ouid] = [f"m-{nick}"]
        fake.details[f"m-{nick}"] = detail(f"m-{nick}", "2026-09-28T10:00:00", ouid, squad, result=result)
    conn.execute("INSERT INTO meta_spid VALUES (101000011, '볼란치L'), (250000009, '볼란치R'), (300000009, '볼란치R')")
    conn.execute("INSERT INTO meta_season VALUES (101, 'ICON', NULL), (250, 'S250', NULL), (300, 'S300', NULL)")
    conn.commit()

    http = httpx.Client(base_url="https://open.api.nexon.com", transport=httpx.MockTransport(fake))
    api = NexonOpenApiClient("k", http, budget=CallBudget(conn), sleep=lambda s: None)
    for formation in ("4-2-3-1", "4-4-2"):
        SquadCollector(api, store, now=LATER).run(select_targets(conn, 1004, formation))
    UsageStore(conn).build_all()
    yield storage
    storage.close()


def test_build_writes_samples_per_combo(db):
    samples = db.conn.execute(
        "SELECT formation, strict, combo_rankers, squads FROM usage_sample ORDER BY formation, strict"
    ).fetchall()
    assert [tuple(r) for r in samples] == [
        ("*", 0, 4, 4), ("*", 1, 4, 3), ("4-2-3-1", 0, 3, 3), ("4-2-3-1", 1, 3, 2), ("4-4-2", 0, 1, 1), ("4-4-2", 1, 1, 1),
    ]  # fmt: skip


def test_card_level_usage(db):
    res = top_players(db.conn, 1004, "4-2-3-1", "DM", by="sp_id", min_sample=1)
    assert (res.sample_size, res.combo_rankers, res.fallback) == (3, 3, False)
    got = [(p.key, p.name, p.ranker_count, p.usage_rate, p.win_rate, p.grade_dist, p.avg_elo) for p in res.players]
    assert got == [
        (101000011, "볼란치L", 2, 0.6667, 0.5, {5: 2}, 3550.0),
        (250000009, "볼란치R", 1, 0.3333, 1.0, {5: 1}, 3600.0),
        (300000009, "볼란치R", 1, 0.3333, 0.0, {5: 1}, 3500.0),
    ]


def test_player_level_usage_merges_seasons(db):
    res = top_players(db.conn, 1004, "4-2-3-1", "DM", by="pid", min_sample=1)
    assert [(p.key, p.name, p.ranker_count) for p in res.players] == [(9, "볼란치R", 2), (11, "볼란치L", 2)]
    seasons = res.players[0].seasons
    assert [(s.sp_id, s.season, s.ranker_count) for s in seasons] == [(250000009, "S250", 1), (300000009, "S300", 1)]
    assert res.players[0].win_rate == 0.5 and res.players[0].grade_dist == {5: 2}


def test_strict_drops_squads_of_other_formations(db):
    loose = top_players(db.conn, 1004, "4-2-3-1", "CM", min_sample=1)
    assert loose.sample_size == 3 and [p.ranker_count for p in loose.players] == [1, 1]  # D의 4-4-2 CM
    strict = top_players(db.conn, 1004, "4-2-3-1", "CM", strict=True, min_sample=1)
    assert strict.sample_size == 2 and strict.players == []
    assert top_players(db.conn, 1004, "4-2-3-1", "DM", strict=True, min_sample=1).players[0].usage_rate == 1.0


def test_small_sample_falls_back_to_all_formations(db):
    res = top_players(db.conn, 1004, "4-2-3-1", "DM", by="sp_id", min_sample=4)
    assert res.fallback and res.formation == ALL_FORMATIONS and res.requested_formation == "4-2-3-1"
    assert res.sample_size == 4 and res.players[0].usage_rate == 0.5

    # 조합 자체가 없으면 폴백 결과도 비어 있다
    empty = top_players(db.conn, 9999, "4-2-3-1", "DM")
    assert (empty.sample_size, empty.players) == (0, [])


def test_rebuild_is_idempotent(db):
    before = db.conn.execute("SELECT COUNT(*) FROM usage_stats").fetchone()[0]
    UsageStore(db.conn).build_all()
    assert db.conn.execute("SELECT COUNT(*) FROM usage_stats").fetchone()[0] == before


def test_unknown_role():
    import sqlite3

    with pytest.raises(ValueError):
        top_players(sqlite3.connect(":memory:"), 1004, "4-2-3-1", "볼란치")


def test_cli_top(db, tmp_path, capsys):
    path = str(tmp_path / "db.sqlite")
    code = main(["--db", path, "top", "--team-color", "아스널", "--formation", "4-2-3-1", "--role", "볼란치", "--min-sample", "1"])
    out = capsys.readouterr().out
    assert code == 0
    assert "아스널 4-2-3-1 DM 사용 TOP 5" in out and "표본 3명 / 조합 3명" in out
    assert "1. 볼란치R: 2명 (66.7%)" in out and "S250 1명, S300 1명" in out
