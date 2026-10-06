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


@dataclass(frozen=True)
class CardDetail:
    """Card popup of the datacenter (`/datacenter/PlayerPreView`) at one grade (강화)."""

    spid: int
    grade: int
    positions: dict[str, int]  # 포지션 → 그 포지션 능력치 (예: {"CM": 125, "CDM": 125})
    birth: str | None  # YYYY-MM-DD
    height: int | None  # cm
    weight: int | None  # kg
    body_type: str | None  # 체형 (보통, 마름 …)
    skill_moves: int | None  # 개인기 별 수
    left_foot: int | None  # 주발 (1~5)
    right_foot: int | None
    reputation: str | None  # 인지도 (월드클래스 …)
    traits: list[str] = field(default_factory=list)  # 특성
    summary: dict[str, int] = field(default_factory=dict)  # 스피드·슛·패스·드리블·수비·피지컬
    stats: dict[str, int] = field(default_factory=dict)  # 세부 능력치 34개 (속력, 가속력 …)
    clubs: list[dict[str, str]] = field(default_factory=list)  # 클럽 경력 [{"years", "club", "loan"}]
    nation: str | None = None  # 국적 ("잉글랜드") — 국가 팀컬러 소속 판단
