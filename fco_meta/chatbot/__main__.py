"""Command line chatbot.

    python -m fco_meta.chatbot                                         # 대화형 (GPT-6 Luna, 키 없으면 규칙 기반)
    python -m fco_meta.chatbot --ask "아스널 4-2-3-1 볼란치 2명 추천해줘"
    python -m fco_meta.chatbot --backend rules                         # 규칙 기반 (키 불필요)
    python -m fco_meta.chatbot --tool recommend_players '{"team_color": "아스널", "roles": ["DM"]}'
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

from ..config import load_env
from .rules import RuleBot
from .tools import Toolbox

DEFAULT_DB = Path("data/fco_meta.sqlite")


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="python -m fco_meta.chatbot")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--backend", choices=["llm", "rules"], default="llm",
        help="llm: GPT-6 Luna (기본, .env의 OPENROUTER_API_KEY 필요 — 없으면 규칙 기반), rules: 규칙 기반",
    )  # fmt: skip
    parser.add_argument("--model", help="OpenRouter 모델 (기본: openai/gpt-6-luna)")
    parser.add_argument("--ask", help="질문 하나만 하고 종료")
    parser.add_argument("--tool", nargs=2, metavar=("NAME", "JSON"), help="도구 하나를 직접 실행해 결과 출력")
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

    backend = args.backend
    if backend == "llm" and not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_COMPAT_API_KEY")):
        print("OPENROUTER_API_KEY가 없어 규칙 기반으로 실행합니다 (.env에 OPENROUTER_API_KEY=... 추가)", file=sys.stderr)
        backend = "rules"
    if backend == "llm":
        ask = _llm(toolbox, args)
    else:
        rules = RuleBot(toolbox)
        ask = lambda q: rules.ask(q).text  # noqa: E731
    if args.ask:
        print(ask(args.ask))
        return 0
    print(f"FCO 랭커 메타 챗봇 ({'GPT-6 Luna' if backend == 'llm' else '규칙 기반'}). 종료: Ctrl+D 또는 'exit'")
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


def _llm(toolbox: Toolbox, args: argparse.Namespace):
    import httpx

    from .llm import LUNA_MODEL, OpenAIChat, ToolCall, describe_error

    chat = OpenAIChat(httpx.Client(), toolbox, model=args.model or LUNA_MODEL,
                      extra={"reasoning": {"enabled": True}, "provider": {"only": ["openai"]}})  # 웹의 기본 모델과 같은 설정  # fmt: skip

    def ask(question: str) -> str:
        try:
            stream = chat.ask_stream(question)
            while True:
                try:
                    piece = next(stream)
                except StopIteration as stop:
                    turn = stop.value
                    break
                if isinstance(piece, ToolCall):
                    print(f"  · {piece.name} {json.dumps(piece.args, ensure_ascii=False)}", file=sys.stderr)
            return turn.text + "".join(f"\n[근거] {e}" for e in turn.evidence)  # 개발용 CLI라 근거도 보여 준다
        except Exception as exc:  # noqa: BLE001 — 원인을 보여 주고 대화는 계속
            logging.getLogger(__name__).debug("llm failed", exc_info=True)
            return f"⚠ {describe_error(exc)}"

    return ask


if __name__ == "__main__":
    sys.exit(main())
