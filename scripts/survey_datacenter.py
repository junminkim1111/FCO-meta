"""0단계 구조 조사: fconline 데이터센터 랭킹/팀컬러 페이지의 네트워크 요청과 표시 항목 기록.

사용법:
    pip install playwright
    python scripts/survey_datacenter.py [--out tests/fixtures/datacenter] [--headed]
    # 브라우저가 설치돼 있지 않으면 --chromium /opt/pw-browsers/chromium 처럼 경로 지정

수집 결과 (--out 아래):
    robots.txt                  robots.txt 원문
    <phase>/page.html           각 단계 직후 DOM 스냅샷
    <phase>/screenshot.png      각 단계 직후 스크린샷
    <phase>/requests.jsonl      해당 단계에서 발생한 요청 (URL, 메서드, 파라미터, 상태, content-type)
    <phase>/bodies/NNN.*        XHR/Fetch/문서 응답 본문
    <phase>/text.txt            랭킹 영역의 보이는 텍스트 (표시 항목 확인용)
    summary.json                단계별 요약과 셀렉터 탐색 성공/실패 기록

페이지 셀렉터는 아직 확인되지 않았으므로 후보 셀렉터를 순서대로 시도하고, 성공/실패를
summary.json에 남긴다. 실패한 단계는 --headed로 직접 보면서 후보를 추가하면 된다.
요청 간격은 PLAN.md 4장 원칙에 맞춰 단계 사이 2초 이상 둔다.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Page, Request, sync_playwright

BASE = "https://fconline.nexon.com"
DELAY_SEC = 2.0
BODY_TYPES = {"xhr", "fetch", "document"}
SKIP_HOST_PATTERNS = re.compile(r"(google|doubleclick|facebook|analytics|gtag|criteo|kakao)")

# 후보 셀렉터 (구조 미확인 → 앞에서부터 시도)
PAGINATION_CANDIDATES = [
    ".pagination a:has-text('2')",
    ".paging a:has-text('2')",
    "a:text-is('2')",
    "button:text-is('2')",
]
MODE_CANDIDATES = [
    "text=감독모드",
    "text=볼타",
    "a:has-text('모드')",
]
OFFICIAL_1V1_CANDIDATES = [
    "text=1vs1 공식경기",
    "text=공식경기",
]
RANKER_ROW_CANDIDATES = [
    ".rank_list .tr >> nth=0",
    ".tbody .tr >> nth=0",
    "table tbody tr >> nth=0",
    ".list_wrap li >> nth=0",
]
RANK_AREA_CANDIDATES = [".rank_list", ".board_list", "table", "#divRankingList", "main", "body"]


class Recorder:
    """단계(phase)별로 요청·응답을 기록한다."""

    def __init__(self, out: Path):
        self.out = out
        self.phase = "init"
        self.seq = 0
        self.rows: dict[str, list[dict]] = {}

    def set_phase(self, name: str) -> None:
        self.phase = name
        (self.out / name / "bodies").mkdir(parents=True, exist_ok=True)
        self.rows.setdefault(name, [])

    def on_response(self, response) -> None:
        req: Request = response.request
        split = urlsplit(req.url)
        if SKIP_HOST_PATTERNS.search(split.netloc):
            return
        row = {
            "phase": self.phase,
            "method": req.method,
            "url": req.url,
            "resource_type": req.resource_type,
            "query": parse_qs(split.query),
            "post_data": req.post_data,
            "request_headers": {
                k: v for k, v in req.headers.items()
                if k.lower() in {"content-type", "x-requested-with", "referer", "accept"}
            },
            "status": response.status,
            "content_type": response.headers.get("content-type", ""),
        }
        if req.resource_type in BODY_TYPES:
            self.seq += 1
            ext = _ext_for(row["content_type"])
            path = self.out / self.phase / "bodies" / f"{self.seq:03d}{ext}"
            try:
                path.write_bytes(response.body())
                row["body_file"] = str(path.relative_to(self.out))
            except Exception as e:  # 리다이렉트 등 본문 없는 응답
                row["body_error"] = repr(e)
        self.rows.setdefault(self.phase, []).append(row)

    def flush(self) -> None:
        for phase, rows in self.rows.items():
            with open(self.out / phase / "requests.jsonl", "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _ext_for(content_type: str) -> str:
    if "json" in content_type:
        return ".json"
    if "html" in content_type:
        return ".html"
    if "javascript" in content_type:
        return ".js"
    return ".bin"


def snapshot(page: Page, out: Path, phase: str) -> None:
    d = out / phase
    d.mkdir(parents=True, exist_ok=True)
    (d / "page.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=str(d / "screenshot.png"), full_page=True)
    for sel in RANK_AREA_CANDIDATES:
        loc = page.locator(sel).first
        if loc.count():
            (d / "text.txt").write_text(f"# selector: {sel}\n{loc.inner_text()}", encoding="utf-8")
            break


def try_click(page: Page, candidates: list[str]) -> dict:
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                loc.click(timeout=5000)
                page.wait_for_load_state("networkidle", timeout=15000)
                return {"clicked": sel}
        except Exception as e:
            return {"clicked": None, "selector": sel, "error": repr(e)}
    return {"clicked": None, "tried": candidates}


def fetch_robots(base: str, out: Path) -> dict:
    try:
        with urllib.request.urlopen(f"{base}/robots.txt", timeout=15) as r:
            body = r.read()
        (out / "robots.txt").write_bytes(body)
        return {"status": r.status, "bytes": len(body)}
    except Exception as e:
        return {"error": repr(e)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/fixtures/datacenter")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--chromium", default=None, help="Chromium 실행 파일 경로")
    ap.add_argument("--base", default=BASE, help="테스트용 기준 URL")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary: dict = {"robots": fetch_robots(base, out), "phases": {}}

    rec = Recorder(out)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, executable_path=args.chromium)
        ctx = browser.new_context(locale="ko-KR", viewport={"width": 1440, "height": 1000})
        ctx.on("response", rec.on_response)  # 팝업 창의 요청까지 포함
        page = ctx.new_page()
        popups: list[Page] = []
        ctx.on("page", lambda pg: popups.append(pg))

        def phase(name: str, action) -> None:
            rec.set_phase(name)
            result = action() or {}
            time.sleep(DELAY_SEC)
            target = popups[-1] if popups and result.get("popup") else page
            snapshot(target, out, name)
            summary["phases"][name] = {"url": target.url, **result}

        phase("01_rank_initial", lambda: page.goto(f"{base}/datacenter/rank", wait_until="networkidle") and None)
        phase("02_official_1v1", lambda: try_click(page, OFFICIAL_1V1_CANDIDATES))
        phase("03_page_2", lambda: try_click(page, PAGINATION_CANDIDATES))
        phase("04_mode_switch", lambda: try_click(page, MODE_CANDIDATES))
        # 모드 전환 후 1vs1 공식경기로 복귀
        phase("05_back_to_1v1", lambda: try_click(page, OFFICIAL_1V1_CANDIDATES))

        def click_ranker() -> dict:
            before = len(popups)
            res = try_click(page, RANKER_ROW_CANDIDATES)
            if len(popups) > before:
                popups[-1].wait_for_load_state("networkidle", timeout=15000)
                res["popup"] = True
            return res

        phase("06_ranker_click", click_ranker)
        phase("07_teamcolor", lambda: page.goto(f"{base}/datacenter/teamcolor", wait_until="networkidle") and None)

        browser.close()

    rec.flush()
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
