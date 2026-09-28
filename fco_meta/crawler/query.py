from __future__ import annotations

from dataclasses import dataclass

# 등급 코드: 3100 = 유망주 3부 ... 900 = 챔피언스, 800 = 슈퍼 챔피언스 (작을수록 높음)
TIER_LOWEST = 3100
TIER_HIGHEST = 800
SQUAD_VALUE_MAX = 18000000000000000000
RANK_MAX = 10000


@dataclass(frozen=True)
class RankQuery:
    """Filter for `/datacenter/rank_inner`, mirroring `goSearchDetail()` on the page."""

    mode: str = "1vs1"  # "1vs1" | "2vs2" | "manager"
    season: int = 0  # 0 = 현재 시즌
    team_color_id: int = 0
    team_color_id_2: int = 0
    team_color_league_id: int = 0
    team_color_league_id_2: int = 0
    team_color_continent_id: int = 0
    team_color_continent_id_2: int = 0
    team_color_count: tuple[int, int] = (1, 11)
    team_color_count_2: tuple[int, int] = (1, 11)
    formation: str = "-"  # "4-2-3-1", 또는 "3"/"4"/"5" (n백 전체)
    formation_2: str = "-"
    squad_value: tuple[int, int] = (0, SQUAD_VALUE_MAX)
    tier: tuple[int, int] = (TIER_LOWEST, TIER_HIGHEST)
    rank: tuple[int, int] = (1, RANK_MAX)

    @property
    def is_filtered(self) -> bool:
        return self != RankQuery(mode=self.mode, season=self.season)

    def to_params(self, page: int) -> dict[str, str]:
        params = {"rt": self.mode, "n4seasonno": str(self.season), "n4pageno": str(page)}
        if not self.is_filtered:
            # 페이지 최초 로딩과 같은 형태
            return params
        params.update(
            {
                "tc_01": str(self.team_color_id),
                "tc_02": str(self.team_color_id_2),
                "tc_l_01": str(self.team_color_league_id),
                "tc_l_02": str(self.team_color_league_id_2),
                "tc_c_01": str(self.team_color_continent_id),
                "tc_c_02": str(self.team_color_continent_id_2),
                "tc_01_cnt_s": str(self.team_color_count[0]),
                "tc_01_cnt_e": str(self.team_color_count[1]),
                "tc_02_cnt_s": str(self.team_color_count_2[0]),
                "tc_02_cnt_e": str(self.team_color_count_2[1]),
                "formation_01": self.formation,
                "formation_02": self.formation_2,
                "cv_s": str(self.squad_value[0]),
                "cv_e": str(self.squad_value[1]),
                "tier_s": str(self.tier[0]),
                "tier_e": str(self.tier[1]),
                "rank_s": str(self.rank[0]),
                "rank_e": str(self.rank[1]),
            }
        )
        return params
