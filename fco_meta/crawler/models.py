from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

PAGE_SIZE = 20


@dataclass(frozen=True)
class RankerRow:
    """One row of the official ranking list (`/datacenter/rank_inner`)."""

    rank: int
    nickname: str
    nexon_sn: int | None  # 넥슨 회원번호 (Open API ouid 아님)
    level: int | None
    tier_icon: int | None  # 현재 등급 아이콘 번호 (ico_rankN)
    squad_value: int | None  # 구단가치 (BP)
    elo: float | None
    win_rate: float | None  # 0.0 ~ 1.0
    wins: int | None
    draws: int | None
    losses: int | None
    team_color_name: str | None  # 표시된 팀컬러 이름 (특수 팀컬러가 있으면 그 이름)
    team_color_count: int | None  # 표시된 팀컬러에 해당하는 선수 수
    team_color_crest: str | None  # 클럽/국가 엠블럼 이미지 id (예: "l1" = 아스널)
    team_color_boost: str | None  # 특수 팀컬러 아이콘 id (예: "4_l999848"), 없으면 None
    formation: str | None
    best_tier_icon: int | None  # 역대 최고 등급 아이콘
    prev_tier_icon: int | None  # 이전 시즌 최고 등급 아이콘


@dataclass(frozen=True)
class RankPage:
    rows: list[RankerRow]
    total_count: int | None
    data_as_of: datetime | None  # 페이지에 표시된 기준 시각 (KST)

    @property
    def total_pages(self) -> int | None:
        if self.total_count is None:
            return None
        return math.ceil(self.total_count / PAGE_SIZE)


@dataclass(frozen=True)
class TeamColor:
    id: int
    name: str
    category: str  # "club" | "nationality" | "special"
    group_id: int  # 리그 id (club) / 대륙 id (nationality) / 자기 자신 (special)


@dataclass(frozen=True)
class RateEntry:
    rank: int
    name: str
    rate: float  # 0.0 ~ 1.0
    team_color_id: int | None = None


@dataclass(frozen=True)
class RankSummary:
    """Top-10 pick rates shown on the ranking shell page."""

    team_colors: list[RateEntry] = field(default_factory=list)
    formations: list[RateEntry] = field(default_factory=list)
