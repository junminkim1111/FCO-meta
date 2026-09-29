"""Ad-hoc aggregation over the collected starting XIs (챗봇의 범용 조회).

usage_stats is pre-aggregated per team color × formation × role, so it cannot answer questions
such as "top 100 only", "who plays next to Rice" or "which grade is most common". This module
filters the raw base squads (≈ 300 rankers × 11 starters per snapshot) and groups them on demand.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from ..market.roles import ROLES
from ..storage import unfiltered_coverage
from .usage import ALL_RANKERS, UsageStore

GROUP_BY = ("player", "card", "season", "grade", "team_color", "formation", "role")
SORT_BY = (
    "rankers", "avg_rating", "season_win_rate", "win_rate", "avg_grade", "avg_elo", "avg_price", "avg_salary", "price", "stat",
    "match_stat",
)  # fmt: skip

# 경기 기록 값: 이름 → (분자 필드, 분모 필드 또는 None = 경기당 평균). 비율은 출전들을 합친 성공/시도.
# 필드는 match-detail player.status / ranker-stats status 이름 그대로 (API 표기 'ballPossesion' 포함)
MATCH_STATS: dict[str, tuple[str, str | None]] = {
    "goal": ("goal", None), "assist": ("assist", None), "shoot": ("shoot", None), "effective_shoot": ("effectiveShoot", None),
    "pass_try": ("passTry", None), "pass_success_rate": ("passSuccess", "passTry"),
    "dribble": ("dribble", None), "dribble_success_rate": ("dribbleSuccess", "dribbleTry"),
    "tackle": ("tackle", None), "block": ("block", None),
    # 아래는 우리 경기 기록(rankers)에만 있음
    "intercept": ("intercept", None), "defending": ("defending", None),
    "aerial_success_rate": ("aerialSuccess", "aerialTry"), "ball_possession_success_rate": ("ballPossesionSuccess", "ballPossesionTry"),
}  # fmt: skip
TOP10000_STATS = ("goal", "assist", "shoot", "effective_shoot", "pass_try", "pass_success_rate", "dribble",
                  "dribble_success_rate", "tackle", "block")  # fmt: skip
MATCH_STAT_SOURCES = ("top10000", "rankers")

# 카드 상세(card_detail, 1강 기준)에서 꺼낼 수 있는 값: 세부 능력치 34개 + 요약 능력치 + 신체·개인기·약발
DETAIL_STATS = (
    "속력", "가속력", "골 결정력", "슛 파워", "중거리 슛", "위치 선정", "발리슛", "페널티 킥", "짧은 패스", "시야", "크로스",
    "긴 패스", "프리킥", "커브", "드리블", "볼 컨트롤", "민첩성", "밸런스", "반응 속도", "대인 수비", "태클", "가로채기",
    "헤더", "슬라이딩 태클", "몸싸움", "스태미너", "적극성", "점프", "침착성",
    "GK 다이빙", "GK 핸들링", "GK 킥", "GK 반응속도", "GK 위치 선정",
)  # fmt: skip
SUMMARY_STATS = ("스피드", "슛", "패스", "수비", "피지컬")  # 요약 드리블은 세부 '드리블'과 이름이 같아 제외
PROFILE_STATS = ("키", "몸무게", "개인기", "약발")
STAT_NAMES = DETAIL_STATS + SUMMARY_STATS + PROFILE_STATS


def resolve_stat(name: str) -> str | None:
    """"골결정력" / "GK다이빙" → 표준 이름 (공백·대소문자 무시). 모르면 None."""
    key = "".join(name.split()).casefold()
    return next((s for s in STAT_NAMES if "".join(s.split()).casefold() == key), None)


def stat_value(detail: dict[str, Any] | None, stat: str) -> int | None:
    if detail is None:
        return None
    if stat in PROFILE_STATS:
        if stat == "약발":
            feet = [f for f in (detail["left_foot"], detail["right_foot"]) if f is not None]
            return min(feet) if feet else None
        return detail[{"키": "height", "몸무게": "weight", "개인기": "skill_moves"}[stat]]
    return detail["stats"].get(stat) if stat in DETAIL_STATS else detail["summary"].get(stat)
PRICED_GROUPS = ("player", "card")

_POSITION_ROLE = {pos: role for role, positions in ROLES.items() for pos in positions}

_STARTERS = """
WITH base AS (
  SELECT q.rank, q.ouid, q.match_id, s.elo, s.formation, s.wins, s.draws, s.losses, t.match_result
  FROM ranker_squad q
  JOIN ranker_snapshot s USING (data_as_of, mode, rank)
  LEFT JOIN match_team t ON t.match_id = q.match_id AND t.ouid = q.ouid
  WHERE q.match_order = 0 AND q.accepted = 1 AND q.data_as_of = :as_of AND q.mode = :mode
    AND (:tc != 0 OR :covered = 0 OR s.rank <= :covered)
    AND (:tc = 0 OR EXISTS (SELECT 1 FROM ranker_team_color m WHERE m.data_as_of = s.data_as_of
                            AND m.mode = s.mode AND m.rank = s.rank AND m.team_color_id = :tc))
    AND (:formation IS NULL OR s.formation = :formation)
    AND (:rank_min IS NULL OR s.rank >= :rank_min)
    AND (:rank_max IS NULL OR s.rank <= :rank_max)
    AND (:elo_min IS NULL OR s.elo >= :elo_min)
    AND (:strict = 0 OR q.formation_match = 1)
)
SELECT b.rank, b.elo, b.formation, b.wins, b.draws, b.losses, b.match_result,
       p.sp_id, p.pid, p.season_id, p.sp_position, p.sp_grade, p.sp_rating, p.stats
FROM base b
JOIN match_player p ON p.match_id = b.match_id AND p.ouid = b.ouid AND p.starter = 1
"""


@dataclass(frozen=True)
class SquadQuery:
    team_color_id: int = ALL_RANKERS
    formation: str | None = None
    rank_min: int | None = None
    rank_max: int | None = None
    elo_min: float | None = None
    strict: bool = False
    with_pid: int | None = None  # 이 선수를 선발로 쓴 랭커만
    stat: str | None = None  # 카드 상세 값 (STAT_NAMES): 행마다 평균을 내고, stat_min이 있으면 그 이상인 출전만 센다
    stat_min: float | None = None
    match_stat: str | None = None  # 경기 기록 값 (MATCH_STATS)
    match_stat_source: str = "top10000"  # top10000: 랭커 스탯(TOP 10,000 20경기 평균), rankers: 우리 랭커의 기준 경기 1경기
    roles: tuple[str, ...] | None = None  # 이 역할(들)로 선발 출전한 선수만 센다
    data_as_of: str | None = None  # None = 가장 최근 스냅샷
    mode: str = "1vs1"


def squad_snapshot(conn: sqlite3.Connection, date: str | None = None, mode: str = "1vs1") -> str | None:
    """Latest snapshot with collected squads (optionally on a KST date 'YYYY-MM-DD')."""
    UsageStore(conn)
    row = conn.execute(
        "SELECT MAX(data_as_of) FROM ranker_squad WHERE match_order = 0 AND mode = ? AND (? IS NULL OR data_as_of LIKE ? || '%')",
        (mode, date, date),
    ).fetchone()
    return row[0]


def _mean(values: list[float | None]) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def query_squads(
    conn: sqlite3.Connection,
    q: SquadQuery,
    group_by: str,
    *,
    sort_by: str = "rankers",
    min_rankers: int = 1,
    limit: int = 10,
) -> dict[str, Any]:
    """Group the starters of the base squads in scope.

    Returns {"data_as_of", "covered", "squads" (= usage_rate denominator), "total_groups", "rows"}.
    Each row: key fields, rankers (squads with a match), usage_rate, avg_rating / avg_grade /
    avg_price / avg_salary (per appearance, with coverage), season_win_rate (pooled W/D/L of the
    rankers from the ranking page), win_rate (their one base match) / avg_elo, and for player/card
    rows the most used card, its most used grade, that price and the card's salary.
    """
    if group_by not in GROUP_BY:
        raise ValueError(f"group_by must be one of {GROUP_BY}")
    if sort_by not in SORT_BY:
        raise ValueError(f"sort_by must be one of {SORT_BY}")
    if sort_by == "price" and group_by not in PRICED_GROUPS:
        raise ValueError("sort_by=price needs group_by player or card")
    if (sort_by == "stat" or q.stat_min is not None) and q.stat is None:
        raise ValueError("sort_by=stat and stat_min need stat")
    if q.stat is not None and q.stat not in STAT_NAMES:
        raise ValueError(f"unknown stat: {q.stat}")
    if sort_by == "match_stat" and q.match_stat is None:
        raise ValueError("sort_by=match_stat needs match_stat")
    if q.match_stat is not None:
        if q.match_stat_source not in MATCH_STAT_SOURCES:
            raise ValueError(f"match_stat_source must be one of {MATCH_STAT_SOURCES}")
        if q.match_stat not in (TOP10000_STATS if q.match_stat_source == "top10000" else MATCH_STATS):
            raise ValueError(f"unknown match_stat for {q.match_stat_source}: {q.match_stat}")
    as_of = q.data_as_of or squad_snapshot(conn, mode=q.mode)
    empty = {"data_as_of": as_of, "covered": 0, "squads": 0, "total_groups": 0, "rows": []}
    if as_of is None:
        return empty
    covered = unfiltered_coverage(conn, as_of, q.mode) if q.team_color_id == ALL_RANKERS else 0
    params = {
        "as_of": as_of, "mode": q.mode, "tc": q.team_color_id, "covered": covered, "formation": q.formation,
        "rank_min": q.rank_min, "rank_max": q.rank_max, "elo_min": q.elo_min, "strict": int(q.strict),
    }  # fmt: skip
    squads: dict[int, list[sqlite3.Row]] = defaultdict(list)
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    for r in cur.execute(_STARTERS, params):
        squads[r["rank"]].append(r)
    if q.with_pid is not None:
        squads = {rank: rows for rank, rows in squads.items() if any(r["pid"] == q.with_pid for r in rows)}
    out = {**empty, "covered": covered, "squads": len(squads)}
    if not squads:
        return out

    sp_ids = {r["sp_id"] for rows in squads.values() for r in rows}
    prices, salaries = _card_values(conn, sp_ids)
    details = _card_details(conn, sp_ids) if q.stat else {}
    top10000 = _top10000(conn, sp_ids) if q.match_stat and q.match_stat_source == "top10000" else {}

    teams: dict[int, set[int]] = defaultdict(set)
    if group_by == "team_color":
        for rank, tc in conn.execute(
            "SELECT rank, team_color_id FROM ranker_team_color WHERE data_as_of = ? AND mode = ?", (as_of, q.mode)
        ):
            teams[rank].add(tc)

    groups: dict[Any, dict[str, Any]] = {}
    for rank, rows in squads.items():
        for r in rows:
            role = _POSITION_ROLE.get(r["sp_position"])
            if q.roles and role not in q.roles:
                continue
            if q.with_pid is not None and r["pid"] == q.with_pid and group_by in PRICED_GROUPS:
                continue  # 기준 선수 자신은 결과에서 뺀다
            value = stat_value(details.get(r["sp_id"]), q.stat) if q.stat else None
            if q.stat_min is not None and (value is None or value < q.stat_min):
                continue
            keys = {
                "player": [r["pid"]], "card": [r["sp_id"]], "season": [r["season_id"]], "grade": [r["sp_grade"]],
                "role": [role], "formation": [r["formation"]], "team_color": sorted(teams.get(rank, ())),
            }[group_by]  # fmt: skip
            for key in keys:
                if key is None:
                    continue
                g = groups.setdefault(
                    key, {"ranks": {}, "ratings": [], "grades": [], "prices": [], "salaries": [], "stats": [], "match": [],
                          "pairs": {}, "cards": Counter()}
                )
                g["stats"].append(value)
                if q.match_stat:
                    if q.match_stat_source == "top10000":
                        found = top10000.get((r["sp_id"], r["sp_position"]))
                        g["match"].append(found[1] if found else None)
                        if found:
                            g["pairs"][(r["sp_id"], r["sp_position"])] = found[0]
                    else:
                        g["match"].append(json.loads(r["stats"]) if r["stats"] else None)
                g["ranks"][rank] = r
                g["ratings"].append(r["sp_rating"])
                g["grades"].append(r["sp_grade"])
                g["prices"].append(prices.get((r["sp_id"], r["sp_grade"])))
                g["salaries"].append(salaries.get(r["sp_id"]))
                g["cards"][(r["sp_id"], r["sp_grade"])] += 1

    rows_out = []
    for key, g in groups.items():
        ranks = g["ranks"].values()
        results = [r["match_result"] for r in ranks if r["match_result"]]
        wins = sum(r["wins"] or 0 for r in ranks)
        games = sum((r["wins"] or 0) + (r["draws"] or 0) + (r["losses"] or 0) for r in ranks)
        n = len(g["prices"])
        row: dict[str, Any] = {
            "key": key,
            "rankers": len(ranks),
            "usage_rate": round(len(ranks) / len(squads), 4),
            "avg_rating": _mean(g["ratings"]),
            "season_win_rate": round(wins / games, 4) if games else None,
            "season_games": games,
            "win_rate": round(results.count("승") / len(results), 4) if results else None,
            "avg_grade": _mean(g["grades"]),
            "avg_elo": _mean([r["elo"] for r in ranks]),
            "avg_price": _mean(g["prices"]),
            "price_coverage": round(sum(p is not None for p in g["prices"]) / n, 4) if n else None,
            "avg_salary": _mean(g["salaries"]),
            "salary_coverage": round(sum(x is not None for x in g["salaries"]) / n, 4) if n else None,
        }
        if q.stat:
            row["stat"] = _mean(g["stats"])
            row["stat_coverage"] = round(sum(x is not None for x in g["stats"]) / n, 4) if n else None
        if q.match_stat:
            known = [m for m in g["match"] if m is not None]
            row["match_stat"] = _combine(known, *MATCH_STATS[q.match_stat])
            row["match_stat_coverage"] = round(len(known) / n, 4) if n else None
            # 표본: top10000 = 그 카드·포지션들의 랭커 경기 수 합, rankers = 기록이 있는 출전 수
            row["match_stat_matches"] = sum(g["pairs"].values()) if q.match_stat_source == "top10000" else len(known)
        if group_by in PRICED_GROUPS:
            by_card = Counter()
            for (sp_id, _), n in g["cards"].items():
                by_card[sp_id] += n
            sp_id = by_card.most_common(1)[0][0]
            grade = Counter({gr: n for (sid, gr), n in g["cards"].items() if sid == sp_id}).most_common(1)[0][0]
            row.update(top_sp_id=sp_id, top_grade=grade, price_bp=prices.get((sp_id, grade)), salary=salaries.get(sp_id))
        rows_out.append(row)

    total = len(rows_out)
    rows_out = [r for r in rows_out if r["rankers"] >= min_rankers]
    if sort_by == "price":
        rows_out = sorted((r for r in rows_out if r["price_bp"] is not None), key=lambda r: (r["price_bp"], -r["rankers"]))
    else:
        # 값이 없는 행은 뒤로, 같으면 사용 랭커 수가 많은 순
        rows_out.sort(key=lambda r: (r[sort_by] is None, -(r[sort_by] or 0), -r["rankers"], r["key"]))
    return {**out, "total_groups": total, "rows": rows_out[:limit]}


def _combine(stats: list[dict[str, Any]], field: str, per: str | None) -> float | None:
    """Mean of `field` over the given status dicts, or pooled success rate field / per."""
    if per is None:
        return _mean([s.get(field) for s in stats])
    tries = sum(s.get(per) or 0 for s in stats)
    return round(sum(s.get(field) or 0 for s in stats) / tries, 4) if tries else None


def _top10000(conn: sqlite3.Connection, sp_ids: set[int]) -> dict[tuple[int, int], tuple[int, dict[str, Any]]]:
    """(spid, 포지션) → (TOP 10,000 랭커 경기 수, 경기당 평균 status)."""
    marks = ",".join("?" * len(sp_ids))
    try:
        rows = conn.execute(
            f"SELECT sp_id, sp_position, match_count, stats FROM ranker_stats WHERE sp_id IN ({marks}) AND match_count > 0",
            sorted(sp_ids),
        ).fetchall()
    except sqlite3.OperationalError:  # 랭커 스탯 수집 전
        return {}
    return {(sp, po): (count, json.loads(stats)) for sp, po, count, stats in rows}


def ranker_stats_summary(conn: sqlite3.Connection, sp_ids: set[int], positions: set[int]) -> dict[str, Any] | None:
    """TOP 10,000 rankers' per-match averages of these cards at these positions, weighted by matches."""
    pairs = [(n, s) for (sp, po), (n, s) in _top10000(conn, sp_ids).items() if po in positions] if sp_ids else []
    if not pairs:
        return None
    total = sum(n for n, _ in pairs)
    out: dict[str, Any] = {"matches": total}
    for name in TOP10000_STATS:
        field, per = MATCH_STATS[name]
        if per is None:
            out[name] = round(sum((s.get(field) or 0) * n for n, s in pairs) / total, 3)
        else:
            tries = sum((s.get(per) or 0) * n for n, s in pairs)
            out[name] = round(sum((s.get(field) or 0) * n for n, s in pairs) / tries, 4) if tries else None
    return out


def _card_details(conn: sqlite3.Connection, sp_ids: set[int]) -> dict[int, dict[str, Any]]:
    marks = ",".join("?" * len(sp_ids))
    try:
        rows = conn.execute(
            "SELECT spid, stats, summary, height, weight, skill_moves, left_foot, right_foot FROM card_detail"
            f" WHERE spid IN ({marks})",
            sorted(sp_ids),
        ).fetchall()
    except sqlite3.OperationalError:  # 상세 테이블 전
        return {}
    return {
        spid: {"stats": json.loads(stats), "summary": json.loads(summary), "height": h, "weight": w,
               "skill_moves": sm, "left_foot": lf, "right_foot": rf}
        for spid, stats, summary, h, w, sm, lf, rf in rows
    }  # fmt: skip


def _card_values(conn: sqlite3.Connection, sp_ids: set[int]) -> tuple[dict[tuple[int, int], int], dict[int, int]]:
    """({(spid, 강화): 최신 시세}, {spid: 급여}) for the given cards; empty before the market tables exist."""
    if not sp_ids:
        return {}, {}
    marks = ",".join("?" * len(sp_ids))
    ids = sorted(sp_ids)
    try:
        prices = {
            (spid, grade): price for spid, grade, price in conn.execute(
                f"SELECT spid, grade, price FROM card_price_latest WHERE spid IN ({marks}) AND price IS NOT NULL", ids
            )
        }  # fmt: skip
        salaries = dict(conn.execute(f"SELECT spid, salary FROM card WHERE spid IN ({marks}) AND salary IS NOT NULL", ids))
    except sqlite3.OperationalError:  # 시세 테이블 전
        return {}, {}
    return prices, salaries
