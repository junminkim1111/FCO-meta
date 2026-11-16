import json
import os
import sqlite3
from pathlib import Path

import pytest

from fco_meta.chatbot.tools import Toolbox

# frozen eval DB (sha256 0dc263f2...); other machines point FCO_BASELINE_DB at their copy
DB = Path(os.environ.get("FCO_BASELINE_DB", "/Users/junmin/fco-meta/reports/orca/run_e7420413ea3c/baseline.sqlite"))


@pytest.fixture(scope="module")
def tb():
    if not DB.exists():
        pytest.fail(f"frozen baseline DB missing: {DB} (set FCO_BASELINE_DB)")
    return Toolbox(sqlite3.connect(f"file:{DB}?mode=ro", uri=True))


def get(tb, rank):
    content, is_error = tb.run("get_ranker_squad", {"rank": rank})
    assert not is_error
    return json.loads(content)


def test_rank_59(tb):
    r = get(tb, 59)
    assert (r["formation"], r["team_color"]) == ("4-2-2-1-1", "브라질")
    assert len(r["lineup"]) == 11 and {c["player"] for c in r["lineup"]} >= {"에데르송", "카카"}


@pytest.mark.parametrize("rank", [0, 123, 20000])
def test_not_collected_has_no_lineup(tb, rank):
    r = get(tb, rank)
    assert not r["lineup"] and "lineup" in r and r["rank"] == rank
