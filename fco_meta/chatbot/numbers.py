"""Numbers in an answer that no tool result backs up — made-up percentages, prices and head counts.

Used live (GeminiChat asks the model to rewrite once) and by the evaluation report.
"""

from __future__ import annotations

import re
from typing import Any

from ..market.money import parse_bp

_PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
_MONEY_RE = re.compile(r"\d[\d,.]*\s*(?:조|억|만)(?:\s*\d[\d,.]*\s*(?:억|만))*")
_COUNT_RE = re.compile(r"(\d+)\s*명")
_PLAIN_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers(value: Any, out: set[float]) -> set[float]:
    """Every number in a tool result (also inside text), plus BP amounts written as text ("3억")."""
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        out.add(float(value))
    elif isinstance(value, str):
        # 설명 문장 속 숫자("상위 300명 중 14명")도 결과에 있는 값으로 친다
        out.update(float(n) for n in _PLAIN_RE.findall(value.replace(",", "")))
        for m in _MONEY_RE.finditer(value):
            try:
                out.add(float(parse_bp(m.group(0))))
            except ValueError:
                pass
    elif isinstance(value, dict):
        for v in value.values():
            _numbers(v, out)
    elif isinstance(value, list):
        for v in value:
            _numbers(v, out)
    return out


def unsupported_numbers(answer: str, results: list[Any], question: str = "") -> list[str]:
    """Percentages, BP amounts and head counts in `answer` that no tool result (or the question) contains."""
    known = _numbers(results, set())
    asked = {float(n) for n in re.findall(r"\d+", question)}
    rates = {round(abs(v) * 100, 1) for v in known if abs(v) <= 1}
    text = "\n".join(line for line in answer.splitlines() if not line.startswith("[근거]"))
    missing = []
    for m in _PERCENT_RE.finditer(text):
        v = abs(float(m.group(1)))
        if not any(abs(v - r) <= 0.051 for r in rates) and v not in known:
            missing.append(m.group(0))
    for m in _MONEY_RE.finditer(text):
        try:
            bp = parse_bp(m.group(0))
        except ValueError:
            continue
        if not any(abs(bp - k) <= max(k * 0.005, 1) for k in known if k >= 10_000) and bp not in asked:
            missing.append(m.group(0).strip())
    for m in _COUNT_RE.finditer(text):
        n = float(m.group(1))
        if n not in known and n not in asked:
            missing.append(m.group(0))
    return missing
