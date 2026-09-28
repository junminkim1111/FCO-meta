from __future__ import annotations

from dataclasses import dataclass

# 한 번의 검색 응답에 담기는 최대 카드 수. 이보다 많으면 조건을 나눠 다시 조회해야 한다.
RESULT_CAP = 200


@dataclass(frozen=True)
class PlayerSearch:
    """Form fields for `POST /datacenter/PlayerList` (only the ones verified to filter).

    페이지 번호(`n4PageNo`)와 가격 범위(`n8PlayerGrade1Min/Max`)는 서버에서 무시되므로 쓰지 않는다.
    """

    team_color_id: int = 0
    positions: tuple[int, ...] = ()  # spposition 코드
    name: str = ""
    salary: tuple[int, int] | None = None  # (min, max), 양 끝 포함
    order_by: str = ""  # 예: " n8playergrade1 descending" (1강 가격 내림차순)

    def to_form(self) -> dict[str, str]:
        form = {"n4PageNo": "1"}
        if self.team_color_id:
            form["teamcolorid"] = str(self.team_color_id)
        if self.positions:
            form["strPosition"] = "," + ",".join(str(p) for p in self.positions) + ","
        if self.name:
            form["strPlayerName"] = self.name
        if self.salary is not None:
            form["n4SalaryMin"], form["n4SalaryMax"] = str(self.salary[0]), str(self.salary[1])
        if self.order_by:
            form["strOrderby"] = self.order_by
        return form
