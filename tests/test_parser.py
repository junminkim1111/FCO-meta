from datetime import datetime, timedelta, timezone

import pytest

from fco_meta.crawler import (
    ParseError,
    parse_formation_options,
    parse_rank_inner,
    parse_rank_summary,
    parse_team_color_catalog,
)

KST = timezone(timedelta(hours=9))


def test_parses_full_page(fixture_html):
    page = parse_rank_inner(fixture_html("rank_inner_1vs1_p1.html"))

    assert page.total_count == 10000
    assert page.total_pages == 500
    assert page.data_as_of == datetime(2026, 9, 28, 20, 0, tzinfo=KST)
    assert [r.rank for r in page.rows] == list(range(1, 21))

    first = page.rows[0]
    assert first.nickname == "user001"
    assert first.nexon_sn == 1000001
    assert first.level == 2280
    assert first.squad_value == 16_708_213_680
    assert first.elo == pytest.approx(3615.87)
    assert first.win_rate == pytest.approx(0.721)
    assert (first.wins, first.draws, first.losses) == (142, 0, 55)
    assert first.team_color_name == "FC 바르셀로나"
    assert first.team_color_count == 11
    assert first.team_color_crest == "l241"
    assert first.team_color_boost is None
    assert first.formation == "4-2-3-1"
    assert first.tier_icon == 0
    assert (first.best_tier_icon, first.prev_tier_icon) == (0, 0)


def test_every_row_has_core_fields(fixture_html):
    for name in ("rank_inner_1vs1_p1.html", "rank_inner_1vs1_arsenal_4231_p1.html"):
        for row in parse_rank_inner(fixture_html(name)).rows:
            assert row.nickname and row.formation and row.team_color_name
            assert row.elo and row.win_rate is not None and row.wins is not None


def test_filtered_pages(fixture_html):
    p1 = parse_rank_inner(fixture_html("rank_inner_1vs1_arsenal_4231_p1.html"))
    p5 = parse_rank_inner(fixture_html("rank_inner_1vs1_arsenal_4231_p5.html"))

    assert p1.total_count == 94 and p1.total_pages == 5
    assert len(p1.rows) == 20 and len(p5.rows) == 14
    assert {(r.team_color_name, r.formation) for r in p1.rows + p5.rows} == {("아스널", "4-2-3-1")}
    assert p1.rows[0].rank < p5.rows[-1].rank


@pytest.mark.parametrize("name,total", [("rank_inner_empty.html", 0), ("rank_inner_past_last_page.html", 94)])
def test_pages_without_rows(fixture_html, name, total):
    page = parse_rank_inner(fixture_html(name))
    assert page.rows == []
    assert page.total_count == total


def test_rejects_unexpected_html():
    with pytest.raises(ParseError):
        parse_rank_inner("<html><body>점검 중입니다</body></html>")


def test_team_color_catalog(fixture_html):
    catalog = parse_team_color_catalog(fixture_html("rank_shell_1vs1.html"))
    by_key = {(tc.name, tc.category): tc for tc in catalog}

    assert len(catalog) > 800
    assert by_key[("아스널", "club")].id == 1004
    assert by_key[("아스널", "club")].group_id == 13  # 프리미어리그
    assert by_key[("잉글랜드", "nationality")].id == 2006
    assert {tc.category for tc in catalog} == {"club", "nationality", "special"}


def test_formation_options(fixture_html):
    options = parse_formation_options(fixture_html("rank_shell_1vs1.html"))
    assert "4-2-3-1" in options and "3-4-3(2)" in options
    assert len(options) == len(set(options))


def test_rank_summary(fixture_html):
    summary = parse_rank_summary(fixture_html("rank_shell_1vs1.html"))

    assert len(summary.team_colors) == 10 and len(summary.formations) == 10
    assert summary.team_colors[0].name == "FC 바르셀로나"
    assert summary.team_colors[0].team_color_id == 1016
    assert summary.team_colors[0].rate == pytest.approx(0.072)
    assert summary.formations[0].name == "4-2-2-2"
    assert summary.formations[0].team_color_id is None


def test_boosted_team_color_keeps_club_crest(fixture_html):
    rows = parse_rank_inner(fixture_html("rank_inner_1vs1_arsenal_4231_boost.html")).rows
    boosted = [r for r in rows if r.team_color_boost]

    assert len(boosted) == 1
    assert boosted[0].team_color_name == "Winning Streak"
    assert boosted[0].team_color_count == 10
    assert boosted[0].team_color_boost == "4_l999848"
    assert boosted[0].team_color_crest == "l1"  # 아스널 엠블럼
    assert all(r.team_color_crest == "l1" for r in rows)
