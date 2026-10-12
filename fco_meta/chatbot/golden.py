"""Golden set: questions with conditions every answer must meet, checked by code (no model judges the answers).

    python -m fco_meta.chatbot.golden              # eval/golden.json을 사이트와 같은 경로(Jev → 모델 → 도구)로 → reports/golden-*.md
    python -m fco_meta.chatbot.golden --oracle     # 모델 없이: 각 질문의 oracle 도구를 직접 불러 조건 자체가 맞는지 (토큰 0)
    python -m fco_meta.chatbot.golden --only 1-5,12

Each case's `expect` (all optional):
    blocked            Jev가 막아 모델을 부르지 않는다
    route              "light"(Flash-Lite로 시작) | "heavy"(처음부터 3.5 Flash)
    no_tools           도구를 부르지 않는다
    tools / tools_any  모두 / 하나 이상 불러야 할 도구
    detail             get_player_detail로 조회해야 할 선수 이름들
    players            마지막 recommend_players 결과: team_color, roles(하나 이상 겹침), max_price, max_salary, traits
    squad              마지막 recommend_squad·check_squad 결과: team_color(전원 소속, 카드 상세로 따로 확인), players(기본 11),
                       max_salary(기본 310), max_price, formation, include(도구가 못 넣으면 답이 그렇다고 말해야), exclude, infeasible(예산이 안 되면 답이 그렇다고 말해야)
    answer_has / answer_any   답에 모두 / 하나 이상 들어 있어야 할 말
    data_date          답에 데이터 수집 날짜
모든 답(거절 제외)은 정상 답(혼잡 안내 아님)이어야 하고, 다시 쓰기 뒤에도 확인 안 된 수치가 없어야 한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import load_env
from .evaluate import DEFAULT_DB, DEFAULT_OUT_DIR, select
from .tools import Toolbox

DEFAULT_CASES = Path("eval/golden.json")
SQUAD_TOOLS = ("recommend_squad", "check_squad")
ADMITS = ("넘", "초과", "부족", "불가", "어렵", "안 됩", "안됩", "못 ", "없습니다", "모자")  # 예산이 안 될 때 답이 인정하는 말

Check = tuple[str, bool, str]  # (조건, 통과, 설명)


def _key(text: Any) -> str:
    return "".join(str(text or "").replace("(국가)", "").split()).casefold()


def _last(calls: list[tuple[str, dict]], names: tuple[str, ...], toolbox: Toolbox) -> dict[str, Any] | None:
    """The full result of the last call to one of `names` (run again — the DB is the same, so the result is too)."""
    for name, args in reversed(calls):
        if name in names:
            content, is_error = toolbox.run(name, args)
            return None if is_error else json.loads(content)
    return None


def check(expect: dict[str, Any], record: dict[str, Any] | None, calls: list[tuple[str, dict]], answer: str | None,
          toolbox: Toolbox) -> list[Check]:  # fmt: skip
    """`record`·`answer` are None in oracle mode (only the tool conditions are checked)."""
    out: list[Check] = []

    def add(name: str, ok: Any, why: str = "") -> None:
        out.append((name, bool(ok), why))

    if record is not None:
        if expect.get("blocked"):
            add("거절", record["outcome"] == "blocked", record["outcome"])
            return out
        add("정상 답", record["outcome"] in ("ok", "cached"), record.get("error") or record["outcome"])
        add("수치 확인", not record.get("unverified"), ", ".join(record.get("unverified") or []))
        if route := expect.get("route"):
            light = "Flash-Lite" in (record.get("route") or "")
            add(f"라우팅 {route}", light == (route == "light"), record.get("route") or "")
    names = [n for n, _ in calls]
    if expect.get("no_tools"):
        add("도구 없음", not calls, ", ".join(names))
    for tool in expect.get("tools", []):
        add(f"{tool} 호출", tool in names, ", ".join(names))
    if wanted := expect.get("tools_any"):
        add(f"{' 또는 '.join(wanted)} 호출", any(t in names for t in wanted), ", ".join(names))
    for name in expect.get("detail", []):
        add(f"{name} 상세 조회", any(t == "get_player_detail" and name in str(a.get("name", "")) for t, a in calls))
    if "players" in expect:
        out += _players(expect["players"], _last(calls, ("recommend_players",), toolbox), answer)
    if "squad" in expect:
        out += _squad(expect["squad"], _last(calls, SQUAD_TOOLS, toolbox), answer, toolbox.conn)
    if answer is not None:
        for text in expect.get("answer_has", []):
            add(f"답에 '{text}'", text in answer)
        if any_of := expect.get("answer_any"):
            add(f"답에 {'·'.join(any_of)} 중 하나", any(t in answer for t in any_of))
        if expect.get("data_date"):
            day = _data_date(toolbox)
            forms = {day.strftime("%Y-%m-%d"), f"{day.month}월 {day.day}일", f"{day.month}/{day.day}", day.strftime("%m-%d")}
            add("답에 수집 날짜", any(f in answer for f in forms), day.strftime("%Y-%m-%d"))
    return out


def _players(want: dict[str, Any], result: dict[str, Any] | None, answer: str | None) -> list[Check]:
    if not result or not result.get("players"):
        return [("추천 선수 있음", False, "recommend_players 결과 없음")]
    out: list[Check] = [("추천 선수 있음", True, f"{len(result['players'])}명")]
    if team := want.get("team_color"):
        out.append((f"팀컬러 {team}", _key(result.get("team_color")) == _key(team), str(result.get("team_color"))))
    if roles := want.get("roles"):
        got = set(str(result.get("role", "")).split("+"))
        out.append((f"역할 {'/'.join(roles)}", bool(got & set(roles)), str(result.get("role"))))
    cards = [c for p in result["players"] for c in p.get("cards", [])]
    if (cap := want.get("max_price")) is not None:
        over = [c["season"] for c in cards if ((c.get("price_at_most_used_grade") or {}).get("price_bp") or 0) > cap]
        out.append((f"카드 시세 ≤ {cap:,}", not over, ", ".join(over[:3])))
    if (cap := want.get("max_salary")) is not None:
        over = [c["season"] for c in cards if (c.get("salary") or 0) > cap]
        out.append((f"카드 급여 ≤ {cap}", not over, ", ".join(over[:3])))
    if traits := want.get("traits"):
        info = result.get("traits") or {}
        out.append((f"특성 {'·'.join(traits)}", set(traits) <= set(info.get("wanted", [])), str(info.get("wanted"))))
        if not info.get("can_add_trait"):  # 원래 가진 특성: 추천된 선수마다 그 특성 카드가 있어야
            missing = [p["name"] for p in result["players"] if not any(set(traits) <= set(c.get("new_traits") or []) for c in p.get("cards", []))]
            out.append(("추천 선수 모두 그 특성 카드", not missing, ", ".join(missing[:3])))
    if answer is not None:
        top = result["players"][0]["name"].split()[-1]
        out.append((f"답에 1순위 '{top}'", top in answer, ""))
    return out


def _squad(want: dict[str, Any], result: dict[str, Any] | None, answer: str | None, conn: sqlite3.Connection) -> list[Check]:
    if not result or not result.get("lineup"):
        return [("스쿼드 있음", False, "recommend_squad·check_squad 결과 없음")]
    lineup = result["lineup"]
    size = want.get("players", 11)
    out: list[Check] = [(f"{size}명 · 중복 없음", len(lineup) == size and len({p["pid"] for p in lineup}) == size, f"{len(lineup)}명")]
    if team := want.get("team_color"):
        out.append((f"팀컬러 {team}", _key(result.get("team_color")) == _key(team), str(result.get("team_color"))))
        outside, unknown = [], 0
        for p in lineup:  # 도구 판단과 따로: 카드 상세의 경력 클럽·국적으로 다시 확인
            row = conn.execute("SELECT clubs, nation FROM card_detail WHERE spid = ?", (p["card"]["sp_id"],)).fetchone()
            if row is None:
                unknown += 1
            elif _key(team) not in {_key(c["club"]) for c in json.loads(row[0] or "[]")} | {_key(row[1])}:
                outside.append(p["player"])
        out.append(("전원 팀컬러 소속 (카드 상세)", not outside, ", ".join(outside) + (f" · 상세 없음 {unknown}명" if unknown else "")))
    cap = want.get("max_salary", 310)
    out.append((f"급여 ≤ {cap}", (result.get("total_salary") or 0) <= cap, str(result.get("total_salary"))))
    if (budget := want.get("max_price")) is not None:
        within = (result.get("total_price_bp") or 0) <= budget
        if want.get("infeasible") and not within:  # 안 되는 예산: 넘긴 걸 답이 말해야
            if answer is not None:
                out.append(("예산 초과를 답이 알림", any(w in answer for w in ADMITS), str(result.get("total_price"))))
        else:
            out.append((f"총액 ≤ {budget:,}", within, str(result.get("total_price"))))
    if formation := want.get("formation"):
        out.append((f"포메이션 {formation}", result.get("formation") == formation, str(result.get("formation"))))
    players = " ".join(p["player"] for p in lineup)
    for name in want.get("include", []):
        if (result.get("include") or {}).get(name) is None and name not in players:  # 도구가 넣지 못함 (그 팀 랭커 기록에 없음 등)
            if answer is not None:
                out.append((f"{name}을 넣지 못한 걸 답이 알림", name in answer and any(w in answer for w in ADMITS), ""))
        else:
            out.append((f"{name} 포함", name in players, ""))
    for name in want.get("exclude", []):
        out.append((f"{name} 제외", name not in players, ""))
    if answer is not None:  # 답 형식: 표 위에 총액·총 급여
        price = str(result.get("total_price") or "")
        out.append(("답에 총액·총 급여", price.split("억")[0] in answer and str(result.get("total_salary")) in answer, f"{price} · {result.get('total_salary')}"))
    return out


def _data_date(toolbox: Toolbox) -> datetime:
    combos = json.loads(toolbox.run("list_available_data", {})[0])["combos"]
    return datetime.fromisoformat(max(c["data_as_of"] for c in combos))


def run_live(cases: list[tuple[int, dict]], db: Path, toolbox: Toolbox) -> list[dict[str, Any]]:
    """Each question in its own conversation, through the web app in this process (Jev, routing, model, tools, number check)."""
    from fastapi.testclient import TestClient

    from ..web.app import RateLimit, create_app
    from ..web.chatlog import ChatLog

    log = ChatLog()
    client = TestClient(create_app(db, backend="gemini", chat_log=log, limit=RateLimit(10**6, 10**6, 10**6)))
    rows = []
    for i, case in cases:
        started = time.monotonic()
        client.post("/api/chat", json={"message": case["q"]})
        record, detail = log.pending[-1], log.details_pending[-1]
        calls = [(t["name"], t.get("args") or {}) for t in detail["trace"] if t["kind"] == "tool"]
        rows.append({"i": i, "case": case, "record": record, "answer": detail["answer"] or "",
                     "checks": check(case["expect"], record, calls, detail["answer"] or "", toolbox),
                     "s": time.monotonic() - started})  # fmt: skip
        print(f"{i:>2} {'통과' if all(c[1] for c in rows[-1]['checks']) else '실패'}  {case['q']}", flush=True)
    return rows


def run_oracle(cases: list[tuple[int, dict]], toolbox: Toolbox) -> list[dict[str, Any]]:
    rows = []
    for i, case in cases:
        if not case.get("oracle"):
            continue  # 거절·인사는 모델(Jev)이 있어야 확인된다
        calls = [(name, args) for name, args in case["oracle"]]
        rows.append({"i": i, "case": case, "record": None, "answer": None, "checks": check(case["expect"], None, calls, None, toolbox), "s": 0})
    return rows


def report(rows: list[dict[str, Any]], oracle: bool) -> str:
    passed = sum(all(c[1] for c in r["checks"]) for r in rows)
    lines = [f"# 골든 세트 {'(oracle — 모델 없이 조건만)' if oracle else ''} — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"**통과 {passed}/{len(rows)}**"]  # fmt: skip
    if not oracle:
        recs = [r["record"] for r in rows]
        cost = sum(r.get("cost", 0) for r in recs)
        lines[-1] += (f" · 수치 미확인 {sum(1 for r in recs if r.get('unverified'))} · 평균 {sum(r['s'] for r in rows) / max(len(rows), 1):.1f}초"
                      f" · 추정 비용 ${cost:.4f}")  # fmt: skip
    lines += ["", "| # | 질문 | 결과 | 실패한 조건 | 모델 · 도구 | 시간 |", "|---|---|---|---|---|---|"]
    for r in rows:
        failed = "<br>".join(f"{n} ({why})" if why else n for n, ok, why in r["checks"] if not ok)
        rec = r["record"] or {}
        used = " · ".join(filter(None, [rec.get("model"), ", ".join(rec.get("tools") or [])]))
        ok = all(c[1] for c in r["checks"])
        lines.append(f"| {r['i']} | {r['case']['q']} | {'✅' if ok else '❌'} | {failed} | {used} | {r['s']:.0f}초 |")
    if not oracle:
        lines += ["", "## 실패한 답"]
        for r in rows:
            if not all(c[1] for c in r["checks"]):
                lines += ["", f"### {r['i']}. {r['case']['q']}", "", r["answer"].strip() or "(답 없음)"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="python -m fco_meta.chatbot.golden")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--only", help="이 번호만 (예: 1-5,12)")
    parser.add_argument("--oracle", action="store_true", help="모델 없이 oracle 도구로 조건만 확인 (토큰 0)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if not args.db.exists():
        print(f"DB가 없습니다: {args.db}", file=sys.stderr)
        return 2
    cases = select(json.loads(args.cases.read_text(encoding="utf-8"))["cases"], args.only)
    toolbox = Toolbox(sqlite3.connect(f"file:{args.db}?mode=ro", uri=True))
    rows = run_oracle(cases, toolbox) if args.oracle else run_live(cases, args.db, toolbox)
    text = report(rows, args.oracle)
    out = args.out or DEFAULT_OUT_DIR / f"golden{'-oracle' if args.oracle else ''}-{datetime.now():%Y%m%d-%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(re.sub(r"\*\*", "", text.splitlines()[2]), f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
