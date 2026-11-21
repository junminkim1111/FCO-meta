"""@구단주명으로 불러온 유저의 최근 경기(최대 20)로 플레이스타일·득실점을 집계한다 (넥슨 Open API, 경기 수 + 1~3회 호출).

경기 원본은 모델에 넘기지 않고 여기서 요약한다: 승무패·득실점, 득점·실점 경로, 슈팅·패스 종류 비중과 성공률, 점유율, 수비, 많이 넣은 선수.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .team import MATCH_TYPES, match_id_time

MAX_MATCHES = 20
MIN_OFFICIAL = 5  # 공식경기가 이보다 적으면 친선경기도 더한다

GOAL_ROUTES = {"헤더": "goalHeading", "프리킥": "goalFreekick", "페널티킥": "goalPenaltyKick",
               "박스 안": "goalInPenalty", "박스 밖": "goalOutPenalty"}  # fmt: skip
SHOT_ROUTES = {"헤더": "shootHeading", "프리킥": "shootFreekick", "페널티킥": "shootPenaltyKick",
               "박스 안": "shootInPenalty", "박스 밖": "shootOutPenalty"}  # fmt: skip
PASSES = {"짧은 패스": "shortPass", "긴 패스": "longPass", "로빙 패스": "bouncingLobPass", "땅볼 패스": "drivenGroundPass",
          "스루 패스": "throughPass", "로빙 스루 패스": "lobbedThroughPass"}  # fmt: skip


def recent_matches(api: Any, ouid: str) -> list[tuple[str, str]]:
    """[(match_id, 경기 종류)] newest first: official 1vs1 first, friendlies added when there are few."""
    found = [(m, MATCH_TYPES[50]) for m in api.user_matches(ouid, 50, limit=MAX_MATCHES)]
    if len(found) < MIN_OFFICIAL:
        for match_type, name in MATCH_TYPES.items():
            if match_type != 50:
                found += [(m, name) for m in api.user_matches(ouid, match_type, limit=MAX_MATCHES)]
        found.sort(key=lambda m: match_id_time(m[0]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return found[:MAX_MATCHES]


def summarize(ouid: str, details: list[tuple[str, dict[str, Any]]], names: dict[int, str]) -> dict[str, Any]:
    """details: [(경기 종류, match-detail)]. names: sp_id → 선수 이름 (많이 넣은 선수 표시용)."""
    mine_total: Counter[str] = Counter()
    theirs_total: Counter[str] = Counter()
    results: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    controllers: Counter[str] = Counter()
    scorers: Counter[int] = Counter()
    possession, rating, dates = [], [], []
    for kind, d in details:
        infos = d.get("matchInfo") or []
        me = next((i for i in infos if i.get("ouid") == ouid), None)
        them = next((i for i in infos if i.get("ouid") != ouid), None)
        if not me:
            continue
        kinds[kind] += 1
        dates.append(d.get("matchDate", "")[:10])
        md = me.get("matchDetail") or {}
        results[md.get("matchResult") or "?"] += 1
        controllers[md.get("controller") or "?"] += 1
        if md.get("possession") is not None:
            possession.append(md["possession"])
        if md.get("averageRating") is not None:
            rating.append(md["averageRating"])
        for side, total in ((me, mine_total), (them, theirs_total)):
            if not side:
                continue
            for group in ("shoot", "pass", "defence"):
                total.update({k: v for k, v in (side.get(group) or {}).items() if isinstance(v, (int, float))})
            total.update({k: (side.get("matchDetail") or {}).get(k) or 0 for k in ("dribble", "foul", "cornerKick", "offsideCount")})
        for p in me.get("player") or []:
            if (goals := (p.get("status") or {}).get("goal")):
                scorers[int(p["spId"])] += goals
    n = sum(kinds.values())
    if not n:
        return {"matches": 0, "note": "최근 공식·친선경기 기록이 없어 분석할 수 없음"}

    def per(v: float) -> float:
        return round(v / n, 2)

    def share(part: float, whole: float) -> str | None:
        return f"{part / whole:.0%}" if whole else None

    def routes(total: Counter[str], keys: dict[str, str], whole: str) -> dict[str, str | None]:
        return {name: share(total[k], total[whole]) for name, k in keys.items()}

    passes = {name: {"경기당": per(mine_total[k + "Try"]), "비중": share(mine_total[k + "Try"], mine_total["passTry"]),
                     "성공률": share(mine_total[k + "Success"], mine_total[k + "Try"])} for name, k in PASSES.items()}  # fmt: skip
    return {
        "matches": n,
        "match_types": dict(kinds),
        "period": f"{min(dates)} ~ {max(dates)}" if dates else None,
        "record": {"승": results["승"], "무": results["무"], "패": results["패"], "승률": share(results["승"], n)},
        "goals": {"경기당 득점": per(mine_total["goalTotal"]), "경기당 실점": per(theirs_total["goalTotal"]),
                  "득점 경로": routes(mine_total, GOAL_ROUTES, "goalTotal"), "실점 경로": routes(theirs_total, GOAL_ROUTES, "goalTotal")},
        "shooting": {"경기당 슈팅": per(mine_total["shootTotal"]), "유효슈팅 비율": share(mine_total["effectiveShootTotal"], mine_total["shootTotal"]),
                     "골 결정률": share(mine_total["goalTotal"], mine_total["shootTotal"]), "슈팅 위치·종류": routes(mine_total, SHOT_ROUTES, "shootTotal"),
                     "경기당 허용 슈팅": per(theirs_total["shootTotal"])},
        "passing": {"경기당 패스": per(mine_total["passTry"]), "패스 성공률": share(mine_total["passSuccess"], mine_total["passTry"]), "종류별": passes},
        "possession_avg": round(sum(possession) / len(possession), 1) if possession else None,
        "defence": {"경기당 태클": per(mine_total["tackleTry"]), "태클 성공률": share(mine_total["tackleSuccess"], mine_total["tackleTry"]),
                    "경기당 블록": per(mine_total["blockTry"]), "블록 성공률": share(mine_total["blockSuccess"], mine_total["blockTry"])},
        "etc": {"경기당 드리블": per(mine_total["dribble"]), "경기당 코너킥": per(mine_total["cornerKick"]), "경기당 파울": per(mine_total["foul"]),
                "평균 평점": round(sum(rating) / len(rating), 2) if rating else None, "컨트롤러": dict(controllers)},
        "top_scorers": [{"player": names.get(sp, str(sp)), "goals": g} for sp, g in scorers.most_common(5)],
        "how_to_read": "경로 비율: 박스 안 + 박스 밖 = 100%. 헤더·프리킥·페널티킥은 그와 겹치는 따로 센 비율 (헤더 골도 대부분 박스 안). "
                       "블록 성공률은 넥슨 기록 기준이라 낮게 나올 수 있으니 답에서 강조하지 않는다",
    }  # fmt: skip


def player_names(conn: sqlite3.Connection, sp_ids: list[int]) -> dict[int, str]:
    if not sp_ids:
        return {}
    rows = conn.execute(f"SELECT sp_id, name FROM meta_spid WHERE sp_id IN ({','.join('?' * len(sp_ids))})", sp_ids).fetchall()
    return {int(sp): name for sp, name in rows}
