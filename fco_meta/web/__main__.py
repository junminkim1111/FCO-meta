"""Run the web UI.

    python -m fco_meta.web                          # http://127.0.0.1:8000 (규칙 기반 챗봇)
    python -m fco_meta.web --backend gemini         # 챗봇을 Gemini로 (GEMINI_API_KEY 필요)
    python -m fco_meta.web --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_DB = Path("data/fco_meta.sqlite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fco_meta.web")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--host", default="127.0.0.1", help="외부에 열려면 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--backend", choices=["rules", "gemini"], default="rules")
    parser.add_argument("--model", help="Gemini 모델")
    args = parser.parse_args(argv)

    try:
        import uvicorn

        from .app import create_app
    except ImportError:
        print('웹 의존성이 필요합니다: pip install -e ".[web]"', file=sys.stderr)
        return 2
    if not args.db.exists():
        print(f"DB가 없습니다: {args.db}", file=sys.stderr)
        return 2
    if args.backend == "gemini":
        try:
            from google import genai

            genai.Client()
        except ImportError:
            print('google-genai가 필요합니다: pip install -e ".[gemini]"', file=sys.stderr)
            return 2
        except ValueError:
            print("GEMINI_API_KEY가 없습니다", file=sys.stderr)
            return 2
    uvicorn.run(create_app(args.db, backend=args.backend, gemini_model=args.model), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
