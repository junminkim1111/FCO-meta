from datetime import datetime, timezone

import httpx
import pytest

from fco_meta.openapi import CallBudget, NexonOpenApiClient
from fco_meta.pipeline import FormationTable, PipelineStore, SquadCollector, role_usage, select_targets
from fco_meta.storage import Storage

AS_OF = "2026-09-28T20:00:00+09:00"  # = 11:00 UTC
LATER = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)  # 반영 지연(2시간) 이후

GK = [(0, 101000001)]
BACK4 = [(3, 101000003), (4, 101000004), (6, 101000006), (7, 101000007)]
SUBS = [(28, 101000100 + i) for i in range(7)]
F4231 = GK + BACK4 + [(9, 250000009), (11, 101000011), (17, 101000017), (18, 101000018), (19, 101000019), (25, 101000025)] + SUBS
F4231_OTHER_DM = GK + BACK4 + [(9, 300000009), (11, 101000011), (17, 101000017), (18, 101000018), (19, 101000019), (25, 101000025)] + SUBS
F442 = GK + BACK4 + [(12, 101000012), (13, 101000013), (15, 101000015), (16, 101000016), (24, 101000024), (26, 101000026)] + SUBS


def detail(match_id, date, ouid, squad, result="승", opponent="ouid-opp"):
    def team(o, players, res):
        return {
            "ouid": o,
            "nickname": "익명",
            "matchDetail": {"matchResult": res, "matchEndType": 0},
            "player": [
                {"spId": sp, "spPosition": pos, "spGrade": 5, "status": {"spRating": 7.0 if pos != 28 else 0}}
                for pos, sp in players
            ],
        }

    return {
        "matchId": match_id,
        "matchDate": date,
        "matchType": 50,
        "matchInfo": [team(ouid, squad, result), team(opponent, F442, "패" if result == "승" else "승")],
    }


class FakeApi:
    """Routes Open API paths to canned responses and records every call."""

    def __init__(self, users, matches, details):
        self.users, self.matches, self.details = users, matches, details
        self.calls: list[str] = []

    def __call__(self, request):
        path, q = request.url.path, request.url.params
        self.calls.append(path)
        if path == "/fconline/v1/id":
            ouid = self.users.get(q["nickname"])
            if ouid is None:
                return httpx.Response(400, json={"error": {"name": "OPENAPI00004", "message": "Please input valid parameter"}})
            return httpx.Response(200, json={"ouid": ouid})
        if path == "/fconline/v1/user/match":
            return httpx.Response(200, json=self.matches[q["ouid"]][: int(q["limit"])])
        if path == "/fconline/v1/match-detail":
            return httpx.Response(200, json=self.details[q["matchid"]])
        raise AssertionError(path)


def collector(api, store, now=LATER, **kw):
    return SquadCollector(api, store, now=now, **kw)


def make_env(tmp_path, fake, *, rankers, run_limit=None):
    storage = Storage(tmp_path / "db.sqlite")
    storage.conn.execute("INSERT INTO crawl_run (id, started_at, mode, query_json) VALUES (1, 'x', '1vs1', '{}')")
    for rank, (nickname, formation) in enumerate(rankers, 1):
        storage.conn.execute(
            "INSERT INTO ranker_snapshot (data_as_of, mode, rank, run_id, nickname, formation) VALUES (?, '1vs1', ?, 1, ?, ?)",
            (AS_OF, rank, nickname, formation),
        )
        storage.conn.execute(
            "INSERT INTO ranker_team_color (data_as_of, mode, rank, team_color_id, run_id) VALUES (?, '1vs1', ?, 1004, 1)",
            (AS_OF, rank),
        )
    storage.conn.commit()
    http = httpx.Client(base_url="https://open.api.nexon.com", transport=httpx.MockTransport(fake))
    budget = CallBudget(storage.conn, run_limit=run_limit)
    api = NexonOpenApiClient("k", http, budget=budget, sleep=lambda s: None)
    return storage, PipelineStore(storage.conn), api


@pytest.fixture
def fake():
    return FakeApi(
        users={"랭커A": "ouid-a", "랭커B": "ouid-b"},
        matches={"ouid-a": ["a3", "a2", "a1", "a0"], "ouid-b": ["b1"]},
        details={
            "a3": detail("a3", "2026-09-28T11:30:00", "ouid-a", F442),  # 스냅샷 이후 경기 → 제외
            "a2": detail("a2", "2026-09-28T10:40:00", "ouid-a", F4231),  # 기본 스쿼드
            "a1": detail("a1", "2026-09-28T10:10:00", "ouid-a", F4231_OTHER_DM, result="패"),
            "a0": detail("a0", "2026-09-28T09:50:00", "ouid-a", F442),
            "b1": detail("b1", "2026-09-28T08:00:00.123", "ouid-b", F4231_OTHER_DM),
        },
    )


def test_select_targets_uses_team_color_membership(tmp_path, fake):
    storage, _, _ = make_env(tmp_path, fake, rankers=[("랭커A", "4-2-3-1"), ("랭커B", "4-2-3-1"), ("랭커C", "4-4-2")])
    targets = select_targets(storage.conn, 1004, "4-2-3-1")
    assert [(t.rank, t.nickname) for t in targets] == [(1, "랭커A"), (2, "랭커B")]
    assert select_targets(storage.conn, 9999, "4-2-3-1") == []


def test_base_squad_is_latest_match_before_snapshot(tmp_path, fake):
    storage, store, api = make_env(tmp_path, fake, rankers=[("랭커A", "4-2-3-1")])
    result = collector(api, store).run(select_targets(storage.conn, 1004, "4-2-3-1"))

    assert result.statuses == {"ok": 1}
    assert fake.calls == ["/fconline/v1/id", "/fconline/v1/user/match", "/fconline/v1/match-detail", "/fconline/v1/match-detail"]
    squad = storage.conn.execute("SELECT * FROM ranker_squad").fetchall()
    assert [(r["match_order"], r["match_id"], r["inferred_formation"], r["accepted"]) for r in squad] == [(0, "a2", "4-2-3-1", 1)]

    players = storage.conn.execute(
        "SELECT sp_id, season_id, pid, sp_position, starter FROM match_player WHERE match_id = 'a2' AND ouid = 'ouid-a'"
    ).fetchall()
    assert len(players) == 18 and sum(p["starter"] for p in players) == 11
    dm = [tuple(p) for p in players if p["sp_position"] == 9]
    assert dm == [(250000009, 250, 9, 9, 1)]
    assert storage.conn.execute("SELECT match_date FROM match WHERE match_id = 'a2'").fetchone()[0] == "2026-09-28T10:40:00+00:00"
    assert [tuple(r) for r in storage.conn.execute("SELECT nickname, ouid FROM account")] == [("랭커A", "ouid-a")]


def test_rerun_uses_caches(tmp_path, fake):
    storage, store, api = make_env(tmp_path, fake, rankers=[("랭커A", "4-2-3-1")])
    targets = select_targets(storage.conn, 1004, "4-2-3-1")
    collector(api, store).run(targets)
    n = len(fake.calls)

    again = collector(api, store).run(targets)
    assert len(fake.calls) == n and again.cached == 1

    # 완료 상태를 지워도 ouid·경기 목록·match-detail은 캐시에서 읽는다
    storage.conn.execute("DELETE FROM ranker_squad_status")
    collector(api, store).run(targets)
    assert len(fake.calls) == n


def test_extra_matches_only_accepted_when_formation_matches(tmp_path, fake):
    storage, store, api = make_env(tmp_path, fake, rankers=[("랭커A", "4-2-3-1")])
    collector(api, store, extra_matches=2).run(select_targets(storage.conn, 1004, "4-2-3-1"))

    squad = storage.conn.execute("SELECT match_order, match_id, inferred_formation, formation_match, accepted FROM ranker_squad").fetchall()
    assert [tuple(r) for r in squad] == [(0, "a2", "4-2-3-1", 1, 1), (1, "a1", "4-2-3-1", 1, 1), (2, "a0", "4-4-2", 0, 0)]


def test_learned_table_is_used_for_consistency(tmp_path, fake):
    storage, store, api = make_env(tmp_path, fake, rankers=[("랭커A", "4-2-3-1")])
    # 학습된 표에 있는 조합이면 라인 기준 추론보다 표의 이름을 우선한다
    table = FormationTable({tuple(sorted(p for p, _ in F4231 if p not in (0, 28))): "4-2-3-1(x)"})
    collector(api, store, table=table).run(select_targets(storage.conn, 1004, "4-2-3-1"))
    row = storage.conn.execute("SELECT inferred_formation, formation_match, accepted FROM ranker_squad").fetchone()
    assert tuple(row) == ("4-2-3-1(x)", 0, 1)  # 기본 스쿼드는 불일치여도 반영, 불일치 여부만 기록


def test_unknown_nickname_is_recorded_and_not_retried(tmp_path, fake):
    storage, store, api = make_env(tmp_path, fake, rankers=[("탈퇴한닉", "4-2-3-1")])
    targets = select_targets(storage.conn, 1004, "4-2-3-1")
    assert collector(api, store).run(targets).statuses == {"nickname_not_found": 1}
    lookup = storage.conn.execute("SELECT status, error_code FROM nickname_lookup").fetchone()
    assert tuple(lookup) == ("not_found", "OPENAPI00004")

    collector(api, store).run(targets)
    assert fake.calls == ["/fconline/v1/id"]
    collector(api, store, retry_failed=True).run(targets)
    assert fake.calls == ["/fconline/v1/id"] * 2


def test_budget_stops_run_and_next_run_resumes(tmp_path, fake):
    storage, store, api = make_env(tmp_path, fake, rankers=[("랭커A", "4-2-3-1"), ("랭커B", "4-2-3-1")], run_limit=5)
    targets = select_targets(storage.conn, 1004, "4-2-3-1")
    first = collector(api, store).run(targets)
    assert first.stopped == "budget" and first.statuses == {"ok": 1} and first.api_calls == 5
    run = storage.conn.execute("SELECT status, api_calls FROM pipeline_run").fetchone()
    assert tuple(run) == ("stopped_budget", 5)

    api.budget.run_limit = None
    second = collector(api, store).run(targets)
    assert second.stopped is None and second.cached == 1 and second.statuses == {"ok": 2}


def test_no_match_before_snapshot(tmp_path):
    fake = FakeApi({"랭커A": "ouid-a"}, {"ouid-a": ["late"]}, {"late": detail("late", "2026-09-28T12:00:00", "ouid-a", F4231)})
    storage, store, api = make_env(tmp_path, fake, rankers=[("랭커A", "4-2-3-1")])
    result = collector(api, store).run(select_targets(storage.conn, 1004, "4-2-3-1"))
    assert result.statuses == {"no_match_before_snapshot": 1}


def test_role_usage(tmp_path, fake):
    storage, store, api = make_env(tmp_path, fake, rankers=[("랭커A", "4-2-3-1"), ("랭커B", "4-2-3-1")])
    collector(api, store, extra_matches=1).run(select_targets(storage.conn, 1004, "4-2-3-1"))
    storage.conn.execute("INSERT INTO meta_spid VALUES (101000011, '볼란치 L'), (250000009, '볼란치 R')")
    storage.conn.execute("INSERT INTO meta_season VALUES (101, 'ICON', NULL), (250, 'SEASON250', NULL)")

    rows = role_usage(storage.conn, 1004, "4-2-3-1", (9, 10, 11))
    assert [(r.key, r.name, r.season, r.rankers, r.sample, r.usage_rate) for r in rows] == [
        (101000011, "볼란치 L", "ICON", 2, 2, 1.0),
        (250000009, "볼란치 R", "SEASON250", 1, 2, 0.5),
        (300000009, None, None, 1, 2, 0.5),
    ]
    by_pid = role_usage(storage.conn, 1004, "4-2-3-1", (9, 10, 11), by="pid", top=1)
    assert [(r.key, r.rankers) for r in by_pid] == [(9, 2)]  # 250·300 시즌 같은 선수(pid 9) 합산


def test_squads_collected_within_api_lag_are_provisional(tmp_path, fake):
    storage, store, api = make_env(tmp_path, fake, rankers=[("랭커A", "4-2-3-1")])
    targets = select_targets(storage.conn, 1004, "4-2-3-1")
    early = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)

    assert collector(api, store, now=early).run(targets).statuses == {"provisional": 1}
    n = len(fake.calls)
    # 다음 실행: 경기 목록만 다시 받고 match-detail은 캐시 사용, 지연 이후면 ok
    assert collector(api, store).run(targets).statuses == {"ok": 1}
    assert fake.calls[n:] == ["/fconline/v1/user/match"]


def test_role_usage_strict_excludes_mismatched_base_squads(tmp_path, fake):
    fake.details["b1"] = detail("b1", "2026-09-28T08:00:00", "ouid-b", F442)
    storage, store, api = make_env(tmp_path, fake, rankers=[("랭커A", "4-2-3-1"), ("랭커B", "4-2-3-1")])
    collector(api, store).run(select_targets(storage.conn, 1004, "4-2-3-1"))

    assert {r.sample for r in role_usage(storage.conn, 1004, "4-2-3-1", (13, 15))} == {2}
    assert role_usage(storage.conn, 1004, "4-2-3-1", (13, 15), strict=True) == []
    strict = role_usage(storage.conn, 1004, "4-2-3-1", (9, 10, 11), strict=True)
    assert {r.sample for r in strict} == {1}
