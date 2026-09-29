"""Rule-based mode: parse a Korean question, call one tool, format the answer. No LLM, no API key."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..market.money import format_bp, parse_bp
from ..market.roles import ROLE_ALIASES, ROLE_GROUP_ALIASES, ROLES, resolve_role
from .tools import TEAM_COLOR_ALIASES, Toolbox
from .tools import format_as_of as _as_of

ROLE_LABELS = {
    "DM": "볼란치(DM)", "CAM": "공미(CAM)", "CM": "중앙 미드필더(CM)", "ST": "스트라이커(ST)", "CF": "CF",
    "CB": "센터백(CB)", "RB": "RB", "LB": "LB", "RWB": "RWB", "LWB": "LWB", "RM": "RM", "LM": "LM",
    "RW": "RW", "LW": "LW", "GK": "골키퍼(GK)",
}  # fmt: skip

_FORMATION_RE = re.compile(r"(?<![\d-])([345](?:-\d){2,4}(?:\(2\))?)(?![\d-])")
_COUNT_RE = re.compile(r"(\d{1,2})\s*(?:명|개|장|인)")
_MONEY_RE = re.compile(r"(?:\d[\d,.]*(?:조|억|만))+(?:\d[\d,.]*)?")
_ROLE_CODE_RE = re.compile(r"(?<![A-Z])(" + "|".join(sorted({*ROLES, "CDM", "RDM", "LDM"}, key=len, reverse=True)) + r")(?![A-Z])")
_STRICT_WORDS = ("엄격", "strict", "실제배치", "배치일치")
_DATA_WORDS = ("데이터", "가능한", "어떤조합", "목록", "뭐있", "무엇이있")
_SQUAD_WORDS = ("스쿼드", "라인업", "베스트11", "베스트일레븐", "선발11")
_META_WORDS = ("메타", "동향", "트렌드", "요즘", "유행", "인기")


def _norm(text: str) -> str:
    return "".join(text.split()).casefold()


@dataclass
class Query:
    team_color: str | None = None
    formation: str | None = None
    role: str | None = None
    top_n: int | None = None
    max_price_bp: int | None = None
    strict: bool = False
    player: str | None = None


@dataclass
class Answer:
    text: str
    tool: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)


class RuleBot:
    def __init__(self, toolbox: Toolbox):
        self.toolbox = toolbox
        catalog = toolbox.catalog
        # 긴 이름부터 찾아야 "레알 마드리드"가 "레알"보다 먼저 잡힌다
        names = {_norm(t.name): t.name for t in catalog.entries if len(_norm(t.name)) >= 2}
        names.update({_norm(k): v for k, v in TEAM_COLOR_ALIASES.items()})
        self._team_colors = sorted(names.items(), key=lambda kv: -len(kv[0]))
        roles = [(_norm(k), v) for k, v in ROLE_ALIASES.items() if not k.isascii()]
        roles += [(_norm(k), "+".join(v)) for k, v in ROLE_GROUP_ALIASES.items() if not k.isascii()]
        self._roles = sorted(roles, key=lambda kv: -len(kv[0]))
        # 전체 이름과 이름을 이루는 단어("데클런 라이스" → "라이스")로 찾는다. 검색은 get_player_detail이 부분 일치로 처리
        players: dict[str, str] = {}
        for (name,) in self._player_names(toolbox.conn):
            for part in [name, *name.split()]:
                if len(_norm(part)) >= 2:
                    players.setdefault(_norm(part), part)
        self._players = sorted(players.items(), key=lambda kv: -len(kv[0]))

    @staticmethod
    def _player_names(conn: sqlite3.Connection) -> list[tuple[str]]:
        return conn.execute(
            "SELECT DISTINCT sp.name FROM usage_stats u JOIN meta_spid sp ON sp.sp_id = u.sp_id"
        ).fetchall()

    # --- parsing -----------------------------------------------------------

    def parse(self, text: str) -> Query:
        q = Query()
        norm = _norm(text)
        q.team_color = next((name for key, name in self._team_colors if key in norm), None)
        if m := _FORMATION_RE.search(text.replace(" ", "")):
            q.formation = m.group(1)
        q.role = next((code for key, code in self._roles if key in norm), None)
        if q.role is None and (m := _ROLE_CODE_RE.search(text.upper())):
            q.role = resolve_role(m.group(1))
        if m := _COUNT_RE.search(text):
            q.top_n = int(m.group(1))
        if m := _MONEY_RE.search(norm):
            try:
                q.max_price_bp = parse_bp(m.group(0))
            except ValueError:
                pass
        q.strict = any(w in norm for w in _STRICT_WORDS)
        q.player = next((name for key, name in self._players if key in norm), None)
        return q

    # --- answering ---------------------------------------------------------

    def ask(self, text: str) -> Answer:
        q = self.parse(text)
        norm = _norm(text)
        if q.role is None and any(w in norm for w in _SQUAD_WORDS):
            tool_input = {
                "team_color": q.team_color, "formation": q.formation, "strict": q.strict, "max_total_price_bp": q.max_price_bp,
            }  # fmt: skip
            return Answer(self._format_squad(self._call("recommend_squad", tool_input)), "recommend_squad", tool_input)
        if q.role:
            # 팀컬러를 말하지 않으면 매일 수집하는 상위 랭커 전체 기준
            wanted = q.top_n or 5
            tool_input = {
                "team_color": q.team_color, "formation": q.formation, "role": q.role,
                "top_n": min(wanted + 2, 20), "strict": q.strict, "max_price_bp": q.max_price_bp,
            }  # fmt: skip
            return Answer(self._format_recommend(self._call("recommend_players", tool_input), wanted), "recommend_players", tool_input)
        if q.player:
            tool_input = {"name": q.player}
            return Answer(self._format_detail(self._call("get_player_detail", tool_input)), "get_player_detail", tool_input)
        if any(w in norm for w in _META_WORDS):
            tool_input = {"team_color": q.team_color} if q.team_color else {}
            return Answer(self._format_trends(self._call("get_meta_trends", tool_input)), "get_meta_trends", tool_input)
        if (q.team_color or "포메이션" in norm) and not any(w in norm for w in _DATA_WORDS):
            tool_input = {"team_color": q.team_color} if q.team_color else {}
            return Answer(self._format_formations(self._call("list_formations", tool_input)), "list_formations", tool_input)
        if any(w in norm for w in _DATA_WORDS) or q.team_color:
            return Answer(self._available_text(), "list_available_data", {})
        return Answer(HELP_TEXT)

    def _call(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        content, _ = self.toolbox.run(name, tool_input)
        return json.loads(content)

    # --- formatting --------------------------------------------------------

    def _format_recommend(self, r: dict[str, Any], wanted: int) -> str:
        if "error" in r:
            return f"{r['error']}"
        role = ROLE_LABELS.get(r["role"], r["role"])
        if not r["players"]:
            if r.get("budget"):
                return f"{r['team_color']} {role}: 예산 {r['budget']['max_price']} 이하로 랭커들이 쓴 카드가 없습니다."
            combo = f"{r['team_color']} {r.get('formation') or ''}".strip()
            return f"{combo} 데이터가 없습니다.\n\n" + self._available_text()
        formation = r["formation_used"] if r["formation_used"] != "전체" else "전체 포메이션"
        scope = f"랭킹 상위 {r['ranking_scope']}명 중 " if r.get("ranking_scope") else ""
        lines = [
            f"{r['team_color']} {formation} {role} 추천 — {scope}랭커 {r['sample_size']}명 스쿼드 기준 ({_as_of(r['data_as_of'])})"
        ]
        if r["fallback_to_all_formations"]:
            lines.append(f"※ {r['requested_formation']} 표본이 적어 {r['team_color']} 전체 포메이션으로 집계했습니다.")
        if r["strict"]:
            lines.append("※ 실제 경기 배치가 포메이션과 일치한 스쿼드만 집계했습니다.")
        if r.get("budget"):
            lines.append(f"※ 예산 {r['budget']['max_price']} 이하 (랭커들이 가장 많이 쓴 강화 단계의 시세 기준)")
        if r["sample_size"] < 30:
            lines.append(f"※ 표본이 {r['sample_size']}명으로 적어 참고용입니다.")
        lines.append("")
        shown, rest = r["players"][:wanted], r["players"][wanted:]
        for i, p in enumerate(shown, 1):
            head = f"{i}. {p['name']} — {p['rankers']}명 사용 ({p['usage_rate']:.1%})"
            if r.get("budget"):
                head += f" · 예산 내 카드 사용 {sum(c['rankers'] for c in p['cards'])}명"
            lines.append(head)
            cards = [_card_text(c) for c in p["cards"][:3]]
            lines.append(f"   주로 쓰는 카드: {', '.join(cards)}")
            detail = [f"평균 강화 {p['avg_grade']:.1f}"] if p["avg_grade"] is not None else []
            if p["avg_elo_of_users"] is not None:
                detail.append(f"사용 랭커 평균 ELO {p['avg_elo_of_users']:.0f}")
            lines.append("   " + " · ".join(detail))
        if rest:
            lines.append("")
            lines.append("다음 후보: " + ", ".join(f"{p['name']} ({p['usage_rate']:.1%})" for p in rest))
        if any(c.get("price_at_most_used_grade") for p in r["players"] for c in p["cards"]):
            lines.append("")
            lines.append("시세는 수집 시각 기준이며 변동될 수 있습니다.")
        if r.get("note"):
            lines.append(f"※ {r['note']}")
        return "\n".join(lines)

    def _format_squad(self, r: dict[str, Any]) -> str:
        if "error" in r:
            return r["error"]
        if not r.get("lineup"):
            combo = " ".join(x for x in (r["team_color"], r.get("formation")) if x)
            return f"{combo}: {r['note']}"
        scope = f"랭킹 상위 {r['ranking_scope']}명 중 " if r.get("ranking_scope") else ""
        lines = [f"{r['team_color']} {r['formation']} 추천 스쿼드 — {scope}랭커 {r['sample_size']}명 스쿼드 기준 ({_as_of(r['data_as_of'])})"]
        if r["formation_source"] == "most_used":
            lines.append(f"※ 포메이션을 말하지 않아 가장 많이 쓰인 {r['formation']}로 짰습니다.")
        if r["fallback_to_all_formations"]:
            lines.append(f"※ {r['formation']} 표본이 적어 선수 사용률은 {r['team_color']} 전체 포메이션으로 집계했습니다.")
        if b := r.get("budget"):
            caps = [f"총예산 {b['max_total_price']}"] if b["max_total_price"] else []
            caps += [f"총 급여 {b['max_total_salary']}"] if b["max_total_salary"] is not None else []
            state = "안에 맞췄습니다" if b["within_budget"] else "안에 맞출 수 없어 가장 가까운 조합입니다"
            lines.append(f"※ {' · '.join(caps)} {state} (시세는 랭커들이 가장 많이 쓴 강화 단계 기준)")
        lines.append("")
        for s in r["lineup"]:
            if s.get("pid") is None:
                lines.append(f"· {s['role']}: 후보 부족")
                continue
            lines.append(f"· {s['role']}: {s['player'] or '이름 미상'} ({s['usage_rate']:.1%}) — {_card_text(s['card'])}")
        unpriced = f" (시세 미수집 {len(r['unpriced_players'])}명 제외)" if r.get("unpriced_players") else ""
        lines += ["", f"총액 {r['total_price']}{unpriced}"]
        alts = [f"{role}: {', '.join(a['player'] or '이름 미상' for a in alt)}" for role, alt in r["alternatives"].items() if alt]
        if alts:
            lines.append("대안 — " + " / ".join(alts))
        return "\n".join(lines)

    def _format_trends(self, r: dict[str, Any]) -> str:
        if "error" in r:
            return r["error"]
        if not r.get("data_as_of"):
            return r["note"]

        def change(x: dict[str, Any], key: str = "share_change") -> str:
            return f" ({x[key] * 100:+.1f}%p)" if x.get(key) is not None else ""

        vs = f", {_as_of(r['compared_with'])}과 비교" if r.get("compared_with") else ""
        lines = [f"{r['scope']} 메타 — 랭킹 상위 {r['ranking_scope']}명 ({_as_of(r['data_as_of'])}{vs})"]
        if r.get("team_colors"):
            lines.append("팀컬러: " + ", ".join(f"{t['team_color']} {t['share']:.1%}{change(t)}" for t in r["team_colors"][:5]))
        if r.get("formations"):
            lines.append("포메이션: " + ", ".join(f"{f['formation']} {f['share']:.1%}{change(f)}" for f in r["formations"][:5]))
        if r.get("top_players"):
            lines.append(
                f"많이 쓰인 선수 (스쿼드 {r['player_sample_size']}명): "
                + ", ".join(f"{p['player']}({p['main_role']}) {p['usage_rate']:.1%}" for p in r["top_players"][:5])
            )
        for key, label in (("rising", "오른 선수"), ("falling", "내린 선수")):
            if r.get(key):
                lines.append(f"{label}: " + ", ".join(f"{p['player']} {p['usage_rate']:.1%}{change(p, 'change')}" for p in r[key]))
        lines.append(f"※ {r['note']}")
        return "\n".join(lines)

    def _format_detail(self, r: dict[str, Any]) -> str:
        if not r.get("usage"):
            return r.get("note") or "찾지 못했습니다."
        lines = [f"{', '.join(r['matched_names'])} — 랭커 사용 현황"]
        for u in r["usage"][:10]:
            lines.append(
                f"· {u['team_color']} {u['formation']} {u['role']} · {_season(u['season'])}: "
                f"{u['rankers']}/{u['sample_size']}명 ({u['usage_rate']:.1%}), 평균 강화 {u['avg_grade']:.1f}"
            )
        lines.append(f"({_as_of(r['usage'][0]['data_as_of'])})")
        if r.get("note"):
            lines.append(f"※ {r['note']}")
        return "\n".join(lines)

    def _format_formations(self, r: dict[str, Any]) -> str:
        if "error" in r:
            return r["error"]
        if not r["formations"]:
            return f"{r['team_color']}: {r['note']}"
        who = r["team_color"] if r["team_color"] == "전체 랭커" else f"{r['team_color']} 랭커"
        lines = [f"{who} 포메이션 ({_as_of(r['data_as_of'])})"]
        for f in r["formations"]:
            done = f", 스쿼드 {f['squads_collected']}명 수집" if f["squads_collected"] else ", 스쿼드 미수집"
            lines.append(f"· {f['formation']}: {f['rankers']}명 ({f['share']:.1%}{done})")
        lines.append(f"※ {r['note']}")
        return "\n".join(lines)

    def _available_text(self) -> str:
        data = self._call("list_available_data", {})
        combos = [c for c in data["combos"] if c["formation"] != "전체"][:12]
        if not combos:
            return "아직 집계된 데이터가 없습니다. README의 수집 순서를 참고하세요."
        lines = []
        if scope := data.get("daily_scope"):
            lines.append(
                f"매일 수집: 랭킹 상위 {scope['top_rankers']}명, 그중 스쿼드 {scope['squads_collected']}명 ({_as_of(scope['data_as_of'])})"
            )
        lines.append("표본이 많은 조합:")
        lines += [f"· {c['team_color']} {c['formation']}: 랭커 {c['squads_collected']}명" for c in combos]
        return "\n".join(lines)


def _season(class_name: str | None) -> str:
    return (class_name or "?").split(" (")[0]


def _card_text(c: dict[str, Any]) -> str:
    text = f"{_season(c['season'])} {c['rankers']}명"
    extra = []
    if c.get("most_used_grade"):
        extra.append(f"주로 {c['most_used_grade']}강")
    price = c.get("price_at_most_used_grade")
    if price:
        extra.append(f"시세 {format_bp(price['price_bp'])}")
    return text + (f" ({', '.join(extra)})" if extra else "")


HELP_TEXT = """\
이렇게 물어보세요:
· 아스널 4-2-3-1 볼란치 2명 추천해줘
· 아스널 볼란치 5억 이하로 추천
· 아스널 4-2-3-1 공미 추천 (엄격하게)
· 4-2-3-1 스쿼드 50억 이하로 짜줘
· 요즘 메타 (많이 쓰는 팀컬러·포메이션·선수)
· 라이스 사용률
· 아스널 포메이션
· 어떤 데이터 있어?"""
