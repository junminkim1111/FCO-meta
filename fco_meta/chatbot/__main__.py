"""Command line chatbot.

    python -m fco_meta.chatbot                                         # 대화형 (규칙 기반, 키 불필요)
    python -m fco_meta.chatbot --ask "아스널 4-2-3-1 볼란치 2명 추천해줘"
    python -m fco_meta.chatbot --backend gemini                        # Gemini (GEMINI_API_KEY 필요)
    python -m fco_meta.chatbot --tool recommend_players '{"team_color": "아스널", "role": "DM"}'
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

from .rules import RuleBot
from .tools import Toolbox

DEFAULT_DB = Path("data/fco_meta.sqlite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fco_meta.chatbot")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--backend", choices=["rules", "gemini"], default="rules", help="rules: 규칙 기반(기본), gemini: LLM")
    parser.add_argument("--model", help="Gemini 모델 (기본: gemini-3.5-flash)")
    parser.add_argument("--ask", help="질문 하나만 하고 종료")
    parser.add_argument("--tool", nargs=2, metavar=("NAME", "JSON"), help="도구 하나를 직접 실행해 결과 출력")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not args.db.exists():
        print(f"DB가 없습니다: {args.db}", file=sys.stderr)
        return 2
    toolbox = Toolbox(sqlite3.connect(str(args.db)))

    if args.tool:
        content, is_error = toolbox.run(args.tool[0], json.loads(args.tool[1]))
        print(json.dumps(json.loads(content), ensure_ascii=False, indent=2))
        return 1 if is_error else 0

    if args.backend == "gemini":
        ask = _gemini(toolbox, args)
    else:
        rules = RuleBot(toolbox)
        ask = lambda q: rules.ask(q).text  # noqa: E731
    if ask is None:
        return 2
    if args.ask:
        print(ask(args.ask))
        return 0
    print(f"FCO 랭커 메타 챗봇 ({args.backend}). 종료: Ctrl+D 또는 'exit'")
    while True:
        try:
            question = input("\n> ").strip()
        except EOFError:
            print()
            return 0
        if question in ("exit", "quit", "종료"):
            return 0
        if question:
            print("\n" + ask(question))


def _gemini(toolbox: Toolbox, args: argparse.Namespace):
    try:
        from google import genai
        from google.genai import errors
    except ImportError:
        print('google-genai가 필요합니다: pip install -e ".[gemini]"', file=sys.stderr)
        return None
    from .gemini import DEFAULT_MODEL, GeminiChat

    try:
        client = genai.Client()  # GEMINI_API_KEY 또는 GOOGLE_API_KEY
    except ValueError as exc:
        print(f"Gemini API 키가 없습니다 (GEMINI_API_KEY): {exc}", file=sys.stderr)
        return None
    chat = GeminiChat(
        client,
        toolbox,
        model=args.model or DEFAULT_MODEL,
        on_tool_call=lambda name, tool_input: print(
            f"  · {name} {json.dumps(tool_input, ensure_ascii=False)}", file=sys.stderr
        ),
    )

    def ask(question: str) -> str:
        try:
            return chat.ask(question).text
        except errors.APIError as exc:
            return f"Gemini API 오류 ({exc.code}): {exc.message}"

    return ask


if __name__ == "__main__":
    sys.exit(main())
