"""Numbers in an answer that no tool result backs up — made-up percentages, prices and head counts.

Used live (GeminiChat asks the model to rewrite once) and by the evaluation report.
"""

from __future__ import annotations

import re
from typing import Any

from ..market.money import parse_bp

# 앞에 숫자·쉼표·점이 붙은 경우는 잘라 읽지 않는다 ("9,996명"을 "996명"으로 읽지 않게)
_PERCENT_RE = re.compile(r"(?<![\d,.])([+-]?\d[\d,]*(?:\.\d+)?)\s*%")
_MONEY_RE = re.compile(r"\d[\d,.]*\s*(?:조|억|만)(?:\s*\d[\d,.]*\s*(?:억|만))*")
_COUNT_RE = re.compile(r"(?<![\d,.])(\d[\d,]*)\s*명")
_PLAIN_RE = re.compile(r"\d+(?:\.\d+)?")
_MONEY_PART_RE = re.compile(r"(\d[\d,.]*)\s*(조|억|만)")
_UNIT = {"조": 1e12, "억": 1e8, "만": 1e4}


def _last_digit(number: str) -> float:
    """Place value of the last digit written: "81" → 1, "3.7" → 0.1, "6,600" → 1."""
    _, _, decimals = number.replace(",", "").partition(".")
    return 10.0 ** -len(decimals)


def _money_tolerance(text: str, bp: float) -> float:
    """How far a written amount may be from a result and still be that result rounded or cut ("약 81억" for 81억 6,600만):
    one unit of its last digit ("81억" ±1억, "3.7억" ±0.1억, "81억 6,600만" ±1만), and never less than 0.5%."""
    parts = _MONEY_PART_RE.findall(text)
    unit = _last_digit(parts[-1][0]) * _UNIT[parts[-1][1]] if parts else 1
    return max(unit, bp * 0.005, 1)


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
        v = abs(float(m.group(1).replace(",", "")))
        # 비율(0.948) 또는 이미 %인 값(94.88)을 쓴 자리수로 반올림한 것도 결과에 있는 값 ("33%" ±0.5%p, "32.8%" ±0.05%p)
        tol = _last_digit(m.group(1)) / 2 + 0.001
        if not any(abs(v - r) <= tol for r in rates) and not any(abs(v - k) <= tol for k in known if 1 < k <= 100):
            missing.append(m.group(0))
    for m in _MONEY_RE.finditer(text):
        try:
            bp = parse_bp(m.group(0))
        except ValueError:
            continue
        tol = _money_tolerance(m.group(0), bp)
        if not any(abs(bp - k) <= tol for k in known if k >= 10_000) and bp not in asked:
            missing.append(m.group(0).strip())
    for m in _COUNT_RE.finditer(text):
        n = float(m.group(1).replace(",", ""))
        if n not in known and n not in asked:
            missing.append(m.group(0))
    return missing
