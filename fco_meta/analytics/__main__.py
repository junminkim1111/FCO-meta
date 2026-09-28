"""Command line entry point.

    python -m fco_meta.analytics build                                          # usage_stats 재계산
    python -m fco_meta.analytics top --team-color 아스널 --formation 4-2-3-1 --role 볼란치
    python -m fco_meta.analytics top --team-color 아스널 --formation 4-2-3-1 --role DM --by sp_id --strict
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..crawler.teamcolors import TeamColorCatalog
from ..market.roles import ROLES, resolve_role
from ..storage import Storage
from .usage import ALL_FORMATIONS, MIN_SAMPLE, UsageStore, top_players

DEFAULT_DB = Path("data/fco_meta.sqlite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fco_meta.analytics")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("build", help="수집된 스쿼드로 usage_stats 재계산")

    p_top = sub.add_parser("top", help="역할별 사용 선수 순위")
    p_top.add_argument("--team-color", required=True, help="팀컬러 이름 또는 id (예: 아스널, 1004)")
    p_top.add_argument("--formation", default=ALL_FORMATIONS, help="포메이션 (기본: 전체)")
    p_top.add_argument("--role", required=True, help=f"역할 ({', '.join(ROLES)}) 또는 별칭 (볼란치, 공미 …)")
    p_top.add_argument("--by", default="pid", choices=["pid", "sp_id"], help="pid=선수별(시즌 합산), sp_id=카드별")
    p_top.add_argument("--top", type=int, default=5)
    p_top.add_argument("--strict", action="store_true", help="추론 포메이션이 스냅샷 포메이션과 같은 스쿼드만")
    p_top.add_argument("--min-sample", type=int, default=MIN_SAMPLE, help="이보다 표본이 적으면 팀컬러 전체로 폴백")
    p_top.add_argument("--mode", default="1vs1")

    args = parser.parse_args(argv)
    storage = Storage(args.db)
    try:
        if args.command == "build":
            for (as_of, mode, tc, formation, strict), n in UsageStore(storage.conn).build_all():
                print(f"{as_of} {mode} tc={tc} {formation}{' strict' if strict else ''}: {n} rows")
            return 0
        return _top(storage, args)
    finally:
        storage.close()


def _top(storage: Storage, args: argparse.Namespace) -> int:
    tc = TeamColorCatalog.load().resolve(args.team_color)
    if tc is None:
        print(f"알 수 없는 팀컬러: {args.team_color}", file=sys.stderr)
        return 2
    role = resolve_role(args.role)
    if role is None:
        print(f"알 수 없는 역할: {args.role}", file=sys.stderr)
        return 2
    res = top_players(
        storage.conn, tc.id, args.formation, role,
        top=args.top, by=args.by, strict=args.strict, mode=args.mode, min_sample=args.min_sample,
    )  # fmt: skip
    if not res.players:
        print("집계 결과가 없습니다. 스쿼드 수집 후 `python -m fco_meta.analytics build`를 실행하세요", file=sys.stderr)
        return 1
    formation = "전체 포메이션" if res.formation == ALL_FORMATIONS else res.formation
    print(
        f"{tc.name} {formation} {role} 사용 TOP {args.top} "
        f"(기준 {res.data_as_of}, 표본 {res.sample_size}명 / 조합 {res.combo_rankers}명)"
    )
    if res.fallback:
        print(f"  ※ {res.requested_formation} 표본이 {args.min_sample}명 미만이라 팀컬러 전체로 집계했습니다")
    for i, p in enumerate(res.players, 1):
        print(
            f"  {i}. {p.name or p.key}: {p.ranker_count}명 ({p.usage_rate:.1%}) · 평균 강화 {_fmt(p.avg_grade, '.1f')}"
            f" · 평균 평점 {_fmt(p.avg_rating, '.2f')} · 사용 랭커 평균 ELO {_fmt(p.avg_elo, '.0f')}"
        )
        if args.by == "pid":
            print("     " + ", ".join(f"{s.season or s.season_id} {s.ranker_count}명" for s in p.seasons))
    return 0


def _fmt(value: float | None, spec: str) -> str:
    return "-" if value is None else format(value, spec)


if __name__ == "__main__":
    sys.exit(main())
