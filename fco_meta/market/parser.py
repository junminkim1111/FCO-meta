from __future__ import annotations

import re
from datetime import date

from selectolax.parser import HTMLParser, Node

from .models import MAX_GRADE, Card, PriceHistory

_UNIT_ID_RE = re.compile(r"area_playerunit_(\d+)")
_SEASON_RE = re.compile(r"/season/([^/\"']+)\.png")
_BP_CLASS_RE = re.compile(r"\bspan_bp(\d+)\b")
_RATING_RE = re.compile(r"([\d.]+)\s*\((\d+)\)")
_SERIES_RE = re.compile(r"var json1\s*=\s*\{(.*?)\};", re.S)
_LABEL_RE = re.compile(r'"(\d{1,2})\.(\d{1,2})"')
_VALUE_RE = re.compile(r'"(-?\d+)"')


class ParseError(ValueError):
    pass


def parse_player_list(html: str) -> list[Card]:
    """Parse `/datacenter/PlayerList` (one `<div id="area_playerunit_{spid}">` per card)."""
    tree = HTMLParser(html)
    cards = []
    for unit in tree.css('div[id^="area_playerunit_"]'):
        m = _UNIT_ID_RE.fullmatch(unit.attributes.get("id") or "")
        if m:
            cards.append(_parse_card(int(m.group(1)), unit))
    return cards


def _parse_card(spid: int, unit: Node) -> Card:
    name = _text(unit, ".info_top .name")
    if not name:
        raise ParseError(f"card {spid}: name not found")

    season_img = unit.css_first(".info_top .season img")
    season = _SEASON_RE.search(season_img.attributes.get("src") or "") if season_img else None

    rating, rating_count = None, None
    rm = _RATING_RE.search(_text(unit, ".td_ar_score"))
    if rm:
        rating, rating_count = float(rm.group(1)), int(rm.group(2))

    prices: dict[int, int | None] = {}
    for span in unit.css(".td_ar_bp span"):
        cm = _BP_CLASS_RE.search(span.attributes.get("class") or "")
        if not cm or not 1 <= int(cm.group(1)) <= MAX_GRADE:
            continue
        price = _to_int(span.attributes.get("title"))
        prices[int(cm.group(1))] = price if price else None  # 0 = 거래 정보 없음

    return Card(
        spid=spid,
        name=name,
        season_code=season.group(1) if season else None,
        main_position=_text(unit, ".info_middle .position .txt") or None,
        ovr=_to_int(_text(unit, ".info_middle .position span:not(.txt)")),
        salary=_to_int(_text(unit, ".pay")),
        rating=rating,
        rating_count=rating_count,
        prices=prices,
    )


def parse_price_graph(html: str, spid: int, grade: int, today: date) -> PriceHistory:
    """Parse `/datacenter/PlayerPriceGraph`.

    The series labels are "M.DD" without a year; they end on or before `today`, so years are
    assigned walking backwards from `today`.
    """
    m = _SERIES_RE.search(html)
    if not m:
        raise ParseError("price series not found")
    head, sep, tail = m.group(1).partition('"value"')
    if not sep:
        raise ParseError("price series has no values")
    labels = [(int(mo), int(d)) for mo, d in _LABEL_RE.findall(head)]
    values = [int(v) for v in _VALUE_RE.findall(tail)]
    if len(labels) != len(values):
        raise ParseError(f"series length mismatch: {len(labels)} labels, {len(values)} values")

    points: list[tuple[date, int]] = []
    year = today.year
    prev: tuple[int, int] | None = None
    for month, day in reversed(labels):
        if prev is None:
            if (month, day) > (today.month, today.day):
                year -= 1
        elif (month, day) > prev:
            year -= 1
        points.append((date(year, month, day), values[len(values) - 1 - len(points)]))
        prev = (month, day)
    points.reverse()

    current = None
    tree = HTMLParser(html)
    for node in tree.css("[title]"):
        value = _to_int(node.attributes.get("title"))
        if value is not None:
            current = value
            break
    return PriceHistory(spid=spid, grade=grade, current_price=current, points=points)


def _text(node: Node, selector: str) -> str:
    found = node.css_first(selector)
    return " ".join(found.text(strip=False).split()) if found else ""


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = value.replace(",", "").strip()
    return int(digits) if digits.isdigit() else None
