"""TOP 10,000 ranker stats for the (card, position) pairs our rankers start."""

import json
from datetime import datetime, timedelta, timezone

from test_analytics import db  # noqa: F401  (4명 랭커 스쿼드)

from fco_meta.openapi.errors import BudgetExceededError
from fco_meta.pipeline.ranker_stats import MAX_AGE, collect_ranker_stats, pairs_needing_stats

NOW = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)


class StubApi:
    def __init__(self, fail_after=None, missing=()):
        self.calls, self.fail_after, self.missing = [], fail_after, set(missing)

    def ranker_stats(self, players):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise BudgetExceededError("daily budget exhausted")
        self.calls.append(list(players))
        return [
            {"spId": sp, "spPosition": po, "createDate": "2026-09-29T01:00:00", "status": {"goal": 0.4, "matchCount": 12}}
            for sp, po in players if (sp, po) not in self.missing
        ]  # fmt: skip


def test_pairs_most_used_first(db):  # noqa: F811
    pairs = pairs_needing_stats(db.conn, NOW)
    assert pairs[:2] == [(101000001, 0), (101000003, 3)]  # 4명 모두 쓰는 GK·RB가 먼저
    assert (101000011, 11) in pairs and len(pairs) == len(set(pairs))


def test_collect_saves_and_skips_fresh(db):  # noqa: F811
    pairs = pairs_needing_stats(db.conn, NOW)
    api = StubApi(missing={pairs[-1]})
    r = collect_ranker_stats(api, db.conn, now=NOW)
    assert (r.pairs, r.calls, r.saved, r.empty, r.remaining) == (len(pairs), 1, len(pairs) - 1, 1, 0)
    count, stats = db.conn.execute("SELECT match_count, stats FROM ranker_stats WHERE sp_id = 101000001").fetchone()
    assert count == 12 and json.loads(stats)["goal"] == 0.4

    assert collect_ranker_stats(StubApi(), db.conn, now=NOW + timedelta(days=1)).pairs == 0  # 7일 안에는 다시 받지 않음
    assert collect_ranker_stats(StubApi(), db.conn, now=NOW + MAX_AGE + timedelta(hours=1)).pairs == len(pairs)


def test_chunks_of_50_call_cap_and_budget_stop(db, monkeypatch):  # noqa: F811
    import fco_meta.pipeline.ranker_stats as mod

    monkeypatch.setattr(mod, "RANKER_STATS_MAX_PLAYERS", 5)  # 테스트 스쿼드는 쌍이 적어 묶음 크기를 줄인다
    pairs = pairs_needing_stats(db.conn, NOW)
    api = StubApi()
    r = collect_ranker_stats(api, db.conn, max_calls=2, now=NOW)
    assert [len(c) for c in api.calls] == [5, 5] and r.remaining == len(pairs) - 10

    r = collect_ranker_stats(StubApi(fail_after=1), db.conn, now=NOW)  # 남은 쌍부터, 두 번째 호출에서 예산 소진
    assert r.stopped == "budget" and r.calls == 1 and r.remaining == len(pairs) - 15
