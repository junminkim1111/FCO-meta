"""Command line entry point.

    python -m fco_meta.market cards --team-color 아스널 --role 볼란치   # 적격 카드 + 강화별 시세 수집
    python -m fco_meta.market find --team-color 아스널 --role DM --grade 5 --max-price 5억 --sort ovr
    python -m fco_meta.market history --spid 100001419 --grade 8     # 365일 시세 이력
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..crawler.client import DatacenterClient
from ..crawler.teamcolors import TeamColorCatalog
from .collector import fetch_price_history, search_all
from .money import format_bp, parse_bp
from .roles import ROLES, resolve_role
from .search import PlayerSearch
from .storage import MarketStorage

DEFAULT_DB = Path("data/fco_meta.sqlite")
KST = timezone(timedelta(hours=9))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fco_meta.market")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--interval", type=float, default=2.0, help="요청 간 최소 간격(초)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_cards = sub.add_parser("cards", help="선수 카드와 강화별 현재가 수집")
    p_cards.add_argument("--team-color", help="팀컬러 이름 또는 id")
    p_cards.add_argument("--role", help=f"포지션 역할 ({', '.join(ROLES)} 또는 '볼란치' 등)")
    p_cards.add_argument("--name", help="선수 이름")

    p_find = sub.add_parser("find", help="저장된 시세에서 조건에 맞는 카드 조회 (싼 순)")
    p_find.add_argument("--team-color")
    p_find.add_argument("--role")
    p_find.add_argument("--grade", type=int, default=1, help="강화 단계 (1~13)")
    p_find.add_argument("--max-price", help="최대 가격 (예: 5억, 3000만)")
    p_find.add_argument("--sort", choices=["price", "ovr"], default="price", help="price: 싼 순, ovr: OVR 높은 순")
    p_find.add_argument("--limit", type=int, default=20)

    p_hist = sub.add_parser("history", help="카드·강화별 365일 시세 이력 수집")
    p_hist.add_argument("--spid", type=int, required=True)
    p_hist.add_argument("--grade", type=int, required=True)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    team_color_id, role = None, None
    if getattr(args, "team_color", None):
        tc = TeamColorCatalog.load().resolve(args.team_color)
        if tc is None:
            print(f"알 수 없는 팀컬러: {args.team_color}", file=sys.stderr)
            return 2
        team_color_id = tc.id
    if getattr(args, "role", None):
        role = resolve_role(args.role)
        if role is None:
            print(f"알 수 없는 포지션: {args.role} (가능: {', '.join(ROLES)})", file=sys.stderr)
            return 2

    args.db.parent.mkdir(parents=True, exist_ok=True)
    storage = MarketStorage(args.db)
    try:
        if args.command == "find":
            rows = storage.find_cards(
                grade=args.grade,
                team_color_id=team_color_id,
                role=role,
                max_price=parse_bp(args.max_price) if args.max_price else None,
                sort=args.sort,
                limit=args.limit,
            )
            for r in rows:
                print(f"{r['name']:<12} {r['season_code'] or '-':<10} {r['main_position'] or '-':<4} "
                      f"OVR {r['ovr'] or '-':<4} 급여 {r['salary'] or '-':<3} "
                      f"{args.grade}강 {format_bp(r['price'])}  (spid {r['spid']})")  # fmt: skip
            if not rows:
                print("조건에 맞는 카드가 없습니다. 먼저 `cards`로 수집해 주세요.")
            return 0

        with DatacenterClient(min_interval=args.interval) as client:
            if args.command == "cards":
                if not (team_color_id or role or args.name):
                    print("--team-color, --role, --name 중 하나 이상 지정해 주세요.", file=sys.stderr)
                    return 2
                search = PlayerSearch(
                    team_color_id=team_color_id or 0,
                    positions=ROLES[role] if role else (),
                    name=args.name or "",
                )
                result = search_all(client, search)
                storage.save_cards(result.cards, team_color_id=team_color_id, role=role)
                note = " (일부 누락 가능: 200장 제한 구간 존재)" if result.truncated else ""
                print(f"{len(result.cards)} cards / {result.requests} requests → {args.db}{note}")
                return 0

            if args.command == "history":
                history = fetch_price_history(client, args.spid, args.grade, datetime.now(KST).date())
                storage.save_price_history(history)
                first, last = history.points[0], history.points[-1]
                print(f"spid {args.spid} {args.grade}강: 현재가 {format_bp(history.current_price)}, "
                      f"{len(history.points)}일치 ({first[0]} {format_bp(first[1])} → {last[0]} {format_bp(last[1])})")  # fmt: skip
                return 0
    finally:
        storage.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
