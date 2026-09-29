"""Full match records kept from match-detail (no extra API calls)."""

import json
import sqlite3
from pathlib import Path

from fco_meta.pipeline import PipelineStore

DETAIL = json.loads((Path(__file__).parent / "fixtures" / "openapi" / "match_detail.json").read_text(encoding="utf-8"))


def test_player_and_team_stats_are_stored():
    conn = sqlite3.connect(":memory:")
    PipelineStore(conn).save_match(DETAIL)
    info = DETAIL["matchInfo"][0]
    first = info["player"][0]
    (stats,) = conn.execute(
        "SELECT stats FROM match_player WHERE match_id = ? AND ouid = ? AND sp_id = ?",
        (DETAIL["matchId"], info["ouid"], first["spId"]),
    ).fetchone()
    assert json.loads(stats) == first["status"]
    detail, shots = conn.execute(
        "SELECT detail, shots FROM match_team WHERE match_id = ? AND ouid = ?", (DETAIL["matchId"], info["ouid"])
    ).fetchone()
    assert json.loads(detail)["matchDetail"]["possession"] == info["matchDetail"]["possession"]
    assert json.loads(detail)["pass"] == info["pass"] and json.loads(shots) == info["shootDetail"]


def test_existing_db_gains_the_new_columns():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE match_player (match_id TEXT, ouid TEXT, sp_id INTEGER, season_id INTEGER, pid INTEGER,"
                 " sp_position INTEGER, sp_grade INTEGER, sp_rating REAL, starter INTEGER, PRIMARY KEY (match_id, ouid, sp_id))")  # fmt: skip
    PipelineStore(conn)
    assert "stats" in {r[1] for r in conn.execute("PRAGMA table_info(match_player)")}
    assert {"detail", "shots"} <= {r[1] for r in conn.execute("PRAGMA table_info(match_team)")}
    PipelineStore(conn)  # 두 번째는 아무것도 안 함
