"""Position groups used by the datacenter player search (spposition codes).

The groups and codes are copied from the position checkboxes on `/datacenter`
(`data-no` attributes); code meanings come from `/static/fconline/meta/spposition.json`.
"""

from __future__ import annotations

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
    "풀백": "RB",
    "골키퍼": "GK",
    "키퍼": "GK",
    "스트라이커": "ST",
    "원톱": "ST",
    "윙어": "RW",
}


def resolve_role(text: str) -> str | None:
    """"볼란치" / "CDM" / "dm" → "DM". Unknown → None."""
    key = "".join(text.split())
    if key.upper() in ROLES:
        return key.upper()
    return ROLE_ALIASES.get(key.casefold())
