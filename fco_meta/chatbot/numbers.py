"""Numbers in an answer that no tool result backs up — made-up percentages, prices and head counts.

Used live (GeminiChat asks the model to rewrite once) and by the evaluation report.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from typing import Any

from ..market.money import parse_bp

# 앞에 숫자·쉼표·점이 붙은 경우는 잘라 읽지 않는다 ("9,996명"을 "996명"으로 읽지 않게)
_PERCENT_RE = re.compile(r"(?<![\d,.])([+-]?\d[\d,]*(?:\.\d+)?)\s*%")
_MONEY_RE = re.compile(r"\d[\d,.]*\s*(?:조|억|만)(?:\s*\d[\d,.]*\s*(?:억|만))*")
_COUNT_RE = re.compile(r"(?<![\d,.])(\d[\d,]*)\s*명")
_PLAIN_RE = re.compile(r"\d+(?:\.\d+)?")
_MONEY_PART_RE = re.compile(r"(\d[\d,.]*)\s*(조|억|만)")
_UNIT = {"조": 1e12, "억": 1e8, "만": 1e4}
RECHECK_AT = 2  # 근거 없는 수치가 이만큼 이상이면 다시 쓰게 한다 (하나뿐이면 다시 쓰지 않고 '수치 미확인'으로 기록만)


def _last_digit(number: str) -> float:
    """Place value of the last digit written: "81" → 1, "3.7" → 0.1, "6,600" → 1."""
    _, _, decimals = number.replace(",", "").partition(".")
    return 10.0 ** -len(decimals)


def _money_unit(text: str) -> float:
    """One unit of the last digit written: "81억" → 1억, "3.7억" → 0.1억, "81억 6,600만" → 1만."""
    parts = _MONEY_PART_RE.findall(text)
    return _last_digit(parts[-1][0]) * _UNIT[parts[-1][1]] if parts else 1


def _money_tolerance(text: str, bp: float) -> float:
    """How far a written amount may be from a result and still be that result rounded or cut ("약 81억" for 81억 6,600만):
    one unit of its last digit ("81억" ±1억, "3.7억" ±0.1억, "81억 6,600만" ±1만), and never less than 0.5%."""
    return max(_money_unit(text), bp * 0.005, 1)


def _near(x: float, values: list[float], tol: float) -> bool:
    """Is some value of the sorted `values` within `tol` of x?"""
    i = bisect_left(values, x - tol)
    return i < len(values) and values[i] <= x + tol


def _combined(x: float, values: list[float], tol: float) -> bool:
    """x is the sum or difference of two values ("올리는 데 3억 1,000만 더", "남는 5,600만", "승률 2.3%p 높다")."""
    return any(_near(x - v, values, tol) or _near(v - x, values, tol) for v in values)


def _amounts(value: Any, money: set[float], rates: set[float], key: str = "") -> None:
    """Only real amounts and rates, for sums and differences: BP fields (`*_bp`) and amounts written as text ("3억"),
    rates (0~1 fields) and percentages written as text — not ids or counts, whose sums would back up almost anything."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        if key.endswith("_bp"):
            money.add(float(value))
        elif 0 < abs(value) <= 1 and isinstance(value, float):
            rates.add(round(abs(value) * 100, 1))
    elif isinstance(value, str):
        for m in _MONEY_RE.finditer(value):
            try:
                money.add(float(parse_bp(m.group(0))))
            except ValueError:
                pass
        rates.update(abs(float(m.group(1).replace(",", ""))) for m in _PERCENT_RE.finditer(value))
    elif isinstance(value, dict):
        for k, v in value.items():
            _amounts(v, money, rates, str(k))
    elif isinstance(value, list):
        for v in value:
            _amounts(v, money, rates, key)


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
    """Percentages, BP amounts and head counts in `answer` that no tool result (or the question, or the system prompt's
    facts like "TOP 10,000") contains — also not as the sum or difference of two of them."""
    from .prompt import SYSTEM_PROMPT

    known = _numbers([*results, question, SYSTEM_PROMPT], set())  # 질문 속 금액도 (@구단주명 팀 블록의 시세·총액)
    asked = {float(n) for n in re.findall(r"\d+", question)}
    rates = {round(abs(v) * 100, 1) for v in known if abs(v) <= 1}
    money_set: set[float] = set()
    rate_set: set[float] = set()
    _amounts([*results, question], money_set, rate_set)
    money, pcts = sorted(money_set), sorted(rate_set)
    text = "\n".join(line for line in answer.splitlines() if not line.startswith("[근거]"))
    missing = []
    for m in _PERCENT_RE.finditer(text):
        v = abs(float(m.group(1).replace(",", "")))
        # 비율(0.948) 또는 이미 %인 값(94.88)을 쓴 자리수로 반올림한 것도 결과에 있는 값 ("33%" ±0.5%p, "32.8%" ±0.05%p)
        tol = _last_digit(m.group(1)) / 2 + 0.001
        direct = any(abs(v - r) <= tol for r in rates) or any(abs(v - k) <= tol for k in known if 1 < k <= 100)
        if not direct and not _combined(v, pcts, tol):
            missing.append(m.group(0))
    for m in _MONEY_RE.finditer(text):
        try:
            bp = parse_bp(m.group(0))
        except ValueError:
            continue
        tol = _money_tolerance(m.group(0), bp)
        direct = any(abs(bp - k) <= tol for k in known if k >= 10_000)
        # 합·차는 쓴 자리수만큼만, 많아야 2%: "7억"(±1억)처럼 거칠게 쓴 금액은 아무 두 값의 합으로도 맞아 버린다
        if not direct and not _combined(bp, money, max(min(_money_unit(m.group(0)), bp * 0.02), 1)) and bp not in asked:
            missing.append(m.group(0).strip())
    for m in _COUNT_RE.finditer(text):
        n = float(m.group(1).replace(",", ""))
        if n not in known and n not in asked:
            missing.append(m.group(0))
    return missing
