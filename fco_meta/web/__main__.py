"""Run the web UI.

    python -m fco_meta.web                          # http://127.0.0.1:8000 (챗봇: Gemini, 키 없으면 규칙 기반)
    python -m fco_meta.web --backend rules          # 챗봇을 규칙 기반으로
    python -m fco_meta.web --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from ..config import load_env

DEFAULT_DB = Path("data/fco_meta.sqlite")


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="python -m fco_meta.web")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--host", default="127.0.0.1", help="외부에 열려면 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--backend", choices=["gemini", "rules"], default="gemini", help="챗봇 (기본 gemini)")
    parser.add_argument("--model", help="Gemini 모델")
    args = parser.parse_args(argv)
    # 챗봇 도구 호출·재시도 로그를 서버 터미널에 남긴다 (문제 확인용)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("fco_meta").setLevel(logging.INFO)

    try:
        import uvicorn

        from .app import create_app
    except ImportError:
        print('웹 의존성이 필요합니다: pip install -e ".[web]"', file=sys.stderr)
        return 2
    if not args.db.exists():
        print(f"DB가 없습니다: {args.db}", file=sys.stderr)
        return 2
    from ..chatbot.gemini import choose_backend

    backend, notice = choose_backend(args.backend)
    if notice:
        print(notice, file=sys.stderr)
    print(f"http://{args.host}:{args.port} 에서 열립니다 (챗봇: {'Gemini' if backend == 'gemini' else '규칙 기반'})", flush=True)
    app = create_app(args.db, backend=backend, gemini_model=args.model)
    threading.Thread(target=app.state.warm, daemon=True).start()  # 첫 화면 조회를 미리 (요청은 그동안에도 받는다)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
