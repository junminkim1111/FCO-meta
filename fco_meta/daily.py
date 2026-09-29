"""Daily collection: ranking TOP N → squads → usage stats.

    python -m fco_meta.daily run --top 330            # 지금 한 번
    python -m fco_meta.daily schedule --at 00:00      # 매일 00:00 (KST)에 반복 (프로세스를 띄워 둔다)
    python -m fco_meta.daily status                   # 수집 상태 확인

Steps
1. 필터 없이 랭킹 상위 N명 크롤링 (20명/페이지, 2초 간격)
2. 표시된 팀컬러·엠블럼으로 팀컬러 소속 기록 (crawler/membership.py)
3. Open API 반영 지연(2시간)이 지날 때까지 대기 — 스냅샷 직전 경기가 API에 들어오도록
4. 상위 N명 스쿼드 수집 (오늘 남은 호출 예산 안에서, 순위 순)
5. 새 카드가 있으면 선수·시즌 메타데이터 갱신
6. 이 스냅샷의 usage_stats 재계산
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import load_env
from .analytics import UsageStore
from .crawler import DatacenterClient, RankQuery, crawl_rankings
from .crawler.membership import record_displayed_membership
from .crawler.models import PAGE_SIZE
from .crawler.teamcolors import TeamColorCatalog
from .openapi import CallBudget, NexonOpenApiClient
from .pipeline import FormationTable, PipelineStore, SquadCollector, select_top_targets
from .pipeline.squads import API_DATA_LAG
from .storage import Storage, latest_unfiltered_snapshot

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
DEFAULT_DB = Path("data/fco_meta.sqlite")
META_RESERVE = 3  # 메타데이터 갱신용으로 남겨 둘 호출 수


@dataclass
class DailyReport:
    data_as_of: str | None = None
    ranked: int = 0
    membership_unresolved: int = 0
    waited_seconds: float = 0.0
    targets: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    api_calls: int = 0
    stopped: str | None = None
    meta_refreshed: bool = False
    usage_rows: int = 0

    def lines(self) -> list[str]:
        return [
            f"스냅샷 {self.data_as_of}: 랭킹 {self.ranked}명 (팀컬러 미확인 {self.membership_unresolved}명)",
            f"스쿼드 대상 {self.targets}명, API {self.api_calls}회"
            + (f", 대기 {self.waited_seconds / 60:.0f}분" if self.waited_seconds else "")
            + (", 메타데이터 갱신" if self.meta_refreshed else ""),
            "  " + ", ".join(f"{k} {v}" for k, v in sorted(self.statuses.items())),
            f"집계 {self.usage_rows}행" + (f" — 중단: {self.stopped} (다음 실행에서 이어서 수집)" if self.stopped else ""),
        ]


def run_daily(
    db: Path,
    *,
    top: int = 330,
    mode: str = "1vs1",
    wait_lag: bool = True,
    daily_limit: int = 1000,
    run_budget: int | None = None,
    datacenter: DatacenterClient | None = None,
    api: NexonOpenApiClient | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep: Callable[[float], None] = time.sleep,
) -> DailyReport:
    report = DailyReport()
    db.parent.mkdir(parents=True, exist_ok=True)
    storage = Storage(db)
    try:
        # 1. 랭킹 TOP N
        own_client = datacenter is None
        datacenter = datacenter or DatacenterClient()
        try:
            crawl = crawl_rankings(datacenter, storage, RankQuery(mode=mode), max_pages=math.ceil(top / PAGE_SIZE))
        finally:
            if own_client:
                datacenter.close()
        latest = latest_unfiltered_snapshot(storage.conn, mode)
        if latest is None:
            raise RuntimeError(f"ranking crawl failed (status={crawl.status})")
        report.data_as_of = latest[0]
        report.ranked = min(top, latest[1])

        # 2. 팀컬러 소속
        members = record_displayed_membership(storage.conn, report.data_as_of, mode, TeamColorCatalog.load())
        report.membership_unresolved = members.unresolved

        # 3. API 반영 지연 대기
        ready_at = datetime.fromisoformat(report.data_as_of).astimezone(timezone.utc) + API_DATA_LAG
        wait = (ready_at - now()).total_seconds()
        if wait_lag and wait > 0:
            log.info("waiting %.0f min for Open API data (until %s)", wait / 60, ready_at.astimezone(KST))
            sleep(wait)
            report.waited_seconds = wait

        # 4. 스쿼드
        budget = CallBudget(storage.conn, daily_limit=daily_limit)
        allowed = max(budget.remaining() - META_RESERVE, 0)
        budget.run_limit = min(run_budget, allowed) if run_budget is not None else allowed
        api = api or NexonOpenApiClient(budget=budget)
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
        report.api_calls = budget.used_this_run

        # 6. 집계
        report.usage_rows = sum(n for _, n in UsageStore(storage.conn).build_all(report.data_as_of))
        return report
    finally:
        storage.close()


def status_lines(db: Path, mode: str = "1vs1") -> list[str]:
    """Collection status of the latest daily snapshot (no nicknames, no API calls)."""
    if not db.exists():
        return [f"DB가 없습니다: {db}"]
    storage = Storage(db)
    try:
        conn = storage.conn
        latest = latest_unfiltered_snapshot(conn, mode)
        if latest is None:
            return ["필터 없이 수집한 랭킹이 없습니다 → python -m fco_meta.daily run"]
        as_of, covered = latest
        lines = [f"최근 스냅샷 {as_of}: 랭킹 상위 {covered}명 수집"]
        statuses = dict(conn.execute(
            "SELECT status, COUNT(*) FROM ranker_squad_status WHERE data_as_of = ? AND mode = ? AND rank <= ? GROUP BY status",
            (as_of, mode, covered),
        ).fetchall())  # fmt: skip
        done = sum(statuses.values())
        lines.append(
            f"스쿼드: {done}/{covered}명 처리 — "
            + (", ".join(f"{k} {v}" for k, v in sorted(statuses.items())) if statuses else "아직 없음")
        )
        by_formation = conn.execute(
            "SELECT formation, squads, combo_rankers FROM usage_sample WHERE data_as_of = ? AND mode = ?"
            " AND team_color_id = 0 AND strict = 0 AND formation != '*' ORDER BY squads DESC LIMIT 6",
            (as_of, mode),
        ).fetchall()
        if by_formation:
            lines.append("포메이션별 스쿼드(전체 랭커): " + ", ".join(f"{f} {n}/{t}명" for f, n, t in by_formation))
        else:
            lines.append("집계 없음 → 스쿼드 수집 후 python -m fco_meta.analytics build")
        for day, calls in conn.execute(
            "SELECT day, SUM(calls) FROM api_usage GROUP BY day ORDER BY day DESC LIMIT 3"
        ).fetchall():
            lines.append(f"Open API 사용 {day}: {calls}회")
        for run_id, started, status, calls in conn.execute(
            "SELECT id, started_at, status, api_calls FROM pipeline_run ORDER BY id DESC LIMIT 3"
        ).fetchall():
            lines.append(f"수집 실행 #{run_id} {started}: {status}, 호출 {calls}회")
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
    common.add_argument("--top", type=int, default=330, help="랭킹 상위 몇 명 (개발 키 하루 약 330명)")
    common.add_argument("--daily-limit", type=int, default=1000, help="Open API 일일 한도")
    common.add_argument("--no-wait", action="store_true", help="API 반영 지연(2시간)을 기다리지 않음")
    common.add_argument("-v", "--verbose", action="store_true")
    parser = argparse.ArgumentParser(prog="python -m fco_meta.daily")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", parents=[common], help="지금 한 번 실행")
    sub.add_parser("status", parents=[common], help="수집 상태 확인 (API 호출 없음)")
    p_sched = sub.add_parser("schedule", parents=[common], help="매일 정해진 시각(KST)에 실행 — 프로세스를 켜 둔다")
    p_sched.add_argument("--at", default="00:00", help="HH:MM, 한국 시간 (기본 00:00)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def once() -> int:
        try:
            report = run_daily(args.db, top=args.top, wait_lag=not args.no_wait, daily_limit=args.daily_limit)
        except ValueError as exc:  # NEXON_API_KEY 없음 등
            print(f"실패: {exc}", file=sys.stderr)
            return 2
        print("\n".join(report.lines()), flush=True)
        return 0

    if args.command == "run":
        return once()
    if args.command == "status":
        print("\n".join(status_lines(args.db)))
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
