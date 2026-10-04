"""Daily collection: ranking TOP N → squads → usage stats.

    python -m fco_meta.daily run --top 10000          # 지금 한 번
    python -m fco_meta.daily schedule --at 00:00      # 매일 00:00 (KST)에 반복 (프로세스를 띄워 둔다)
    python -m fco_meta.daily status                   # 수집 상태 확인

Steps
1. 필터 없이 랭킹 상위 N명 크롤링 (20명/페이지, 2초 간격)
2. 표시된 팀컬러·엠블럼으로 팀컬러 소속 기록 (crawler/membership.py)
3. Open API 반영 지연(2시간)이 지날 때까지 대기 — 스냅샷 직전 경기가 API에 들어오도록
4. 상위 N명 스쿼드 수집 (오늘 남은 호출 예산 안에서, 순위 순)
5. 새 카드가 있으면 선수·시즌 메타데이터 갱신
6. 이 스냅샷의 usage_stats 재계산
7. 많이 쓰이는 선수(기본 150명)의 강화별 시세 갱신 — 데이터센터 선수 검색, 선수당 2초
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import load_env
from .retention import prune
from .analytics import UsageStore
from .crawler import DatacenterClient, RankQuery, crawl_rankings
from .crawler.membership import record_displayed_membership
from .crawler.models import PAGE_SIZE
from .crawler.teamcolors import TeamColorCatalog
from .market.details import DEFAULT_CARDS, DetailResult, refresh_card_details
from .market.refresh import DEFAULT_PLAYERS, MAX_REQUESTS, RefreshResult, refresh_used_prices
from .market.storage import SCHEMA as MARKET_SCHEMA
from .openapi import CallBudget, NexonOpenApiClient
from .pipeline import FormationTable, PipelineStore, SquadCollector, select_top_targets
from .pipeline.ranker_stats import DEFAULT_CALLS as RANKER_STATS_CALLS
from .pipeline.ranker_stats import SCHEMA as RANKER_STATS_SCHEMA
from .pipeline.ranker_stats import RankerStatsResult, collect_ranker_stats
from .pipeline.squads import API_DATA_LAG, STOP_HINTS
from .storage import Storage, latest_unfiltered_snapshot

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
DEFAULT_DB = Path("data/fco_meta.sqlite")
META_RESERVE = 3  # 메타데이터 갱신용으로 남겨 둘 호출 수
DEFAULT_TOP = 10_000  # 스쿼드를 받을 랭커 수 (랭커당 2~3회 호출). 일일 한도가 모자라면 남은 랭커는 다음 날 이어서 수집
DEFAULT_RANK_TOP = 10_000  # 웹에서 받을 랭킹 범위 (팀컬러·포메이션·시즌 전적, API 불필요, 500페이지 ≈ 17분)
RANK_RESTARTS = 1  # 긴 랭킹 수집 도중 정각 갱신이 일어나면 처음부터 다시 받는 횟수


@dataclass
class DailyReport:
    data_as_of: str | None = None
    ranked: int = 0
    full_ranking: str | None = None  # 2단계 전체 랭킹 수집 결과 ("상위 N명 (기준 시각)" 또는 실패 사유)
    membership_unresolved: int = 0
    waited_seconds: float = 0.0
    targets: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    api_calls: int = 0
    stopped: str | None = None
    meta_refreshed: bool = False
    usage_rows: int = 0
    prices: RefreshResult | None = None
    details: DetailResult | None = None
    ranker_stats: RankerStatsResult | None = None
    pruned: dict[str, int] = field(default_factory=dict)  # 오래돼 지운 행 (retention.py)

    def lines(self) -> list[str]:
        return [
            f"스냅샷 {self.data_as_of}: 랭킹 {self.ranked}명 (팀컬러 미확인 {self.membership_unresolved}명)",
            *([f"전체 랭킹: {self.full_ranking}"] if self.full_ranking else []),
            f"스쿼드 대상 {self.targets}명, API {self.api_calls}회"
            + (f", 대기 {self.waited_seconds / 60:.0f}분" if self.waited_seconds else "")
            + (", 메타데이터 갱신" if self.meta_refreshed else ""),
            "  " + ", ".join(f"{k} {v}" for k, v in sorted(self.statuses.items())),
            f"집계 {self.usage_rows}행" + (f" — 중단: {STOP_HINTS.get(self.stopped, self.stopped)}" if self.stopped else ""),
        ] + (
            [
                f"랭커 스탯(TOP 10,000 20경기 평균): {self.ranker_stats.saved + self.ranker_stats.empty}쌍 조회,"
                f" API {self.ranker_stats.calls}회"
                + (f", 다음 실행으로 넘김 {self.ranker_stats.remaining}쌍" if self.ranker_stats.remaining else "")
                + (f" — 중단: {self.ranker_stats.stopped}" if self.ranker_stats.stopped else "")
            ]
            if self.ranker_stats
            else []
        ) + (
            [
                f"시세: 많이 쓰는 선수 {self.prices.players}명 중 {self.prices.requests}명 갱신"
                f" (사용 시즌 카드 {self.prices.cards}장, 최근 갱신이라 건너뜀 {self.prices.skipped_fresh}명"
                + (f", 실패 {self.prices.failed}명" if self.prices.failed else "")
                + (f", 다음 실행으로 미룸 {self.prices.deferred}명" if self.prices.deferred else "") + ")"
            ]
            if self.prices
            else []
        ) + (
            [
                f"카드 상세(능력치): {self.details.fetched}장 수집"
                + (f", 다음 실행으로 넘김 {self.details.remaining}장" if self.details.remaining else "")
                + (f", 실패 {self.details.failed}장" if self.details.failed else "")
            ]
            if self.details
            else []
        ) + ([f"오래된 기록 정리: {', '.join(f'{t} {n}행' for t, n in self.pruned.items())}"] if self.pruned else [])


def run_daily(
    db: Path,
    *,
    top: int = DEFAULT_TOP,
    rank_top: int = DEFAULT_RANK_TOP,
    mode: str = "1vs1",
    wait_lag: bool = True,
    daily_limit: int = 1000,
    per_second: int = 5,
    run_budget: int | None = None,
    datacenter: DatacenterClient | None = None,
    price_players: int = DEFAULT_PLAYERS,
    price_requests: int = MAX_REQUESTS,
    detail_cards: int = DEFAULT_CARDS,
    ranker_stats_calls: int = RANKER_STATS_CALLS,
    api: NexonOpenApiClient | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep: Callable[[float], None] = time.sleep,
) -> DailyReport:
    report = DailyReport()
    db.parent.mkdir(parents=True, exist_ok=True)
    own_client = datacenter is None
    datacenter = datacenter or DatacenterClient()  # 랭킹(1단계)과 시세(7단계)에 같이 쓴다
    storage = Storage(db)
    try:
        # 1. 랭킹 TOP N (스쿼드 대상, 필수) + 2. 팀컬러 소속
        # TOP 10,000이면 약 17분이라 도중에 정각 갱신을 만날 수 있다 → 처음부터 다시
        crawl = crawl_rankings(
            datacenter, storage, RankQuery(mode=mode), max_pages=math.ceil(top / PAGE_SIZE), restarts=RANK_RESTARTS
        )
        if crawl.status == "partial":
            # 중간에 빈 페이지로 끊긴 랭킹으로 진행하면 범위가 줄어든 날이 최신이 된다 → 실패로 끝내 어제 데이터를 지킨다
            raise RuntimeError(f"ranking crawl stopped early: {crawl.rows} of {crawl.total_count} rows")
        latest = latest_unfiltered_snapshot(storage.conn, mode)
        if latest is None:
            raise RuntimeError(f"ranking crawl failed (status={crawl.status})")
        report.data_as_of = latest[0]
        report.ranked = min(top, latest[1])
        members = record_displayed_membership(storage.conn, report.data_as_of, mode, TeamColorCatalog.load())
        report.membership_unresolved = members.unresolved

        # 1-1. 전체 랭킹 TOP rank_top (분포·시즌 전적용, 실패해도 스쿼드 수집은 계속)
        if rank_top > top:
            try:
                report.full_ranking = crawl_full_ranking(storage, datacenter, rank_top, mode)
            except Exception as exc:
                log.warning("full ranking crawl failed: %s", exc)
                report.full_ranking = f"실패 ({exc})"

        # 3. API 반영 지연 대기
        ready_at = datetime.fromisoformat(report.data_as_of).astimezone(timezone.utc) + API_DATA_LAG
        wait = (ready_at - now()).total_seconds()
        if wait_lag and wait > 0:
            log.info("waiting %.0f min for Open API data (until %s)", wait / 60, ready_at.astimezone(KST))
            sleep(wait)
            report.waited_seconds = wait

        # 4. 스쿼드
        budget = CallBudget(storage.conn, daily_limit=daily_limit)
        # 메타데이터(3회)와 랭커 스탯 몫(최대 ranker_stats_calls회, 남은 예산의 10% 이하)은 남겨 둔다
        stats_reserve = min(max(ranker_stats_calls, 0), budget.remaining() // 10)
        allowed = max(budget.remaining() - META_RESERVE - stats_reserve, 0)
        budget.run_limit = min(run_budget, allowed) if run_budget is not None else allowed
        api = api or NexonOpenApiClient(budget=budget, per_second=per_second)
        api.budget = budget
        store = PipelineStore(storage.conn)
        targets = select_top_targets(storage.conn, top, mode, report.data_as_of)
        report.targets = len(targets)
        result = SquadCollector(api, store, table=FormationTable.load()).run(
            targets, {"daily": True, "top": top, "data_as_of": report.data_as_of}
        )
        report.statuses = dict(result.statuses)
        report.stopped = result.stopped

        # 5. 새 카드 메타데이터 (이름을 모르는 spId가 있으면)
        missing = storage.conn.execute(
            "SELECT COUNT(DISTINCT p.sp_id) FROM match_player p LEFT JOIN meta_spid m ON m.sp_id = p.sp_id"
            " WHERE m.sp_id IS NULL"
        ).fetchone()[0]
        if missing:
            budget.run_limit = None  # 남겨 둔 예산 사용
            try:
                for name in ("spposition", "seasonid", "spid"):
                    store.save_metadata(name, api.metadata(name))
                report.meta_refreshed = True
            except Exception as exc:  # 메타데이터 실패는 다음 날 재시도
                log.warning("metadata refresh failed: %s", exc)

        # 5-1. 쓰인 카드의 TOP 10,000 랭커 20경기 평균 스탯 (남겨 둔 예산 안에서)
        if ranker_stats_calls > 0:
            budget.run_limit = None
            try:
                report.ranker_stats = collect_ranker_stats(api, storage.conn, max_calls=ranker_stats_calls)
            except Exception as exc:
                log.warning("ranker-stats failed: %s", exc)
        report.api_calls = budget.used_this_run

        # 6. 집계
        report.usage_rows = sum(n for _, n in UsageStore(storage.conn).build_all(report.data_as_of))

        # 7. 많이 쓰이는 선수 시세 (데이터센터, 넥슨 API 한도와 무관)
        if price_players > 0:
            try:
                report.prices = refresh_used_prices(datacenter, db, limit=price_players, max_requests=price_requests)
            except Exception as exc:  # 시세 실패가 수집 결과를 막지 않게
                log.warning("price refresh failed: %s", exc)

        # 8. 쓰인 카드의 상세·능력치 (데이터센터, 한 번 받으면 30일 유지)
        if detail_cards > 0:
            try:
                report.details = refresh_card_details(datacenter, db, limit=detail_cards)
            except Exception as exc:
                log.warning("card detail refresh failed: %s", exc)

        # 9. 오래된 원본 정리 (경기 원본 14일·랭킹 30일·시세 30일, 집계는 유지)
        try:
            report.pruned = prune(storage.conn, now())
        except Exception as exc:  # 정리 실패가 수집 결과를 막지 않게
            log.warning("prune failed: %s", exc)
        return report
    finally:
        storage.close()
        if own_client:
            datacenter.close()


def crawl_full_ranking(storage: Storage, datacenter: DatacenterClient, rank_top: int, mode: str = "1vs1") -> str:
    """Crawl ranks 1..rank_top (restarting once on an hourly refresh) and record displayed team colors."""
    crawl = crawl_rankings(
        datacenter, storage, RankQuery(mode=mode), max_pages=math.ceil(rank_top / PAGE_SIZE), restarts=RANK_RESTARTS
    )
    as_of = storage.conn.execute("SELECT data_as_of FROM crawl_run WHERE id = ?", (crawl.run_id,)).fetchone()[0]
    if crawl.status != "ok":
        return f"완료 못 함 ({crawl.status}, {crawl.rows}명)"
    record_displayed_membership(storage.conn, as_of, mode, TeamColorCatalog.load())
    return f"상위 {crawl.rows}명 ({as_of})"


def status_lines(db: Path, mode: str = "1vs1", top: int = DEFAULT_TOP) -> list[str]:
    """Collection status of the latest daily snapshot (no nicknames, no API calls)."""
    if not db.exists():
        return [f"DB가 없습니다: {db}"]
    storage = Storage(db)
    try:
        conn = storage.conn
        UsageStore(conn)  # 집계 전에 멈춘 DB에도 조회할 테이블이 있도록 (CREATE IF NOT EXISTS)
        CallBudget(conn)
        latest = latest_unfiltered_snapshot(conn, mode)
        if latest is None:
            return ["필터 없이 수집한 랭킹이 없습니다 → python -m fco_meta.daily run"]
        as_of, covered = latest
        lines = [f"최근 랭킹 {as_of}: 상위 {covered}명 수집 (웹)"]
        # 스쿼드는 전체 랭킹과 다른 (조금 이른) 스냅샷에 있을 수 있다
        squad_as_of = conn.execute(
            "SELECT MAX(data_as_of) FROM ranker_squad_status WHERE mode = ?", (mode,)
        ).fetchone()[0] or as_of
        statuses = dict(conn.execute(
            "SELECT status, COUNT(*) FROM ranker_squad_status WHERE data_as_of = ? AND mode = ? AND rank <= ? GROUP BY status",
            (squad_as_of, mode, top),
        ).fetchall())  # fmt: skip
        done = sum(statuses.values())
        lines.append(
            f"스쿼드 ({squad_as_of}): 상위 {top}명 중 {done}명 처리 — "
            + (", ".join(f"{k} {v}" for k, v in sorted(statuses.items())) if statuses else "아직 없음")
        )
        by_formation = conn.execute(
            "SELECT formation, squads, combo_rankers FROM usage_sample WHERE data_as_of = ? AND mode = ?"
            " AND team_color_id = 0 AND strict = 0 AND formation != '*' ORDER BY squads DESC LIMIT 6",
            (squad_as_of, mode),
        ).fetchall()
        if by_formation:
            lines.append("포메이션별 스쿼드(전체 랭커): " + ", ".join(f"{f} {n}/{t}명" for f, n, t in by_formation))
        else:
            lines.append("집계 없음 → 스쿼드 수집 후 python -m fco_meta.analytics build")
        conn.executescript(MARKET_SCHEMA + RANKER_STATS_SCHEMA)  # 아직 안 받은 DB에서도 개수를 세도록
        cards, priced, detailed, pairs = conn.execute(
            "SELECT (SELECT COUNT(*) FROM card), (SELECT COUNT(DISTINCT spid) FROM card_price),"
            " (SELECT COUNT(*) FROM card_detail), (SELECT COUNT(*) FROM ranker_stats WHERE match_count > 0)"
        ).fetchone()
        lines.append(f"시세·급여 카드 {priced}장 (카드 정보 {cards}장), 능력치 카드 {detailed}장, 랭커 스탯 {pairs}쌍(카드×포지션)")
        for day, calls in conn.execute(
            "SELECT day, SUM(calls) FROM api_usage GROUP BY day ORDER BY day DESC LIMIT 3"
        ).fetchall():
            lines.append(f"Open API 사용 {day}: {calls}회")
        for run_id, started, status, calls in conn.execute(
            "SELECT id, started_at, status, api_calls FROM pipeline_run ORDER BY id DESC LIMIT 3"
        ).fetchall():
            lines.append(f"수집 실행 #{run_id} {started}: {status}, 호출 {calls}회")
            reason = status.removeprefix("stopped_")
            if status.startswith("stopped_") and reason in STOP_HINTS:
                lines.append(f"  └ {STOP_HINTS[reason]}")
        return lines
    finally:
        storage.close()


def next_run(at: str, current: datetime) -> datetime:
    """Next occurrence of HH:MM (KST) after `current`."""
    hour, minute = (int(x) for x in at.split(":"))
    local = current.astimezone(KST)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    load_env()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", type=Path, default=DEFAULT_DB)
    common.add_argument("--top", type=int, default=DEFAULT_TOP, help=f"스쿼드를 받을 랭킹 상위 몇 명 (기본 {DEFAULT_TOP})")
    common.add_argument(
        "--rank-top", type=int, default=DEFAULT_RANK_TOP,
        help=f"웹에서 받을 랭킹 범위 (기본 {DEFAULT_RANK_TOP}, 팀컬러·포메이션·시즌 전적, 20명당 2초, --top 이하면 생략)",
    )  # fmt: skip
    # 넥슨 키 한도: 개발 키 하루 1,000회·초당 5회, 서비스 키 하루 2천만 회·초당 500회 (.env의 NEXON_DAILY_LIMIT, NEXON_RPS)
    common.add_argument("--daily-limit", type=int, default=int(os.environ.get("NEXON_DAILY_LIMIT", 1000)), help="Open API 일일 한도")
    common.add_argument("--rps", type=int, default=int(os.environ.get("NEXON_RPS", 5)), help="Open API 초당 최대 호출")
    common.add_argument("--no-wait", action="store_true", help="API 반영 지연(2시간)을 기다리지 않음")
    common.add_argument(
        "--price-players", type=int, default=DEFAULT_PLAYERS,
        help=f"시세를 살펴볼 '많이 쓰는 선수' 수 (기본 {DEFAULT_PLAYERS} = 사실상 쓰인 선수 전부, 0이면 생략)",
    )  # fmt: skip
    common.add_argument(
        "--price-requests", type=int, default=MAX_REQUESTS,
        help=f"한 번 실행에 시세를 받을 최대 선수 수 (기본 {MAX_REQUESTS}, 선수당 약 2초. 상위 500명은 매일, 나머지는 주 1회)",
    )  # fmt: skip
    common.add_argument(
        "--ranker-stats-calls", type=int, default=RANKER_STATS_CALLS,
        help=f"랭커 스탯(TOP 10,000 20경기 평균)에 쓸 최대 API 호출 (기본 {RANKER_STATS_CALLS}, 1회 50쌍, 0이면 생략)",
    )  # fmt: skip
    common.add_argument(
        "--detail-cards", type=int, default=DEFAULT_CARDS,
        help=f"능력치 등 카드 상세를 받을 최대 카드 수 (기본 {DEFAULT_CARDS}, 0이면 생략, 카드당 2초, 받은 카드는 30일 유지)",
    )  # fmt: skip
    common.add_argument("-v", "--verbose", action="store_true")
    parser = argparse.ArgumentParser(prog="python -m fco_meta.daily")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", parents=[common], help="지금 한 번 실행")
    sub.add_parser("status", parents=[common], help="수집 상태 확인 (API 호출 없음)")
    sub.add_parser("rank", parents=[common], help="랭킹 상위 --rank-top명만 웹에서 수집 (API 키 불필요)")
    p_sched = sub.add_parser("schedule", parents=[common], help="매일 정해진 시각(KST)에 실행 — 프로세스를 켜 둔다")
    p_sched.add_argument("--at", default="00:00", help="HH:MM, 한국 시간 (기본 00:00)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def once() -> int:
        try:
            report = run_daily(
                args.db, top=args.top, rank_top=args.rank_top, wait_lag=not args.no_wait, daily_limit=args.daily_limit,
                per_second=args.rps,
                price_players=args.price_players, price_requests=args.price_requests, detail_cards=args.detail_cards,
                ranker_stats_calls=args.ranker_stats_calls,
            )  # fmt: skip
        except ValueError as exc:  # NEXON_API_KEY 없음 등
            print(f"실패: {exc}", file=sys.stderr)
            return 2
        print("\n".join(report.lines()), flush=True)
        return 0

    if args.command == "run":
        return once()
    if args.command == "status":
        print("\n".join(status_lines(args.db, top=args.top)))
        return 0
    if args.command == "rank":
        args.db.parent.mkdir(parents=True, exist_ok=True)
        storage = Storage(args.db)
        try:
            with DatacenterClient() as datacenter:
                print(f"랭킹 수집: {crawl_full_ranking(storage, datacenter, args.rank_top)}")
        finally:
            storage.close()
        return 0
    while True:
        at = next_run(args.at, datetime.now(timezone.utc))
        print(f"다음 실행: {at.astimezone(KST):%Y-%m-%d %H:%M} KST", flush=True)
        time.sleep(max((at - datetime.now(timezone.utc)).total_seconds(), 0))
        try:
            once()
        except Exception:  # 하루 실패해도 다음 날은 돈다
            log.exception("daily run failed")


if __name__ == "__main__":
    sys.exit(main())
