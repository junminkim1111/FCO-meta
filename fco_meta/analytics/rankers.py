"""Ranking-page aggregates over the whole crawled ranking (TOP 10,000), no squads needed.

Each ranking row has the ranker's team color (displayed), formation, ELO, season W/D/L and squad
value. This groups the rows of the latest unfiltered snapshot by team color / formation / rank band.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from ..storage import latest_unfiltered_snapshot, unfiltered_coverage
from .usage import ALL_RANKERS

RANKER_GROUP_BY = ("team_color", "formation", "rank_band")
RANKER_SORT_BY = ("rankers", "season_win_rate", "avg_elo", "avg_squad_value")


def query_rankers(
    conn: sqlite3.Connection,
    group_by: str,
    *,
    team_color_id: int = ALL_RANKERS,
    formation: str | None = None,
    rank_min: int | None = None,
    rank_max: int | None = None,
    elo_min: float | None = None,
    band: int = 1000,
    sort_by: str = "rankers",
    min_rankers: int = 1,
    limit: int = 10,
    mode: str = "1vs1",
) -> dict[str, Any]:
    """Returns {"data_as_of", "covered", "rankers" (in scope), "total_groups", "rows"}; each row:
    key, rankers, share, season_win_rate (pooled W/D/L), season_games, avg_elo, avg_squad_value."""
    if group_by not in RANKER_GROUP_BY:
        raise ValueError(f"group_by must be one of {RANKER_GROUP_BY}")
    if sort_by not in RANKER_SORT_BY:
        raise ValueError(f"sort_by must be one of {RANKER_SORT_BY}")
    latest = latest_unfiltered_snapshot(conn, mode)
    if latest is None:
        return {"data_as_of": None, "covered": 0, "rankers": 0, "total_groups": 0, "rows": []}
    as_of, covered = latest
    params = {
        "as_of": as_of, "mode": mode, "covered": covered, "tc": team_color_id, "formation": formation,
        "rank_min": rank_min, "rank_max": rank_max, "elo_min": elo_min,
    }  # fmt: skip
    rows = conn.execute(
        "SELECT s.rank, s.elo, s.wins, s.draws, s.losses, s.squad_value, s.formation"
        " FROM ranker_snapshot s WHERE s.data_as_of = :as_of AND s.mode = :mode AND s.rank <= :covered"
        " AND (:tc = 0 OR EXISTS (SELECT 1 FROM ranker_team_color m WHERE m.data_as_of = s.data_as_of"
        "      AND m.mode = s.mode AND m.rank = s.rank AND m.team_color_id = :tc))"
        " AND (:formation IS NULL OR s.formation = :formation)"
        " AND (:rank_min IS NULL OR s.rank >= :rank_min) AND (:rank_max IS NULL OR s.rank <= :rank_max)"
        " AND (:elo_min IS NULL OR s.elo >= :elo_min)",
        params,
    ).fetchall()
    teams: dict[int, set[int]] = defaultdict(set)
    if group_by == "team_color":
        for rank, tc in conn.execute(
            "SELECT rank, team_color_id FROM ranker_team_color WHERE data_as_of = ? AND mode = ?", (as_of, mode)
        ):
            teams[rank].add(tc)

    groups: dict[Any, list] = defaultdict(list)
    for r in rows:
        rank = r[0]
        if group_by == "team_color":
            keys = teams.get(rank, ())
        elif group_by == "formation":
            keys = [r[6]]
        else:
            keys = [(rank - 1) // band * band + 1]
        for key in keys:
            if key is not None:
                groups[key].append(r)

    out_rows = []
    for key, rs in groups.items():
        wins = sum(r[2] or 0 for r in rs)
        games = sum((r[2] or 0) + (r[3] or 0) + (r[4] or 0) for r in rs)
        elos = [r[1] for r in rs if r[1] is not None]
        values = [r[5] for r in rs if r[5]]
        out_rows.append({
            "key": key,
            "rankers": len(rs),
            "share": round(len(rs) / len(rows), 4),
            "season_win_rate": round(wins / games, 4) if games else None,
            "season_games": games,
            "avg_elo": round(sum(elos) / len(elos), 1) if elos else None,
            "avg_squad_value": round(sum(values) / len(values), -6) if values else None,  # 평균이라 백만 단위로
        })  # fmt: skip
    total = len(out_rows)
    out_rows = [r for r in out_rows if r["rankers"] >= min_rankers]
    if group_by == "rank_band" and sort_by == "rankers":
        out_rows.sort(key=lambda r: r["key"])  # 순위 구간은 위에서부터
    else:
        out_rows.sort(key=lambda r: (r[sort_by] is None, -(r[sort_by] or 0), -r["rankers"]))
    return {"data_as_of": as_of, "covered": covered, "rankers": len(rows), "total_groups": total, "rows": out_rows[:limit]}
