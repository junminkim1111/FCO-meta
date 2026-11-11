"""스쿼드 피드 ID 받기: a squad answer saved as a public squad maker feed under the site's own FC Online account
(its login cookies in FCO_FEED_COOKIE), so the visitor can open it in the squad maker by its ID.

Only a public feed gets an ID, and the ID dies with the saved squad: never delete what this saved.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .team import POSITION_CODE, ROLE_OF

SAVE_URL = "https://fconline.nexon.com/squadmakerapi/SquadMakerProc"
# 포메이션 → 스쿼드메이커 11자리 (자리, x, y). scripts/feed_slots.py로 만든다
SLOTS: dict[str, list[dict[str, Any]]] = json.loads(Path(__file__).with_name("feed_slots.json").read_text(encoding="utf-8"))
TACTICS = {"defensivestyle": 1, "defwidth": 6, "defdepth": 6, "offensive_style_buildup": 1, "offensive_style_chancecreation": 1,
           "offwidth": 6, "offplayerinbox": 6, "offcornerkick": 3, "offfreekick": 3, "teammentality": 3}  # 스쿼드메이커 기본값  # fmt: skip
BENCH = 7
NONE = [-2, -2, -2]  # 특성·트레이너 없음


class FeedError(Exception):
    """The squad could not become a feed (the message is shown to the visitor)."""


def lineup_of(result: dict[str, Any]) -> list[dict[str, Any]]:
    """recommend_squad / check_squad result → [{role, pid, sp_id, grade}] (the grade its price is quoted at)."""
    if result.get("ok") is False or not result.get("lineup") or any(p.get("player") is None for p in result["lineup"]):
        raise FeedError("규칙을 다 맞춘 스쿼드만 피드로 만들 수 있어요.")
    return [
        {"role": p["role"], "pid": p["pid"], "sp_id": p["card"]["sp_id"],
         "grade": (p["card"].get("price_at_most_used_grade") or {}).get("grade") or p["card"].get("most_used_grade") or 1}
        for p in result["lineup"]
    ]  # fmt: skip


def feed_squad(lineup: list[dict[str, Any]], formation: str | None, title: str, about: str) -> dict[str, Any]:
    """The squad JSON the squad maker saves: each player on its formation slot, then an empty bench.
    Salary, value and OVR are left at 0 — NEXON's server works them out from the cards."""
    slots = SLOTS.get(formation or "")
    if not slots:
        raise FeedError(f"{formation} 포메이션은 스쿼드메이커에 없어 피드로 만들 수 없어요.")
    left = list(lineup)
    players = []
    for slot in slots:
        p = next((p for p in left if p["role"] == ROLE_OF[POSITION_CODE[slot["role"]]]), None)
        if p is None:
            raise FeedError("스쿼드의 자리가 스쿼드메이커 포메이션과 맞지 않아 피드로 만들 수 없어요.")
        left.remove(p)
        players.append({"role": slot["role"], "position": "", "position2": "", "position3": "", "state": 1, "x": slot["x"],
                        "y": slot["y"], "buildUp": p["grade"], "strategy": [], "participation": [], "traits": NONE,
                        "player_traits": [], "trait": "", "trainer": NONE, "thumb_custom": "", "pid": p["pid"], "spid": p["sp_id"]})  # fmt: skip
    empty = {"role": "", "position": "", "position2": "", "position3": "", "state": 0, "x": 0, "y": 0, "buildUp": 0,
             "strategy": [], "participation": [], "traits": NONE, "player_traits": [], "trait": "", "trainer": NONE}  # fmt: skip
    return {"squadName": title, "contents": about, "mode": "1", "formation": formation, "coach": 0, "adap": 5, "preset": 0,
            "backcolor": None, "ovr": {"fw": 0, "mf": 0, "df": 0, "total": 0}, "players": players + [dict(empty) for _ in range(BENCH)],
            "teamSt": TACTICS, "coachinfo": None, "totalPay": "0", "totalPrice": "0", "totalTeamColor": None}  # fmt: skip


def save_feed(squad: dict[str, Any], cookie: str) -> str:
    """Save `squad` as a public feed with the account's cookies; returns its feed ID."""
    r = httpx.post(
        SAVE_URL, timeout=15, headers={"Cookie": cookie, "X-Requested-With": "XMLHttpRequest"},
        data={"strMethod": "savesquadinfo", "strSquadJson": json.dumps(squad, ensure_ascii=False), "n1Feed": "1"},
    )  # fmt: skip
    r.raise_for_status()
    body = r.json()
    feed_id = (body.get("ResultData") or {}).get("strFeedId") if body.get("ResultCode") == 1 else None
    message = body.get("ResultMsg") or ""
    if not feed_id and "로그인" in message:  # FCO_FEED_COOKIE 만료 ("로그인 후 사용 가능합니다.")
        raise FeedError("지금은 피드 ID를 만들 수 없어요 (사이트 계정 로그인이 풀렸어요).")
    if not feed_id:
        raise FeedError(message or "스쿼드메이커가 저장을 받지 않았어요.")
    return feed_id
