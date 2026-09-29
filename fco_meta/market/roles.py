"""Position groups used by the datacenter player search (spposition codes).

The groups and codes are copied from the position checkboxes on `/datacenter`
(`data-no` attributes); code meanings come from `/static/fconline/meta/spposition.json`.
"""

from __future__ import annotations

import re

ROLES: dict[str, tuple[int, ...]] = {
    "GK": (0,),
    "CB": (1, 4, 5, 6),  # SW, RCB, CB, LCB
    "RB": (3,),
    "LB": (7,),
    "RWB": (2,),
    "LWB": (8,),
    "DM": (9, 10, 11),  # RDM, CDM, LDM
    "CM": (13, 14, 15),  # RCM, CM, LCM
    "CAM": (17, 18, 19),  # RAM, CAM, LAM
    "RM": (12,),
    "LM": (16,),
    "RW": (23,),
    "LW": (27,),
    "CF": (20, 21, 22),  # RF, CF, LF
    "ST": (24, 25, 26),  # RS, ST, LS
}

# 데이터센터의 "전체" 체크박스 묶음 (검색 결과가 너무 많을 때 나눠 조회하는 용도)
POSITION_GROUPS: dict[str, tuple[int, ...]] = {
    "GK": (0,),
    "DF": (1, 4, 5, 6, 3, 7, 2, 8),
    "MF": (13, 14, 15, 17, 18, 19, 9, 10, 11, 16, 12),
    "FW": (24, 25, 26, 20, 21, 22, 27, 23),
}

ROLE_ALIASES: dict[str, str] = {
    "볼란치": "DM",
    "수미": "DM",
    "수비형미드필더": "DM",
    "cdm": "DM",
    "rdm": "DM",
    "ldm": "DM",
    "dm": "DM",
    "중앙미드필더": "CM",
    "중미": "CM",
    "공미": "CAM",
    "공격형미드필더": "CAM",
    "센터백": "CB",
    "센백": "CB",
    "골키퍼": "GK",
    "키퍼": "GK",
    "스트라이커": "ST",
    "원톱": "ST",
    "좌윙": "LW",
    "왼쪽윙": "LW",
    "레프트윙": "LW",
    "우윙": "RW",
    "오른쪽윙": "RW",
    "라이트윙": "RW",
    "좌풀백": "LB",
    "왼쪽풀백": "LB",
    "레프트백": "LB",
    "우풀백": "RB",
    "오른쪽풀백": "RB",
    "라이트백": "RB",
    "세컨톱": "CF",
    "섀도우": "CF",
}


# 여러 역할을 묶어 부르는 말 → 역할들 (챗봇에서 한 번에 조회)
ROLE_GROUP_ALIASES: dict[str, tuple[str, ...]] = {
    "윙어": ("RW", "LW"),
    "윙": ("RW", "LW"),
    "측면공격수": ("RW", "LW"),
    "공격수": ("ST", "CF", "RW", "LW"),
    "포워드": ("ST", "CF", "RW", "LW"),
    "fw": ("ST", "CF", "RW", "LW"),
    "풀백": ("RB", "LB"),
    "측면수비수": ("RB", "LB", "RWB", "LWB"),
    "윙백": ("RWB", "LWB"),
    "측면미드필더": ("RM", "LM"),
    "사이드미드필더": ("RM", "LM"),
    "미드필더": ("DM", "CM", "CAM", "RM", "LM"),
    "mf": ("DM", "CM", "CAM", "RM", "LM"),
    "수비수": ("CB", "RB", "LB", "RWB", "LWB"),
    "df": ("CB", "RB", "LB", "RWB", "LWB"),
}


def resolve_roles(text: str) -> tuple[str, ...] | None:
    """"윙어" → ("RW", "LW"), "볼란치" → ("DM",), "RW,LW" / "RW+LW" → ("RW", "LW"). Unknown → None."""
    key = "".join(text.split()).casefold()
    if key in ROLE_GROUP_ALIASES:
        return ROLE_GROUP_ALIASES[key]
    roles: list[str] = []
    for part in re.split(r"[,+/]", key):
        role = resolve_role(part) if part else None
        if role is None:
            return None
        if role not in roles:
            roles.append(role)
    return tuple(roles) or None


def resolve_role(text: str) -> str | None:
    """"볼란치" / "CDM" / "dm" → "DM". Unknown → None."""
    key = "".join(text.split())
    if key.upper() in ROLES:
        return key.upper()
    return ROLE_ALIASES.get(key.casefold())
