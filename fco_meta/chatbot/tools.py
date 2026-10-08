"""Tools the chatbot calls (규칙 기반·LLM 공통). Every number in an answer must come from one of these."""

from __future__ import annotations

import inspect
import itertools
import json
import logging
import math
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..analytics import query_squads as aggregate_squads
from ..analytics import (
    ALL_FORMATIONS,
    ALL_RANKERS,
    GROUP_BY,
    MATCH_STATS,
    RANKER_GROUP_BY,
    RANKER_SORT_BY,
    SORT_BY,
    STAT_NAMES,
    TOP10000_STATS,
    SquadQuery,
    UsageStore,
    best_eleven,
    formation_matchups,
    player_roles,
    query_rankers,
    ranker_stats_summary,
    resolve_stat,
    role_slots,
    squad_range,
    squad_snapshot,
    top_players,
    top_ranker_squad,
    usage_history,
)
from ..crawler import teamcolor_info
from ..crawler.models import TeamColor
from ..crawler.teamcolors import TeamColorCatalog
from ..market.money import format_bp
from ..market.roles import ROLES, resolve_roles
from ..market.storage import SCHEMA as MARKET_SCHEMA
from ..pipeline.formation import FORMATIONS
from ..storage import latest_unfiltered_snapshot, unfiltered_coverage

log = logging.getLogger(__name__)

# 흔한 줄임말·다른 표기 → 팀컬러 목록의 이름 (data/team_color_aliases.json, 키는 공백 제거·소문자)
ALIASES_PATH = Path(__file__).resolve().parents[2] / "data" / "team_color_aliases.json"


def load_team_color_aliases(path: Path = ALIASES_PATH) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))["aliases"]
    return {"".join(k.split()).casefold(): v for k, v in raw.items()}


TEAM_COLOR_ALIASES = load_team_color_aliases()

MAX_LISTED_COMBOS = 40
CATEGORY_LABELS = {"club": "클럽", "nationality": "국가", "special": "특수"}
CATEGORY_CODES = {v: k for k, v in CATEGORY_LABELS.items()}
# team_color에 이 값들이 오면 팀컬러 무관 상위 랭커 전체 (공백·대소문자 무시)
ALL_RANKER_WORDS = {"", "0", "전체", "전체랭커", "랭커전체", "상위랭커", "상위랭커전체", "all", "allrankers", "*", "none", "null"}

ROLE_HELP = (
    "DM(볼란치: RDM/CDM/LDM), CAM(가운데 공미), RAM·LAM(오른쪽·왼쪽 공미), CM, RM, LM, RW, LW, ST, CF, CB, RB, LB, RWB, LWB, GK. "
    "여러 역할은 'RW,LW'처럼 쉼표로, 묶음 이름도 가능: 윙어(RW,LW,RM,LM,RAM,LAM), 공격수(ST,CF,RW,LW), 풀백(RB,LB), 윙백, 측면미드필더, "
    "미드필더, 수비수"
)
DEFAULT_MIN_USAGE_FOR_PRICE_SORT = 0.03  # 가성비(시세 낮은 순) 정렬에서 이보다 적게 쓰인 선수는 제외
TREND_MIN_RANKERS = 3  # 사용률 변화 목록에 넣을 최소 사용 랭커 수 (두 스냅샷 중 큰 쪽)
QUERY_MIN_RANKERS = 3  # query_squads를 평점·승률 등으로 정렬할 때 기본 최소 사용 랭커 수 (1명짜리 1위 방지)
QUERY_MAX_ROWS = 30
MAX_TEAM_COLOR_INFO = 10  # get_team_color_info가 상세까지 보여 줄 최대 팀컬러 수 (넘으면 이름만)
SALARY_CAP = 310  # 게임의 팀 급여 상한: 선발 11명을 짤 때는 항상 이 안에서 고른다
# 신규특성(금특·신특): 8강(금카) 이상이면 어떤 카드든 하나를 더 달 수 있다. 줄임말 → 카드 상세의 특성 이름
NEW_TRAITS = {
    "라인 브레이커": ["라브"], "체이서": ["체"], "파이터": ["파"], "트릭스터": ["트릭"], "스피드스터": ["스스"],
    "크로스 포쳐": ["크포", "크로스포처"], "아크로바틱 피니셔": ["아크로", "아크로바틱"], "타이탄": [], "블로커": [],
    "프레데터": [], "레이저 슈터": ["레이저"], "커맨더": [],
}  # fmt: skip
TRAIT_NAMES = {"".join(k.split()): k for k in NEW_TRAITS} | {a: k for k, v in NEW_TRAITS.items() for a in v}
ADD_TRAIT_GRADE = 8
MAX_TEAM_COLOR_NAMES = 40
# 포메이션을 지정해도 그 포메이션 스쿼드가 이보다 적으면 팀컬러 전체 포메이션의 같은 역할로 본다
# (롬바르디아 4-1-4-1 16명보다 롬바르디아 전체 479명의 ST가 낫다 — 원톱은 4-1-4-1이든 4-2-3-1이든 같은 역할)
FORMATION_MIN_SAMPLE = 50
RANKERS_MIN_FOR_SORT = 30  # query_rankers를 승률 등으로 정렬할 때 기본 최소 랭커 수 (10,000명 규모라 넉넉히)


def format_as_of(value: str | None) -> str:
    if not value:
        return "기준 시각 미상"
    return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M 기준")


def normalize_formation(value: str | None) -> str | None:
    """"4231" / "4-2-3-1" / "4 4 2 (2)" → 게임 포메이션 이름. 비었거나 '전체'면 None, 모르는 값은 그대로."""
    text = "".join(str(value or "").split())
    if text in ("", "*", "전체"):
        return None
    if text in FORMATIONS:
        return text
    digits = re.sub(r"\D", "", text)
    matches = [f for f in FORMATIONS if re.sub(r"\D", "", f) == digits]
    return matches[0] if len(matches) == 1 else text


def parse_traits(text: str) -> list[str]:
    """"라브, 트릭" / "체 파" / "크로스 포처" → 신규특성 이름들."""
    out = []
    for part in re.split(r"[,+/·]+", text):
        words = part.split()
        if not words:
            continue
        # 띄어 쓴 한 이름("크로스 포처")이면 붙여서, 아니면 낱말마다("체 파")
        for word in [w for w in ["".join(words)] if w in TRAIT_NAMES] or words:
            if word not in TRAIT_NAMES:
                raise ToolError(f"알 수 없는 신규특성: {word} (가능: {', '.join(NEW_TRAITS)})")
            out.append(TRAIT_NAMES[word])
    return list(dict.fromkeys(out))


def _arg_name(key: str, known: Iterable[str]) -> str:
    """모델이 망가뜨린 인자 이름을 실제 이름으로: 'top_n`' / 'team_color」 string=' / 'Role' / 'team_color_rankers'
    → 'top_n' / 'team_color' / 'role' / 'team_color' (영문·숫자·밑줄 앞부분, 그래도 모르면 가장 길게 겹치는 실제 이름)."""
    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", key)
    name = m.group(1).lower() if m else key
    if name in known:
        return name
    return max((k for k in known if name.startswith(k + "_")), key=len, default=name)


def _price_bp(card: dict[str, Any]) -> int | None:
    price = card["price_for_traits"] if "price_for_traits" in card else card.get("price_at_most_used_grade")
    return price["price_bp"] if price else None


MIN_CARD_SHARE = 0.1  # 스쿼드 후보 카드: 그 선수를 쓴 랭커 중 이 비율 이상이 쓴 카드만 (UP 3강처럼 한두 명만 쓴 카드 제외)


def _common_cards(cards: list[dict[str, Any]], users: int, keep: Callable[[dict[str, Any]], bool] = lambda c: False) -> list[dict[str, Any]]:
    """Cards enough of the player's users picked — the most used one always stays, and so do cards `keep` asks for.

    Every card option scores the player's whole usage rate, so without this a card one ranker used (cheap, low grade)
    beats the card everyone uses as soon as there is a budget or salary limit."""
    top = max(cards, key=lambda c: c["rankers"], default=None)
    return [c for c in cards if c is top or c["rankers"] >= MIN_CARD_SHARE * users or keep(c)]


def _cost(card: dict[str, Any], kind: str) -> int | None:
    return _price_bp(card) if kind == "price" else card.get("salary")


MAX_EXHAUSTIVE = 300_000  # 자리 조합 수가 이 이하면 전부 따져 최적 조합을 고른다 (보통 2~3자리)


def _choose(
    slot_options: list[tuple[str, list[dict[str, Any]]]], limits: dict[str, int], reqs: dict[str, int] | None = None
) -> list[dict[str, Any] | None]:
    """One option per slot, never the same player twice.

    Without limits: the most used player per slot. With limits (total price/salary): among the
    combinations within every limit, the one with the highest summed usage rate — exhaustively when
    the combinations are few, otherwise by greedy swaps that cut the overrun per usage lost.
    Unknown costs count as 0 (the caller reports those players). `reqs` (tag → n, 케미·시즌 단일): at least n chosen
    options whose "tags" hold the tag — a shortfall counts like going over a limit, so the same swaps fill it. Limits
    weigh far more, so a swap never buys a tag by going over the salary cap or budget.
    """
    reqs = reqs or {}
    chosen: list[dict[str, Any] | None] = []
    for _, options in slot_options:
        used = {c["entry"]["pid"] for c in chosen if c}
        chosen.append(next((o for o in options if o["entry"]["pid"] not in used), None))
    if not limits and not reqs:
        return chosen

    def overrun(combo) -> float:
        over = 100 * sum(max(0, sum(c[k] or 0 for c in combo if c) - v) / max(v, 1) for k, v in limits.items())
        for tag, need in reqs.items():  # ponytail: 탐욕 교체라 조건을 채우는 최적 조합은 아닐 수 있음
            over += max(0, need - sum(1 for c in combo if c and tag in c["tags"])) / need
        return over

    if all(options for _, options in slot_options) and math.prod(len(o) for _, o in slot_options) <= MAX_EXHAUSTIVE:
        best, best_key = None, None
        for combo in itertools.product(*(options for _, options in slot_options)):
            pids = [o["entry"]["pid"] for o in combo]
            if len(set(pids)) < len(pids) or overrun(combo) > 0:
                continue
            key = (round(sum(o["entry"]["usage_rate"] for o in combo), 6), sum(o["salary"] or 0 for o in combo))
            if best_key is None or key > best_key:
                best, best_key = list(combo), key
        if best is not None:
            return best

    current = overrun(chosen)
    while current > 0:
        best = None
        for i, (_, options) in enumerate(slot_options):
            cur = chosen[i]
            if cur is None:
                continue
            used = {c["entry"]["pid"] for j, c in enumerate(chosen) if c and j != i}
            for o in options:
                if o is cur or o["entry"]["pid"] in used:
                    continue
                after = overrun(chosen[:i] + [o] + chosen[i + 1:])
                if after >= current:
                    continue
                loss = max(cur["entry"]["usage_rate"] - o["entry"]["usage_rate"], 0.0)
                score = (current - after) / (loss + 0.01)
                if best is None or score > best[0]:
                    best = (score, i, o, after)
        if best is None:
            break
        _, i, o, current = best
        chosen[i] = o
    return _fill(chosen, slot_options, overrun) if current == 0 else chosen


def _fill(chosen: list[dict[str, Any] | None], slot_options, overrun: Callable[[Any], float]) -> list[dict[str, Any] | None]:
    """Spend what the limits leave, as rankers do (급여 310이면 308~310): keep swapping while every limit and
    requirement still holds and the summed usage goes up — or stays the same and the salary goes up, i.e. the same
    player's higher-salary (better) card. Usage first, so this never trades a popular player for a pricier one."""

    def key(combo) -> tuple[float, int]:
        return round(sum(c["entry"]["usage_rate"] for c in combo if c), 6), sum(c["salary"] or 0 for c in combo if c)

    while True:
        best = None
        for i, (_, options) in enumerate(slot_options):
            used = {c["entry"]["pid"] for j, c in enumerate(chosen) if c and j != i}
            for o in options:
                if o is chosen[i] or o["entry"]["pid"] in used:
                    continue
                combo = chosen[:i] + [o] + chosen[i + 1:]
                if overrun(combo) == 0 and key(combo) > key(chosen) and (best is None or key(combo) > best[0]):
                    best = (key(combo), i, o)
        if best is None:
            return chosen
        chosen[best[1]] = best[2]


_ISO_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?$")


def _is_rate(key: str) -> bool:
    # avg_season_win_rate_of_users·win_rate_of_those_matches처럼 rate가 중간에 있는 이름도 비율
    return "rate" in key or "share" in key or key.endswith("coverage") or key == "change"


def model_view(value: Any, key: str = "") -> Any:
    """A tool result as the model sees it: rates as "52.8%" (changes as "+3.4%p"), other decimals rounded
    (ELO 2769.9, 평점 6.21), times as "2026-10-01 09:00". The web and the number check use the raw result;
    the model then writes these values as they are instead of "사용률 0.528" or "ELO 2769.9251"."""
    if isinstance(value, dict):
        return {k: model_view(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [model_view(v, key) for v in value]
    if isinstance(value, float):
        if _is_rate(key) and -1 <= value <= 1:
            pct = round(value * 100, 1)
            return f"{pct:+.1f}%p" if key.endswith("change") else f"{pct:.1f}%"
        return round(value, 1) if abs(value) >= 100 else round(value, 2)
    if isinstance(value, str) and (m := _ISO_TIME_RE.match(value)):
        return f"{m.group(1)} {m.group(2)}"
    return value


# 화면·내부용 값: 모델의 답에는 쓰이지 않아 모델에게는 보내지 않는다 (입력 토큰을 줄인다)
SCREEN_ONLY = {"season_img", "sp_id", "pid", "fetched_at", "mode"}
MAX_DETAIL_ROLES, MAX_DETAIL_CARDS, MAX_DETAIL_USAGE = 6, 6, 10  # get_player_detail을 모델에게 줄 때 남기는 수


def for_model(name: str, result: Any) -> Any:
    """A tool result as the model gets it: model_view without screen-only values (image URLs, ids, fetch times, BP
    integers next to their formatted price), short season names ("25 UCL"), and get_player_detail cut to what answers
    use. The web and the number check keep the full result."""
    if name == "get_player_detail" and isinstance(result, dict):
        result = _brief_player_detail(result)
    return model_view(_strip(result))


def _strip(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _strip(v, k) for k, v in value.items()
                if k not in SCREEN_ONLY and not (k == "price_bp" and "price" in value)}  # fmt: skip
    if isinstance(value, list):
        return [_strip(v, key) for v in value]
    if key == "season" and isinstance(value, str):
        return value.split(" (")[0]  # "25 UCL (25 UEFA Champions League)" → "25 UCL"
    return value


def _brief_player_detail(result: dict[str, Any]) -> dict[str, Any]:
    """Roles the player is really used in (3+ rankers, at least the top one), their most used cards, the biggest
    per-formation rows, and each card's prices by grade as one map instead of a row per grade."""
    season_of: dict[int, str] = {}
    players = []
    for p in result.get("players", []):
        roles = sorted(p.get("roles", []), key=lambda r: -r.get("rankers", 0))
        kept = [r for i, r in enumerate(roles) if i == 0 or r.get("rankers", 0) >= 3][:MAX_DETAIL_ROLES]
        for r in roles:
            for c in r.get("cards", []):
                season_of[c.get("sp_id")] = c.get("season")
        for c in p.get("card_profiles") or []:
            season_of.setdefault(c.get("sp_id"), c.get("season"))
        players.append({**p, "roles": [{**r, "cards": r.get("cards", [])[:MAX_DETAIL_CARDS]} for r in kept]})
    prices: dict[str, dict[str, str]] = {}
    for row in result.get("prices", []):
        card = season_of.get(row.get("sp_id")) or str(row.get("sp_id"))
        prices.setdefault(card, {})[str(row.get("grade"))] = row.get("price")
    out = {**result, "players": players}
    if "usage" in result:
        out["usage"] = sorted(result["usage"], key=lambda u: -u.get("rankers", 0))[:MAX_DETAIL_USAGE]
    if "prices" in result:
        out["prices"] = [{"season": season, "price_by_grade": by_grade} for season, by_grade in prices.items()]
    return out


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Plain JSON Schema (LLM 제공자와 무관). 선택 항목은 required에서 뺀다."""
    return {"type": "object", "properties": properties, "required": required}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "resolve_terms",
        "description": (
            "사용자가 쓴 팀컬러·역할 표현을 표준 값으로 바꾼다. 예: '아스날' → 아스널(1004), '볼란치' → DM. "
            "다른 도구가 '알 수 없는 팀컬러/역할' 오류를 돌려줬을 때만 호출한다 (다른 도구도 별칭을 스스로 해석한다)."
        ),
        "input_schema": _schema(
            {
                "team_color": {"type": "string", "description": "팀컬러 이름 또는 id"},
                "role": {"type": "string", "description": "역할/포지션 표현 (볼란치, 공미, CDM 등)"},
            },
            [],
        ),
    },
    {
        "name": "list_available_data",
        "description": (
            "집계가 끝난 팀컬러×포메이션 조합과 표본 수, 기준 시각을 돌려준다. "
            "추천할 수 있는 범위를 확인하거나 데이터가 없는 조합을 물었을 때 사용한다."
        ),
        "input_schema": _schema({}, []),
    },
    {
        "name": "list_formations",
        "description": (
            "랭커들의 포메이션 분포(최신 스냅샷)와 스쿼드 수집 여부. team_color를 생략하면 매일 수집하는 상위 랭커 전체."
        ),
        "input_schema": _schema(
            {"team_color": {"type": "string", "description": "팀컬러 이름 또는 id. 생략하면 전체 랭커"}}, []
        ),
    },
    {
        "name": "recommend_players",
        "description": (
            "팀컬러×포메이션 랭커들이 특정 역할(여러 역할 가능)에 선발로 기용한 선수 순위(사용률). 선수별 시즌 카드 내역, 강화 분포, "
            "사용 랭커 평균 ELO·시즌 승률, 시세·급여(수집된 경우)를 함께 준다. 표본이 적으면 팀컬러 전체 포메이션으로 자동 폴백하고 표시한다. "
            f"role: {ROLE_HELP}. 한 장 예산은 max_price_bp, 급여 상한은 max_salary로 걸러낸다. "
            "신규특성 조건은 traits로 건다."
        ),
        "input_schema": _schema(
            {
                "team_color": {
                    "type": "string",
                    "description": (
                        "팀컬러 이름 또는 id (예: 아스널). 사용자가 팀컬러를 말하지 않았으면 생략하거나 '전체 랭커' → 상위 랭커 전체"
                    ),
                },
                "formation": {"type": "string", "description": "포메이션 (예: 4-2-3-1, 4231). 생략하면 팀컬러 전체"},
                "role": {"type": "string", "description": "역할 (DM, CAM, ST … 또는 볼란치 같은 별칭)"},
                "top_n": {"type": "integer", "description": "몇 명까지 (기본 5, 최대 20)"},
                "strict": {
                    "type": "boolean",
                    "description": "true면 실제 경기 배치가 해당 포메이션과 일치한 스쿼드만 집계",
                },
                "max_price_bp": {
                    "type": "integer",
                    "description": "카드 1장 최대 가격(BP). 랭커들이 가장 많이 쓴 강화 단계의 시세로 비교",
                },
                "max_salary": {"type": "integer", "description": "카드 1장 최대 급여 (예: '급여 29 미만' = 28)"},
                "sort": {
                    "type": "string",
                    "enum": ["usage", "price", "salary"],
                    "description": (
                        "usage(기본): 사용률 높은 순. price·salary: 가성비 — 사용률이 min_usage_rate 이상인 선수 중 "
                        "시세·급여 낮은 순 (미수집 카드는 제외)"
                    ),
                },
                "min_usage_rate": {
                    "type": "number",
                    "description": f"sort=price일 때 최소 사용률 (기본 {DEFAULT_MIN_USAGE_FOR_PRICE_SORT})",
                },
                "traits": {
                    "type": "string",
                    "description": "신규특성(금특·신특) 조건, 쉼표로 (예: '라인 브레이커,트릭스터', 줄임말 '라브,트릭'). "
                    f"가능: {', '.join(NEW_TRAITS)}",
                },
                "can_add_trait": {
                    "type": "boolean",
                    "description": f"'달 수 있는'이면 true: {ADD_TRAIT_GRADE}강(금카) 이상은 신규특성을 하나 더 달 수 있으므로, "
                    f"하나가 모자란 카드도 {ADD_TRAIT_GRADE}강 이상 기준(시세도 그 강화)으로 넣는다. "
                    "'달린'(원래 가진 카드만)이면 false(기본)",
                },
            },
            ["role"],
        ),
    },
    {
        "name": "recommend_squad",
        "description": (
            "여러 자리를 한 번에 추천한다: 포메이션 선발 11명, 또는 slots로 준 일부 자리(예: 투볼란치 'DM,DM', 좌우 윙 'RW,LW'). "
            "포지션 구성은 그 포메이션으로 실제 경기한 랭커 스쿼드에서 가져오고, 자리마다 사용률 높은 선수를 (같은 선수 중복 없이) 넣는다. "
            "자리별 대안, 카드 시세·급여, 총액·총 급여를 준다. max_total_price_bp·max_total_salary를 주면 그 한도 안에서 "
            "사용률 합이 가장 큰 조합을 고른다 (자리가 적으면 모든 조합을 비교). '두 명 급여 합 54 미만' = slots 'DM,DM', max_total_salary 53. "
            f"선발 11명은 게임 급여 상한 {SALARY_CAP}을 항상 지킨다 (max_total_salary를 안 줘도, 더 크게 줘도 {SALARY_CAP})"
        ),
        "input_schema": _schema(
            {
                "team_color": {"type": "string", "description": "팀컬러. 클럽·국가면 11명 모두 그 소속 카드, 시즌 단일(예: 'WS')이면 그 시즌 카드를 최대한. 생략하면 상위 랭커 전체"},
                "formation": {"type": "string", "description": "포메이션 (예: 4-2-3-1). 생략하면 그 범위에서 가장 많이 쓰인 포메이션"},
                "strict": {"type": "boolean", "description": "true면 실제 배치가 포메이션과 일치한 스쿼드만 집계"},
                "max_total_price_bp": {"type": "integer", "description": "고른 선수들의 총 예산(BP). 예: 50억 = 5000000000"},
                "max_total_salary": {"type": "integer", "description": "고른 선수들의 급여 합 상한 ('미만'이면 1 뺀 값)"},
                "slots": {
                    "type": "string",
                    "description": "일부 자리만 고를 때, 자리마다 역할 하나를 쉼표로 (예: 'DM,DM', 'RW,LW', 'ST,ST'). 생략하면 포메이션 11명",
                },
                "chemistry": {
                    "type": "string",
                    "description": "관계(케미) 팀컬러 이름 (예: '19-20 FC 바르셀로나', '25-26 레알 마드리드'). 주면 그 명단 선수를 "
                    "케미 발동 인원(첫 단계) 이상 넣는다. 클럽·국가 이름이 붙은 케미('21-22 롬바르디아 FC', '리버풀 중원 트리오')만 주고 "
                    "team_color를 생략하면 그 클럽·국가 팀컬러도 지킨다 (나머지 선수도 그 소속). 결과의 chemistry.active로 발동 여부 확인",
                },
                "include": {
                    "type": "string",
                    "description": "꼭 넣을 선수 이름, 쉼표로 (예: '슈케르', '라이스,사카'). 랭커들이 그 선수를 쓴 자리에 넣고 나머지를 한도 안에서 "
                    "다시 고른다. 결과의 include로 들어갔는지 확인",
                },
            },
            [],
        ),
    },
    {
        "name": "get_player_detail",
        "description": (
            "선수 이름(일부 가능)으로 랭커 사용 현황을 조회한다: 역할별 사용률과 그 역할 내 순위, 경기 평점, 시즌 카드별 사용 수·강화·시세, "
            "날짜별 사용률 추이, 어떤 팀컬러·포메이션에서 썼는지. 선수 비교는 같은 team_color·formation으로 선수마다 호출한다."
        ),
        "input_schema": _schema(
            {
                "name": {"type": "string", "description": "선수 이름 (예: 라이스)"},
                "team_color": {"type": "string", "description": "팀컬러로 범위 제한. 생략하면 상위 랭커 전체"},
                "formation": {"type": "string", "description": "포메이션으로 범위 제한. 생략하면 전체 포메이션"},
            },
            ["name"],
        ),
    },
    {
        "name": "query_squads",
        "description": (
            "범용 조회: 다른 도구로 딱 맞지 않는 집계 질문에 쓴다. 최신 스냅샷 랭커들의 기준 경기 선발 명단을 조건으로 거른 뒤 "
            "group_by로 묶어 사용 수·사용률·경기 평점·승률·평균 강화·사용 랭커 평균 ELO·시세를 준다. 예: "
            "평점 높은 볼란치 = role DM, group_by player, sort_by avg_rating / 상위 100위가 쓰는 공미 = rank_max 100, role CAM, group_by player / "
            "라이스와 같이 쓰는 볼란치 = with_player 라이스, role DM, group_by player / 볼란치 강화 분포 = role DM, group_by grade / "
            "4-2-2-1-1 랭커들의 팀컬러 = formation 4-2-2-1-1, group_by team_color / 라이스를 쓰는 포메이션 = with_player 라이스, group_by formation"
        ),
        "input_schema": _schema(
            {
                "group_by": {
                    "type": "string", "enum": list(GROUP_BY),
                    "description": "묶는 기준: player(선수, 시즌 합산), card(시즌 카드), season(시즌), grade(강화), team_color, formation, role",
                },
                "sort_by": {
                    "type": "string", "enum": list(SORT_BY),
                    "description": (
                        "정렬: rankers(기본, 많이 쓰인 순), avg_rating, season_win_rate(랭커 시즌 승률, 승률은 보통 이것), win_rate(기준 경기 1경기), "
                        "avg_grade, avg_elo, avg_price, avg_salary, stat (높은 순), price(낮은 순, player·card만)"
                    ),
                },
                "team_color": {"type": "string", "description": "팀컬러로 랭커 제한. 생략하면 상위 랭커 전체"},
                "formation": {"type": "string", "description": "이 포메이션 랭커만"},
                "role": {"type": "string", "description": "이 역할로 선발 출전한 선수만 센다 (DM, CAM, 볼란치 …)"},
                "with_player": {"type": "string", "description": "이 선수(이름 일부 가능)를 선발로 쓴 랭커만. 그 선수 자신은 player·card 결과에서 빠짐"},
                "rank_min": {"type": "integer", "description": "랭킹 이 순위부터"},
                "rank_max": {"type": "integer", "description": "랭킹 이 순위까지 (예: 상위 100위 = 100)"},
                "elo_min": {"type": "number", "description": "랭킹 점수(ELO) 이상인 랭커만"},
                "strict": {"type": "boolean", "description": "true면 실제 배치가 스냅샷 포메이션과 일치한 스쿼드만"},
                "min_rankers": {
                    "type": "integer",
                    "description": f"사용 랭커가 이보다 적은 항목은 뺀다 (기본: rankers 정렬 1, 그 밖의 정렬 {QUERY_MIN_RANKERS})",
                },
                "limit": {"type": "integer", "description": f"결과 행 수 (기본 10, 최대 {QUERY_MAX_ROWS})"},
                "date": {"type": "string", "description": "YYYY-MM-DD: 그날 스냅샷으로 조회 (생략하면 최신)"},
                "stat": {
                    "type": "string",
                    "description": (
                        "카드 능력치(1강 기준)·신체 값: 행마다 평균(stat)을 내고 sort_by=stat으로 정렬. "
                        "세부 능력치(속력, 가속력, 골 결정력, 짧은 패스, 태클, 몸싸움 … 34개), 요약(스피드·슛·패스·수비·피지컬), 키, 몸무게, 개인기, 약발"
                    ),
                },
                "stat_min": {"type": "number", "description": "stat이 이 값 이상인 카드의 출전만 센다 (예: 속력 120 이상)"},
                "match_stat": {
                    "type": "string", "enum": list(MATCH_STATS),
                    "description": (
                        "경기 기록 값 (sort_by=match_stat): goal 골, assist 도움, shoot 슈팅, effective_shoot 유효 슈팅, pass_try 패스 시도, "
                        "pass_success_rate 패스 성공률, dribble 드리블 거리, dribble_success_rate 드리블 성공률, tackle 태클, block 블록 "
                        "(여기까지 top10000·rankers 모두), intercept 가로채기, defending 수비, aerial_success_rate 공중볼 성공률, "
                        "ball_possession_success_rate 볼 경합 성공률 (rankers만). 비율이 아닌 값은 경기당 평균"
                    ),
                },
                "match_stat_source": {
                    "type": "string", "enum": ["top10000", "rankers"],
                    "description": (
                        "top10000(기본): 그 카드·포지션의 TOP 10,000 랭커 최근 20경기 평균(넥슨 랭커 스탯, 표본 큼). "
                        "rankers: 우리 수집 랭커들의 기준 경기 1경기 기록(표본 작음, 가로채기·공중볼 등 더 많은 항목)"
                    ),
                },
            },
            ["group_by"],
        ),
    },
    {
        "name": "query_rankers",
        "description": (
            "랭킹 페이지 정보만으로 답하는 조회 — 웹에서 받은 랭킹 전체(보통 상위 10,000명) 기준. 랭커를 조건으로 거르고 "
            "팀컬러·포메이션·순위 구간으로 묶어 랭커 수·비율·시즌 승률(승/무/패 합산)·평균 ELO·평균 구단가치를 준다. "
            "선수 정보는 없음(선수는 상위 수백 명 스쿼드 기준인 다른 도구). 예: 10,000명 중 많이 쓰는 팀컬러 = group_by team_color / "
            "승률 높은 포메이션 = group_by formation, sort_by season_win_rate / 1,000~2,000위 포메이션 = rank_min 1000, rank_max 2000 / "
            "순위 구간별 4-2-3-1 비율 = formation 4-2-3-1, group_by rank_band"
        ),
        "input_schema": _schema(
            {
                "group_by": {
                    "type": "string", "enum": list(RANKER_GROUP_BY),
                    "description": "team_color, formation, rank_band(순위 구간, band명 단위)",
                },
                "sort_by": {
                    "type": "string", "enum": list(RANKER_SORT_BY),
                    "description": "rankers(기본), season_win_rate, avg_elo, avg_squad_value (높은 순). rank_band는 기본이 순위 순",
                },
                "team_color": {"type": "string", "description": "이 팀컬러 랭커만"},
                "formation": {"type": "string", "description": "이 포메이션 랭커만"},
                "rank_min": {"type": "integer", "description": "이 순위부터"},
                "rank_max": {"type": "integer", "description": "이 순위까지"},
                "elo_min": {"type": "number", "description": "랭킹 점수 이상"},
                "band": {"type": "integer", "description": "rank_band 구간 크기 (기본 1000)"},
                "min_rankers": {"type": "integer", "description": f"랭커가 이보다 적은 묶음은 뺀다 (기본: rankers 정렬 1, 그 밖의 정렬 {RANKERS_MIN_FOR_SORT})"},
                "limit": {"type": "integer", "description": "결과 행 수 (기본 10, 최대 30)"},
            },
            ["group_by"],
        ),
    },
    {
        "name": "get_formation_overview",
        "description": (
            "포메이션 하나의 종합 정보: 랭킹 상위 10,000명 중 사용 순위·비율·시즌 승률(전체 평균 대비)·평균 ELO·구단가치·최고 순위, "
            "많이 쓰는 팀컬러, 순위 구간별 사용 비율, 그 포메이션을 쓰는 최상위 랭커의 실제 선발 11명, 랭커들의 베스트 11(자리별 최다 사용), "
            "상대 포메이션별 전적(수집된 경기 기준). '4-2-3-1 어때?', '4-2-2-2는 어떤 전술에 강해?' 같은 질문에 쓴다."
        ),
        "input_schema": _schema({"formation": {"type": "string", "description": "포메이션 (예: 4-2-3-1, 4231)"}}, ["formation"]),
    },
    {
        "name": "get_team_color_overview",
        "description": (
            "팀컬러 하나의 종합 정보: 랭킹 상위 10,000명 중 사용 순위·비율·시즌 승률(전체 평균 대비)·평균 ELO·구단가치·최고 순위, "
            "그 팀컬러 랭커들이 많이 쓰는 포메이션, 순위 구간별 비율, 그 팀컬러 최상위 랭커의 실제 선발 11명, 랭커들의 베스트 11(자리별 최다 사용). "
            "'레알 팀컬러 어때?', '아스널 쓰는 랭커들은 누구를 써?' 같은 질문에 쓴다."
        ),
        "input_schema": _schema({"team_color": {"type": "string", "description": "팀컬러 (별칭 가능, 예: 레알, AC밀란)"}}, ["team_color"]),
    },
    {
        "name": "get_team_color_info",
        "description": (
            "관계 팀컬러(특성, 예: 레드데블스 철벽라인)와 스페셜 팀컬러(시즌 클래스, 예: 19 UEFA Champions League)의 설명, "
            "단계별 필요 인원과 능력치 효과, 관계 팀컬러의 적용 선수(데이터센터 기준). 셋 중 하나 이상: "
            "team_color(이름 일부도 가능), player(그 선수가 들어간 관계 팀컬러), effect(그 능력치를 올려 주는 팀컬러, 예: 골 결정력). "
            "클럽·국가 팀컬러는 get_team_color_overview."
        ),
        "input_schema": _schema(
            {
                "team_color": {"type": "string", "description": "관계·스페셜 팀컬러 이름 (일부만 써도 됨)"},
                "player": {"type": "string", "description": "선수 이름 (예: 박지성)"},
                "effect": {"type": "string", "description": "능력치 이름 (예: 골 결정력, 전체 능력치)"},
            },
            [],
        ),
    },
    {
        "name": "get_meta_trends",
        "description": (
            "랭커 메타 동향: 많이 쓰는 팀컬러(전체 랭커일 때)·포메이션 비율, 가장 많이 쓰인 선수, "
            "이전 스냅샷(약 days일 전) 대비 비율·사용률 변화(오른 선수·내린 선수)."
        ),
        "input_schema": _schema(
            {
                "team_color": {"type": "string", "description": "팀컬러로 범위 제한. 생략하면 상위 랭커 전체"},
                "days": {"type": "integer", "description": "며칠 전 스냅샷과 비교할지 (기본 7, 없으면 가장 가까운 이전 스냅샷)"},
            },
            [],
        ),
    },
]


class ToolError(Exception):
    """Invalid tool input; returned to the model as an error result."""


class Toolbox:
    def __init__(
        self,
        conn: sqlite3.Connection,
        catalog: TeamColorCatalog | None = None,
        *,
        min_sample: int = FORMATION_MIN_SAMPLE,
        team_color_info: list[dict[str, Any]] | None = None,
    ):
        self.conn = conn
        self.min_sample = min_sample  # 이보다 표본이 적으면 팀컬러 전체 포메이션으로 폴백
        self.conn.row_factory = sqlite3.Row
        UsageStore(conn)
        self.conn.executescript(MARKET_SCHEMA)  # 시세 미수집이어도 조인이 되도록
        self.catalog = catalog or TeamColorCatalog.load()
        self.team_color_info = teamcolor_info.load() if team_color_info is None else team_color_info
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "resolve_terms": self.resolve_terms,
            "list_available_data": self.list_available_data,
            "list_formations": self.list_formations,
            "recommend_players": self.recommend_players,
            "recommend_squad": self.recommend_squad,
            "get_player_detail": self.get_player_detail,
            "get_meta_trends": self.get_meta_trends,
            "query_squads": self.query_squads,
            "query_rankers": self.query_rankers,
            "get_formation_overview": self.get_formation_overview,
            "get_team_color_overview": self.get_team_color_overview,
            "get_team_color_info": self.get_team_color_info,
        }

    def run(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        """Execute a tool call → (JSON text, is_error)."""
        handler = self._handlers.get(name)
        if handler is None:
            return json.dumps({"error": f"unknown tool {name}"}, ensure_ascii=False), True
        # 모델이 인자 이름에 표기 찌꺼기를 붙여 보낼 때가 있다 ("top_n`", "team_color」 string=", "Role") → 이름 부분만
        known = inspect.signature(handler).parameters
        tool_input = {_arg_name(k, known): v for k, v in tool_input.items()}
        try:
            return json.dumps(handler(**tool_input), ensure_ascii=False), False
        except (ToolError, TypeError, ValueError) as exc:  # 잘못된 인자·조합 → 모델이 고쳐 다시 부른다
            return json.dumps({"error": str(exc)}, ensure_ascii=False), True
        except Exception as exc:  # 예상 못 한 오류(DB 등): 답 전체를 실패시키지 않고 도구 오류로 돌려준다
            log.exception("tool %s(%s) failed", name, tool_input)
            error = f"도구 실행 중 오류({type(exc).__name__}) — 조건을 바꿔 다시 부르거나 이 도구 없이 답할 것"
            return json.dumps({"error": error}, ensure_ascii=False), True

    # --- helpers -----------------------------------------------------------

    def _lookup_team_color(self, value: str | int):
        """Name / id / alias → TeamColor. "대한민국"처럼 클럽·국가에 같은 이름이 있으면
        "(국가)"·"(클럽)" 표기를 따르고, 없으면 실제로 집계된 데이터가 있는 쪽을 고른다."""
        if isinstance(value, int) or str(value).strip().isdigit():
            return self.catalog.resolve(value)
        text = str(value).strip()
        category = None
        if m := re.fullmatch(r"(.+?)\s*\((클럽|국가|특수)\)", text):
            text, category = m.group(1), CATEGORY_CODES[m.group(2)]
        candidates = self.catalog.find(text)
        if not candidates:
            alias = TEAM_COLOR_ALIASES.get("".join(text.split()).casefold())
            candidates = self.catalog.find(alias) if alias else []
        if not candidates:  # 시즌 약칭 → 시즌 단일 팀컬러 ("WS" → "WS (Winning Streak)" → Winning Streak)
            row = self.conn.execute("SELECT class_name FROM meta_season WHERE class_name LIKE ?", (f"{text} (%",)).fetchone()
            candidates = self.catalog.find(row[0].partition(" (")[2].rstrip(")")) if row else []
        if not candidates:
            # "롬바르디아" → "롬바르디아 FC": 이름 일부가 한 팀컬러 이름에만 들어 있으면 그 팀컬러
            key = "".join(text.split()).casefold()
            partial = {t.name for t in self.catalog.entries if len(key) >= 2 and key in "".join(t.name.split()).casefold()}
            if len(partial) == 1:
                candidates = self.catalog.find(partial.pop())
        if category:
            candidates = [t for t in candidates if t.category == category] or candidates
        if len(candidates) > 1:
            squads = {
                tc_id: n
                for tc_id, n in self.conn.execute(
                    f"SELECT team_color_id, MAX(squads) FROM usage_sample WHERE team_color_id IN"
                    f" ({','.join('?' * len(candidates))}) GROUP BY team_color_id",
                    [t.id for t in candidates],
                )
            }
            with_data = [t for t in candidates if squads.get(t.id)]
            if with_data:
                return max(with_data, key=lambda t: squads[t.id])
        return candidates[0] if candidates else None

    def _team_color_candidates(self, value: str) -> list[str]:
        key = "".join(value.split())
        found = [t for t in self.catalog.entries if key and key in "".join(t.name.split())]
        return [f"{t.name}({t.id}, {t.category})" for t in found[:8]]

    def _team_color(self, value: str | int | None):
        if value is None or str(value).strip() == "":
            raise ToolError("team_color가 필요합니다")
        tc = self._lookup_team_color(value)
        if tc is None:
            hint = self._team_color_candidates(str(value))
            raise ToolError(
                f"알 수 없는 팀컬러: {value}" + (f". 후보: {', '.join(hint)}" if hint else "")
                + ". 팀컬러와 무관하게 보려면 team_color를 생략(상위 랭커 전체)"
            )
        return tc

    def _scope(self, value: str | int | None) -> tuple[int, str]:  # noqa: D401
        """(team_color_id, 표시 이름). 비어 있거나 '전체 랭커'(list_available_data가 쓰는 이름) 등이면 상위 랭커 전체(0)."""
        if value is None or "".join(str(value).split()).casefold() in ALL_RANKER_WORDS:
            return ALL_RANKERS, "전체 랭커"
        tc = self._team_color(value)
        return tc.id, self._scope_name(tc.id)

    def _team_color_cards(self, tc: TeamColor) -> Callable[[int], bool] | None:
        """sp_id → belongs to the team color: club = club history (loans count), nation = nationality (card details),
        special = its season(s) (e.g. "Winning Streak" = WS). Cards without details count as outside."""
        key = lambda t: "".join((t or "").split()).casefold()  # noqa: E731
        want = key(tc.name)
        if tc.category == "special":
            seasons = set()
            for season_id, cls in self.conn.execute("SELECT season_id, class_name FROM meta_season"):
                code, _, long = cls.partition(" (")  # "WS (Winning Streak)", "19 TOTY (19 Team Of The Year)"
                if want in (key(code), key(long.rstrip(")"))) or tc.name.upper() in code.upper().split():
                    seasons.add(season_id)
            return lambda sp_id: sp_id // 1_000_000 in seasons
        if tc.category == "club":
            ok = {spid for spid, clubs in self.conn.execute("SELECT spid, clubs FROM card_detail")
                  if any(key(c["club"]) == want for c in json.loads(clubs))}  # fmt: skip
        else:
            try:
                ok = {spid for spid, nation in self.conn.execute("SELECT spid, nation FROM card_detail") if key(nation) == want}
            except sqlite3.OperationalError:  # 국적 칸이 생기기 전의 DB
                ok = set()
        return ok.__contains__

    def _special_need(self, name: str) -> int:
        """Players needed for a special (시즌 단일) team color's top level, from the collected info (else 8)."""
        entry = next((e for e in self.team_color_info if e["type"] == "special" and e["name"] == name), None)
        return max((lv["players"] for lv in entry["levels"]), default=8) if entry else 8

    def _chemistry(self, name: str) -> dict[str, Any]:
        """관계(케미) 팀컬러 이름 → 수집한 정보 (이름이 정확히 같거나, 한 팀컬러 이름에만 들어 있으면)."""
        key = lambda t: "".join(t.split()).casefold()  # noqa: E731
        relation = [e for e in self.team_color_info if e["type"] == "relation"]
        if not relation:
            raise ToolError("관계(케미) 팀컬러 정보를 아직 수집하지 않음 — chemistry 없이 다시 호출")
        want = key(name)
        hits = [e for e in relation if key(e["name"]) == want] or [e for e in relation if want in key(e["name"])]
        if len(hits) != 1:
            names = ", ".join(e["name"] for e in hits[:10])
            raise ToolError(f"케미 팀컬러 '{name}'를 하나로 정할 수 없음" + (f": {names} 중 하나로" if hits else " — get_team_color_info로 이름 확인"))
        return hits[0]

    def _chemistry_club(self, chem: dict[str, Any]):
        """The club or nation a chemistry is named after: the name without its leading season/year, then shorter and
        shorter word prefixes ('21-22 롬바르디아 FC' → 롬바르디아 FC, '리버풀 중원 트리오' → 리버풀, '2002 대한민국' →
        대한민국), or None when no prefix names one ('아버지와 아들')."""
        words = [re.sub(r"의$", "", w) for w in re.sub(r"^(\d{2}-\d{2}|\d{2,4})\s+", "", chem["name"]).split()]  # '바이언의' → '바이언'
        for n in range(len(words), 0, -1):
            tc = self._lookup_team_color(" ".join(words[:n]))
            if tc and tc.category in ("club", "nationality"):
                return tc
        return None

    def team_color_icons(self) -> dict[str, str]:
        """Team color display name → its icon on the ranking pages (path under the Nexon CDN's externalAssets/common):
        club crest, special team color icon, or the nation's flag (saved since team_color_flag was added)."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(ranker_snapshot)")}
        flag_col = "team_color_flag" if "team_color_flag" in cols else "NULL"
        out: dict[str, str] = {}
        for name, crest, boost, flag in self.conn.execute(
            f"SELECT DISTINCT team_color_name, team_color_crest, team_color_boost, {flag_col} FROM ranker_snapshot"
            " WHERE team_color_name IS NOT NULL"
        ):
            found = {t.category: t for t in reversed(self.catalog.find(name))}
            if boost:
                tc, path = found.get("special"), f"teamcolorboost/icon/medium/{boost}.png"
            elif crest:
                tc, path = found.get("club"), f"crests/light/medium/{crest}.png"
            elif flag:  # 엠블럼 없이 국기가 보이면 국가 팀컬러 (crawler/membership.py와 같은 판단)
                tc, path = found.get("nationality") or found.get("club"), f"countries/largeflags/{flag}.png"
            else:
                continue
            if tc:
                out.setdefault(self._scope_name(tc.id), path)
        return out

    def _scope_name(self, team_color_id: int) -> str:
        """Display name that `_scope` resolves back to the same team color."""
        if team_color_id == ALL_RANKERS:
            return "전체 랭커"
        tc = next((t for t in self.catalog.entries if t.id == team_color_id), None)
        if tc is None:
            return str(team_color_id)
        if len({t.category for t in self.catalog.find(tc.name)}) > 1:
            return f"{tc.name}({CATEGORY_LABELS[tc.category]})"
        return tc.name

    def _roles(self, value: str) -> tuple[str, ...]:
        roles = resolve_roles(str(value))
        if roles is None:
            raise ToolError(f"알 수 없는 역할: {value}. 가능한 값: {ROLE_HELP}")
        return roles

    def _season_img(self, season_id: int) -> str | None:
        """Season icon URL from seasonid.json metadata (웹 화면용)."""
        if not hasattr(self, "_season_imgs"):
            try:
                self._season_imgs = dict(self.conn.execute("SELECT season_id, season_img FROM meta_season"))
            except sqlite3.OperationalError:  # 메타데이터 전
                self._season_imgs = {}
        return self._season_imgs.get(season_id) or None

    def _price(self, sp_id: int, grade: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT price, fetched_at FROM card_price_latest WHERE spid = ? AND grade = ?", (sp_id, grade)
        ).fetchone()
        if row is None or row["price"] is None:
            return None
        return {"grade": grade, "price_bp": row["price"], "price": format_bp(row["price"]), "fetched_at": row["fetched_at"]}

    # --- tools -------------------------------------------------------------

    def resolve_terms(self, team_color: str | None = None, role: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if team_color:
            tc = self._lookup_team_color(team_color)
            candidates = [] if tc else self._team_color_candidates(team_color)
            out["team_color"] = {"id": tc.id, "name": tc.name, "category": tc.category} if tc else None
            if candidates:
                out["team_color_candidates"] = candidates
        if role:
            resolved = resolve_roles(role)
            out["role"] = (
                {"code": "+".join(resolved), "roles": list(resolved), "positions": [p for r in resolved for p in ROLES[r]]}
                if resolved else None
            )  # fmt: skip
        return out

    def list_available_data(self) -> dict[str, Any]:
        # 팀컬러별 가장 최근 스냅샷만, 표본 많은 순
        rows = self.conn.execute(
            "SELECT u.data_as_of, u.mode, u.team_color_id, u.formation, u.combo_rankers, u.squads FROM usage_sample u"
            " WHERE u.strict = 0 AND u.data_as_of = (SELECT MAX(data_as_of) FROM usage_sample x"
            "  WHERE x.team_color_id = u.team_color_id AND x.mode = u.mode)"
            " ORDER BY (u.team_color_id = 0) DESC, u.squads DESC LIMIT ?",
            (MAX_LISTED_COMBOS,),
        ).fetchall()
        combos = [
            {
                "team_color": self._scope_name(r["team_color_id"]),
                "formation": "전체" if r["formation"] == ALL_FORMATIONS else r["formation"],
                "rankers": r["combo_rankers"],
                "squads_collected": r["squads"],
                "data_as_of": r["data_as_of"],
                "mode": r["mode"],
            }
            for r in rows
        ]
        out: dict[str, Any] = {"combos": combos, "note": f"표본 많은 순 최대 {MAX_LISTED_COMBOS}개. 없는 조합은 아직 수집되지 않음"}
        latest = latest_unfiltered_snapshot(self.conn)
        if latest:
            # 스쿼드는 전체 랭킹보다 이른 스냅샷에 있을 수 있다 (랭킹만 따로 새로 받은 경우)
            squad_as_of = squad_snapshot(self.conn) or latest[0]
            squads = self.conn.execute(
                "SELECT COUNT(*) FROM ranker_squad_status WHERE data_as_of = ? AND rank <= ? AND status IN ('ok', 'provisional')",
                (squad_as_of, squad_range(self.conn, squad_as_of)),
            ).fetchone()[0]
            out["daily_scope"] = {
                "data_as_of": latest[0], "top_rankers": latest[1],
                "squads_collected": squads, "squad_range": squad_range(self.conn, squad_as_of), "squads_as_of": squad_as_of,
            }  # fmt: skip
        return out

    def list_formations(self, team_color: str | None = None) -> dict[str, Any]:
        tc_id, name = self._scope(team_color)
        if tc_id == ALL_RANKERS:
            latest = latest_unfiltered_snapshot(self.conn)
            if latest is None:
                return {"team_color": name, "formations": [], "note": "필터 없이 수집한 랭킹 스냅샷이 없음"}
            as_of, covered = latest
            rows = self.conn.execute(
                "SELECT formation, COUNT(*) AS rankers FROM ranker_snapshot WHERE data_as_of = ? AND rank <= ?"
                " GROUP BY formation ORDER BY rankers DESC",
                (as_of, covered),
            ).fetchall()
            note = f"랭킹 상위 {covered}명 기준"
        else:
            as_of = self.conn.execute(
                "SELECT MAX(data_as_of) FROM ranker_team_color WHERE team_color_id = ?", (tc_id,)
            ).fetchone()[0]
            if as_of is None:
                return {"team_color": name, "formations": [], "note": "이 팀컬러의 랭킹 스냅샷이 없음"}
            rows = self.conn.execute(
                "SELECT s.formation, COUNT(*) AS rankers FROM ranker_snapshot s"
                " JOIN ranker_team_color m USING (data_as_of, mode, rank)"
                " WHERE m.team_color_id = ? AND s.data_as_of = ? GROUP BY s.formation ORDER BY rankers DESC",
                (tc_id, as_of),
            ).fetchall()
            covered = unfiltered_coverage(self.conn, as_of)
            note = (
                f"랭킹 상위 {covered}명 중 이 팀컬러를 쓰는 랭커"
                if covered
                else "팀컬러×포메이션 조건으로 수집한 랭커만 포함할 수 있음 (전체 분포가 아닐 수 있음)"
            )
        collected = {
            r[0]: r[1]
            for r in self.conn.execute(
                "SELECT formation, squads FROM usage_sample WHERE team_color_id = ? AND data_as_of = ? AND strict = 0",
                (tc_id, as_of),
            )
        }
        total = sum(r["rankers"] for r in rows) or 1
        return {
            "team_color": name,
            "data_as_of": as_of,
            "note": note,
            "formations": [
                {
                    "formation": r["formation"],
                    "rankers": r["rankers"],
                    "share": round(r["rankers"] / total, 4),
                    "squads_collected": collected.get(r["formation"], 0),
                }
                for r in rows
            ],
        }


    def recommend_players(
        self,
        role: str,
        team_color: str | None = None,
        formation: str | None = None,
        top_n: int = 5,
        strict: bool = False,
        max_price_bp: int | None = None,
        max_salary: int | None = None,
        sort: str = "usage",
        min_usage_rate: float | None = None,
        traits: str | None = None,
        can_add_trait: bool = False,
    ) -> dict[str, Any]:
        tc_id, tc_name = self._scope(team_color)
        wanted = parse_traits(traits) if traits else []
        roles = self._roles(role)
        role_label = "+".join(roles)
        formation = normalize_formation(formation)
        top_n = max(1, min(int(top_n or 5), 20))
        # LLM이 숫자를 실수(5.0)로 넘기는 경우가 있어 정수로 맞춘다
        max_price_bp = int(max_price_bp) if max_price_bp is not None else None
        max_salary = int(max_salary) if max_salary is not None else None
        sort = sort or "usage"
        if sort not in ("usage", "price", "salary"):
            raise ToolError(f"sort는 usage, price, salary 중 하나: {sort}")
        min_usage = float(min_usage_rate) if min_usage_rate is not None else DEFAULT_MIN_USAGE_FOR_PRICE_SORT
        need_price = max_price_bp is not None or sort == "price"
        need_salary = max_salary is not None or sort == "salary"
        # 예산·급여 필터와 가격순 정렬은 후보를 넉넉히 받아 거른다
        res = top_players(
            self.conn, tc_id, formation or ALL_FORMATIONS, roles,
            top=100 if (need_price or need_salary or wanted) else top_n, by="pid", strict=bool(strict), min_sample=self.min_sample,
        )  # fmt: skip
        if not res.players:
            out_empty: dict[str, Any] = {
                "team_color": tc_name, "formation": formation, "role": role_label, "players": [],
                "sample_size": res.sample_size, "data_as_of": res.data_as_of,
            }  # fmt: skip
            if res.sample_size:
                out_empty["note"] = (
                    f"표본 {res.sample_size}명의 스쿼드에 이 역할({role_label})로 선발 출전한 선수가 없음."
                    " 표본이 매우 작으면 수집이 덜 된 것 — 사용자에게 그대로 알린다"
                )
            else:
                out_empty["note"] = "이 조합은 스쿼드가 수집·집계되지 않음. 다른 조합을 찾기보다 사용자에게 그대로 알린다"
            return out_empty

        def affordable(c: dict[str, Any]) -> bool:
            price, salary = _price_bp(c), c["salary"]
            if need_price and (price is None or (max_price_bp is not None and price > max_price_bp)):
                return False
            return not need_salary or (salary is not None and (max_salary is None or salary <= max_salary))

        players = []
        for p in res.players:
            entry = self._player_entry(res, roles, p)
            if wanted:
                entry["cards"] = [c for c in entry["cards"] if self._with_traits(c, wanted, bool(can_add_trait))]
                if not entry["cards"]:
                    continue
            if need_price or need_salary:
                entry["cards"] = [c for c in entry["cards"] if affordable(c)]
                if not entry["cards"]:
                    continue
            if sort != "usage" and entry["usage_rate"] < min_usage:
                continue
            players.append(entry)
        if sort == "price":
            players.sort(key=lambda e: (min(_price_bp(c) for c in e["cards"]), -e["rankers"]))
        elif sort == "salary":
            players.sort(key=lambda e: (min(c["salary"] for c in e["cards"]), -e["rankers"]))
        players = players[:top_n]

        out: dict[str, Any] = {
            "team_color": tc_name,
            "requested_formation": formation or "전체",
            "formation_used": "전체" if res.formation == ALL_FORMATIONS else res.formation,
            "fallback_to_all_formations": res.fallback,
            "role": role_label,
            "positions": [p for r in roles for p in ROLES[r]],
            "strict": bool(strict),
            "sort": sort,
            "data_as_of": res.data_as_of,
            "sample_size": res.sample_size,
            "combo_rankers": res.combo_rankers,
            "players": players,
            "definitions": {
                "usage_rate": "표본 랭커 중 이 선수를 이 역할(들)로 선발 기용한 비율",
                "win_rate_of_those_matches": "사용 랭커의 기준 경기 1경기씩의 승률 (표본 작음, 참고용)",
                "avg_season_win_rate_of_users": "사용 랭커들의 이번 시즌 승률(랭킹 페이지 승/무/패) 평균",
                "price_at_most_used_grade": "랭커들이 가장 많이 쓴 강화 단계의 최근 수집 시세 (없으면 미수집)",
                "salary": "카드 급여 (데이터센터, 없으면 미수집)",
            },
        }
        if sort in ("price", "salary"):
            what = "시세" if sort == "price" else "급여"
            out["definitions"]["sort"] = (
                f"가성비: 사용률 {min_usage:.0%} 이상인 선수 중 ({what}가 수집된 카드의) 최저 {what} 낮은 순. {what} 미수집 선수는 빠짐"
            )
        if tc_id == ALL_RANKERS and res.data_as_of:
            out["ranking_scope"] = squad_range(self.conn, res.data_as_of)  # 스쿼드를 수집한 랭킹 범위 (상위 몇 명)
        if max_price_bp is not None or max_salary is not None:
            out["budget"] = {
                "max_price_bp": max_price_bp, "max_price": format_bp(max_price_bp) if max_price_bp is not None else None,
                "max_salary": max_salary,
            }  # fmt: skip
        if wanted:
            out["traits"] = {"wanted": wanted, "can_add_trait": bool(can_add_trait)}
            out["definitions"]["new_traits"] = "카드가 원래 가진 신규특성 (카드 상세 기준, 상세 미수집 카드는 빠짐)"
            if can_add_trait:
                out["definitions"]["added_trait"] = (
                    f"{ADD_TRAIT_GRADE}강 이상에서 새로 달 특성. price_for_traits는 그 강화(가장 많이 쓴 강화와 {ADD_TRAIT_GRADE} 중 큰 쪽)의 시세"
                )
        notes = []
        if need_price and not self._has_prices(res):
            notes.append("이 역할 카드의 시세가 수집되지 않아 예산·가격으로 거를 수 없음")
        if need_salary and not self._has_salaries(res):
            notes.append("이 역할 카드의 급여가 수집되지 않아 급여로 거를 수 없음")
        if notes:
            out["note"] = " / ".join(notes)
        return out

    def _player_entry(self, res, roles: tuple[str, ...], p) -> dict[str, Any]:
        """One player of a `top_players` result with per-card usage, most used grade, its price and salary."""
        return {
            "name": p.name,
            "pid": p.key,
            "rankers": p.ranker_count,
            "usage_rate": p.usage_rate,
            "avg_grade": p.avg_grade,
            "avg_rating_in_match": p.avg_rating,
            "win_rate_of_those_matches": p.win_rate,
            "avg_season_win_rate_of_users": p.avg_ranker_win_rate,
            "avg_elo_of_users": p.avg_elo,
            "cards": self._cards((res.data_as_of, res.team_color_id, res.formation, res.strict), roles, p.seasons),
        }

    def _cards(self, key: tuple[str, int, str, bool], roles: tuple[str, ...], seasons) -> list[dict[str, Any]]:
        """key = (data_as_of, team_color_id, formation, strict) of the usage rows the seasons came from."""
        cards = []
        for s in seasons:
            grades = Counter(self._grade_dist(key, roles, s.sp_id))
            typical_grade = grades.most_common(1)[0][0] if grades else None
            cards.append({
                "sp_id": s.sp_id,
                "season": s.season,
                "season_img": self._season_img(s.season_id),
                "rankers": s.ranker_count,
                "avg_grade": s.avg_grade,
                "most_used_grade": typical_grade,
                "price_at_most_used_grade": self._price(s.sp_id, typical_grade) if typical_grade else None,
                "salary": self._salary(s.sp_id),
            })  # fmt: skip
        return cards

    def _with_traits(self, card: dict[str, Any], wanted: list[str], can_add: bool) -> bool:
        """Keep a card that has the wanted new traits — or, with can_add, lacks one that 8강+ can add (then priced at 8강+)."""
        row = self.conn.execute("SELECT traits FROM card_detail WHERE spid = ?", (card["sp_id"],)).fetchone()
        if row is None:
            return False
        own = [t for t in json.loads(row["traits"]) if t in NEW_TRAITS]
        missing = [t for t in wanted if t not in own]
        if len(missing) > (1 if can_add else 0):
            return False
        card["new_traits"] = own
        if missing:
            grade = max(card["most_used_grade"] or 0, ADD_TRAIT_GRADE)
            card["added_trait"] = missing[0]
            card["price_for_traits"] = self._price(card["sp_id"], grade)
        return True

    def _card_profile(self, sp_id: int) -> dict[str, Any] | None:
        """Card details (1강 기준): 포지션별 능력치·신체·개인기·주발·특성·요약/세부 능력치·클럽 경력."""
        row = self.conn.execute("SELECT * FROM card_detail WHERE spid = ?", (sp_id,)).fetchone()
        if row is None:
            return None
        return {
            "stats_grade": row["grade"], "positions": json.loads(row["positions"]), "height": row["height"],
            "weight": row["weight"], "body_type": row["body_type"], "skill_moves": row["skill_moves"],
            "foot": f"L{row['left_foot']}-R{row['right_foot']}", "reputation": row["reputation"], "birth": row["birth"],
            "traits": json.loads(row["traits"]), "summary": json.loads(row["summary"]), "stats": json.loads(row["stats"]),
            "clubs": json.loads(row["clubs"]),
        }  # fmt: skip

    def _grade_dist(self, key: tuple[str, int, str, bool], roles: tuple[str, ...], sp_id: int) -> dict[int, int]:
        data_as_of, team_color_id, formation, strict = key
        dist: Counter[int] = Counter()
        for (raw,) in self.conn.execute(
            "SELECT grade_dist FROM usage_stats WHERE data_as_of = ? AND mode = '1vs1' AND team_color_id = ?"
            f" AND formation = ? AND strict = ? AND sp_id = ? AND role IN ({','.join('?' * len(roles))})",
            (data_as_of, team_color_id, formation, int(strict), sp_id, *roles),
        ):
            dist.update({int(g): c for g, c in json.loads(raw).items()})
        return dict(dist)

    def _salary(self, sp_id: int) -> int | None:
        row = self.conn.execute("SELECT salary FROM card WHERE spid = ?", (sp_id,)).fetchone()
        return row[0] if row else None

    def _has_prices(self, res) -> bool:
        ids = [s.sp_id for p in res.players for s in p.seasons]
        if not ids:
            return False
        marks = ",".join("?" * len(ids))
        return self.conn.execute(f"SELECT 1 FROM card_price WHERE spid IN ({marks}) LIMIT 1", ids).fetchone() is not None

    def _has_salaries(self, res) -> bool:
        ids = [s.sp_id for p in res.players for s in p.seasons]
        marks = ",".join("?" * len(ids))
        return bool(ids) and self.conn.execute(
            f"SELECT 1 FROM card WHERE spid IN ({marks}) AND salary IS NOT NULL LIMIT 1", ids
        ).fetchone() is not None

    def _most_used_formation(self, team_color_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT formation FROM usage_sample WHERE team_color_id = ? AND strict = 0 AND formation != '*'"
            " AND data_as_of = (SELECT MAX(data_as_of) FROM usage_sample WHERE team_color_id = ?)"
            " ORDER BY squads DESC, formation LIMIT 1",
            (team_color_id, team_color_id),
        ).fetchone()
        return row[0] if row else None

    def _slots(self, text: str) -> dict[str, int]:
        """"DM,DM" / "RW LW" → {"DM": 2} / {"RW": 1, "LW": 1} (자리마다 역할 하나)."""
        layout: Counter[str] = Counter()
        for part in re.split(r"[,+/\s]+", text.strip()):
            if not part:
                continue
            roles = self._roles(part)
            if len(roles) != 1:
                raise ToolError(f"slots는 자리마다 역할 하나씩 (예: 'DM,DM', 'RW,LW'): {part}")
            layout[roles[0]] += 1
        if not layout:
            raise ToolError("slots가 비어 있음 (예: 'DM,DM')")
        order = list(ROLES)
        return dict(sorted(layout.items(), key=lambda kv: order.index(kv[0])))

    def recommend_squad(
        self,
        team_color: str | None = None,
        formation: str | None = None,
        strict: bool = False,
        max_total_price_bp: int | None = None,
        max_total_salary: int | None = None,
        slots: str | None = None,
        chemistry: str | None = None,
        include: str | None = None,
    ) -> dict[str, Any]:
        chem = self._chemistry(chemistry) if chemistry else None
        must = ["".join(n.split()) for n in re.split(r"[,/+]", include or "") if n.strip()]  # 꼭 넣을 선수 (이름 일부, 띄어쓰기 무시)
        derived = self._chemistry_club(chem) if chem and self._scope(team_color)[0] == ALL_RANKERS else None
        if derived:  # "21-22 롬바르디아 FC"·"리버풀 중원 트리오" 케미만 받았으면 그 클럽 팀컬러도 지킨다 (나머지도 그 클럽 경력)
            team_color = derived.id
        tc_id, tc_name = self._scope(team_color)
        tc = next((t for t in self.catalog.entries if t.id == tc_id), None)
        # 팀컬러 규칙: 클럽·국가는 11명 전원 그 소속 카드만, 시즌 단일(스페셜)은 11명을 노리되 발동 인원 이상
        fits = self._team_color_cards(tc) if tc else None
        season_rule = tc is not None and tc.category == "special"
        # 시즌 단일은 그 팀컬러 랭커가 적은 경우가 많아, 후보는 전체 랭커가 쓴 카드에서 그 시즌 카드를 찾는다
        usage_id = ALL_RANKERS if season_rule else tc_id
        members = frozenset(p["pid"] for p in chem["players"]) if chem else frozenset()
        formation = normalize_formation(formation)
        source = "requested"
        layout_squads = None
        if slots:
            layout = self._slots(slots)
            source = "slots"
        else:
            if formation is None:
                formation, source = self._most_used_formation(usage_id), "most_used"
            if formation is None:
                return {"team_color": tc_name, "lineup": [], "note": "이 범위는 스쿼드가 수집·집계되지 않음"}
            layout, layout_squads = role_slots(self.conn, formation)
            if not layout:
                return {
                    "team_color": tc_name, "formation": formation, "lineup": [],
                    "note": f"{formation}으로 실제 경기한 스쿼드가 수집되지 않아 포지션 구성을 알 수 없음",
                }  # fmt: skip
        reqs = {"chem": min(lv["players"] for lv in chem["levels"])} if chem else {}  # 케미: 첫 단계 발동 인원
        reqs.update({f"include:{name}": 1 for name in must})  # 꼭 넣을 선수: 이름이 맞는 선수 한 명씩
        if season_rule:
            reqs["season"] = sum(layout.values())
        auto_cap = not slots and max_total_salary is None
        if not slots:  # 선발 11명은 게임의 팀 급여 상한을 넘을 수 없다
            max_total_salary = min(int(max_total_salary), SALARY_CAP) if max_total_salary is not None else SALARY_CAP
        limits = {
            k: int(v) for k, v in (("price", max_total_price_bp), ("salary", max_total_salary)) if v is not None
        }  # fmt: skip

        # 자리마다 후보 = (선수, 카드). 제약이 없으면 선수당 가장 많이 쓰인 카드, 있으면 그 값이 수집된 모든 카드
        # (사용자가 말하지 않은 자동 급여 상한은 급여 미수집 카드도 후보로 둔다 — 0으로 셈)
        known = [k for k in limits if not (auto_cap and k == "salary")]
        slot_options: list[tuple[str, list[dict[str, Any]]]] = []
        first = None
        for role, n in layout.items():
            top = n + (25 if slots else 8)
            res = top_players(
                self.conn, usage_id, formation or ALL_FORMATIONS, role, top=200 if (chem or fits or must) else top, by="pid",
                strict=bool(strict), min_sample=self.min_sample,
            )  # fmt: skip
            first = first or res
            options = []
            # 상위 후보에 더해, 사용률이 낮아도 그 자리에 쓰인 케미 명단 선수·팀컬러 카드는 모두 후보
            for i, p in enumerate(res.players):
                wanted = {f"include:{name}" for name in must if name in "".join(p.name.split())}
                if not (i < top or p.key in members or wanted or (fits and any(fits(x.sp_id) for x in p.seasons))):
                    continue
                entry = self._player_entry(res, (role,), p)
                cards = entry["cards"]
                if fits and not season_rule:  # 클럽·국가: 그 소속 카드만
                    cards = [c for c in cards if fits(c["sp_id"])]
                    if not cards:
                        continue
                cards = _common_cards(cards, entry["rankers"], keep=lambda c: season_rule and fits(c["sp_id"]))
                if limits:
                    cards = [c for c in cards if all(_cost(c, k) is not None for k in known)] or cards[:1]
                for card in cards if limits else cards[:1]:
                    tags = {t for t, ok in (("chem", p.key in members), ("season", season_rule and fits(card["sp_id"]))) if ok} | wanted
                    options.append({"entry": entry, "card": card, "price": _price_bp(card), "salary": card["salary"], "tags": tags})
            slot_options += [(role, options)] * n
        if first is None or not first.sample_size:
            return {"team_color": tc_name, "formation": formation, "lineup": [], "note": "이 조합은 스쿼드가 수집·집계되지 않음"}

        chosen = _choose(slot_options, limits, reqs)

        def total(kind: str) -> int:
            return sum(c[kind] for c in chosen if c and c[kind] is not None)

        lineup, unpriced, no_salary = [], [], []
        for (role, _), c in zip(slot_options, chosen):
            if c is None:
                lineup.append({"role": role, "player": None, "note": "이 자리에 쓸 후보가 부족함"})
                continue
            e, card = c["entry"], c["card"]
            if c["price"] is None:
                unpriced.append(e["name"])
            if c["salary"] is None:
                no_salary.append(e["name"])
            lineup.append({
                "role": role, "player": e["name"], "pid": e["pid"], "rankers": e["rankers"], "usage_rate": e["usage_rate"],
                "card": {k: card[k] for k in ("sp_id", "season", "season_img", "rankers", "most_used_grade",
                                              "price_at_most_used_grade", "salary")},
            })  # fmt: skip
        picked = {c["entry"]["pid"] for c in chosen if c}
        alternatives: dict[str, list[dict[str, Any]]] = {}
        for role, options in slot_options:
            if role in alternatives:
                continue
            seen: set[int] = set()
            alts = []
            for o in options:
                e = o["entry"]
                if e["pid"] in picked or e["pid"] in seen:
                    continue
                seen.add(e["pid"])
                price = o["card"]["price_at_most_used_grade"]
                alts.append({"player": e["name"], "usage_rate": e["usage_rate"], "season": o["card"]["season"],
                             "price": price["price"] if price else None, "salary": o["salary"]})  # fmt: skip
            alternatives[role] = alts[:3]

        out: dict[str, Any] = {
            "team_color": tc_name,
            "formation": formation,
            "formation_source": source,
            "layout": layout,
            "layout_from_squads": layout_squads,
            "strict": bool(strict),
            "data_as_of": first.data_as_of,
            "sample_size": first.sample_size,
            "fallback_to_all_formations": first.fallback,
            "lineup": lineup,
            "alternatives": alternatives,
            "total_price_bp": total("price"),
            "total_price": format_bp(total("price")),
            "total_salary": total("salary"),
            "definitions": {
                "usage_rate": "표본 랭커 중 이 선수를 이 역할로 선발 기용한 비율",
                "price_at_most_used_grade": "랭커들이 그 카드를 가장 많이 쓴 강화 단계의 최근 수집 시세",
                "layout": "slots로 준 자리, 아니면 그 포메이션으로 실제 경기한 랭커 스쿼드에서 가장 흔한 역할 구성",
                "total_price·total_salary": "시세·급여가 수집된 선수만 더한 값",
            },
        }
        notes = []
        if unpriced:
            out["unpriced_players"] = unpriced
            notes.append(f"시세 미수집 {len(unpriced)}명은 총액에서 빠짐")
        if no_salary:
            out["no_salary_players"] = no_salary
            notes.append(f"급여 미수집 {len(no_salary)}명은 총 급여에서 빠짐")
        if usage_id == ALL_RANKERS and first.data_as_of:
            out["ranking_scope"] = squad_range(self.conn, first.data_as_of)
        if fits and not season_rule:
            empty = sum(1 for c in chosen if c is None)
            who = "고른 선수 모두" if slots else "11명 모두"  # slots면 그 자리들만
            out["team_color_rule"] = f"{tc_name}: {who} 그 {'클럽 경력(임대 포함)' if tc.category == 'club' else '국적'} 카드"
            if empty:
                notes.append(f"{tc_name} 소속 카드 중 랭커들이 그 자리에 쓴 선수가 없어 {empty}자리를 비움")
        if season_rule:
            count = sum(1 for c in chosen if c and "season" in c["tags"])
            least = self._special_need(tc.name)
            out["season_rule"] = {"team_color": tc_name, "players": count, "target": reqs["season"], "need": least,
                                  "active": count >= least}  # fmt: skip
            if count < reqs["season"]:
                notes.append(f"{tc_name} 카드로 {count}명 (전원은 못 채움 — 발동 인원 {least}명 {'충족' if count >= least else '미달'})")
        if must:
            got = {name: next((c["entry"]["name"] for c in chosen if c and f"include:{name}" in c["tags"]), None) for name in must}
            out["include"] = got
            for name, player in got.items():
                if player is None:
                    notes.append(f"'{name}'를 넣지 못함 (이 팀컬러·포메이션 랭커들이 쓴 기록이 없거나, 소속·급여·예산 조건에 맞는 카드가 없음)")
        if chem:
            need = reqs["chem"]
            if derived:
                out["team_color_from_chemistry"] = f"{chem['name']} 케미라 {tc_name} 팀컬러도 지킴 (11명 모두 그 소속)"
            inside = [c["entry"]["name"] for c in chosen if c and "chem" in c["tags"]]
            out["chemistry"] = {"team_color": chem["name"], "need": need, "players_in_lineup": inside,
                                "active": len(inside) >= need, "levels": chem["levels"]}  # fmt: skip
            if len(inside) < need:
                notes.append(f"케미 발동 인원 {need}명 중 {len(inside)}명만 넣음 (명단 선수 중 이 범위 랭커들이 그 자리에 쓴 선수가 부족하거나 급여·예산 한도 때문)")
        if limits:
            within = all(total(k) <= v for k, v in limits.items())
            out["budget"] = {
                "max_total_price_bp": limits.get("price"),
                "max_total_price": format_bp(limits["price"]) if "price" in limits else None,
                "max_total_salary": limits.get("salary"),
                "salary_cap": None if slots else SALARY_CAP,
                "within_budget": within,
            }
            if not within:
                notes.append("후보를 가장 싸게 바꿔도 예산·급여 한도를 넘음")
        if notes:
            out["note"] = " / ".join(notes)
        return out

    def get_player_detail(self, name: str, team_color: str | None = None, formation: str | None = None) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ToolError("name이 필요합니다")
        formation = normalize_formation(formation)
        tc_id, tc_name = self._scope(team_color)
        like = f"%{name.replace(' ', '')}%"
        filters, args = "", [like]
        if team_color:
            filters += " AND u.team_color_id = ?"
            args.append(tc_id)
        if formation:
            filters += " AND u.formation = ?"
            args.append(formation)
        rows = self.conn.execute(
            # 팀컬러마다 최신 집계만. 최신 시각은 한 번만 구한다 (행마다 하위 쿼리면 10,000명 규모에서 2분 넘게 걸림)
            "WITH latest AS (SELECT team_color_id, MAX(data_as_of) AS as_of FROM usage_stats GROUP BY team_color_id)"
            " SELECT u.team_color_id, u.formation, u.role, u.sp_id, u.pid, u.ranker_count, u.sample_size,"
            " u.usage_rate, u.avg_grade, u.data_as_of, sp.name, ss.class_name"
            " FROM usage_stats u JOIN latest l ON l.team_color_id = u.team_color_id AND u.data_as_of = l.as_of"
            " JOIN meta_spid sp ON sp.sp_id = u.sp_id"
            " LEFT JOIN meta_season ss ON ss.season_id = u.season_id"
            f" WHERE u.strict = 0 AND u.formation != '*' AND REPLACE(sp.name, ' ', '') LIKE ?{filters}"
            " ORDER BY u.ranker_count DESC LIMIT 40",
            args,
        ).fetchall()
        players = self._player_summaries(like, tc_id, tc_name, formation)
        if not rows and not players:
            return {"name": name, "usage": [], "note": "수집된 랭커 스쿼드에서 이 이름의 선수를 찾지 못함"}
        names = sorted({r["name"] for r in rows} | {p["name"] for p in players})
        usage = []
        for r in rows:
            usage.append({
                "player": r["name"],
                "team_color": self._scope_name(r["team_color_id"]),
                "formation": r["formation"],
                "role": r["role"],
                "season": r["class_name"],
                "sp_id": r["sp_id"],
                "rankers": r["ranker_count"],
                "sample_size": r["sample_size"],
                "usage_rate": r["usage_rate"],
                "avg_grade": r["avg_grade"],
                "data_as_of": r["data_as_of"],
            })  # fmt: skip
        sp_ids = sorted({r["sp_id"] for r in rows})
        prices = [
            {"sp_id": p["spid"], "grade": p["grade"], "price_bp": p["price"], "price": format_bp(p["price"])}
            for p in self.conn.execute(
                f"SELECT spid, grade, price FROM card_price_latest WHERE spid IN ({','.join('?' * len(sp_ids))})"
                " AND price IS NOT NULL ORDER BY spid, grade",
                sp_ids,
            )
        ]
        out: dict[str, Any] = {"matched_names": names, "players": players, "usage": usage, "prices": prices}
        if players:
            out["definitions"] = {
                "roles": "범위 안 우리 랭커들의 사용 현황 (rank_in_role = 그 역할에서 사용 랭커 수 순위)",
                "top10000_stats": "이 선수 카드들을 그 역할 포지션으로 쓴 TOP 10,000 랭커 최근 20경기의 경기당 평균 (matches = 합친 기록의 경기 수, 카드·포지션마다 최근 20경기. 비율은 성공/시도)",
                "card_profiles": "많이 쓰인 카드의 능력치·신체·특성 (1강 기준)",
            }
        if len(names) > 1:
            out["note"] = "이름이 여러 선수와 일치함 — 어느 선수인지 사용자에게 확인 필요할 수 있음"
        return out

    def _player_summaries(self, like: str, tc_id: int, tc_name: str, formation: str | None) -> list[dict[str, Any]]:
        """Per matched player: roles in the scope (usage, rank in role, cards with prices) and usage by snapshot."""
        fm = formation or ALL_FORMATIONS
        pids = self.conn.execute(
            "SELECT u.pid, MIN(sp.name) FROM usage_stats u JOIN meta_spid sp ON sp.sp_id = u.sp_id"
            " WHERE REPLACE(sp.name, ' ', '') LIKE ? AND u.team_color_id = ? AND u.formation = ? AND u.strict = 0"
            " GROUP BY u.pid ORDER BY SUM(u.ranker_count) DESC LIMIT 5",
            (like, tc_id, fm),
        ).fetchall()
        out = []
        for pid, player_name in pids:
            as_of, sample, roles = player_roles(self.conn, tc_id, fm, pid)
            if not roles:
                continue
            history = usage_history(self.conn, tc_id, fm, pid)
            key = (as_of, tc_id, fm, False)
            out.append({
                "name": player_name,
                "pid": pid,
                "scope": {"team_color": tc_name, "formation": formation or "전체", "sample_size": sample, "data_as_of": as_of},
                "roles": [
                    {
                        "role": r.role,
                        "rank_in_role": r.rank,
                        "players_in_role": r.players_in_role,
                        "rankers": r.usage.ranker_count,
                        "usage_rate": r.usage.usage_rate,
                        "avg_rating_in_match": r.usage.avg_rating,
                        "win_rate_of_those_matches": r.usage.win_rate,
                        "avg_grade": r.usage.avg_grade,
                        "top10000_stats": ranker_stats_summary(
                            self.conn, {s.sp_id for s in r.usage.seasons}, set(ROLES[r.role])
                        ),
                        "cards": self._cards(key, (r.role,), r.usage.seasons),
                    }
                    for r in roles
                ],
                "usage_history": history if len(history) > 1 else None,
                "card_profiles": self._card_profiles(roles),
            })  # fmt: skip
        return out

    def _card_profiles(self, roles) -> list[dict[str, Any]]:
        """Full details of the player's most used cards (up to 3) in the scope."""
        used: Counter[int] = Counter()
        seasons: dict[int, str | None] = {}
        for r in roles:
            for s in r.usage.seasons:
                used[s.sp_id] += s.ranker_count
                seasons[s.sp_id] = s.season
        out = []
        for sp_id, _ in used.most_common(3):
            if profile := self._card_profile(sp_id):
                out.append({"sp_id": sp_id, "season": seasons[sp_id], **profile})
        return out

    # --- meta trends -------------------------------------------------------

    def _unfiltered_snapshots(self) -> list[tuple[str, int]]:
        """(data_as_of, covered ranks) of every snapshot with an unfiltered crawl, newest first."""
        out = []
        for (as_of,) in self.conn.execute(
            "SELECT DISTINCT data_as_of FROM crawl_run WHERE mode = '1vs1' AND status = 'ok' AND data_as_of IS NOT NULL"
            " ORDER BY data_as_of DESC"
        ):
            if covered := unfiltered_coverage(self.conn, as_of):
                out.append((as_of, covered))
        return out

    def _formation_counts(self, tc_id: int, as_of: str, covered: int) -> Counter[str]:
        member = (
            "" if tc_id == ALL_RANKERS else
            " AND EXISTS (SELECT 1 FROM ranker_team_color m WHERE m.data_as_of = s.data_as_of AND m.mode = s.mode"
            " AND m.rank = s.rank AND m.team_color_id = :tc)"
        )  # fmt: skip
        return Counter(dict(self.conn.execute(
            "SELECT s.formation, COUNT(*) FROM ranker_snapshot s WHERE s.data_as_of = :as_of AND s.mode = '1vs1'"
            f" AND s.rank <= :covered AND s.formation IS NOT NULL{member} GROUP BY s.formation",
            {"as_of": as_of, "covered": covered, "tc": tc_id},
        ).fetchall()))  # fmt: skip

    def _team_color_counts(self, as_of: str, covered: int) -> Counter[int]:
        return Counter(dict(self.conn.execute(
            "SELECT team_color_id, COUNT(DISTINCT rank) FROM ranker_team_color WHERE data_as_of = ? AND mode = '1vs1'"
            " AND rank <= ? GROUP BY team_color_id",
            (as_of, covered),
        ).fetchall()))  # fmt: skip

    def _player_usage(self, tc_id: int, as_of: str) -> tuple[int, dict[int, dict[str, Any]]]:
        """(표본, pid → 이름·사용 랭커 수·주 역할) from the all-formation stats of one snapshot."""
        sample = self.conn.execute(
            "SELECT squads FROM usage_sample WHERE data_as_of = ? AND mode = '1vs1' AND team_color_id = ?"
            " AND formation = '*' AND strict = 0",
            (as_of, tc_id),
        ).fetchone()
        if sample is None:
            return 0, {}
        players: dict[int, dict[str, Any]] = {}
        by_role: dict[int, Counter[str]] = defaultdict(Counter)
        for pid, role, n, name in self.conn.execute(
            "SELECT u.pid, u.role, SUM(u.ranker_count), MIN(sp.name) FROM usage_stats u"
            " LEFT JOIN meta_spid sp ON sp.sp_id = u.sp_id WHERE u.data_as_of = ? AND u.mode = '1vs1'"
            " AND u.team_color_id = ? AND u.formation = '*' AND u.strict = 0 GROUP BY u.pid, u.role",
            (as_of, tc_id),
        ):
            p = players.setdefault(pid, {"name": name, "rankers": 0})
            p["rankers"] += n
            by_role[pid][role] += n
        for pid, p in players.items():
            p["main_role"] = by_role[pid].most_common(1)[0][0]
            p["usage_rate"] = round(p["rankers"] / sample[0], 4)
        return sample[0], players

    def get_meta_trends(self, team_color: str | None = None, days: int = 7) -> dict[str, Any]:
        tc_id, tc_name = self._scope(team_color)
        snaps = self._unfiltered_snapshots()
        if not snaps:
            return {"scope": tc_name, "note": "필터 없이 수집한 랭킹 스냅샷이 없음 (매일 수집을 먼저 실행)"}
        as_of, covered = snaps[0]
        target = datetime.fromisoformat(as_of) - timedelta(days=int(days or 7))
        older = snaps[1:]
        prev = next((s for s in older if datetime.fromisoformat(s[0]) <= target), older[-1] if older else None)

        def shares(now: Counter, before: Counter | None, now_total: int, before_total: int, top: int) -> list[tuple[Any, dict]]:
            rows = []
            for key, n in now.most_common(top):
                row = {"rankers": n, "share": round(n / now_total, 4)}
                if before is not None:
                    row["share_change"] = round(n / now_total - before.get(key, 0) / before_total, 4)
                rows.append((key, row))
            return rows

        out: dict[str, Any] = {
            "scope": tc_name, "data_as_of": as_of, "ranking_scope": covered,
            "compared_with": prev[0] if prev else None,
        }  # fmt: skip
        if tc_id == ALL_RANKERS:
            now_tc = self._team_color_counts(as_of, covered)
            before_tc = self._team_color_counts(*prev) if prev else None
            cats = {t.id: CATEGORY_LABELS.get(t.category, t.category) for t in self.catalog.entries}
            out["team_colors"] = [
                {"team_color": self._scope_name(tc), "category": cats.get(tc), **r}
                for tc, r in shares(now_tc, before_tc, covered, prev[1] if prev else 1, 10)
            ]
        now_f = self._formation_counts(tc_id, as_of, covered)
        before_f = self._formation_counts(tc_id, *prev) if prev else None
        total_now = sum(now_f.values()) or 1
        total_before = (sum(before_f.values()) or 1) if before_f is not None else 1
        out["formations"] = [{"formation": f, **r} for f, r in shares(now_f, before_f, total_now, total_before, 8)]

        sample, now_p = self._player_usage(tc_id, as_of)
        out["player_sample_size"] = sample
        out["top_players"] = [
            {"player": p["name"], "main_role": p["main_role"], "rankers": p["rankers"], "usage_rate": p["usage_rate"]}
            for p in sorted(now_p.values(), key=lambda p: -p["rankers"])[:10]
        ]
        prev_sample, before_p = self._player_usage(tc_id, prev[0]) if prev else (0, {})
        # 이전 표본이 지금의 절반도 안 되면(예: 그날 스쿼드 1명만 집계) 비교하면 모든 선수가 "급상승"으로 보인다
        comparable = prev_sample and prev_sample * 2 >= sample
        if comparable:
            out["player_compared_sample_size"] = prev_sample
            changes = []
            for pid in now_p.keys() | before_p.keys():
                a, b = now_p.get(pid), before_p.get(pid)
                if max((a or {}).get("rankers", 0), (b or {}).get("rankers", 0)) < TREND_MIN_RANKERS:
                    continue
                cur = a["usage_rate"] if a else 0.0
                old = b["usage_rate"] if b else 0.0
                changes.append({
                    "player": (a or b)["name"], "main_role": (a or b)["main_role"],
                    "usage_rate": cur, "previous_usage_rate": old, "change": round(cur - old, 4),
                })  # fmt: skip
            out["rising"] = sorted((c for c in changes if c["change"] > 0), key=lambda c: -c["change"])[:5]
            out["falling"] = sorted((c for c in changes if c["change"] < 0), key=lambda c: c["change"])[:5]
        notes = []
        if prev is None:
            notes.append("비교할 이전 스냅샷이 없어 변화량은 없음 (매일 수집이 쌓이면 생김)")
        elif not prev_sample:
            notes.append("이전 스냅샷은 스쿼드가 집계되지 않아 선수 사용률 변화는 없음")
        elif not comparable:
            notes.append(f"이전 스냅샷은 스쿼드가 {prev_sample}명만 집계돼(지금 {sample}명) 선수 사용률 변화는 비교하지 않음")
        notes.append(f"share_change·change는 이전 스냅샷 대비 차이(%p). 선수 변화는 사용 랭커 {TREND_MIN_RANKERS}명 이상만")
        out["note"] = " / ".join(notes)
        return out

    # --- 범용 조회 -----------------------------------------------------------

    def _resolve_player(self, name: str) -> tuple[int, str, list[str]]:
        """이름(일부) → (가장 많이 선발로 쓰인 pid, 이름, 같이 일치한 다른 선수 이름들)."""
        rows = self.conn.execute(
            "SELECT p.pid, MIN(sp.name), COUNT(*) FROM match_player p JOIN meta_spid sp ON sp.sp_id = p.sp_id"
            " WHERE p.starter = 1 AND REPLACE(sp.name, ' ', '') LIKE ? GROUP BY p.pid ORDER BY 3 DESC LIMIT 5",
            (f"%{name.replace(' ', '')}%",),
        ).fetchall()
        if not rows:
            raise ToolError(f"수집된 랭커 스쿼드에서 '{name}' 선수를 찾지 못함")
        return rows[0][0], rows[0][1], [r[1] for r in rows[1:]]

    def _lookup(self, table: str, key: str, value: str, id_: int) -> str | None:
        row = self.conn.execute(f"SELECT {value} FROM {table} WHERE {key} = ?", (id_,)).fetchone()
        return row[0] if row else None

    def query_squads(
        self,
        group_by: str,
        sort_by: str = "rankers",
        team_color: str | None = None,
        formation: str | None = None,
        role: str | None = None,
        with_player: str | None = None,
        rank_min: int | None = None,
        rank_max: int | None = None,
        elo_min: float | None = None,
        strict: bool = False,
        min_rankers: int | None = None,
        limit: int = 10,
        date: str | None = None,
        stat: str | None = None,
        stat_min: float | None = None,
        match_stat: str | None = None,
        match_stat_source: str | None = None,
    ) -> dict[str, Any]:
        sort_by = sort_by or "rankers"
        if group_by not in GROUP_BY:
            raise ToolError(f"group_by는 {', '.join(GROUP_BY)} 중 하나: {group_by}")
        if sort_by not in SORT_BY:
            raise ToolError(f"sort_by는 {', '.join(SORT_BY)} 중 하나: {sort_by}")
        if sort_by == "price" and group_by not in ("player", "card"):
            raise ToolError("sort_by=price는 group_by가 player 또는 card일 때만 쓸 수 있음")
        tc_id, tc_name = self._scope(team_color)
        role_codes = self._roles(role) if role else None
        formation = normalize_formation(formation)
        as_of = squad_snapshot(self.conn, date)
        if as_of is None:
            raise ToolError(f"{date}에 수집된 스쿼드가 없음" if date else "수집된 스쿼드가 없음")
        pid, player_name, other_names = self._resolve_player(with_player) if with_player else (None, None, [])
        rank_min = int(rank_min) if rank_min is not None else None
        rank_max = int(rank_max) if rank_max is not None else None
        min_rankers = int(min_rankers) if min_rankers is not None else (1 if sort_by == "rankers" else QUERY_MIN_RANKERS)
        limit = max(1, min(int(limit or 10), QUERY_MAX_ROWS))
        stat_name = None
        if stat:
            stat_name = resolve_stat(stat)
            if stat_name is None:
                raise ToolError(f"알 수 없는 능력치: {stat}. 가능한 값: {', '.join(STAT_NAMES)}")
        if (sort_by == "stat" or stat_min is not None) and stat_name is None:
            raise ToolError("sort_by=stat이나 stat_min을 쓰려면 stat(능력치 이름)이 필요함")
        source = match_stat_source or "top10000"
        if sort_by == "match_stat" and not match_stat:
            raise ToolError("sort_by=match_stat을 쓰려면 match_stat이 필요함")
        if match_stat and match_stat not in MATCH_STATS:
            raise ToolError(f"알 수 없는 match_stat: {match_stat}. 가능한 값: {', '.join(MATCH_STATS)}")
        if match_stat and source == "top10000" and match_stat not in TOP10000_STATS:
            raise ToolError(f"{match_stat}은 top10000에 없음 — match_stat_source='rankers'로 (기준 경기 1경기 기록)")
        query = SquadQuery(
            team_color_id=tc_id, formation=formation, rank_min=rank_min, rank_max=rank_max,
            elo_min=float(elo_min) if elo_min is not None else None, strict=bool(strict), with_pid=pid,
            roles=role_codes, data_as_of=as_of, stat=stat_name, stat_min=float(stat_min) if stat_min is not None else None,
            match_stat=match_stat, match_stat_source=source,
        )  # fmt: skip
        res = aggregate_squads(self.conn, query, group_by, sort_by=sort_by, min_rankers=min_rankers, limit=limit)

        parts = [
            (f"스쿼드 수집 랭커(랭킹 상위 {squad_range(self.conn, as_of)}명)" if res["covered"] else "수집된 전체 랭커")
            if tc_id == ALL_RANKERS else f"{tc_name} 랭커"
        ]
        if formation:
            parts.append(f"포메이션 {formation}")
        if rank_min or rank_max:
            parts.append(f"순위 {rank_min or 1}~{rank_max or ''}위")
        if elo_min is not None:
            parts.append(f"ELO {elo_min:g} 이상")
        if strict:
            parts.append("배치 일치 스쿼드만")
        if player_name:
            parts.append(f"{player_name}을(를) 선발로 쓴 랭커만")
        if role_codes:
            parts.append(f"{'+'.join(role_codes)} 자리만")
        if stat_min is not None:
            parts.append(f"{stat_name} {stat_min:g} 이상 카드만")

        rows = [self._query_row(group_by, r) for r in res["rows"]]
        notes = []
        if not res["squads"]:
            notes.append("조건에 맞는 스쿼드가 없음 — 조건을 완화하거나 사용자에게 그대로 알린다")
        elif res["squads"] < 30:
            notes.append(f"표본 {res['squads']}개로 작아 참고용")
        if res["total_groups"] > len(rows) and min_rankers > 1:
            notes.append(f"사용 랭커 {min_rankers}명 미만 항목은 뺌 (전체 {res['total_groups']}개 항목)")
        if player_name and group_by in ("player", "card"):
            notes.append(f"기준 선수({player_name}) 자신은 결과에서 뺌")
        if group_by == "team_color":
            notes.append("랭커 한 명이 여러 팀컬러(클럽·국가)에 속할 수 있어 비율 합이 100%를 넘을 수 있음")
        if other_names:
            notes.append(f"'{with_player}'와 일치하는 다른 선수: {', '.join(other_names)} (가장 많이 쓰인 {player_name}로 조회)")
        out: dict[str, Any] = {
            "scope": {"description": " · ".join(parts), "data_as_of": as_of},
            "squads": res["squads"],
            "group_by": group_by,
            "sort_by": sort_by,
            "min_rankers": min_rankers,
            "total_groups": res["total_groups"],
            "rows": rows,
            "definitions": {
                "squads": "조건에 맞는 랭커 스쿼드 수 = usage_rate의 분모" + (" (그 선수를 쓴 랭커 수)" if player_name else ""),
                "rankers": "그 항목이 선발(역할 조건이 있으면 그 자리)에 있는 스쿼드 수",
                "avg_rating·avg_grade": "해당 선발 출전들의 경기 평점·강화 평균",
                "season_win_rate": "그 랭커들의 이번 시즌 전적(랭킹 페이지 승/무/패)을 합친 승률, season_games = 경기 수 합",
                "win_rate": "그 랭커들의 기준 경기 1경기씩의 승률 (참고용, 승률 질문에는 season_win_rate)",
                "avg_price·avg_salary": "해당 선발 출전들의 카드 시세(그 강화)·급여 평균. coverage = 값이 수집된 비율",
                "stat": "해당 선발 출전 카드들의 그 능력치 평균 (1강 기준, 강화 보너스 미포함). stat_coverage = 상세가 수집된 비율",
                "match_stat": (
                    "top10000: 그 카드·포지션을 쓴 TOP 10,000 랭커 최근 20경기의 경기당 평균(비율은 성공/시도), match_stat_matches = 합친 기록의 경기 수(카드·포지션마다 최근 20경기). "
                    "rankers: 우리 랭커들의 기준 경기 1경기 기록 평균, match_stat_matches = 기록이 있는 출전 수. coverage = 값이 있는 출전 비율"
                ),
                "avg_elo": "그 랭커들의 평균 랭킹 점수",
                "price": "가장 많이 쓰인 카드의 가장 많이 쓰인 강화 단계 최근 수집 시세 (없으면 미수집)",
            },
        }
        if notes:
            out["note"] = " / ".join(notes)
        return out

    def query_rankers(
        self,
        group_by: str,
        sort_by: str = "rankers",
        team_color: str | None = None,
        formation: str | None = None,
        rank_min: int | None = None,
        rank_max: int | None = None,
        elo_min: float | None = None,
        band: int = 1000,
        min_rankers: int | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        sort_by = sort_by or "rankers"
        if group_by not in RANKER_GROUP_BY:
            raise ToolError(f"group_by는 {', '.join(RANKER_GROUP_BY)} 중 하나: {group_by}")
        if sort_by not in RANKER_SORT_BY:
            raise ToolError(f"sort_by는 {', '.join(RANKER_SORT_BY)} 중 하나: {sort_by}")
        tc_id, tc_name = self._scope(team_color)
        formation = normalize_formation(formation)
        band = max(int(band or 1000), 1)
        min_rankers = int(min_rankers) if min_rankers is not None else (1 if sort_by == "rankers" else RANKERS_MIN_FOR_SORT)
        res = query_rankers(
            self.conn, group_by, team_color_id=tc_id, formation=formation,
            rank_min=int(rank_min) if rank_min is not None else None, rank_max=int(rank_max) if rank_max is not None else None,
            elo_min=float(elo_min) if elo_min is not None else None, band=band, sort_by=sort_by, min_rankers=min_rankers,
            limit=max(1, min(int(limit or 10), QUERY_MAX_ROWS)),
        )  # fmt: skip
        if res["data_as_of"] is None:
            return {"rows": [], "note": "필터 없이 수집한 랭킹이 없음 (매일 수집을 먼저 실행)"}
        parts = [f"랭킹 상위 {res['covered']}명"]
        if tc_id != ALL_RANKERS:
            parts.append(f"{tc_name} 랭커")
        if formation:
            parts.append(f"포메이션 {formation}")
        if rank_min or rank_max:
            parts.append(f"순위 {rank_min or 1}~{rank_max or res['covered']}위")
        if elo_min is not None:
            parts.append(f"ELO {elo_min:g} 이상")
        rows = []
        for r in res["rows"]:
            key = r["key"]
            label = {
                "team_color": lambda: {"team_color": self._scope_name(key)},
                "formation": lambda: {"formation": key},
                "rank_band": lambda: {"rank_band": f"{key}~{key + band - 1}위"},
            }[group_by]()
            value = r["avg_squad_value"]
            rows.append({**label, **{k: r[k] for k in ("rankers", "share", "season_win_rate", "season_games", "avg_elo")},
                         "avg_squad_value": format_bp(int(value)) if value is not None else None})  # fmt: skip
            if group_by == "rank_band":  # 그 구간(순위 범위로 잘린 만큼) 랭커 중 조건에 맞는 비율
                last = min(key + band - 1, res["covered"], int(rank_max) if rank_max else res["covered"])
                rows[-1]["share_of_band"] = round(r["rankers"] / max(last - max(key, int(rank_min or 1)) + 1, 1), 4)
        notes = []
        if res["total_groups"] > len(rows) and min_rankers > 1:
            notes.append(f"랭커 {min_rankers}명 미만 묶음은 뺌 (전체 {res['total_groups']}개)")
        if group_by == "team_color" or tc_id != ALL_RANKERS:
            notes.append("팀컬러는 랭킹 화면에 표시된 팀컬러·엠블럼으로 추정한 소속 (특수 팀컬러가 표시되면 원래 클럽을 엠블럼으로 추정)")
        return {
            "scope": {"description": " · ".join(parts), "data_as_of": res["data_as_of"]},
            "rankers": res["rankers"],
            "groups": res["total_groups"],
            "group_by": group_by,
            "sort_by": sort_by,
            "rows": rows,
            "definitions": {
                "rankers·share": "조건에 맞는 랭커 중 그 묶음의 랭커 수와 비율",
                **({"share_of_band": "그 순위 구간 랭커 전체 중 조건(포메이션·팀컬러 등)에 맞는 랭커 비율 — '구간별로 몇 %가 쓰나'는 이 값"}
                   if group_by == "rank_band" else {}),
                "season_win_rate": "그 랭커들의 이번 시즌 전적(승/무/패)을 합친 승률, season_games = 경기 수 합",
                "avg_squad_value": "랭킹 화면의 구단가치 평균",
                "note": "선수·스쿼드 정보는 없음 — 선수 질문은 스쿼드 기반 도구(상위 수백 명)",
            },
            **({"note": " / ".join(notes)} if notes else {}),
        }

    def get_formation_overview(self, formation: str) -> dict[str, Any]:
        name = normalize_formation(formation)
        if not name:
            raise ToolError("formation이 필요합니다 (예: 4-2-3-1)")
        everyone = query_rankers(self.conn, "formation", limit=1000)
        if everyone["data_as_of"] is None:
            return {"formation": name, "note": "필터 없이 수집한 랭킹이 없음"}
        ranked = everyone["rows"]  # 랭커 많은 순
        row = next((r for r in ranked if r["key"] == name), None)
        wins_games = [(r["season_win_rate"] or 0) * r["season_games"] for r in ranked]
        total_games = sum(r["season_games"] for r in ranked)
        out: dict[str, Any] = {
            "formation": name,
            "data_as_of": everyone["data_as_of"],
            "ranking_scope": everyone["covered"],
            "all_formations_win_rate": round(sum(wins_games) / total_games, 4) if total_games else None,
        }
        if row is None:
            out["note"] = f"랭킹 상위 {everyone['covered']}명 중 {name}을 쓰는 랭커가 없음"
        else:
            best = self.conn.execute(
                "SELECT MIN(rank) FROM ranker_snapshot WHERE data_as_of = ? AND mode = '1vs1' AND formation = ?",
                (everyone["data_as_of"], name),
            ).fetchone()[0]
            value = row["avg_squad_value"]
            out["ranking"] = {
                "usage_rank": ranked.index(row) + 1, "formations": len(ranked), "rankers": row["rankers"], "share": row["share"],
                "season_win_rate": row["season_win_rate"], "season_games": row["season_games"], "avg_elo": row["avg_elo"],
                "avg_squad_value": format_bp(int(value)) if value is not None else None, "best_rank": best,
            }  # fmt: skip
            teams = query_rankers(self.conn, "team_color", formation=name, limit=5)
            out["team_colors"] = [
                {"team_color": self._scope_name(r["key"]), "rankers": r["rankers"], "share": r["share"]} for r in teams["rows"]
            ]
            out["rank_bands"] = self._rank_bands(everyone["covered"], formation=name)
        out["top_ranker_squad"] = top_ranker_squad(self.conn, name)
        out["best_eleven"] = best_eleven(self.conn, name)
        out["matchups"] = formation_matchups(self.conn, name)
        out["definitions"] = {
            "ranking": "랭킹 상위 10,000명(웹) 기준. season_win_rate = 그 랭커들의 시즌 승/무/패 합산, usage_rank = 사용 랭커 수 순위",
            "top_ranker_squad": "스쿼드 수집 랭커 중 이 포메이션을 쓰는 가장 높은 순위 랭커의 스냅샷 직전 경기 선발 (닉네임 제외, squad_value = 선발 최신 시세 합, unpriced = 시세 몰라 뺀 선수 수)",
            "best_eleven": "이 포메이션으로 실제 경기한 스쿼드들의 가장 흔한 배치에서 자리마다 가장 많이 쓰인 선수 (rankers = 그 자리 사용 수)",
            "matchups": "수집된 공식경기 중 양쪽 선발을 아는 경기의 상대 포메이션별 결과 (포메이션은 배치로 추론, 표본 작으면 참고용)",
        }
        return out

    def get_team_color_overview(self, team_color: str) -> dict[str, Any]:
        tc_id, tc_name = self._scope(team_color)
        if tc_id == ALL_RANKERS:
            raise ToolError("team_color가 필요합니다 (예: 레알 마드리드, 아스널)")
        everyone = query_rankers(self.conn, "team_color", limit=1000)
        if everyone["data_as_of"] is None:
            return {"team_color": tc_name, "note": "필터 없이 수집한 랭킹이 없음"}
        ranked = everyone["rows"]  # 랭커 많은 순 (랭커 한 명이 팀컬러 여럿일 수 있음)
        row = next((r for r in ranked if r["key"] == tc_id), None)
        overall = query_rankers(self.conn, "rank_band", band=everyone["covered"], limit=1)["rows"]  # 랭커 전체 한 묶음
        out: dict[str, Any] = {
            "team_color": tc_name,
            "data_as_of": everyone["data_as_of"],
            "ranking_scope": everyone["covered"],
            "all_rankers_win_rate": overall[0]["season_win_rate"] if overall else None,
        }
        if row is None:
            out["note"] = f"랭킹 상위 {everyone['covered']}명 중 {tc_name} 팀컬러 랭커가 없음"
        else:
            best = self.conn.execute(
                "SELECT MIN(rank) FROM ranker_team_color WHERE data_as_of = ? AND mode = '1vs1' AND team_color_id = ?",
                (everyone["data_as_of"], tc_id),
            ).fetchone()[0]
            value = row["avg_squad_value"]
            out["ranking"] = {
                "usage_rank": ranked.index(row) + 1, "team_colors": len(ranked), "rankers": row["rankers"], "share": row["share"],
                "season_win_rate": row["season_win_rate"], "season_games": row["season_games"], "avg_elo": row["avg_elo"],
                "avg_squad_value": format_bp(int(value)) if value is not None else None, "best_rank": best,
            }  # fmt: skip
            forms = query_rankers(self.conn, "formation", team_color_id=tc_id, limit=5)
            out["formations"] = [{"formation": r["key"], "rankers": r["rankers"], "share": r["share"]} for r in forms["rows"]]
            out["rank_bands"] = self._rank_bands(everyone["covered"], team_color_id=tc_id)
        out["top_ranker_squad"] = top_ranker_squad(self.conn, team_color_id=tc_id)
        out["best_eleven"] = best_eleven(self.conn, team_color_id=tc_id)
        out["definitions"] = {
            "ranking": "랭킹 상위 10,000명(웹) 기준. season_win_rate = 그 팀컬러 랭커들의 시즌 승/무/패 합산, usage_rank = 사용 랭커 수 순위",
            "formations": "그 팀컬러 랭커들이 랭킹 화면에서 쓰는 포메이션",
            "top_ranker_squad": "스쿼드 수집 랭커 중 이 팀컬러의 가장 높은 순위 랭커의 스냅샷 직전 경기 선발 (닉네임 제외, squad_value = 선발 최신 시세 합, unpriced = 시세 몰라 뺀 선수 수)",
            "best_eleven": "이 팀컬러 랭커들의 스쿼드(포메이션 무관)에서 가장 흔한 배치의 자리마다 가장 많이 쓰인 선수",
        }
        return out

    def _rank_bands(self, covered: int, **scope: Any) -> list[dict[str, Any]]:
        """Share of each 2,000-rank band (formation or team_color_id scope)."""
        band = 2000
        rows = query_rankers(self.conn, "rank_band", band=band, limit=30, **scope)["rows"]
        return [
            {"rank_band": f"{r['key']}~{min(r['key'] + band - 1, covered)}위", "rankers": r["rankers"],
             "share_of_band": round(r["rankers"] / (min(r["key"] + band - 1, covered) - r["key"] + 1), 4)}
            for r in rows
        ]  # fmt: skip

    def _query_row(self, group_by: str, r: dict[str, Any]) -> dict[str, Any]:
        key = r["key"]
        if group_by == "player":
            label = {"player": self._lookup("meta_spid", "sp_id", "name", r["top_sp_id"]), "pid": key}
        elif group_by == "card":
            label = {"player": self._lookup("meta_spid", "sp_id", "name", key), "sp_id": key}
        elif group_by == "season":
            label = {"season": self._lookup("meta_season", "season_id", "class_name", key) or str(key)}
        elif group_by == "team_color":
            label = {"team_color": self._scope_name(key)}
        else:
            label = {group_by: key}
        metrics = {
            k: r[k] for k in ("rankers", "usage_rate", "avg_rating", "season_win_rate", "season_games", "win_rate",
                              "avg_grade", "avg_elo", "avg_salary", "salary_coverage", "price_coverage")
        }  # fmt: skip
        # 평균은 만 단위로 ("23억 821만 2,756"처럼 원 단위까지 쓰지 않게)
        avg_price = r["avg_price"]
        if avg_price is not None:
            avg_price = int(round(avg_price, -4) if avg_price >= 100_000 else round(avg_price))
        metrics["avg_price"] = format_bp(avg_price) if avg_price is not None else None
        if "stat" in r:
            metrics["stat"], metrics["stat_coverage"] = r["stat"], r["stat_coverage"]
        if "match_stat" in r:
            for k in ("match_stat", "match_stat_coverage", "match_stat_matches"):
                metrics[k] = r[k]
        if group_by in ("player", "card"):
            season_id = r["top_sp_id"] // 1_000_000
            metrics["most_used_card"] = {
                "season": self._lookup("meta_season", "season_id", "class_name", season_id),
                "grade": r["top_grade"],
                "price": format_bp(r["price_bp"]) if r["price_bp"] is not None else None,
                "price_bp": r["price_bp"],
                "salary": r["salary"],
            }
        return {**label, **metrics}

    def get_team_color_info(self, team_color: str = "", player: str = "", effect: str = "") -> dict[str, Any]:
        if not self.team_color_info:
            return {"note": "관계·스페셜 팀컬러 정보를 아직 수집하지 않음"}
        if not (team_color or player or effect).strip():
            raise ToolError("team_color, player, effect 중 하나가 필요합니다")
        def key(text: str) -> str:
            return "".join(text.split()).casefold()

        entries = self.team_color_info
        if team_color:
            want = key(team_color)
            exact = [e for e in entries if key(e["name"]) == want]
            entries = exact or [e for e in entries if want in key(e["name"])]
        if player:
            want = key(player)
            entries = [e for e in entries if any(key(p["name"]) == want for p in e.get("players") or [])]
        if effect:
            want = key(effect)
            entries = [e for e in entries if any(want in key(x) for lv in e["levels"] for x in lv["effects"])]
        base = {"source": "FC온라인 데이터센터 팀컬러 페이지 (관계·스페셜)", "matches": len(entries)}
        if not entries:
            hint = None
            if team_color and self.catalog.resolve(TEAM_COLOR_ALIASES.get(key(team_color), team_color)):
                hint = "클럽·국가 팀컬러입니다 → get_team_color_overview"
            return {**base, "team_colors": [], "hint": hint}
        if len(entries) > MAX_TEAM_COLOR_INFO:  # 많으면 이름만
            return {**base, "names": [e["name"] for e in entries[:MAX_TEAM_COLOR_NAMES]],
                    "note": "많아서 이름만 보여 줌. 하나를 골라 team_color로 다시 조회"}  # fmt: skip
        return {**base, "team_colors": [
            {"name": e["name"], "type": teamcolor_info.TYPES[e["type"]], "description": e["description"], "levels": e["levels"],
             **({"players": [p["name"] for p in e["players"]], "players_complete": e["players_complete"]} if e["type"] == "relation"
                else {"players": "해당 시즌 클래스 카드 전부"})}
            for e in entries
        ]}  # fmt: skip

    # --- evidence ----------------------------------------------------------

    def evidence(self, name: str, result: dict[str, Any]) -> str | None:
        """One line naming the data a tool result came from (scope, sample, as-of), for the answer footer."""
        as_of = format_as_of(result.get("data_as_of")) if result.get("data_as_of") else None
        if name == "recommend_players" and result.get("sample_size"):
            fm = result.get("formation_used") or "전체"
            return f"{result['team_color']} {fm if fm != '전체' else '전체 포메이션'} {result['role']} — 랭커 {result['sample_size']}명 스쿼드 ({as_of})"
        if name == "recommend_squad" and result.get("lineup"):
            return f"{result['team_color']} {result['formation']} 스쿼드 — 랭커 {result['sample_size']}명 스쿼드 ({as_of})"
        if name == "get_player_detail" and result.get("players"):
            s = result["players"][0]["scope"]
            who = ", ".join(p["name"] for p in result["players"])
            fm = s["formation"] if s["formation"] != "전체" else "전체 포메이션"
            return f"{who} — {s['team_color']} {fm} 랭커 {s['sample_size']}명 스쿼드 ({format_as_of(s['data_as_of'])})"
        if name == "get_meta_trends" and result.get("data_as_of"):
            vs = f", {format_as_of(result['compared_with'])}과 비교" if result.get("compared_with") else ""
            return f"{result['scope']} 메타 — 랭킹 상위 {result['ranking_scope']}명 ({as_of}{vs})"
        if name == "query_squads" and result.get("squads"):
            return f"{result['scope']['description']} — 스쿼드 {result['squads']}개 ({format_as_of(result['scope']['data_as_of'])})"
        if name == "query_rankers" and result.get("rankers"):
            return f"{result['scope']['description']} — 랭커 {result['rankers']}명, 랭킹 페이지 기준 ({format_as_of(result['scope']['data_as_of'])})"
        if name == "get_formation_overview" and result.get("ranking"):
            return f"{result['formation']} — 랭킹 상위 {result['ranking_scope']}명 + 수집 경기 {result['matchups']['games']}경기 ({format_as_of(result['data_as_of'])})"
        if name == "list_formations" and result.get("formations"):
            return f"{result['team_color']} 포메이션 분포 ({as_of})"
        return None
