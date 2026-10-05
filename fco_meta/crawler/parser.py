from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from selectolax.parser import HTMLParser, Node

from .models import RankerRow, RankPage, RankSummary, RateEntry, TeamColor

KST = timezone(timedelta(hours=9))

_TOTAL_RE = re.compile(r"([\d,]+)명의 구단주님이 검색 되었습니다")
_AS_OF_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) 기준")
_TIER_ICON_RE = re.compile(r"ico_rank(\d+)\.png")
_CREST_RE = re.compile(r"/crests/[^\"']*/(\w+)\.png")
_BOOST_RE = re.compile(r"/teamcolorboost/[^\"']*/(\w+)\.png")
_FLAG_RE = re.compile(r"/countries/[^\"']*/(\w+)\.png")
_TEAM_COLOR_RE = re.compile(r"^(.*?)\s*\((\d+)명\)$")
_SELECT_TC_RE = re.compile(r"select_tc\((\d+),\s*'([^']*)'\)")
_CATALOG_CLASS_RE = re.compile(r"\b(club|nationality|special)_item\b")


class ParseError(ValueError):
    pass


def parse_rank_inner(html: str) -> RankPage:
    """Parse the HTML fragment returned by `/datacenter/rank_inner`."""
    tree = HTMLParser(html)
    if tree.css_first(".datacenter_rank_list") is None:
        raise ParseError("ranking list container not found")

    rows = [_parse_row(tr) for tr in tree.css(".tbody .tr") if "empty" not in _classes(tr)]

    total = _TOTAL_RE.search(html)
    as_of = _AS_OF_RE.search(html)
    return RankPage(
        rows=rows,
        total_count=_to_int(total.group(1)) if total else None,
        data_as_of=datetime.strptime(as_of.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        if as_of
        else None,
    )


def _parse_row(tr: Node) -> RankerRow:
    rank = _to_int(_text(tr, ".td.rank_no"))
    if rank is None:
        raise ParseError("row without rank number")

    name_node = tr.css_first(".rank_coach .name")
    nickname = name_node.text(strip=True) if name_node else ""
    if not nickname:
        raise ParseError(f"row {rank}: nickname not found")

    price = tr.css_first(".rank_coach .price")
    wins, draws, losses = _parse_wdl(_text(tr, ".td.rank_before .bottom"))

    tc_name, tc_count = None, None
    tc_text = _text(tr, ".td.team_color .inner")
    if tc_text:
        m = _TEAM_COLOR_RE.match(tc_text)
        tc_name, tc_count = (m.group(1), int(m.group(2))) if m else (tc_text, None)
    tc_icons = [img.attributes.get("src") or "" for img in tr.css(".td.team_color img")]
    crest = _first_match(_CREST_RE, tc_icons)
    boost = _first_match(_BOOST_RE, tc_icons)
    flag = _first_match(_FLAG_RE, tc_icons)

    best_icons = [_tier_icon(img) for img in tr.css(".td.rank_best img")]
    best_icons += [None] * (2 - len(best_icons))

    return RankerRow(
        rank=rank,
        nickname=nickname,
        nexon_sn=_to_int(name_node.attributes.get("data-sn")),
        level=_to_int(_text(tr, ".rank_coach .lv .txt")),
        tier_icon=_tier_icon(tr.css_first(".rank_coach > .ico_rank img")),
        squad_value=_to_int(price.attributes.get("title")) if price else None,
        elo=_to_float(_text(tr, ".td.rank_r_win_point")),
        win_rate=_percent(_text(tr, ".td.rank_before .top")),
        wins=wins,
        draws=draws,
        losses=losses,
        team_color_name=tc_name,
        team_color_count=tc_count,
        team_color_crest=crest,
        team_color_boost=boost,
        formation=_text(tr, ".td.formation") or None,
        best_tier_icon=best_icons[0],
        prev_tier_icon=best_icons[1],
        team_color_flag=flag,
    )


def parse_team_color_catalog(shell_html: str) -> list[TeamColor]:
    """Extract the team color picker (club / nationality / special) from `/datacenter/rank`."""
    tree = HTMLParser(shell_html)
    seen: set[tuple[int, str, str]] = set()
    result: list[TeamColor] = []
    for a in tree.css("a.select_item"):
        m = _SELECT_TC_RE.search(a.attributes.get("onclick") or "")
        cat = _CATALOG_CLASS_RE.search(a.attributes.get("class") or "")
        if not m or not cat:
            continue
        tc = TeamColor(
            id=int(m.group(1)),
            name=m.group(2).strip(),
            category=cat.group(1),
            group_id=_to_int(a.attributes.get("data-no")) or 0,
        )
        key = (tc.id, tc.name, tc.category)
        if key not in seen:
            seen.add(key)
            result.append(tc)
    return result


def parse_formation_options(shell_html: str) -> list[str]:
    """All formations selectable in the ranking filter (e.g. "4-2-3-1")."""
    tree = HTMLParser(shell_html)
    out: list[str] = []
    for a in tree.css("a[data-value]"):
        value = a.attributes.get("data-value") or ""
        if "select_formation" in (a.attributes.get("onclick") or "") and "-" in value and value not in out:
            out.append(value)
    return out


def parse_rank_summary(shell_html: str) -> RankSummary:
    """Top-10 team color / formation pick rates among the TOP 10,000."""
    tree = HTMLParser(shell_html)
    summary = RankSummary()
    for block in tree.css(".recommend-list_wrap"):
        header = _text(block, ".tit_list .fom")
        entries = []
        for a in block.css("li a"):
            rank = _to_int(_text(a, ".chart"))
            rate = _percent(_text(a, ".rate"))
            name = _text(a, ".fom")
            if rank is None or rate is None or not name:
                continue
            tc = _SELECT_TC_RE.search(a.attributes.get("onclick") or "")
            entries.append(RateEntry(rank, name, rate, int(tc.group(1)) if tc else None))
        if header == "팀컬러":
            summary.team_colors.extend(entries)
        elif header == "포메이션":
            summary.formations.extend(entries)
    return summary


def _classes(node: Node) -> list[str]:
    return (node.attributes.get("class") or "").split()


def _text(node: Node, selector: str) -> str:
    found = node.css_first(selector)
    return " ".join(found.text(strip=False).split()) if found else ""


def _first_match(pattern: re.Pattern[str], values: list[str]) -> str | None:
    for value in values:
        m = pattern.search(value)
        if m:
            return m.group(1)
    return None


def _tier_icon(img: Node | None) -> int | None:
    if img is None:
        return None
    m = _TIER_ICON_RE.search(img.attributes.get("src") or "")
    return int(m.group(1)) if m else None


def _parse_wdl(text: str) -> tuple[int | None, int | None, int | None]:
    parts = [_to_int(p) for p in text.split("|")]
    return tuple(parts) if len(parts) == 3 else (None, None, None)  # type: ignore[return-value]


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = value.replace(",", "").strip()
    return int(digits) if digits.lstrip("-").isdigit() else None


def _to_float(value: str | None) -> float | None:
    try:
        return float(value.replace(",", "")) if value else None
    except ValueError:
        return None


def _percent(value: str | None) -> float | None:
    if not value or not value.endswith("%"):
        return None
    number = _to_float(value[:-1])
    return round(number / 100, 6) if number is not None else None
