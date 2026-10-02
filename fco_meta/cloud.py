"""Run the web on a host without a persistent disk (Render free): the DB lives in a private Hugging Face dataset.

    python -m fco_meta.cloud pull-db   # 데이터셋의 DB를 data/로 받는다 (GitHub Actions 매일 수집의 첫 단계, 맥에서 최신본 받기)
    python -m fco_meta.cloud push-db   # 매일 수집 뒤: DB를 데이터셋에 올리고 Render에 재배포를 요청
    python -m fco_meta.cloud serve     # 서버에서 (Render 시작 명령): 데이터셋의 DB를 받아 웹을 연다

맥 .env: HF_TOKEN(쓰기 권한), 선택으로 HF_DATA_REPO(기본 <계정>/fclm-data), RENDER_DEPLOY_HOOK(Render의 Deploy Hook 주소).
서버 환경 변수: HF_DATA_REPO, HF_TOKEN(쓰기 권한 — 답변 기록을 logs/에 올림), GEMINI_API_KEY, ADMIN_KEY(/admin 비밀번호). 포트는 PORT(Render가 넣어 줌).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_env

DB = Path("data/fco_meta.sqlite")
DB_NAME = "fco_meta.sqlite"  # 데이터셋 안 파일 이름


def data_repo(api: Any) -> str:
    return os.environ.get("HF_DATA_REPO") or f"{api.whoami()['name']}/fclm-data"


def snapshot(db: Path, dest: Path) -> None:
    """A consistent copy of the DB even if another process is writing to it."""
    src, out = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(dest)
    with out:
        src.backup(out)
    src.close()
    out.close()


def push_db(api: Any, post: Any, db: Path = DB) -> None:
    repo = data_repo(api)
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)  # 랭커 데이터는 비공개
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / DB_NAME
        snapshot(db, copy)
        api.upload_file(path_or_fileobj=str(copy), path_in_repo=DB_NAME, repo_id=repo, repo_type="dataset",
                        commit_message=f"DB {datetime.now():%Y-%m-%d %H:%M}")  # fmt: skip
    api.super_squash_history(repo, repo_type="dataset")  # 매일 27MB씩 쌓이지 않게 최신 DB만 남긴다
    print(f"DB를 올렸습니다: {repo} (비공개)")
    if hook := os.environ.get("RENDER_DEPLOY_HOOK"):
        post(hook).raise_for_status()  # 다시 켜질 때 새 DB를 받는다
        print("Render에 재배포를 요청했습니다 (몇 분 걸림)")


def pull_db(repo: str) -> str:
    """Download the dataset's DB to data/fco_meta.sqlite (replacing a local one); returns its path."""
    from huggingface_hub import hf_hub_download

    return hf_hub_download(repo, DB_NAME, repo_type="dataset", local_dir=str(DB.parent))


def serve(port: int) -> int:
    from .web.__main__ import main as web

    # 받기는 따로 프로세스에서: 다운로더(xet)가 300MB 넘게 쓰는데, 같은 프로세스면 웹을 여는 동안에도 그 메모리가
    # 남아 Render 무료(512MB)를 넘긴다. 따로 돌리면 끝날 때 모두 반납된다.
    repo = os.environ["HF_DATA_REPO"]
    subprocess.run([sys.executable, "-c", f"from fco_meta.cloud import pull_db; pull_db({repo!r})"], check=True)
    return web(["--db", str(DB), "--host", "0.0.0.0", "--port", str(port), "--log-to-dataset", repo])


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="python -m fco_meta.cloud")
    parser.add_argument("command", choices=["pull-db", "push-db", "serve"])
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = parser.parse_args(argv)
    try:
        from huggingface_hub import HfApi
    except ImportError:
        print('필요한 패키지: pip install -e ".[cloud]"', file=sys.stderr)
        return 2
    if args.command == "serve":
        return serve(args.port)
    if not os.environ.get("HF_TOKEN"):
        print(".env에 HF_TOKEN(쓰기 권한 토큰)이 필요합니다: https://huggingface.co/settings/tokens", file=sys.stderr)
        return 2
    if args.command == "pull-db":
        print(f"DB를 받았습니다: {pull_db(data_repo(HfApi()))}")
        return 0
    import httpx

    push_db(HfApi(), httpx.post)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
