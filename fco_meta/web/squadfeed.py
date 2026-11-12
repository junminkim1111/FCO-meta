"""스쿼드메이커로 내보내기: a squad answer as a squad maker link (https://fconline.nexon.com/squadmaker#fclm=…).
The visitor opens it logged in to NEXON and presses the FCLM bookmarklet (static/index.html), which saves the squad
to their own squad storage — this server never touches a NEXON account.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .team import POSITION_CODE, ROLE_OF

MAKER_URL = "https://fconline.nexon.com/squadmaker"
# 포메이션 → 스쿼드메이커 11자리 (자리, x, y). scripts/feed_slots.py로 만든다
SLOTS: dict[str, list[dict[str, Any]]] = json.loads(Path(__file__).with_name("feed_slots.json").read_text(encoding="utf-8"))


class FeedError(Exception):
    """The squad cannot go to the squad maker (the message is shown to the visitor)."""


def lineup_of(result: dict[str, Any]) -> list[dict[str, Any]]:
    """recommend_squad / check_squad result → [{role, player, season, sp_id, grade}] (the grade its price is quoted at)."""
    if result.get("ok") is False or not result.get("lineup") or any(p.get("player") is None for p in result["lineup"]):
        raise FeedError("규칙을 다 맞춘 스쿼드만 스쿼드메이커로 보낼 수 있어요.")
    return [
        {"role": p["role"], "player": p["player"], "season": (p["card"].get("season") or "").split(" (")[0], "sp_id": p["card"]["sp_id"],
         "grade": (p["card"].get("price_at_most_used_grade") or {}).get("grade") or p["card"].get("most_used_grade") or 1}
        for p in result["lineup"]
    ]  # fmt: skip


def place(lineup: list[dict[str, Any]], formation: str | None) -> list[dict[str, Any]]:
    """Each player on its squad maker formation slot: [{position, x, y, player, season, sp_id, grade}] in slot order."""
    slots = SLOTS.get(formation or "")
    if not slots:
        raise FeedError(f"{formation} 포메이션은 스쿼드메이커에 없어 보낼 수 없어요.")
    left = list(lineup)
    placed = []
    for slot in slots:
        p = next((p for p in left if p["role"] == ROLE_OF[POSITION_CODE[slot["role"]]]), None)
        if p is None:
            raise FeedError("스쿼드의 자리가 스쿼드메이커 포메이션과 맞지 않아 보낼 수 없어요.")
        left.remove(p)
        placed.append({"position": slot["role"], "x": slot["x"], "y": slot["y"], **{k: p[k] for k in ("player", "season", "sp_id", "grade")}})
    return placed


def squad_link(placed: list[dict[str, Any]], formation: str, title: str) -> str:
    """The squad maker URL whose #fclm= part the bookmarklet reads: {t: title, f: formation, p: [[position, x, y, spid,
    grade] × 11]} (NEXON works out salary, value and OVR when it is saved)."""
    players = [[p["position"], p["x"], p["y"], p["sp_id"], p["grade"]] for p in placed]
    data = json.dumps({"t": title, "f": formation, "p": players}, ensure_ascii=False, separators=(",", ":"))
    return f"{MAKER_URL}#fclm={quote(data, safe='')}"
