"""Ranker snapshot → ouid → recent official matches → squad."""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..openapi import (
    OFFICIAL_MATCH,
    BudgetExceededError,
    DataNotReadyError,
    MaintenanceError,
    NexonOpenApiClient,
    NotFoundError,
    OpenApiError,
    RateLimitError,
)
from ..storage import latest_unfiltered_snapshot, unfiltered_coverage
from .formation import FormationTable, signature
from .store import PipelineStore

log = logging.getLogger(__name__)

# Open API는 매시 정각에 2시간 전까지의 경기를 반영한다 → 스냅샷 직후에는 마지막 경기가 아직 없을 수 있음
API_DATA_LAG = timedelta(hours=2)


def match_id_time(match_id: str) -> datetime | None:
    """Creation time embedded in a matchId (ObjectId-style: first 4 bytes = Unix seconds).

    Observed to be 1–21 minutes *before* `matchDate` (it is created when the match starts), so it
    is only used one way: an id time at/after the snapshot means the match ended after it too.
    """
    try:
        return datetime.fromtimestamp(int(match_id[:8], 16), timezone.utc) if len(match_id) == 24 else None
    except ValueError:
        return None


@dataclass(frozen=True)
class SquadTarget:
    data_as_of: str  # ranker_snapshot.data_as_of (KST, ISO 8601)
    mode: str
    rank: int
    nickname: str
    formation: str | None

    @property
    def as_of_utc(self) -> datetime:
        return datetime.fromisoformat(self.data_as_of).astimezone(timezone.utc)


@dataclass
class SquadRunResult:
    run_id: int
    targets: int
    statuses: Counter[str] = field(default_factory=Counter)
    cached: int = 0
    api_calls: int = 0
    stopped: str | None = None  # budget | maintenance | rate_limit

    def summary(self) -> dict[str, object]:
        return {
            "targets": self.targets,
            "cached": self.cached,
            "statuses": dict(self.statuses),
            "api_calls": self.api_calls,
            "stopped": self.stopped,
        }


def select_targets(
    conn: sqlite3.Connection,
    team_color_id: int,
    formation: str | None,
    mode: str = "1vs1",
    data_as_of: str | None = None,
) -> list[SquadTarget]:
    """Rankers of the latest (or given) snapshot who belong to `team_color_id` and play `formation`."""
    where = "m.team_color_id = ? AND s.mode = ?"
    args: list[object] = [team_color_id, mode]
    if formation:
        where += " AND s.formation = ?"
        args.append(formation)
    if data_as_of is None:
        row = conn.execute(
            f"SELECT MAX(s.data_as_of) FROM ranker_snapshot s JOIN ranker_team_color m USING (data_as_of, mode, rank)"
            f" WHERE {where}",
            args,
        ).fetchone()
        data_as_of = row[0]
        if data_as_of is None:
            return []
    rows = conn.execute(
        f"SELECT s.data_as_of, s.mode, s.rank, s.nickname, s.formation"
        f" FROM ranker_snapshot s JOIN ranker_team_color m USING (data_as_of, mode, rank)"
        f" WHERE {where} AND s.data_as_of = ? ORDER BY s.rank",
        [*args, data_as_of],
    ).fetchall()
    return [SquadTarget(*r) for r in rows]


def select_top_targets(
    conn: sqlite3.Connection, top: int, mode: str = "1vs1", data_as_of: str | None = None
) -> list[SquadTarget]:
    """Rankers 1..`top` of the latest (or given) snapshot that an unfiltered crawl covered."""
    if data_as_of is None:
        latest = latest_unfiltered_snapshot(conn, mode)
        if latest is None:
            return []
        data_as_of = latest[0]
    limit = min(top, unfiltered_coverage(conn, data_as_of, mode))
    rows = conn.execute(
        "SELECT data_as_of, mode, rank, nickname, formation FROM ranker_snapshot"
        " WHERE data_as_of = ? AND mode = ? AND rank <= ? ORDER BY rank",
        (data_as_of, mode, limit),
    ).fetchall()
    return [SquadTarget(*r) for r in rows]


class SquadCollector:
    """Collect squads for ranking snapshot rows.

    For each ranker: nickname → ouid (cached) → `user/match` (newest first, cached per snapshot)
    → `match-detail` (cached by matchId) until the most recent match played before the snapshot
    time is found. That match is the base squad (`match_order` 0). With `extra_matches` > 0 the
    next older matches are also stored, but only accepted when their inferred formation equals
    the snapshot formation (or their starting positions equal the base squad's).
    """

    def __init__(
        self,
        api: NexonOpenApiClient,
        store: PipelineStore,
        *,
        table: FormationTable | None = None,
        extra_matches: int = 0,
        list_limit: int = 10,
        max_details: int = 5,
        retry_failed: bool = False,
        now: datetime | None = None,
    ):
        self.api = api
        self.store = store
        self.table = table or FormationTable()
        self.extra_matches = extra_matches
        self.list_limit = list_limit
        self.max_details = max_details
        self.retry_failed = retry_failed
        self.now = now

    def run(self, targets: list[SquadTarget], params: dict[str, object] | None = None) -> SquadRunResult:
        run_id = self.store.start_run(params or {})
        result = SquadRunResult(run_id, len(targets))
        calls_before = self.api.budget.used_this_run
        self._warn_if_too_fresh(targets)
        for t in targets:
            done = self.store.squad_status(t.data_as_of, t.mode, t.rank)
            if done == "ok" or (done in ("nickname_not_found", "no_match_before_snapshot") and not self.retry_failed):
                result.cached += 1
                result.statuses[done] += 1
                continue
            try:
                status, ouid, detail = self.collect_one(t, run_id)
            except BudgetExceededError as exc:
                result.stopped = "budget"
                log.warning("stopping: %s", exc)
                break
            except MaintenanceError as exc:
                result.stopped = "maintenance"
                log.warning("stopping: %s", exc)
                break
            except RateLimitError as exc:
                result.stopped = "rate_limit"
                log.warning("stopping: %s", exc)
                break
            except DataNotReadyError as exc:
                status, ouid, detail = "data_not_ready", None, str(exc)
            except OpenApiError as exc:
                status, ouid, detail = "error", None, str(exc)
            self.store.save_squad_status(t.data_as_of, t.mode, t.rank, ouid, status, detail)
            result.statuses[status] += 1
            log.info("rank %d: %s %s", t.rank, status, detail or "")
        result.api_calls = self.api.budget.used_this_run - calls_before
        run_status = f"stopped_{result.stopped}" if result.stopped else "ok"
        self.store.finish_run(run_id, result.api_calls, run_status, result.summary())
        return result

    def resolve_ouid(self, nickname: str) -> str | None:
        cached = self.store.cached_lookup(nickname)
        if cached is not None and (cached["status"] == "ok" or not self.retry_failed):
            return cached["ouid"]
        try:
            ouid = self.api.get_ouid(nickname)
        except NotFoundError as exc:
            self.store.save_lookup(nickname, None, "not_found", exc.code)
            return None
        self.store.save_lookup(nickname, ouid, "ok")
        return ouid

    def collect_one(self, t: SquadTarget, run_id: int | None = None) -> tuple[str, str | None, str | None]:
        ouid = self.resolve_ouid(t.nickname)
        if ouid is None:
            return "nickname_not_found", None, None

        as_of = t.as_of_utc
        # 반영 지연 이후에 받은 목록만 스냅샷 이전 경기를 모두 담고 있다고 본다
        complete_after = as_of + API_DATA_LAG
        match_ids = self.store.cached_match_list(ouid, OFFICIAL_MATCH, fetched_after=complete_after)
        provisional = False
        if match_ids is None:
            provisional = self._now() < complete_after
            try:
                match_ids = self.api.user_matches(ouid, OFFICIAL_MATCH, limit=self.list_limit)
            except NotFoundError:
                # ouid가 바뀌었을 수 있음 → 다음 실행에서 닉네임부터 다시 조회
                self.store.forget_lookup(t.nickname)
                raise
            self.store.save_match_list(ouid, OFFICIAL_MATCH, match_ids, fetched_at=self._now())

        chosen: list[tuple[int, str, str, str | None, bool, bool]] = []
        base_sig = None
        new_details = 0
        skipped_after = 0
        for match_id in match_ids:
            if len(chosen) > self.extra_matches:
                break
            started = match_id_time(match_id)
            if started is not None and started >= as_of:
                # 매치 id의 시각(경기 시작 무렵)이 이미 스냅샷 이후 → 상세를 받지 않고 건너뜀
                skipped_after += 1
                continue
            if not self.store.has_match(match_id):
                if new_details >= self.max_details:
                    break
                self.store.save_match(self.api.match_detail(match_id))
                new_details += 1
            played = self.store.match_date(match_id)
            if played is None or played >= as_of:
                skipped_after += 1
                continue
            players = self.store.players(match_id, ouid)
            if not players:  # 상대가 먼저 나가 내 기록이 없는 경기
                continue
            positions = [p.sp_position for p in players]
            inferred = self.table.infer(positions)
            formation_match = inferred is not None and inferred == t.formation
            if not chosen:
                base_sig = signature(positions)
                accepted = True
            else:
                accepted = formation_match or signature(positions) == base_sig
            chosen.append((len(chosen), ouid, match_id, inferred, formation_match, accepted))

        if not chosen:
            detail = f"{len(match_ids)} matches listed, {skipped_after} after snapshot"
            return "no_match_before_snapshot", ouid, detail
        self.store.save_squad(t.data_as_of, t.mode, t.rank, chosen, run_id)
        base = chosen[0]
        # provisional: 스냅샷 직전 경기가 아직 API에 없을 수 있음 → 다음 실행에서 경기 목록부터 다시 확인
        return ("provisional" if provisional else "ok"), ouid, f"base {base[2]} ({base[3]})"

    def _now(self) -> datetime:
        return self.now or datetime.now(timezone.utc)

    def _warn_if_too_fresh(self, targets: list[SquadTarget]) -> None:
        if not targets:
            return
        now = self._now()
        as_of = targets[0].as_of_utc
        if now - as_of < API_DATA_LAG:
            log.warning(
                "snapshot %s is less than %s old: Open API may not have the rankers' latest matches yet"
                " (squads are saved as provisional and re-checked on the next run)",
                targets[0].data_as_of,
                API_DATA_LAG,
            )
