"""What the web page shows for one formation: a top ranker's squad, the rankers' best XI and the
results against each opponent formation."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from typing import Any

from ..pipeline.formation import SUB, FormationTable
from .query import squad_snapshot
from .usage import UsageStore

_PLAYERS = """
SELECT p.sp_id, p.pid, p.sp_position, p.sp_grade, p.sp_rating, sp.name, ss.class_name, ss.season_img
FROM match_player p
LEFT JOIN meta_spid sp ON sp.sp_id = p.sp_id
LEFT JOIN meta_season ss ON ss.season_id = p.season_id
WHERE p.match_id = ? AND p.ouid = ? AND p.starter = 1
ORDER BY p.sp_position
"""


def _player(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "sp_id": row["sp_id"], "pid": row["pid"], "position": row["sp_position"], "name": row["name"],
        "season": row["class_name"], "season_img": row["season_img"] or None, "grade": row["sp_grade"],
        "rating": row["sp_rating"],
    }  # fmt: skip


def _score(conn: sqlite3.Connection, match_id: str, ouid: str) -> str | None:
    """"2:1" (this side first) from the stored team details, if both sides have them."""
    goals = {}
    for side, detail in conn.execute("SELECT ouid, detail FROM match_team WHERE match_id = ?", (match_id,)):
        shoot = (json.loads(detail) if detail else {}).get("shoot") or {}
        goals[side] = shoot.get("goalTotal")
    mine = goals.pop(ouid, None)
    theirs = next(iter(goals.values()), None)
    return f"{mine}:{theirs}" if mine is not None and theirs is not None else None


def top_ranker_squad(conn: sqlite3.Connection, formation: str, mode: str = "1vs1") -> dict[str, Any] | None:
    """Highest-ranked ranker of the latest squad snapshot who plays `formation` (actual line-up first)."""
    as_of = squad_snapshot(conn, mode=mode)
    if as_of is None:
        return None
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    r = cur.execute(
        "SELECT q.rank, q.ouid, q.match_id, q.inferred_formation, q.formation_match, s.elo, s.wins, s.draws, s.losses,"
        " s.team_color_name, t.match_result, m.match_date"
        " FROM ranker_squad q JOIN ranker_snapshot s USING (data_as_of, mode, rank)"
        " LEFT JOIN match_team t ON t.match_id = q.match_id AND t.ouid = q.ouid"
        " LEFT JOIN match m ON m.match_id = q.match_id"
        " WHERE q.data_as_of = ? AND q.mode = ? AND q.match_order = 0 AND q.accepted = 1 AND s.formation = ?"
        " ORDER BY q.formation_match DESC, q.rank LIMIT 1",
        (as_of, mode, formation),
    ).fetchone()
    if r is None:
        return None
    return {
        "rank": r["rank"], "elo": r["elo"], "wins": r["wins"], "draws": r["draws"], "losses": r["losses"],
        "team_color": r["team_color_name"], "played_formation": r["inferred_formation"],
        "formation_match": bool(r["formation_match"]), "result": r["match_result"], "score": _score(conn, r["match_id"], r["ouid"]),
        "match_date": r["match_date"], "data_as_of": as_of,
        "players": [_player(p) for p in cur.execute(_PLAYERS, (r["match_id"], r["ouid"]))],
    }  # fmt: skip


def best_eleven(conn: sqlite3.Connection, formation: str, mode: str = "1vs1") -> dict[str, Any] | None:
    """Most used player at each position of the most common layout, over the latest snapshot's squads
    that actually played `formation`."""
    as_of = squad_snapshot(conn, mode=mode)
    if as_of is None:
        return None
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    rows = cur.execute(
        "SELECT q.rank, p.sp_position, p.pid, p.sp_id, p.sp_grade FROM ranker_squad q"
        " JOIN ranker_snapshot s USING (data_as_of, mode, rank)"
        " JOIN match_player p ON p.match_id = q.match_id AND p.ouid = q.ouid AND p.starter = 1"
        " WHERE q.data_as_of = ? AND q.mode = ? AND q.match_order = 0 AND q.accepted = 1 AND q.formation_match = 1"
        " AND s.formation = ?",
        (as_of, mode, formation),
    ).fetchall()
    squads: dict[int, list[int]] = defaultdict(list)
    at: dict[int, Counter[int]] = defaultdict(Counter)  # 포지션 → 선수(pid)별 사용 수
    cards: dict[tuple[int, int], Counter[int]] = defaultdict(Counter)
    grades: dict[tuple[int, int], Counter[int]] = defaultdict(Counter)
    for r in rows:
        squads[r["rank"]].append(r["sp_position"])
        at[r["sp_position"]][r["pid"]] += 1
        cards[(r["sp_position"], r["pid"])][r["sp_id"]] += 1
        grades[(r["sp_position"], r["pid"])][r["sp_grade"]] += 1
    layouts = Counter(tuple(sorted(p)) for p in squads.values() if len(p) == 11)
    if not layouts:
        return None
    layout = layouts.most_common(1)[0][0]
    used: set[int] = set()
    players = []
    for pos in layout:
        pid = next((pid for pid, _ in at[pos].most_common() if pid not in used), None)
        if pid is None:
            continue
        used.add(pid)
        sp_id = cards[(pos, pid)].most_common(1)[0][0]
        p = cur.execute(
            "SELECT sp.name, ss.class_name, ss.season_img FROM meta_spid sp"
            " LEFT JOIN meta_season ss ON ss.season_id = ? WHERE sp.sp_id = ?",
            (sp_id // 1_000_000, sp_id),
        ).fetchone()
        players.append({
            "sp_id": sp_id, "pid": pid, "position": pos, "name": p["name"] if p else None,
            "season": p["class_name"] if p else None, "season_img": (p["season_img"] or None) if p else None,
            "grade": grades[(pos, pid)].most_common(1)[0][0], "rankers": at[pos][pid],
        })  # fmt: skip
    return {"squads": len(squads), "layout_squads": layouts[layout], "data_as_of": as_of, "players": players}


def formation_matchups(conn: sqlite3.Connection, formation: str, table: FormationTable | None = None) -> dict[str, Any]:
    """Results of `formation` against each opponent formation, over every stored match where both
    starting line-ups are known (formations inferred from positions, both sides counted)."""
    UsageStore(conn)
    table = table or FormationTable.load()
    positions: dict[tuple[str, str], list[int]] = defaultdict(list)
    for match_id, ouid, pos in conn.execute("SELECT match_id, ouid, sp_position FROM match_player WHERE sp_position != ?", (SUB,)):
        positions[(match_id, ouid)].append(pos)
    sides: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    results = {(m, o): res for m, o, res in conn.execute("SELECT match_id, ouid, match_result FROM match_team")}
    for (match_id, ouid), pos in positions.items():
        if len(pos) == 11:
            sides[match_id].append((ouid, table.infer(pos)))
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    for match_id, pair in sides.items():
        if len(pair) != 2:
            continue
        for (me, mine), (_, theirs) in (pair, pair[::-1]):
            result = results.get((match_id, me))
            if mine == formation and theirs and result in ("승", "무", "패"):
                tally[theirs][result] += 1
    rows = [
        {"opponent": opp, "games": sum(c.values()), "wins": c["승"], "draws": c["무"], "losses": c["패"],
         "win_rate": round(c["승"] / sum(c.values()), 4)}
        for opp, c in tally.items()
    ]  # fmt: skip
    rows.sort(key=lambda r: (-r["games"], r["opponent"]))
    return {"games": sum(r["games"] for r in rows), "opponents": rows}
