"""Position codes (`spposition.json`) and formation inference from a starting XI."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

SUB = 28

POSITION_NAMES: dict[int, str] = {
    0: "GK", 1: "SW", 2: "RWB", 3: "RB", 4: "RCB", 5: "CB", 6: "LCB", 7: "LB", 8: "LWB",
    9: "RDM", 10: "CDM", 11: "LDM", 12: "RM", 13: "RCM", 14: "CM", 15: "LCM", 16: "LM",
    17: "RAM", 18: "CAM", 19: "LAM", 20: "RF", 21: "CF", 22: "LF", 23: "RW", 24: "RS",
    25: "ST", 26: "LS", 27: "LW", 28: "SUB",
}  # fmt: skip

# 챗봇·집계에서 쓰는 역할 이름 → 포지션 코드
ROLE_POSITIONS: dict[str, tuple[int, ...]] = {
    "GK": (0,),
    "CB": (1, 4, 5, 6),
    "FB": (2, 3, 7, 8),
    "볼란치": (9, 10, 11),
    "CM": (13, 14, 15),
    "WM": (12, 16),
    "AM": (17, 18, 19),
    "WG": (23, 27),
    "ST": (20, 21, 22, 24, 25, 26),
}

# 뒤에서 앞으로의 라인. 포메이션 이름은 비어 있지 않은 라인의 인원 수를 이어 붙인 것으로 본다.
#   수비(윙백 포함) / 수비형 MF / 중앙·측면 MF / 공격형 MF / 세컨드 톱(RF·CF·LF) / 최전방(윙·스트라이커)
LINES: tuple[frozenset[int], ...] = (
    frozenset(range(1, 9)),
    frozenset({9, 10, 11}),
    frozenset(range(12, 17)),
    frozenset({17, 18, 19}),
    frozenset({20, 21, 22}),
    frozenset(range(23, 28)),
)

DEFAULT_TABLE_PATH = Path(__file__).resolve().parents[2] / "data" / "formations.json"

Signature = tuple[int, ...]


def starters(positions: Iterable[int]) -> list[int]:
    return [p for p in positions if p != SUB]


def signature(positions: Iterable[int]) -> Signature:
    """Sorted outfield starting positions (GK and subs excluded)."""
    return tuple(sorted(p for p in positions if p not in (SUB, 0)))


def signature_key(sig: Signature) -> str:
    return ",".join(POSITION_NAMES[p] for p in sig)


def parse_signature_key(key: str) -> Signature:
    codes = {name: code for code, name in POSITION_NAMES.items()}
    return tuple(sorted(codes[n] for n in key.split(",") if n))


def line_shape(positions: Iterable[int]) -> str | None:
    """Formation string by counting players per line, e.g. LDM/RDM + LAM/CAM/RAM → "4-2-3-1".

    Returns None unless exactly 10 outfield starters are given.
    """
    sig = signature(positions)
    if len(sig) != 10:
        return None
    counts = [sum(1 for p in sig if p in line) for line in LINES]
    return "-".join(str(c) for c in counts if c)


class FormationTable:
    """Starting-position signature → formation name.

    Signatures are learned from squads whose formation is known (the ranking page shows the
    formation of each ranker's most recent official match). Unknown signatures fall back to
    `line_shape`, which cannot tell variants such as "4-4-2" / "4-4-2(2)" apart.
    """

    def __init__(self, known: dict[Signature, str] | None = None):
        self.known: dict[Signature, str] = dict(known or {})

    @classmethod
    def load(cls, path: Path = DEFAULT_TABLE_PATH) -> FormationTable:
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls({parse_signature_key(k): v for k, v in raw.get("signatures", {}).items()})

    def save(self, path: Path = DEFAULT_TABLE_PATH) -> None:
        items = sorted(self.known.items(), key=lambda kv: (kv[1], kv[0]))
        data = {
            "note": "선발 필드 플레이어 포지션 조합 → 랭킹 페이지 포메이션. `python -m fco_meta.pipeline formations`로 갱신",
            "signatures": {signature_key(sig): name for sig, name in items},
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def infer(self, positions: Iterable[int]) -> str | None:
        positions = list(positions)
        return self.known.get(signature(positions)) or line_shape(positions)

    def learn(self, observations: Iterable[tuple[Iterable[int], str]], min_share: float = 0.8) -> dict[Signature, Counter[str]]:
        """Add signatures whose observed formation is unambiguous (majority ≥ `min_share`).

        Returns the raw counts for every signature so callers can report conflicts.
        """
        counts: dict[Signature, Counter[str]] = defaultdict(Counter)
        for positions, formation in observations:
            sig = signature(positions)
            if len(sig) == 10 and formation:
                counts[sig][formation] += 1
        for sig, c in counts.items():
            name, n = c.most_common(1)[0]
            if n / sum(c.values()) >= min_share:
                self.known[sig] = name
        return counts
