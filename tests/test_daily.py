from datetime import datetime, timezone

import httpx
from test_pipeline import F442, F4231, FakeApi, detail

from fco_meta.crawler import DatacenterClient
from fco_meta.daily import next_run, run_daily
from fco_meta.openapi import NexonOpenApiClient
from fco_meta.storage import Storage

AS_OF_UTC = datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc)  # 픽스처 기준 시각 20:00 KST


def clients(tmp_path, fixture_html):
    dc = DatacenterClient(
        httpx.Client(
            base_url="https://fconline.nexon.com",
            # 랭킹은 1페이지(20명)까지만 있고 그 뒤는 빈 페이지 — 전체 랭킹 수집도 20명에서 끝난다
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text=fixture_html(
                "rank_inner_1vs1_p1.html" if r.url.params.get("n4pageno", "1") == "1" else "rank_inner_past_last_page.html"
            ))),
        ),
        min_interval=0,
        sleep=lambda s: None,
    )
    # 닉네임은 픽스처에서 읽어 가짜 API에 등록
    from fco_meta.crawler.parser import parse_rank_inner

    rows = parse_rank_inner(fixture_html("rank_inner_1vs1_p1.html")).rows
    fake = FakeApi({}, {}, {})
    for r in rows:
        ouid = f"ouid-{r.rank}"
        fake.users[r.nickname] = ouid
        fake.matches[ouid] = [f"m{r.rank}"]
        squad = F4231 if r.formation == "4-2-3-1" else F442
        fake.details[f"m{r.rank}"] = detail(f"m{r.rank}", "2026-09-28T10:30:00", ouid, squad)

    def handler(request):
        if request.url.path.startswith("/static/fconline/meta/"):
            name = request.url.path.rsplit("/", 1)[-1]
            fake.calls.append(request.url.path)
            data = {
                "spposition.json": [{"spposition": 0, "desc": "GK"}],
                "seasonid.json": [{"seasonId": 101, "className": "ICON", "seasonImg": ""}],
                "spid.json": [{"id": 101000011, "name": "볼란치L"}],
            }[name]
            return httpx.Response(200, json=data)
        return fake(request)

    api = NexonOpenApiClient(
        "k", httpx.Client(base_url="https://open.api.nexon.com", transport=httpx.MockTransport(handler)), sleep=lambda s: None
    )
    return dc, api, fake


def test_daily_run_waits_for_api_lag_then_collects(tmp_path, fixture_html):
    dc, api, fake = clients(tmp_path, fixture_html)
    slept = []
    report = run_daily(
        tmp_path / "db.sqlite", top=10, datacenter=dc, api=api,
        now=lambda: datetime(2026, 9, 28, 11, 30, tzinfo=timezone.utc), sleep=slept.append,
    )  # fmt: skip

    assert slept == [5400.0]  # 기준 시각 + 2시간 까지
    assert report.data_as_of == "2026-09-28T20:00:00+09:00" and report.ranked == 10 and report.targets == 10
    assert report.full_ranking == "상위 20명 (2026-09-28T20:00:00+09:00)"  # 전체 랭킹 단계 (픽스처는 20명뿐)
    assert report.statuses == {"ok": 10} and report.stopped is None
    # 스쿼드 30회 + 메타데이터 3회 + 랭커 스탯 1회 (쓰인 카드·포지션 쌍 50개 이하)
    assert report.meta_refreshed and report.api_calls == 30 + 3 + 1
    assert report.ranker_stats.calls == 1 and report.ranker_stats.saved == report.ranker_stats.pairs > 0
    assert report.usage_rows > 0

    storage = Storage(tmp_path / "db.sqlite")
    shown = storage.conn.execute("SELECT COUNT(DISTINCT rank) FROM ranker_team_color WHERE source = 'display'").fetchone()[0]
    assert shown == 20  # 크롤링한 20명 모두 팀컬러 소속 추정
    assert storage.conn.execute("SELECT COUNT(*) FROM usage_sample WHERE team_color_id = 0").fetchone()[0] > 0


def test_daily_run_stays_within_budget_and_resumes(tmp_path, fixture_html):
    dc, api, fake = clients(tmp_path, fixture_html)
    later = lambda: datetime(2026, 9, 29, tzinfo=timezone.utc)  # noqa: E731
    first = run_daily(tmp_path / "db.sqlite", top=10, daily_limit=25, datacenter=dc, api=api, now=later)
    # 25 - 메타데이터 예비 3 - 랭커 스탯 예비 2(남은 예산의 10%) = 20회 → 6명(18회) 수집 후 중단, 남은 예비분으로 메타데이터·랭커 스탯
    assert first.stopped == "budget" and first.statuses == {"ok": 6}
    assert first.api_calls <= 25 and first.ranker_stats.calls == 1

    dc, api, _ = clients(tmp_path, fixture_html)
    second = run_daily(tmp_path / "db.sqlite", top=10, daily_limit=1000, datacenter=dc, api=api, now=later)
    assert second.stopped is None and second.statuses == {"ok": 10}
    assert second.api_calls < 30  # 앞서 받은 6명은 건너뜀


def test_no_wait_flag(tmp_path, fixture_html):
    dc, api, _ = clients(tmp_path, fixture_html)
    slept = []
    run_daily(tmp_path / "db.sqlite", top=5, wait_lag=False, datacenter=dc, api=api,
              now=lambda: AS_OF_UTC, sleep=slept.append)  # fmt: skip
    assert slept == []


def test_next_run_is_kst():
    # 2026-09-28 14:59 UTC = 23:59 KST → 다음 00:00 KST = 15:00 UTC
    assert next_run("00:00", datetime(2026, 9, 28, 14, 59, tzinfo=timezone.utc)) == datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
    assert next_run("00:00", datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)) == datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)
    assert next_run("12:30", datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)) == datetime(2026, 9, 28, 3, 30, tzinfo=timezone.utc)


def test_cli_accepts_options_after_subcommand(monkeypatch, tmp_path):
    import fco_meta.daily as daily

    seen = {}
    monkeypatch.setattr(daily, "run_daily", lambda db, **kw: seen.update(db=db, **kw) or daily.DailyReport())
    assert daily.main(["run", "--top", "50", "--no-wait", "--db", str(tmp_path / "x.sqlite")]) == 0
    assert seen["top"] == 50 and seen["wait_lag"] is False


def test_status_lines(tmp_path, fixture_html):
    from fco_meta.daily import status_lines

    assert status_lines(tmp_path / "none.sqlite")[0].startswith("DB가 없습니다")
    dc, api, _ = clients(tmp_path, fixture_html)
    run_daily(tmp_path / "db.sqlite", top=10, datacenter=dc, api=api, now=lambda: datetime(2026, 9, 29, tzinfo=timezone.utc))
    text = "\n".join(status_lines(tmp_path / "db.sqlite"))
    assert "상위 20명 수집 (웹)" in text and "상위 10명 중 10명 처리 — ok 10" not in text
    assert "상위 10000명 중 10명 처리 — ok 10" in text  # 기본 스쿼드 범위 10,000명 중 (랭킹이 20명뿐이라 10명)
    assert "상위 10명 중 10명 처리 — ok 10" in "\n".join(status_lines(tmp_path / "db.sqlite", top=10))
    assert "포메이션별 스쿼드(전체 랭커):" in text and "Open API 사용" in text
    assert "랭커" in text and "ouid" not in text  # 개인 식별 정보 없음


def test_status_explains_rate_limit_stop(tmp_path):
    from fco_meta.daily import status_lines
    from fco_meta.pipeline import PipelineStore

    storage = Storage(tmp_path / "db.sqlite")
    storage.conn.execute(
        "INSERT INTO crawl_run (started_at, mode, query_json, data_as_of, rows_saved, status)"
        " VALUES ('x', '1vs1', '{\"mode\": \"1vs1\"}', '2026-09-28T22:00:00+09:00', 20, 'ok')"
    )
    store = PipelineStore(storage.conn)
    run = store.start_run({})
    store.finish_run(run, 8, "stopped_rate_limit", {})
    storage.close()
    text = "\n".join(status_lines(tmp_path / "db.sqlite"))
    assert "stopped_rate_limit" in text and "일일 한도(1,000회)가 이미 소진됐을 수 있음" in text
