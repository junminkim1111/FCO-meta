"""Command line entry point.

    python -m fco_meta.pipeline meta                                   # spid/seasonid/spposition 메타데이터 저장
    python -m fco_meta.pipeline squads --team-color 아스널 --formation 4-2-3-1 --budget 300
    python -m fco_meta.pipeline usage --team-color 아스널 --formation 4-2-3-1 --role 볼란치
    python -m fco_meta.pipeline formations --save                     # 포지션 조합 → 포메이션 표 갱신
    python -m fco_meta.pipeline budget                                 # 오늘 호출 수
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..crawler.teamcolors import TeamColorCatalog
from ..openapi import CallBudget, NexonOpenApiClient
from ..storage import Storage
from .formation import DEFAULT_TABLE_PATH, ROLE_POSITIONS, FormationTable, signature_key
from .report import role_usage
from .squads import SquadCollector, select_targets
from .store import PipelineStore

DEFAULT_DB = Path("data/fco_meta.sqlite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fco_meta.pipeline")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--daily-limit", type=int, default=1000, help="일일 호출 한도 (개발 키 1,000)")
    parser.add_argument("--rps", type=int, default=5, help="초당 최대 호출 수 (개발 키 5)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("meta", help="메타데이터(spid, seasonid, spposition) 저장")
    sub.add_parser("budget", help="오늘(KST) 호출 수")

    p_sq = sub.add_parser("squads", help="팀컬러×포메이션 랭커 스쿼드 수집")
    _combo_args(p_sq)
    p_sq.add_argument("--budget", type=int, help="이번 실행에서 쓸 최대 호출 수")
    p_sq.add_argument("--limit", type=int, help="상위 N명만")
    p_sq.add_argument("--extra-matches", type=int, default=0, help="기본 스쿼드 외에 추가로 볼 경기 수")
    p_sq.add_argument("--max-details", type=int, default=5, help="랭커당 새로 받을 match-detail 최대 수")
    p_sq.add_argument("--retry-failed", action="store_true", help="닉네임 조회 실패 등도 다시 시도")

    p_use = sub.add_parser("usage", help="역할별 선수 사용률")
    _combo_args(p_use)
    p_use.add_argument("--role", default="볼란치", choices=sorted(ROLE_POSITIONS))
    p_use.add_argument("--by", default="sp_id", choices=["sp_id", "pid"], help="sp_id=시즌별, pid=선수별")
    p_use.add_argument("--top", type=int, default=5)

    p_form = sub.add_parser("formations", help="수집된 기본 스쿼드로 포지션 조합→포메이션 표 학습")
    p_form.add_argument("--save", action="store_true", help=f"{DEFAULT_TABLE_PATH.name}에 저장")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

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
        if args.command == "usage":
            return _usage(storage, args)
        return _collect(storage, store, args)
    finally:
        storage.close()


def _combo_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--team-color", required=True, help="팀컬러 이름 또는 id (예: 아스널, 1004)")
    p.add_argument("--formation", required=True, help="포메이션 (예: 4-2-3-1)")
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

        tc_id = _team_color_id(args.team_color)
        if tc_id is None:
            return 2
        targets = select_targets(storage.conn, tc_id, args.formation, args.mode, args.as_of)
        if not targets:
            print("대상 랭커가 없습니다. 먼저 `python -m fco_meta.crawler rank ...`로 수집하세요", file=sys.stderr)
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
            print(f"  중단: {result.stopped} — 같은 명령으로 이어서 수집할 수 있습니다")
        return 0


def _usage(storage: Storage, args: argparse.Namespace) -> int:
    tc_id = _team_color_id(args.team_color)
    if tc_id is None:
        return 2
    rows = role_usage(
        storage.conn, tc_id, args.formation, ROLE_POSITIONS[args.role],
        data_as_of=args.as_of, mode=args.mode, by=args.by, top=args.top,
    )  # fmt: skip
    if not rows:
        print("수집된 스쿼드가 없습니다", file=sys.stderr)
        return 1
    print(f"{args.team_color} {args.formation} {args.role} 사용 선수 TOP {args.top} (표본 {rows[0].sample}명)")
    for i, r in enumerate(rows, 1):
        label = f"{r.name or r.key}" + (f" [{r.season}]" if r.season and args.by == "sp_id" else "")
        print(
            f"  {i}. {label}: {r.rankers}명 ({r.usage_rate:.1%}), 평균 강화 {r.avg_grade}, "
            f"평균 평점 {r.avg_rating}, 해당 경기 승률 {r.win_rate:.0%}"
        )
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
