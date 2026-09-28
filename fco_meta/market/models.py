from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

MAX_GRADE = 13


@dataclass(frozen=True)
class Card:
    """One player card from the datacenter player search (`/datacenter/PlayerList`)."""

    spid: int
    name: str
    season_code: str | None  # 시즌 아이콘 파일명 (예: "26TOTY", "ICONTM")
    main_position: str | None  # 대표 포지션 (예: "CDM")
    ovr: int | None  # 대표 포지션 기준 OVR (강화 전)
    salary: int | None
    rating: float | None  # 유저 평가 평균
    rating_count: int | None
    prices: dict[int, int | None] = field(default_factory=dict)  # 강화(1~13) → 현재가 BP, 0/미표시는 None

    @property
    def pid(self) -> int:
        return self.spid % 1_000_000

    @property
    def season_id(self) -> int:
        return self.spid // 1_000_000


@dataclass(frozen=True)
class PriceHistory:
    spid: int
    grade: int
    current_price: int | None
    points: list[tuple[date, int]]  # 일별 시세 (오래된 순)
