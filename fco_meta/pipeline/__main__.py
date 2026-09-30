"""Command line entry point.

    python -m fco_meta.pipeline meta                                   # spid/seasonid/spposition 메타데이터 저장
    python -m fco_meta.pipeline squads --team-color 아스널 --formation 4-2-3-1 --budget 300
    python -m fco_meta.pipeline squads --top 1000                      # 필터 없는 랭킹 상위 1,000명
    python -m fco_meta.pipeline formations --save                     # 포지션 조합 → 포메이션 표 갱신
    python -m fco_meta.pipeline budget                                 # 오늘 호출 수
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ..config import load_env
from ..crawler.teamcolors import TeamColorCatalog
from ..openapi import CallBudget, NexonOpenApiClient
from ..storage import Storage
from .formation import DEFAULT_TABLE_PATH, FormationTable, signature_key
from .ranker_stats import DEFAULT_CALLS as RANKER_STATS_CALLS
from .ranker_stats import collect_ranker_stats
from .squads import STOP_HINTS, SquadCollector, select_targets, select_top_targets
from .store import PipelineStore

DEFAULT_DB = Path("data/fco_meta.sqlite")


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="python -m fco_meta.pipeline")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--daily-limit", type=int, default=int(os.environ.get("NEXON_DAILY_LIMIT", 1000)),
                        help="일일 호출 한도 (.env NEXON_DAILY_LIMIT, 개발 키 1,000)")  # fmt: skip
    parser.add_argument("--rps", type=int, default=int(os.environ.get("NEXON_RPS", 5)),
                        help="초당 최대 호출 수 (.env NEXON_RPS, 개발 키 5)")  # fmt: skip
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("meta", help="메타데이터(spid, seasonid, spposition) 저장")
    sub.add_parser("budget", help="오늘(KST) 호출 수")
    p_rs = sub.add_parser("ranker-stats", help="수집된 스쿼드의 카드·포지션별 TOP 10,000 랭커 20경기 평균 스탯 (1회 50쌍)")
    p_rs.add_argument("--calls", type=int, default=RANKER_STATS_CALLS, help="이번 실행 최대 호출 수")

    p_sq = sub.add_parser("squads", help="팀컬러×포메이션 또는 상위 N명 랭커 스쿼드 수집")
    p_sq.add_argument("--top", type=int, help="필터 없이 수집한 랭킹의 상위 N명 (팀컬러·포메이션 대신)")
    _combo_args(p_sq, required=False)
    p_sq.add_argument("--budget", type=int, help="이번 실행에서 쓸 최대 호출 수")
    p_sq.add_argument("--limit", type=int, help="상위 N명만")
    p_sq.add_argument("--extra-matches", type=int, default=0, help="기본 스쿼드 외에 추가로 볼 경기 수")
    p_sq.add_argument("--max-details", type=int, default=5, help="랭커당 새로 받을 match-detail 최대 수")
    p_sq.add_argument("--retry-failed", action="store_true", help="닉네임 조회 실패 등도 다시 시도")

    p_form = sub.add_parser("formations", help="수집된 기본 스쿼드로 포지션 조합→포메이션 표 학습")
    p_form.add_argument("--save", action="store_true", help=f"{DEFAULT_TABLE_PATH.name}에 저장")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 요청 URL에 닉네임·ouid가 들어가므로 출력하지 않음

    args.db.parent.mkdir(parents=True, exist_ok=True)
    storage = Storage(args.db)
    store = PipelineStore(storage.conn)
    try:
        if args.command == "budget":
            budget = CallBudget(storage.conn, daily_limit=args.daily_limit)
            print(f"오늘 사용 {budget.used_today()} / {args.daily_limit}")
            return 0
        if args.command == "formations":
            return _learn_formations(store, args.save)
        return _collect(storage, store, args)
    finally:
        storage.close()


def _combo_args(p: argparse.ArgumentParser, required: bool = True) -> None:
    p.add_argument("--team-color", required=required, help="팀컬러 이름 또는 id (예: 아스널, 1004)")
    p.add_argument("--formation", required=required, help="포메이션 (예: 4-2-3-1)")
    p.add_argument("--mode", default="1vs1")
    p.add_argument("--as-of", help="스냅샷 기준 시각 (기본: 가장 최근)")


def _team_color_id(value: str) -> int | None:
    tc = TeamColorCatalog.load().resolve(value)
    if tc is None:
        print(f"알 수 없는 팀컬러: {value}", file=sys.stderr)
        return None
    return tc.id


def _collect(storage: Storage, store: PipelineStore, args: argparse.Namespace) -> int:
    budget = CallBudget(storage.conn, daily_limit=args.daily_limit, run_limit=getattr(args, "budget", None))
    try:
        api = NexonOpenApiClient(budget=budget, per_second=args.rps)
    except ValueError as exc:
        print(f"{exc}: .env 또는 환경 변수에 NEXON_API_KEY를 설정하세요", file=sys.stderr)
        return 2
    with api:
        if args.command == "meta":
            for name in ("spposition", "seasonid", "spid"):
                print(f"{name}: {store.save_metadata(name, api.metadata(name))}")
            return 0

        if args.command == "ranker-stats":
            r = collect_ranker_stats(api, storage.conn, max_calls=args.calls)
            print(f"랭커 스탯: 대상 {r.pairs}쌍, 호출 {r.calls}회, 데이터 {r.saved}쌍 / 기록 없음 {r.empty}쌍"
                  + (f", 남음 {r.remaining}쌍" if r.remaining else "") + (f" — 중단: {r.stopped}" if r.stopped else ""))  # fmt: skip
            return 0

        if args.top:
            tc_id = None
            targets = select_top_targets(storage.conn, args.top, args.mode, args.as_of)
            hint = f"python -m fco_meta.crawler rank --max-pages {-(-args.top // 20)}"
        elif args.team_color and args.formation:
            tc_id = _team_color_id(args.team_color)
            if tc_id is None:
                return 2
            targets = select_targets(storage.conn, tc_id, args.formation, args.mode, args.as_of)
            hint = "python -m fco_meta.crawler rank --team-color ... --formation ..."
        else:
            print("--top N 또는 --team-color와 --formation을 지정하세요", file=sys.stderr)
            return 2
        if not targets:
            print(f"대상 랭커가 없습니다. 먼저 `{hint}`로 랭킹을 수집하세요", file=sys.stderr)
            return 1
        if args.limit:
            targets = targets[: args.limit]
        collector = SquadCollector(
            api,
            store,
            table=FormationTable.load(),
            extra_matches=args.extra_matches,
            max_details=args.max_details,
            retry_failed=args.retry_failed,
        )
        params = {
            "team_color_id": tc_id,
            "top": args.top,
            "formation": args.formation,
            "mode": args.mode,
            "data_as_of": targets[0].data_as_of,
            "budget": args.budget,
            "extra_matches": args.extra_matches,
        }
        result = collector.run(targets, params)
        print(
            f"run {result.run_id}: 대상 {result.targets}명 (기준 {targets[0].data_as_of}), "
            f"호출 {result.api_calls}회, 오늘 누적 {budget.used_today()}/{args.daily_limit}"
        )
        print("  " + ", ".join(f"{k} {v}" for k, v in sorted(result.statuses.items())))
        if result.stopped:
            print(f"  중단: {STOP_HINTS.get(result.stopped, result.stopped)}")
        return 0


def _learn_formations(store: PipelineStore, save: bool) -> int:
    rows = store.conn.execute(
        "SELECT q.match_id, q.ouid, s.formation FROM ranker_squad q JOIN ranker_snapshot s USING (data_as_of, mode, rank)"
        " WHERE q.match_order = 0"
    ).fetchall()
    observations = [([p.sp_position for p in store.players(r["match_id"], r["ouid"])], r["formation"]) for r in rows]
    table = FormationTable.load()
    counts = table.learn(observations)
    for sig, c in sorted(counts.items(), key=lambda kv: -sum(kv[1].values())):
        mark = "" if len(c) == 1 else "  ← 불일치"
        print(f"{signature_key(sig)}: {dict(c)}{mark}")
    print(f"{len(observations)} squads, {len(counts)} signatures, table size {len(table.known)}")
    if save:
        table.save()
        print(f"saved → {DEFAULT_TABLE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
