"""Keep the DB from growing without bound: drop old raw rows, keep what the web and chatbot read.

- 스쿼드·경기 원본(ranker_squad·ranker_squad_status·match·match_player·match_team): RAW_DAYS일
  (추천·베스트 11·범용 조회는 최신 스냅샷만 쓰고, 상대 포메이션 전적은 남은 경기 전부를 쓴다.
  랭커 10,000명이면 하루 약 1만 경기라 짧게 둔다. 선발 스탯은 범용 조회의 경기 스탯에만 쓰여 최신 스쿼드 스냅샷 경기만 남긴다)
- 랭킹 스냅샷(ranker_snapshot·ranker_team_color): RANKING_DAYS일 (메타 동향이 며칠 전 스냅샷과 비교)
- 시세 기록(card_price): PRICE_DAYS일
- 사용률 집계(usage_stats·usage_sample): USAGE_DAYS일 (메타 동향이 7일 전과 비교, 10,000명이면 스냅샷당 약 55MB)
- 가장 최신 스냅샷(스쿼드·랭킹·집계)과 카드별 최신 시세는 날짜와 상관없이 남긴다.
- 예전 방식으로 저장된 경기 기록은 지금 방식(선발 기록과 스코어만)으로 줄인다.
매일 수집 끝에 실행하고, 지운 만큼 파일이 줄도록 VACUUM 한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

RAW_DAYS = 3
RANKING_DAYS = 30
PRICE_DAYS = 30
USAGE_DAYS = 8
KST = timezone(timedelta(hours=9))


def _cutoff(now: datetime, days: int) -> str:
    """Same text format as data_as_of ('2026-09-30T08:00:00+09:00'), so strings compare as times."""
    return (now - timedelta(days=days)).astimezone(KST).isoformat(timespec="seconds")


def prune(conn: sqlite3.Connection, now: datetime | None = None, vacuum: bool = True) -> dict[str, int]:
    """Deletes old rows; returns rows deleted per table (tables that do not exist yet are skipped)."""
    now = now or datetime.now(timezone.utc)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
    deleted: dict[str, int] = {}

    def run(table: str, sql: str, *args: object, key: str | None = None) -> None:
        if table in tables:
            deleted[key or table] = conn.execute(sql, args).rowcount

    # 예전 방식으로 저장된 경기 기록 줄이기 (지금 수집은 처음부터 이렇게 저장한다, pipeline/store.py save_match)
    run("match_player", "UPDATE match_player SET stats = NULL WHERE starter = 0 AND stats IS NOT NULL", key="교체 선수 기록 비움")
    run(
        "match_team",
        "UPDATE match_team SET shots = NULL,"
        " detail = json_object('shoot', json_object('goalTotal', json_extract(detail, '$.shoot.goalTotal')))"
        " WHERE shots IS NOT NULL",
        key="팀 상세를 스코어만 남김",
    )

    raw, ranking = _cutoff(now, RAW_DAYS), _cutoff(now, RANKING_DAYS)
    if "ranker_squad" in tables:
        (latest,) = conn.execute("SELECT MAX(data_as_of) FROM ranker_squad").fetchone()
        for table in ("ranker_squad", "ranker_squad_status"):
            run(table, f"DELETE FROM {table} WHERE data_as_of < ? AND data_as_of != ?", raw, latest or "")
        run(
            "match_player",
            "UPDATE match_player SET stats = NULL WHERE stats IS NOT NULL"
            " AND match_id NOT IN (SELECT match_id FROM ranker_squad WHERE data_as_of = ? AND match_id IS NOT NULL)",
            latest or "",
            key="최신 스냅샷 밖 경기 스탯 비움",
        )
        # RAW_DAYS보다 오래됐고 남은 스쿼드도 가리키지 않는 경기 (최근 경기는 상대 포메이션 전적에 쓰여 남긴다)
        old_matches = (
            "SELECT match_id FROM match WHERE match_date < ?"
            " AND match_id NOT IN (SELECT match_id FROM ranker_squad WHERE match_id IS NOT NULL)"
        )
        match_cutoff = (now - timedelta(days=RAW_DAYS)).isoformat(timespec="seconds")  # match_date는 UTC
        for table in ("match_player", "match_team", "match"):
            run(table, f"DELETE FROM {table} WHERE match_id IN ({old_matches})", match_cutoff)
    if "ranker_snapshot" in tables:
        (latest,) = conn.execute("SELECT MAX(data_as_of) FROM ranker_snapshot").fetchone()
        for table in ("ranker_snapshot", "ranker_team_color"):
            run(table, f"DELETE FROM {table} WHERE data_as_of < ? AND data_as_of != ?", ranking, latest or "")
    if "usage_stats" in tables:  # 집계 시각은 스쿼드 스냅샷 시각
        (latest,) = conn.execute("SELECT MAX(data_as_of) FROM usage_stats").fetchone()
        for table in ("usage_stats", "usage_sample"):
            run(table, f"DELETE FROM {table} WHERE data_as_of < ? AND data_as_of != ?", _cutoff(now, USAGE_DAYS), latest or "")
    if "card_price_latest" in tables:  # 카드별 최신 시세는 오래됐어도 남긴다 (그것뿐일 수 있음)
        run(
            "card_price",
            "DELETE FROM card_price WHERE fetched_at < ? AND rowid NOT IN"
            " (SELECT p.rowid FROM card_price p JOIN card_price_latest l USING (spid, grade, fetched_at))",
            (now - timedelta(days=PRICE_DAYS)).isoformat(timespec="seconds"),
        )
    conn.commit()
    if vacuum and any(deleted.values()):
        conn.execute("VACUUM")
    return {t: n for t, n in deleted.items() if n}
