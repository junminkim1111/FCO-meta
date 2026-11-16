import json

import pytest
from test_top_rankers import setup

from fco_meta.chatbot import Toolbox


def get(tb, rank):
    content, is_error = tb.run("get_ranker_squad", {"rank": rank})
    assert not is_error
    return json.loads(content)


def test_collected_rank_returns_exactly_its_starters(tmp_path, fixture_html):
    storage, as_of, targets = setup(tmp_path, fixture_html, top=10)
    tb = Toolbox(storage.conn, min_sample=1)
    for t in targets:
        r = get(tb, t.rank)
        stored = storage.conn.execute(
            "SELECT team_color_name FROM ranker_snapshot WHERE data_as_of = ? AND mode = '1vs1' AND rank = ?", (as_of, t.rank)
        ).fetchone()[0]
        starters = {
            row[0]
            for row in storage.conn.execute(
                "SELECT p.sp_id FROM ranker_squad q JOIN match_player p ON p.match_id = q.match_id AND p.ouid = q.ouid AND p.starter = 1"
                " WHERE q.data_as_of = ? AND q.rank = ? AND q.match_order = 0 AND q.accepted = 1",
                (as_of, t.rank),
            )
        }
        assert (r["rank"], r["formation"], r["team_color"]) == (t.rank, t.formation, stored)
        assert len(r["lineup"]) == 11 and {c["sp_id"] for c in r["lineup"]} == starters
        assert all({"position", "player", "season", "grade"} <= c.keys() for c in r["lineup"])


@pytest.mark.parametrize("rank", [0, 15, 20000])  # 15위: 랭킹엔 있으나 스쿼드 미수집
def test_not_collected_has_no_lineup(tmp_path, fixture_html, rank):
    storage, _, _ = setup(tmp_path, fixture_html, top=10)
    r = get(Toolbox(storage.conn, min_sample=1), rank)
    assert r["rank"] == rank and not r["lineup"] and "formation" not in r
