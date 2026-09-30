"""Match records kept from match-detail (no extra API calls): what later queries read."""

import json
import sqlite3
from pathlib import Path

from fco_meta.pipeline import PipelineStore

DETAIL = json.loads((Path(__file__).parent / "fixtures" / "openapi" / "match_detail.json").read_text(encoding="utf-8"))


def test_only_what_is_read_later_is_stored():
    """선발의 경기 기록과 스코어만 남긴다 (만 명 수집에서 DB가 커지지 않게). 교체는 포지션만, 슈팅 목록은 버린다."""
    conn = sqlite3.connect(":memory:")
    PipelineStore(conn).save_match(DETAIL)
    info = DETAIL["matchInfo"][0]
    starter = next(p for p in info["player"] if p["spPosition"] != 28)
    (stats,) = conn.execute(
        "SELECT stats FROM match_player WHERE match_id = ? AND ouid = ? AND sp_id = ?",
        (DETAIL["matchId"], info["ouid"], starter["spId"]),
    ).fetchone()
    assert json.loads(stats) == starter["status"]
    subs = conn.execute("SELECT stats FROM match_player WHERE match_id = ? AND sp_position = 28", (DETAIL["matchId"],)).fetchall()
    assert subs and all(s is None for (s,) in subs)
    detail, shots = conn.execute(
        "SELECT detail, shots FROM match_team WHERE match_id = ? AND ouid = ?", (DETAIL["matchId"], info["ouid"])
    ).fetchone()
    assert json.loads(detail) == {"shoot": {"goalTotal": info["shoot"]["goalTotal"]}} and shots is None


def test_existing_db_gains_the_new_columns():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE match_player (match_id TEXT, ouid TEXT, sp_id INTEGER, season_id INTEGER, pid INTEGER,"
                 " sp_position INTEGER, sp_grade INTEGER, sp_rating REAL, starter INTEGER, PRIMARY KEY (match_id, ouid, sp_id))")  # fmt: skip
    PipelineStore(conn)
    assert "stats" in {r[1] for r in conn.execute("PRAGMA table_info(match_player)")}
    assert {"detail", "shots"} <= {r[1] for r in conn.execute("PRAGMA table_info(match_team)")}
    PipelineStore(conn)  # 두 번째는 아무것도 안 함
