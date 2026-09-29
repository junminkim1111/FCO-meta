"""usage_stats: which cards the rankers of a team color × formation start in each role."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..market.roles import ROLES
from ..pipeline.store import PipelineStore
from ..storage import unfiltered_coverage

ALL_FORMATIONS = "*"  # 팀컬러 전체 (표본이 적을 때 폴백)
ALL_RANKERS = 0  # team_color_id 0 = 팀컬러 무관 전체 랭커 (필터 없이 수집한 상위 N위 범위만)
MIN_SAMPLE = 10

SCHEMA = """
-- 집계 단위 = 스냅샷 × 팀컬러 × 포메이션('*' = 전체) × strict
CREATE TABLE IF NOT EXISTS usage_sample (
    data_as_of     TEXT NOT NULL,
    mode           TEXT NOT NULL,
    team_color_id  INTEGER NOT NULL,
    formation      TEXT NOT NULL,
    strict         INTEGER NOT NULL,        -- 1: 추론 포메이션 = 스냅샷 포메이션인 스쿼드만
    combo_rankers  INTEGER NOT NULL,        -- 스냅샷에서 이 조합에 속한 랭커 수
    squads         INTEGER NOT NULL,        -- 그중 기본 스쿼드가 수집된 랭커 수 = 사용률 분모
    computed_at    TEXT NOT NULL,
    PRIMARY KEY (data_as_of, mode, team_color_id, formation, strict)
);

-- 역할(market.roles.ROLES) × 카드(sp_id)별 사용 현황. 선발(spPosition != 28)만 센다.
CREATE TABLE IF NOT EXISTS usage_stats (
    data_as_of          TEXT NOT NULL,
    mode                TEXT NOT NULL,
    team_color_id       INTEGER NOT NULL,
    formation           TEXT NOT NULL,
    strict              INTEGER NOT NULL,
    role                TEXT NOT NULL,
    sp_id               INTEGER NOT NULL,
    pid                 INTEGER NOT NULL,
    season_id           INTEGER NOT NULL,
    ranker_count        INTEGER NOT NULL,   -- 이 카드를 이 역할로 선발 기용한 랭커 수
    sample_size         INTEGER NOT NULL,   -- = usage_sample.squads
    usage_rate          REAL NOT NULL,      -- ranker_count / sample_size
    win_rate            REAL,               -- 해당 기본 스쿼드 경기 승률 (랭커당 1경기라 참고용)
    avg_rating          REAL,               -- 해당 경기 선수 평점 평균
    avg_grade           REAL,               -- 평균 강화 등급
    grade_dist          TEXT NOT NULL,      -- {"강화": 랭커 수} JSON
    avg_elo             REAL,               -- 사용 랭커 평균 랭킹 점수
    avg_ranker_win_rate REAL,               -- 사용 랭커의 시즌 승률(랭킹 페이지) 평균
    computed_at         TEXT NOT NULL,
    PRIMARY KEY (data_as_of, mode, team_color_id, formation, strict, role, sp_id)
);
CREATE INDEX IF NOT EXISTS idx_usage_lookup
    ON usage_stats (team_color_id, formation, role, strict, data_as_of);
"""

# 팀컬러 조건: 0이면 필터 없는 수집이 덮은 순위(1..:covered) 전체, 아니면 소속(ranker_team_color)
_TEAM_COLOR_FILTER = """
  AND CASE WHEN :team_color_id = 0 THEN s.rank <= :covered
      ELSE EXISTS (SELECT 1 FROM ranker_team_color m WHERE m.data_as_of = s.data_as_of AND m.mode = s.mode
                   AND m.rank = s.rank AND m.team_color_id = :team_color_id) END
"""

_BASE_SQUADS = f"""
SELECT q.rank, q.ouid, q.match_id, s.elo, s.win_rate
FROM ranker_squad q
JOIN ranker_snapshot s USING (data_as_of, mode, rank)
WHERE q.match_order = 0 AND q.accepted = 1
  AND q.data_as_of = :data_as_of AND q.mode = :mode {_TEAM_COLOR_FILTER}
  AND (:formation = '*' OR s.formation = :formation)
  AND (:strict = 0 OR q.formation_match = 1)
"""

_STARTERS = f"""
WITH base AS ({_BASE_SQUADS})
SELECT b.rank, b.elo, b.win_rate AS ranker_win_rate, t.match_result,
       p.sp_id, p.pid, p.season_id, p.sp_position, p.sp_grade, p.sp_rating
FROM base b
JOIN match_player p ON p.match_id = b.match_id AND p.ouid = b.ouid AND p.starter = 1
LEFT JOIN match_team t ON t.match_id = b.match_id AND t.ouid = b.ouid
"""

_COMBO_RANKERS = f"""
SELECT COUNT(*) FROM ranker_snapshot s
WHERE s.data_as_of = :data_as_of AND s.mode = :mode {_TEAM_COLOR_FILTER}
  AND (:formation = '*' OR s.formation = :formation)
"""

_POSITION_ROLE = {pos: role for role, positions in ROLES.items() for pos in positions}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


class UsageStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        PipelineStore(conn)  # 입력 테이블(ranker_squad, match_player …)이 없을 때도 쿼리가 돌도록
        self.conn.executescript(SCHEMA)

    def combos(self, data_as_of: str | None = None) -> list[tuple[str, str, int, str]]:
        """(data_as_of, mode, team_color_id, formation) that have at least one collected squad.

        Includes team_color_id 0 (all rankers) for snapshots an unfiltered crawl covered.
        """
        where = " AND q.data_as_of = ?" if data_as_of else ""
        args = [data_as_of] if data_as_of else []
        rows = {
            tuple(r)
            for r in self.conn.execute(
                "SELECT DISTINCT q.data_as_of, q.mode, m.team_color_id, s.formation FROM ranker_squad q"
                " JOIN ranker_snapshot s USING (data_as_of, mode, rank)"
                " JOIN ranker_team_color m USING (data_as_of, mode, rank)"
                f" WHERE q.match_order = 0 AND s.formation IS NOT NULL{where}",
                args,
            )
        }
        for as_of, mode in self.conn.execute(
            f"SELECT DISTINCT q.data_as_of, q.mode FROM ranker_squad q WHERE q.match_order = 0{where}", args
        ).fetchall():
            covered = unfiltered_coverage(self.conn, as_of, mode)
            if not covered:
                continue
            for (formation,) in self.conn.execute(
                "SELECT DISTINCT s.formation FROM ranker_squad q JOIN ranker_snapshot s USING (data_as_of, mode, rank)"
                " WHERE q.match_order = 0 AND q.data_as_of = ? AND q.mode = ? AND q.rank <= ? AND s.formation IS NOT NULL",
                (as_of, mode, covered),
            ):
                rows.add((as_of, mode, ALL_RANKERS, formation))
        return sorted(rows)

    def build(self, data_as_of: str, mode: str, team_color_id: int, formation: str, strict: bool) -> int:
        """(Re)compute usage for one combo. Returns the number of usage_stats rows written."""
        params = {
            "data_as_of": data_as_of, "mode": mode, "team_color_id": team_color_id,
            "formation": formation, "strict": int(strict),
            "covered": unfiltered_coverage(self.conn, data_as_of, mode) if team_color_id == ALL_RANKERS else 0,
        }  # fmt: skip
        key = (data_as_of, mode, team_color_id, formation, int(strict))
        squads = self.conn.execute(f"SELECT COUNT(*) FROM ({_BASE_SQUADS})", params).fetchone()[0]
        combo_rankers = self.conn.execute(_COMBO_RANKERS, params).fetchone()[0]
        where = "data_as_of = ? AND mode = ? AND team_color_id = ? AND formation = ? AND strict = ?"
        self.conn.execute(f"DELETE FROM usage_stats WHERE {where}", key)
        self.conn.execute(f"DELETE FROM usage_sample WHERE {where}", key)
        if squads == 0:
            self.conn.commit()
            return 0
        now = _now()
        self.conn.execute(
            "INSERT INTO usage_sample VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (*key, combo_rankers, squads, now)
        )

        groups: dict[tuple[str, int], list[sqlite3.Row]] = defaultdict(list)
        for r in self.conn.execute(_STARTERS, params):
            role = _POSITION_ROLE.get(r["sp_position"])
            if role is not None:
                groups[(role, r["sp_id"])].append(r)

        rows = []
        for (role, sp_id), used in groups.items():
            by_ranker = {u["rank"]: u for u in used}.values()  # 같은 역할에 같은 카드는 랭커당 1번
            n = len(by_ranker)
            grades = [u["sp_grade"] for u in by_ranker]
            results = [u["match_result"] for u in by_ranker if u["match_result"]]
            first = next(iter(by_ranker))
            rows.append((
                *key[:4], key[4], role, sp_id, first["pid"], first["season_id"],
                n, squads, round(n / squads, 4),
                round(results.count("승") / len(results), 4) if results else None,
                _mean([u["sp_rating"] for u in by_ranker]),
                _mean(grades),
                json.dumps(dict(sorted(Counter(g for g in grades if g is not None).items()))),
                _mean([u["elo"] for u in by_ranker]),
                _mean([u["ranker_win_rate"] for u in by_ranker]),
                now,
            ))  # fmt: skip
        self.conn.executemany(f"INSERT INTO usage_stats VALUES ({', '.join('?' * 19)})", rows)
        self.conn.commit()
        return len(rows)

    def build_all(self, data_as_of: str | None = None) -> list[tuple[tuple[str, str, int, str, bool], int]]:
        """Every combo with squads (optionally one snapshot), plus the all-formation fallback per
        team color; strict and not."""
        keys: dict[tuple[str, str, int, str], None] = {}  # 순서 유지 + 중복 제거
        for as_of, mode, tc, formation in self.combos(data_as_of):
            keys[(as_of, mode, tc, formation)] = None
            keys[(as_of, mode, tc, ALL_FORMATIONS)] = None
        done = []
        for as_of, mode, tc, formation in keys:
            for strict in (False, True):
                done.append(((as_of, mode, tc, formation, strict), self.build(as_of, mode, tc, formation, strict)))
        return done


@dataclass(frozen=True)
class SeasonUsage:
    sp_id: int
    season_id: int
    season: str | None
    ranker_count: int
    avg_grade: float | None


@dataclass(frozen=True)
class PlayerUsage:
    key: int  # sp_id 또는 pid
    name: str | None
    ranker_count: int
    usage_rate: float
    win_rate: float | None
    avg_rating: float | None
    avg_grade: float | None
    grade_dist: dict[int, int]
    avg_elo: float | None
    avg_ranker_win_rate: float | None = None  # 사용 랭커들의 시즌 승률(랭킹 페이지) 평균
    seasons: list[SeasonUsage] = field(default_factory=list)  # 많이 쓰인 순


@dataclass(frozen=True)
class UsageResult:
    team_color_id: int
    formation: str  # 실제로 집계에 쓴 포메이션 (폴백 시 '*')
    requested_formation: str
    role: str
    strict: bool
    data_as_of: str | None
    combo_rankers: int
    sample_size: int
    players: list[PlayerUsage]

    @property
    def fallback(self) -> bool:
        return self.formation != self.requested_formation


def top_players(
    conn: sqlite3.Connection,
    team_color_id: int,
    formation: str,
    role: str | tuple[str, ...],
    *,
    top: int = 5,
    by: str = "pid",
    strict: bool = False,
    mode: str = "1vs1",
    data_as_of: str | None = None,
    min_sample: int = MIN_SAMPLE,
) -> UsageResult:
    """Most used cards (`by="sp_id"`) or players across seasons (`by="pid"`) in `role`
    (or several roles, e.g. ("RW", "LW") — a player starts once per squad, so counts add up).

    Falls back to the team color's all-formation stats when fewer than `min_sample` squads
    of `formation` were collected.
    """
    roles = (role,) if isinstance(role, str) else tuple(role)
    if not roles or any(r not in ROLES for r in roles):
        raise ValueError(f"unknown role: {role}")
    if by not in ("sp_id", "pid"):
        raise ValueError("by must be 'sp_id' or 'pid'")
    UsageStore(conn)
    result = _query(conn, team_color_id, formation, roles, top, by, strict, mode, data_as_of)
    if result.sample_size < min_sample and formation != ALL_FORMATIONS:
        fallback = _query(conn, team_color_id, ALL_FORMATIONS, roles, top, by, strict, mode, data_as_of)
        if fallback.sample_size > result.sample_size:
            return UsageResult(**{**fallback.__dict__, "requested_formation": formation})
    return result


def _query(conn, team_color_id, formation, roles, top, by, strict, mode, data_as_of) -> UsageResult:
    role = "+".join(roles)
    sample_where = "team_color_id = ? AND formation = ? AND strict = ? AND mode = ?"
    args = [team_color_id, formation, int(strict), mode]
    if data_as_of is None:
        row = conn.execute(f"SELECT MAX(data_as_of) FROM usage_sample WHERE {sample_where}", args).fetchone()
        data_as_of = row[0]
    sample = conn.execute(
        f"SELECT combo_rankers, squads FROM usage_sample WHERE {sample_where} AND data_as_of = ?", [*args, data_as_of]
    ).fetchone()
    if sample is None:
        return UsageResult(team_color_id, formation, formation, role, strict, data_as_of, 0, 0, [])
    combo_rankers, squads = sample

    rows = conn.execute(
        "SELECT u.*, sp.name, ss.class_name FROM usage_stats u"
        " LEFT JOIN meta_spid sp ON sp.sp_id = u.sp_id"
        " LEFT JOIN meta_season ss ON ss.season_id = u.season_id"
        f" WHERE u.{sample_where.replace(' AND ', ' AND u.')} AND u.data_as_of = ?"
        f" AND u.role IN ({','.join('?' * len(roles))})",
        [*args, data_as_of, *roles],
    ).fetchall()

    grouped: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        grouped[r[by]].append(r)
    players = [_merge(key, rs, squads) for key, rs in grouped.items()]
    players.sort(key=lambda p: (-p.ranker_count, -(p.avg_rating or 0), p.key))
    return UsageResult(team_color_id, formation, formation, role, strict, data_as_of, combo_rankers, squads, players[:top])


def _merge(key: int, rows: list[sqlite3.Row], squads: int) -> PlayerUsage:
    """Combine card rows of one player. A squad cannot hold two cards of the same player,
    so ranker counts add up without double counting."""
    n = sum(r["ranker_count"] for r in rows)

    def weighted(col: str) -> float | None:
        pairs = [(r[col], r["ranker_count"]) for r in rows if r[col] is not None]
        if not pairs:
            return None
        return round(sum(v * w for v, w in pairs) / sum(w for _, w in pairs), 4)

    dist: Counter[int] = Counter()
    for r in rows:
        dist.update({int(g): c for g, c in json.loads(r["grade_dist"]).items()})
    by_card: dict[int, list[sqlite3.Row]] = defaultdict(list)  # 여러 역할을 합치면 같은 카드가 역할마다 한 행
    for r in rows:
        by_card[r["sp_id"]].append(r)
    seasons = []
    for sp_id, rs in by_card.items():
        count = sum(r["ranker_count"] for r in rs)
        grades = [(r["avg_grade"], r["ranker_count"]) for r in rs if r["avg_grade"] is not None]
        avg_grade = round(sum(g * w for g, w in grades) / sum(w for _, w in grades), 4) if grades else None
        seasons.append(SeasonUsage(sp_id, rs[0]["season_id"], rs[0]["class_name"], count, avg_grade))
    seasons.sort(key=lambda s: -s.ranker_count)
    return PlayerUsage(
        key=key,
        name=next((r["name"] for r in rows if r["name"]), None),
        ranker_count=n,
        usage_rate=round(n / squads, 4),
        win_rate=weighted("win_rate"),
        avg_rating=weighted("avg_rating"),
        avg_grade=weighted("avg_grade"),
        grade_dist=dict(sorted(dist.items())),
        avg_elo=weighted("avg_elo"),
        avg_ranker_win_rate=weighted("avg_ranker_win_rate"),
        seasons=seasons,
    )


@dataclass(frozen=True)
class RoleUsage:
    role: str
    rank: int  # 이 역할에서 사용 랭커 수 순위 (1 = 가장 많이 쓰임)
    players_in_role: int
    usage: PlayerUsage


def player_roles(
    conn: sqlite3.Connection,
    team_color_id: int,
    formation: str,
    pid: int,
    *,
    strict: bool = False,
    mode: str = "1vs1",
) -> tuple[str | None, int, list[RoleUsage]]:
    """(data_as_of, sample size, roles the player started in, most used first) for the latest snapshot."""
    UsageStore(conn)
    where = "team_color_id = ? AND formation = ? AND strict = ? AND mode = ?"
    args = [team_color_id, formation, int(strict), mode]
    row = conn.execute(f"SELECT MAX(data_as_of) FROM usage_sample WHERE {where}", args).fetchone()
    data_as_of = row[0]
    if data_as_of is None:
        return None, 0, []
    squads = conn.execute(f"SELECT squads FROM usage_sample WHERE {where} AND data_as_of = ?", [*args, data_as_of]).fetchone()[0]
    rows = conn.execute(
        "SELECT u.*, sp.name, ss.class_name FROM usage_stats u"
        " LEFT JOIN meta_spid sp ON sp.sp_id = u.sp_id"
        " LEFT JOIN meta_season ss ON ss.season_id = u.season_id"
        f" WHERE u.{where.replace(' AND ', ' AND u.')} AND u.data_as_of = ? AND u.pid = ?",
        [*args, data_as_of, pid],
    ).fetchall()
    by_role: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_role[r["role"]].append(r)
    out = []
    for role, rs in by_role.items():
        usage = _merge(pid, rs, squads)
        counts = [
            n for (n,) in conn.execute(
                f"SELECT SUM(ranker_count) FROM usage_stats WHERE {where} AND data_as_of = ? AND role = ? GROUP BY pid",
                [*args, data_as_of, role],
            )
        ]  # fmt: skip
        out.append(RoleUsage(role, 1 + sum(n > usage.ranker_count for n in counts), len(counts), usage))
    out.sort(key=lambda r: -r.usage.ranker_count)
    return data_as_of, squads, out


def usage_history(
    conn: sqlite3.Connection, team_color_id: int, formation: str, pid: int, *, limit: int = 14, mode: str = "1vs1"
) -> list[dict]:
    """Per snapshot (oldest first): how many sampled rankers started the player in any role."""
    UsageStore(conn)
    samples = conn.execute(
        "SELECT data_as_of, squads FROM usage_sample WHERE team_color_id = ? AND formation = ? AND strict = 0 AND mode = ?"
        " ORDER BY data_as_of DESC LIMIT ?",
        (team_color_id, formation, mode, limit),
    ).fetchall()
    used = dict(
        conn.execute(
            "SELECT data_as_of, SUM(ranker_count) FROM usage_stats WHERE team_color_id = ? AND formation = ? AND strict = 0"
            " AND mode = ? AND pid = ? GROUP BY data_as_of",
            (team_color_id, formation, mode, pid),
        ).fetchall()
    )
    return [
        {"data_as_of": as_of, "rankers": used.get(as_of, 0), "sample_size": n, "usage_rate": round(used.get(as_of, 0) / n, 4)}
        for as_of, n in reversed(samples)
    ]


def squad_range(conn: sqlite3.Connection, data_as_of: str, mode: str = "1vs1") -> int:
    """Squad collection range of a snapshot: the largest k such that ranks 1..k were all processed.

    The ranking may cover TOP 10,000 while squads cover only the top few hundred (API limit), and
    team-color collections add scattered deeper ranks — those don't extend the range.
    """
    UsageStore(conn)
    k = 0
    for (rank,) in conn.execute(
        "SELECT DISTINCT rank FROM ranker_squad_status WHERE data_as_of = ? AND mode = ? ORDER BY rank", (data_as_of, mode)
    ):
        if rank != k + 1:
            break
        k = rank
    return k
