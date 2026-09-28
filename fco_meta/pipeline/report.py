"""Usage of players in a role among the rankers of one team color × formation."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

# 기본 스쿼드(match_order 0)를 가진 조합 랭커 = 분모
BASE_SQUADS_SQL = """
SELECT q.data_as_of, q.mode, q.rank, q.ouid, q.match_id
FROM ranker_squad q
JOIN ranker_snapshot s USING (data_as_of, mode, rank)
JOIN ranker_team_color m USING (data_as_of, mode, rank)
WHERE q.match_order = 0 AND q.accepted = 1
  AND m.team_color_id = :team_color_id AND s.formation = :formation
  AND q.data_as_of = :data_as_of AND q.mode = :mode
  AND (:strict = 0 OR q.formation_match = 1)
"""

# {key}: sp_id(시즌 구분) 또는 pid(시즌 무관 같은 선수)
ROLE_USAGE_SQL = """
WITH base AS ({base}),
n AS (SELECT COUNT(*) AS rankers FROM base)
SELECT p.{key} AS key,
       MAX(sp.name) AS name,
       CASE WHEN COUNT(DISTINCT p.season_id) = 1 THEN MAX(ss.class_name) END AS season,
       COUNT(DISTINCT b.rank) AS rankers,
       n.rankers AS sample,
       ROUND(1.0 * COUNT(DISTINCT b.rank) / n.rankers, 4) AS usage_rate,
       ROUND(AVG(CASE t.match_result WHEN '승' THEN 1.0 WHEN '무' THEN 0.0 ELSE 0.0 END), 4) AS win_rate,
       ROUND(AVG(p.sp_grade), 2) AS avg_grade,
       ROUND(AVG(p.sp_rating), 2) AS avg_rating
FROM base b
JOIN match_player p ON p.match_id = b.match_id AND p.ouid = b.ouid
JOIN match_team t ON t.match_id = b.match_id AND t.ouid = b.ouid
CROSS JOIN n
LEFT JOIN meta_spid sp ON sp.sp_id = p.sp_id
LEFT JOIN meta_season ss ON ss.season_id = p.season_id
WHERE p.sp_position IN ({positions})
GROUP BY p.{key}
ORDER BY rankers DESC, avg_rating DESC, key
LIMIT :top
"""


@dataclass(frozen=True)
class UsageRow:
    key: int
    name: str | None
    season: str | None
    rankers: int
    sample: int
    usage_rate: float
    win_rate: float | None
    avg_grade: float | None
    avg_rating: float | None


def latest_squad_snapshot(conn: sqlite3.Connection, team_color_id: int, formation: str, mode: str = "1vs1") -> str | None:
    row = conn.execute(
        "SELECT MAX(q.data_as_of) FROM ranker_squad q JOIN ranker_snapshot s USING (data_as_of, mode, rank)"
        " JOIN ranker_team_color m USING (data_as_of, mode, rank)"
        " WHERE m.team_color_id = ? AND s.formation = ? AND q.mode = ?",
        (team_color_id, formation, mode),
    ).fetchone()
    return row[0]


def role_usage(
    conn: sqlite3.Connection,
    team_color_id: int,
    formation: str,
    positions: Sequence[int],
    *,
    data_as_of: str | None = None,
    mode: str = "1vs1",
    by: str = "sp_id",
    top: int = 5,
    strict: bool = False,
) -> list[UsageRow]:
    """`strict`: only base squads whose inferred formation equals the snapshot formation."""
    if by not in ("sp_id", "pid"):
        raise ValueError("by must be 'sp_id' or 'pid'")
    data_as_of = data_as_of or latest_squad_snapshot(conn, team_color_id, formation, mode)
    if data_as_of is None:
        return []
    sql = ROLE_USAGE_SQL.format(base=BASE_SQUADS_SQL, key=by, positions=",".join(str(int(p)) for p in positions))
    params = {"team_color_id": team_color_id, "formation": formation, "data_as_of": data_as_of, "mode": mode, "top": top, "strict": int(strict)}
    return [UsageRow(*r) for r in conn.execute(sql, params).fetchall()]
