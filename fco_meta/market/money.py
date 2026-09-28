"""BP amount helpers: "1억 5,000만" ↔ 150000000."""

from __future__ import annotations

import re

_UNITS = {"조": 10**12, "억": 10**8, "만": 10**4}
_PART_RE = re.compile(r"([\d,.]+)(조|억|만)?")
_AMOUNT_RE = re.compile(r"(?:[\d,.]+(?:조|억|만)?)+")


def parse_bp(text: str) -> int:
    """"5억" → 500000000, "1억 2,000만" → 120000000, "3500000" → 3500000."""
    cleaned = text.replace(" ", "").replace("BP", "").replace("bp", "")
    if not _AMOUNT_RE.fullmatch(cleaned):
        raise ValueError(f"invalid BP amount: {text!r}")
    total = 0.0
    for number, unit in _PART_RE.findall(cleaned):
        total += float(number.replace(",", "")) * _UNITS.get(unit, 1)
    return int(round(total))


def format_bp(value: int | None) -> str:
    """150000000 → "1억 5,000만"."""
    if value is None:
        return "-"
    parts = []
    for unit in ("조", "억", "만"):
        size = _UNITS[unit]
        if value >= size:
            parts.append(f"{value // size:,}{unit}")
            value %= size
    if value or not parts:
        parts.append(f"{value:,}")
    return " ".join(parts)
