"""Command line entry point.

    python -m fco_meta.crawler catalog                 # 팀컬러 목록 갱신 → data/teamcolors.json
    python -m fco_meta.crawler summary                 # TOP 10,000 팀컬러/포메이션 이용률 TOP 10
    python -m fco_meta.crawler rank --team-color 아스널 --formation 4-2-3-1
    python -m fco_meta.crawler rank --max-pages 50     # 필터 없이 상위 1,000명
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ..storage import Storage
from .client import DatacenterClient
from .jobs import crawl_rankings
from .parser import parse_formation_options, parse_rank_summary, parse_team_color_catalog
from .query import RankQuery
from .teamcolors import DEFAULT_CATALOG_PATH, TeamColorCatalog

DEFAULT_DB = Path("data/fco_meta.sqlite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fco_meta.crawler")
    parser.add_argument("--interval", type=float, default=2.0, help="요청 간 최소 간격(초)")
    parser.add_argument("--raw-dir", type=Path, help="원본 HTML 저장 경로")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_catalog = sub.add_parser("catalog", help="팀컬러 목록 갱신")
    p_catalog.add_argument("--out", type=Path, default=DEFAULT_CATALOG_PATH)

    sub.add_parser("summary", help="팀컬러/포메이션 이용률 TOP 10")

    p_rank = sub.add_parser("rank", help="랭킹 수집")
    p_rank.add_argument("--mode", default="1vs1", choices=["1vs1", "2vs2", "manager"])
    p_rank.add_argument("--team-color", help="팀컬러 이름 또는 id (예: 아스널, 1004)")
    p_rank.add_argument("--formation", help="포메이션 (예: 4-2-3-1)")
    p_rank.add_argument("--max-pages", type=int, help="최대 페이지 수 (20명/페이지)")
    p_rank.add_argument("--db", type=Path, default=DEFAULT_DB)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    raw_dir = args.raw_dir / datetime.now().strftime("%Y%m%d-%H%M%S") if args.raw_dir else None
    with DatacenterClient(min_interval=args.interval, raw_dir=raw_dir) as client:
        if args.command == "catalog":
            shell = client.fetch_shell()
            catalog = TeamColorCatalog(parse_team_color_catalog(shell))
            catalog.save(args.out)
            print(f"{len(catalog.entries)} team colors → {args.out}")
            print(f"formations: {', '.join(parse_formation_options(shell))}")
            return 0

        if args.command == "summary":
            summary = parse_rank_summary(client.fetch_shell())
            for title, entries in (("팀컬러", summary.team_colors), ("포메이션", summary.formations)):
                print(f"[{title} 이용률]")
                for e in entries:
                    print(f"  {e.rank:>2}. {e.name} {e.rate:.1%}")
            return 0

        query = RankQuery(mode=args.mode)
        if args.team_color:
            tc = TeamColorCatalog.load().resolve(args.team_color)
            if tc is None:
                print(f"알 수 없는 팀컬러: {args.team_color}", file=sys.stderr)
                return 2
            query = RankQuery(mode=args.mode, team_color_id=tc.id)
        if args.formation:
            query = replace(query, formation=args.formation)

        args.db.parent.mkdir(parents=True, exist_ok=True)
        storage = Storage(args.db)
        try:
            result = crawl_rankings(client, storage, query, max_pages=args.max_pages)
        finally:
            storage.close()
        print(
            f"run {result.run_id}: {result.rows} rows / {result.pages} pages "
            f"(검색 결과 {result.total_count}명, status={result.status}) → {args.db}"
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
