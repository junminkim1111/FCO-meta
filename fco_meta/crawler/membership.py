"""Team color membership from the team color *shown* on each ranking row.

Filtered crawls (`tc_01=…`) give exact membership. An unfiltered crawl (e.g. the daily TOP N)
only shows one team color per row, so membership is inferred from it:

- club team color: the row has a club crest (`l<id>`) → the club entry with that name
- nationality team color: no crest → the nationality entry with that name ("프랑스" club vs nation)
- special team color ("Winning Streak" …): the special entry, plus the club behind it found by
  its crest (crest → club learned from other rows that show the club name with the same crest)

Rows whose team color can't be resolved are left without membership.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .models import TeamColor
from .teamcolors import TeamColorCatalog


@dataclass(frozen=True)
class MembershipResult:
    rows: int
    recorded: int
    unresolved: int


def _pick(candidates: list[TeamColor], category: str) -> TeamColor | None:
    return next((t for t in candidates if t.category == category), None)


def infer_team_colors(
    name: str | None, crest: str | None, boost: str | None, catalog: TeamColorCatalog, crest_clubs: dict[str, TeamColor]
) -> list[TeamColor]:
    if not name:
        return []
    found = catalog.find(name)
    if boost:
        special = _pick(found, "special")
        club = crest_clubs.get(crest) if crest else None
        return [t for t in (special, club) if t]
    if crest:
        club = _pick(found, "club")
        return [club] if club else []
    nation = _pick(found, "nationality") or _pick(found, "club")
    return [nation] if nation else []


def crest_club_map(conn: sqlite3.Connection, catalog: TeamColorCatalog) -> dict[str, TeamColor]:
    """Crest id → club, from every non-special row seen so far (e.g. "l1" → 아스널)."""
    out: dict[str, TeamColor] = {}
    for name, crest in conn.execute(
        "SELECT DISTINCT team_color_name, team_color_crest FROM ranker_snapshot"
        " WHERE team_color_crest IS NOT NULL AND team_color_boost IS NULL"
    ):
        club = _pick(catalog.find(name or ""), "club")
        if club:
            out.setdefault(crest, club)
    return out


def record_displayed_membership(
    conn: sqlite3.Connection,
    data_as_of: str,
    mode: str,
    catalog: TeamColorCatalog | None = None,
    run_id: int | None = None,
) -> MembershipResult:
    """Add `ranker_team_color` rows (source='display') for one snapshot."""
    catalog = catalog or TeamColorCatalog.load()
    crest_clubs = crest_club_map(conn, catalog)
    rows = conn.execute(
        "SELECT rank, run_id, team_color_name, team_color_crest, team_color_boost FROM ranker_snapshot"
        " WHERE data_as_of = ? AND mode = ?",
        (data_as_of, mode),
    ).fetchall()
    inserts = []
    unresolved = 0
    for rank, row_run, name, crest, boost in rows:
        teams = infer_team_colors(name, crest, boost, catalog, crest_clubs)
        if not teams:
            unresolved += 1
        inserts += [(data_as_of, mode, rank, t.id, run_id or row_run, "display") for t in teams]
    # 필터 조회로 이미 확인된 소속(source='filter')은 덮어쓰지 않는다
    conn.executemany(
        "INSERT OR IGNORE INTO ranker_team_color (data_as_of, mode, rank, team_color_id, run_id, source)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        inserts,
    )
    conn.commit()
    return MembershipResult(len(rows), len(inserts), unresolved)
