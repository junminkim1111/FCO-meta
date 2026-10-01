"""Relation (관계) and special (스페셜) team colors from the datacenter team color page, collected once.

- 목록: `GET /datacenter/teamcolor?strTeamColorType=,relation,` (관계 = 특성 팀컬러) / `,special,`
- 상세: `GET /datacenter/TeamColorDetail?teamcolorid=` → 설명, 단계별 필요 인원과 효과
- 적용 선수: `GET /DataCenter/TeamColorPlayerList?teamcolorid=` → 카드 목록(최대 100장). 관계 팀컬러는 선수 단위로
  적용되므로 선수(pid)로 묶는다. 100장에 걸리면 포지션 묶음(GK·DF·MF·FW)으로 나누고, 묶음도 걸리면 같은 조건을
  능력치 낮은 순으로 한 번 더 받는다 (위·아래 100장씩). "19-20 FC 바르셀로나" 같은 시즌 스쿼드는 선수마다 카드가
  많아 그래도 빠지는 선수가 있을 수 있다 (players_complete = False).
  스페셜은 "19 UEFA Champions League 클래스 선수들"처럼 시즌 카드 전체라 선수 목록을 받지 않는다.

거의 바뀌지 않는 정보라 매일 받지 않고 `python -m fco_meta.crawler teamcolor-info`로 한 번 받아
data/teamcolor_info.json에 둔다 (새 팀컬러가 나오면 다시 실행).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from selectolax.parser import HTMLParser

from ..market.roles import POSITION_GROUPS
from .client import DatacenterClient

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "teamcolor_info.json"
TYPES = {"relation": "관계", "special": "스페셜"}
PLAYER_CAP = 100  # 적용 선수 응답 한 번의 최대 카드 수
_ID_RE = re.compile(r"GetTeamColorDetail\((\d+)\)")
_NUM_RE = re.compile(r"(\d+)")


def parse_list(html: str) -> list[tuple[int, str]]:
    """Team color list page → [(id, name)] in page order."""
    out = []
    for item in HTMLParser(html).css(".teamcolor_item"):
        link, name = item.css_first(".btn_detail_link"), item.css_first(".name")
        m = _ID_RE.search(link.attributes.get("onclick") or "") if link else None
        if m and name:
            out.append((int(m.group(1)), name.text(strip=True)))
    return out


def parse_detail(html: str) -> dict[str, Any]:
    """Detail popup → {"description", "levels": [{"level", "players", "effects"}]}."""
    tree = HTMLParser(html)
    desc = tree.css_first(".header .tit span")
    levels = []
    for node in tree.css(".content_header .level"):
        title, need = node.css_first(".tit"), node.css_first(".desc")
        effects = [li.text(strip=True) for li in node.css(".ap_list li")]
        levels.append({
            "level": int(_NUM_RE.search(title.text()).group(1)),
            "players": int(_NUM_RE.search(need.text()).group(1)),
            "effects": [e for e in effects if e and e != "-"],
        })  # fmt: skip
    return {"description": desc.text(strip=True) if desc else None, "levels": levels}


def _player_cards(
    client: DatacenterClient, team_color_id: int, positions: tuple[int, ...] = (), order: str = "descending"
) -> list[dict]:
    params = {"teamcolorid": str(team_color_id), "strOrderby": f"overallrating {order}"}
    if positions:
        params["strPosition"] = "," + ",".join(map(str, positions)) + ","
    text = client.get("/DataCenter/TeamColorPlayerList", params, referer="/datacenter/teamcolor")
    return json.loads(text).get("players") or []


def fetch_players(client: DatacenterClient, team_color_id: int) -> tuple[list[dict[str, Any]], bool]:
    """([{"pid", "name"}], complete) — complete is False when a position group still hit the card cap."""
    found: dict[int, str] = {}
    complete = True

    def run(positions: tuple[int, ...] = (), order: str = "descending") -> bool:
        cards = _player_cards(client, team_color_id, positions, order)
        for c in cards:
            found.setdefault(int(c["pid"]), c["name"])
        return len(cards) >= PLAYER_CAP

    if run():
        for group in POSITION_GROUPS.values():
            if run(group) and run(group, "ascending"):
                complete = False
    return [{"pid": pid, "name": name} for pid, name in found.items()], complete


def collect(client: DatacenterClient) -> list[dict[str, Any]]:
    out = []
    for kind in TYPES:
        items = parse_list(client.get("/datacenter/teamcolor", {"strTeamColorType": f",{kind},"}))
        log.info("%s: %d team colors", kind, len(items))
        for n, (tc_id, name) in enumerate(items, 1):
            detail = parse_detail(client.get("/datacenter/TeamColorDetail", {"teamcolorid": str(tc_id)}, referer="/datacenter/teamcolor"))
            entry = {"id": tc_id, "name": name, "type": kind, **detail}
            if kind == "relation":
                entry["players"], entry["players_complete"] = fetch_players(client, tc_id)
            out.append(entry)
            if n % 50 == 0:
                log.info("%s: %d/%d", kind, n, len(items))
    return out


def save(entries: list[dict[str, Any]], path: Path = DEFAULT_PATH) -> None:
    Path(path).write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load(path: Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    """[] before the first collection."""
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else []
