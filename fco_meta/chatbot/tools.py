"""Tools the chatbot calls (규칙 기반·LLM 공통). Every number in an answer must come from one of these."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from typing import Any

from ..analytics import ALL_FORMATIONS, ALL_RANKERS, MIN_SAMPLE, UsageStore, top_players
from ..crawler.teamcolors import TeamColorCatalog
from ..market.money import format_bp
from ..market.roles import ROLES, resolve_role
from ..market.storage import SCHEMA as MARKET_SCHEMA
from ..storage import latest_unfiltered_snapshot, unfiltered_coverage

# 흔한 줄임말·다른 표기 → 팀컬러 목록의 이름
TEAM_COLOR_ALIASES = {
    "아스날": "아스널",
    "맨유": "맨체스터 유나이티드",
    "맨시티": "맨체스터 시티",
    "레알": "레알 마드리드",
    "레알마드리드": "레알 마드리드",
    "바르사": "FC 바르셀로나",
    "바르셀로나": "FC 바르셀로나",
    "토트넘": "토트넘 홋스퍼",
    "바이에른": "바이에른 뮌헨",
    "뮌헨": "바이에른 뮌헨",
    "psg": "파리 생제르맹",
    "파리": "파리 생제르맹",
    "도르트문트": "보루시아 도르트문트",
    "아틀레티코": "아틀레티코 마드리드",
    "뉴캐슬": "뉴캐슬 유나이티드",
    "나폴리": "SSC 나폴리",
}

MAX_LISTED_COMBOS = 40
CATEGORY_LABELS = {"club": "클럽", "nationality": "국가", "special": "특수"}
CATEGORY_CODES = {v: k for k, v in CATEGORY_LABELS.items()}
# team_color에 이 값들이 오면 팀컬러 무관 상위 랭커 전체 (공백·대소문자 무시)
ALL_RANKER_WORDS = {"", "0", "전체", "전체랭커", "랭커전체", "상위랭커", "상위랭커전체", "all", "allrankers", "*", "none", "null"}

ROLE_HELP = "DM(볼란치: RDM/CDM/LDM), CAM(공미), CM, RM, LM, RW, LW, ST, CF, CB, RB, LB, RWB, LWB, GK"


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Plain JSON Schema (LLM 제공자와 무관). 선택 항목은 required에서 뺀다."""
    return {"type": "object", "properties": properties, "required": required}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "resolve_terms",
        "description": (
            "사용자가 쓴 팀컬러·역할 표현을 표준 값으로 바꾼다. 예: '아스날' → 아스널(1004), '볼란치' → DM. "
            "다른 도구에 넘기기 전에 이름이 애매하면 먼저 호출한다."
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
            "팀컬러×포메이션 랭커들이 특정 역할에 선발로 기용한 선수 순위(사용률). 선수별 시즌 카드 내역, 강화 분포, "
            "사용 랭커 평균 ELO, 시세(수집된 경우)를 함께 준다. 표본이 적으면 팀컬러 전체 포메이션으로 자동 폴백하고 표시한다. "
            f"role: {ROLE_HELP}. 예산이 주어지면 max_price_bp로 걸러낸다."
        ),
        "input_schema": _schema(
            {
                "team_color": {
                    "type": "string",
                    "description": (
                        "팀컬러 이름 또는 id (예: 아스널). 사용자가 팀컬러를 말하지 않았으면 생략하거나 '전체 랭커' → 상위 랭커 전체"
                    ),
                },
                "formation": {"type": "string", "description": "포메이션 (예: 4-2-3-1). 생략하면 팀컬러 전체"},
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
            },
            ["role"],
        ),
    },
    {
        "name": "get_player_detail",
        "description": (
            "선수 이름(일부 가능)으로 랭커 사용 현황을 조회한다: 어떤 팀컬러·포메이션·역할에서 몇 명이 썼는지, "
            "시즌 카드별 사용 수와 강화, 수집된 시세."
        ),
        "input_schema": _schema({"name": {"type": "string", "description": "선수 이름 (예: 라이스)"}}, ["name"]),
    },
]


class ToolError(Exception):
    """Invalid tool input; returned to the model as an error result."""


class Toolbox:
    def __init__(
        self, conn: sqlite3.Connection, catalog: TeamColorCatalog | None = None, *, min_sample: int = MIN_SAMPLE
    ):
        self.conn = conn
        self.min_sample = min_sample  # 이보다 표본이 적으면 팀컬러 전체 포메이션으로 폴백
        self.conn.row_factory = sqlite3.Row
        UsageStore(conn)
        self.conn.executescript(MARKET_SCHEMA)  # 시세 미수집이어도 조인이 되도록
        self.catalog = catalog or TeamColorCatalog.load()
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "resolve_terms": self.resolve_terms,
            "list_available_data": self.list_available_data,
            "list_formations": self.list_formations,
            "recommend_players": self.recommend_players,
            "get_player_detail": self.get_player_detail,
        }

    def run(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        """Execute a tool call → (JSON text, is_error)."""
        handler = self._handlers.get(name)
        if handler is None:
            return json.dumps({"error": f"unknown tool {name}"}, ensure_ascii=False), True
        try:
            return json.dumps(handler(**tool_input), ensure_ascii=False), False
        except (ToolError, TypeError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False), True

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

    def _role(self, value: str) -> str:
        role = resolve_role(value)
        if role is None:
            raise ToolError(f"알 수 없는 역할: {value}. 가능한 값: {ROLE_HELP}")
        return role

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
            resolved = resolve_role(role)
            out["role"] = {"code": resolved, "positions": list(ROLES[resolved])} if resolved else None
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
            squads = self.conn.execute(
                "SELECT COUNT(*) FROM ranker_squad_status WHERE data_as_of = ? AND rank <= ? AND status IN ('ok', 'provisional')",
                latest,
            ).fetchone()[0]
            out["daily_scope"] = {"data_as_of": latest[0], "top_rankers": latest[1], "squads_collected": squads}
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
    ) -> dict[str, Any]:
        tc_id, tc_name = self._scope(team_color)
        role_code = self._role(role)
        top_n = max(1, min(int(top_n or 5), 20))
        # LLM이 숫자를 실수(5.0)로 넘기는 경우가 있어 정수로 맞춘다
        max_price_bp = int(max_price_bp) if max_price_bp is not None else None
        # 예산 필터는 후보를 넉넉히 받아 거른다
        res = top_players(
            self.conn, tc_id, formation or ALL_FORMATIONS, role_code,
            top=100 if max_price_bp else top_n, by="pid", strict=bool(strict), min_sample=self.min_sample,
        )  # fmt: skip
        if not res.players:
            out_empty: dict[str, Any] = {
                "team_color": tc_name, "formation": formation, "role": role_code, "players": [],
                "sample_size": res.sample_size, "data_as_of": res.data_as_of,
            }  # fmt: skip
            if res.sample_size:
                out_empty["note"] = (
                    f"표본 {res.sample_size}명의 스쿼드에 이 역할({role_code})로 선발 출전한 선수가 없음."
                    " 표본이 매우 작으면 수집이 덜 된 것 — 사용자에게 그대로 알린다"
                )
            else:
                out_empty["note"] = "이 조합은 스쿼드가 수집·집계되지 않음. 다른 조합을 찾기보다 사용자에게 그대로 알린다"
            return out_empty

        players = []
        for p in res.players:
            cards = []
            for s in p.seasons:
                grades = Counter(self._grade_dist(res, role_code, s.sp_id))
                typical_grade = grades.most_common(1)[0][0] if grades else None
                cards.append({
                    "sp_id": s.sp_id,
                    "season": s.season,
                    "rankers": s.ranker_count,
                    "avg_grade": s.avg_grade,
                    "most_used_grade": typical_grade,
                    "price_at_most_used_grade": self._price(s.sp_id, typical_grade) if typical_grade else None,
                })  # fmt: skip
            if max_price_bp is not None:
                affordable = [
                    c for c in cards
                    if c["price_at_most_used_grade"] and c["price_at_most_used_grade"]["price_bp"] <= max_price_bp
                ]  # fmt: skip
                if not affordable:
                    continue
                cards = affordable
            players.append({
                "name": p.name,
                "pid": p.key,
                "rankers": p.ranker_count,
                "usage_rate": p.usage_rate,
                "avg_grade": p.avg_grade,
                "avg_rating_in_match": p.avg_rating,
                "win_rate_of_those_matches": p.win_rate,
                "avg_elo_of_users": p.avg_elo,
                "cards": cards,
            })  # fmt: skip
            if len(players) >= top_n:
                break

        out: dict[str, Any] = {
            "team_color": tc_name,
            "requested_formation": formation or "전체",
            "formation_used": "전체" if res.formation == ALL_FORMATIONS else res.formation,
            "fallback_to_all_formations": res.fallback,
            "role": role_code,
            "positions": list(ROLES[role_code]),
            "strict": bool(strict),
            "data_as_of": res.data_as_of,
            "sample_size": res.sample_size,
            "combo_rankers": res.combo_rankers,
            "players": players,
            "definitions": {
                "usage_rate": "표본 랭커 중 이 선수를 이 역할로 선발 기용한 비율",
                "win_rate_of_those_matches": "사용 랭커의 기준 경기 1경기씩의 승률 (표본 작음, 참고용)",
                "price_at_most_used_grade": "랭커들이 가장 많이 쓴 강화 단계의 최근 수집 시세 (없으면 미수집)",
            },
        }
        if tc_id == ALL_RANKERS and res.data_as_of:
            out["ranking_scope"] = unfiltered_coverage(self.conn, res.data_as_of)  # 랭킹 상위 몇 명 범위인지
        if max_price_bp is not None:
            out["budget"] = {"max_price_bp": max_price_bp, "max_price": format_bp(max_price_bp)}
            if not self._has_prices(res):
                out["note"] = "이 역할 카드의 시세가 수집되지 않아 예산으로 거를 수 없음"
        return out

    def _grade_dist(self, res, role: str, sp_id: int) -> dict[int, int]:
        row = self.conn.execute(
            "SELECT grade_dist FROM usage_stats WHERE data_as_of = ? AND mode = '1vs1' AND team_color_id = ?"
            " AND formation = ? AND strict = ? AND role = ? AND sp_id = ?",
            (res.data_as_of, res.team_color_id, res.formation, int(res.strict), role, sp_id),
        ).fetchone()
        return {int(g): c for g, c in json.loads(row["grade_dist"]).items()} if row else {}

    def _has_prices(self, res) -> bool:
        ids = [s.sp_id for p in res.players for s in p.seasons]
        if not ids:
            return False
        marks = ",".join("?" * len(ids))
        return self.conn.execute(f"SELECT 1 FROM card_price WHERE spid IN ({marks}) LIMIT 1", ids).fetchone() is not None

    def get_player_detail(self, name: str) -> dict[str, Any]:
        name = name.strip()
        if not name:
            raise ToolError("name이 필요합니다")
        rows = self.conn.execute(
            "SELECT u.team_color_id, u.formation, u.role, u.sp_id, u.pid, u.ranker_count, u.sample_size,"
            " u.usage_rate, u.avg_grade, u.data_as_of, sp.name, ss.class_name"
            " FROM usage_stats u JOIN meta_spid sp ON sp.sp_id = u.sp_id"
            " LEFT JOIN meta_season ss ON ss.season_id = u.season_id"
            " WHERE u.strict = 0 AND u.formation != '*' AND REPLACE(sp.name, ' ', '') LIKE ?"
            " AND u.data_as_of = (SELECT MAX(data_as_of) FROM usage_stats WHERE team_color_id = u.team_color_id)"
            " ORDER BY u.ranker_count DESC LIMIT 40",
            (f"%{name.replace(' ', '')}%",),
        ).fetchall()
        if not rows:
            return {"name": name, "usage": [], "note": "수집된 랭커 스쿼드에서 이 이름의 선수를 찾지 못함"}
        names = sorted({r["name"] for r in rows})
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
        prices = [
            {"sp_id": p["spid"], "grade": p["grade"], "price_bp": p["price"], "price": format_bp(p["price"])}
            for p in self.conn.execute(
                "SELECT spid, grade, price FROM card_price_latest WHERE spid IN"
                f" ({','.join('?' * len({r['sp_id'] for r in rows}))}) AND price IS NOT NULL ORDER BY spid, grade",
                sorted({r["sp_id"] for r in rows}),
            )
        ]
        out: dict[str, Any] = {"matched_names": names, "usage": usage, "prices": prices}
        if len(names) > 1:
            out["note"] = "이름이 여러 선수와 일치함 — 어느 선수인지 사용자에게 확인 필요할 수 있음"
        return out
