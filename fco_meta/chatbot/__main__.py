"""Command line chatbot.

    python -m fco_meta.chatbot                                  # 대화형
    python -m fco_meta.chatbot --ask "아스널 4-2-3-1 볼란치 2명 추천해줘"
    python -m fco_meta.chatbot --tool recommend_players '{"team_color": "아스널", ...}'   # 모델 없이 도구만 실행

Claude API 키는 ANTHROPIC_API_KEY (또는 `ant auth login` 프로필)로 읽습니다.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

from .bot import DEFAULT_MODEL, Chatbot
from .tools import Toolbox

DEFAULT_DB = Path("data/fco_meta.sqlite")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fco_meta.chatbot")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="기본: 모델 기본값")
    parser.add_argument("--no-fallbacks", action="store_true", help="거절 시 서버 측 대체 모델 실행 끄기")
    parser.add_argument("--ask", help="질문 하나만 하고 종료")
    parser.add_argument("--tool", nargs=2, metavar=("NAME", "JSON"), help="모델 없이 도구 하나를 실행해 결과 출력")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not args.db.exists():
        print(f"DB가 없습니다: {args.db}", file=sys.stderr)
        return 2
    toolbox = Toolbox(sqlite3.connect(str(args.db)))

    if args.tool:
        content, is_error = toolbox.run(args.tool[0], json.loads(args.tool[1]))
        print(json.dumps(json.loads(content), ensure_ascii=False, indent=2))
        return 1 if is_error else 0

    import anthropic

    bot = Chatbot(
        anthropic.Anthropic(),
        toolbox,
        model=args.model,
        effort=args.effort,
        fallbacks=not args.no_fallbacks,
        on_tool_call=lambda name, tool_input: print(
            f"  · {name} {json.dumps(tool_input, ensure_ascii=False)}", file=sys.stderr
        ),
    )
    questions = [args.ask] if args.ask else None
    try:
        if questions:
            print(bot.ask(questions[0]).text)
            return 0
        print("FCO 랭커 메타 챗봇입니다. 종료: Ctrl+D 또는 'exit'")
        while True:
            try:
                question = input("\n> ").strip()
            except EOFError:
                print()
                return 0
            if question in ("exit", "quit", "종료"):
                return 0
            if question:
                print("\n" + bot.ask(question).text)
    except anthropic.AuthenticationError:
        print("Claude API 인증 실패: ANTHROPIC_API_KEY를 확인하세요", file=sys.stderr)
        return 2
    except anthropic.APIConnectionError as exc:
        print(f"Claude API 연결 실패: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
