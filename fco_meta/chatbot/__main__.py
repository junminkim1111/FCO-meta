"""Command line chatbot.

    python -m fco_meta.chatbot                                         # 대화형 (Gemini, 키 없으면 규칙 기반)
    python -m fco_meta.chatbot --ask "아스널 4-2-3-1 볼란치 2명 추천해줘"
    python -m fco_meta.chatbot --backend rules                         # 규칙 기반 (키 불필요)
    python -m fco_meta.chatbot --tool recommend_players '{"team_color": "아스널", "role": "DM"}'
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

from ..config import load_env
from .gemini import choose_backend
from .rules import RuleBot
from .tools import Toolbox

DEFAULT_DB = Path("data/fco_meta.sqlite")


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="python -m fco_meta.chatbot")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--backend", choices=["gemini", "rules"], default="gemini",
        help="gemini: LLM (기본, .env의 GEMINI_API_KEY 필요 — 없으면 규칙 기반), rules: 규칙 기반",
    )  # fmt: skip
    parser.add_argument("--model", help="Gemini 모델 (기본: .env의 GEMINI_MODEL 또는 gemini-3.5-flash-lite)")
    parser.add_argument("--ask", help="질문 하나만 하고 종료")
    parser.add_argument("--tool", nargs=2, metavar=("NAME", "JSON"), help="도구 하나를 직접 실행해 결과 출력")
    parser.add_argument("--list-models", action="store_true", help="이 Gemini 키로 쓸 수 있는 채팅 모델 목록")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.list_models:
        return _list_models()
    if not args.db.exists():
        print(f"DB가 없습니다: {args.db}", file=sys.stderr)
        return 2
    toolbox = Toolbox(sqlite3.connect(str(args.db)))

    if args.tool:
        content, is_error = toolbox.run(args.tool[0], json.loads(args.tool[1]))
        print(json.dumps(json.loads(content), ensure_ascii=False, indent=2))
        return 1 if is_error else 0

    backend, notice = choose_backend(args.backend)
    if notice:
        print(notice, file=sys.stderr)
    if backend == "gemini":
        ask = _gemini(toolbox, args)
    else:
        rules = RuleBot(toolbox)
        ask = lambda q: rules.ask(q).text  # noqa: E731
    if ask is None:
        return 2
    if args.ask:
        print(ask(args.ask))
        return 0
    print(f"FCO 랭커 메타 챗봇 ({'Gemini' if backend == 'gemini' else '규칙 기반'}). 종료: Ctrl+D 또는 'exit'")
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


def _list_models() -> int:
    from .gemini import available_models, describe_error, models_from_env, unavailable_reason

    if reason := unavailable_reason():
        print(reason, file=sys.stderr)
        return 2
    from google import genai

    try:
        names = available_models(genai.Client())
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ {describe_error(exc)}", file=sys.stderr)
        return 1
    primary, fallbacks = models_from_env()
    print(f"설정: 기본 {primary}, 대체 {', '.join(fallbacks) or '없음'}")
    print("이 키로 쓸 수 있는 채팅 모델 (추천 순):")
    for name in names:
        print(f"  {name}")
    print("\n.env에 GEMINI_MODEL=<이름>으로 지정할 수 있습니다.")
    return 0


def _gemini(toolbox: Toolbox, args: argparse.Namespace):
    try:
        from google import genai
    except ImportError:
        print('google-genai가 필요합니다: pip install -e ".[web]"', file=sys.stderr)
        return None
    from .gemini import GeminiChat, describe_error, models_from_env

    try:
        client = genai.Client()  # GEMINI_API_KEY 또는 GOOGLE_API_KEY
    except ValueError as exc:
        print(f"Gemini API 키가 없습니다 (GEMINI_API_KEY): {exc}", file=sys.stderr)
        return None
    primary, fallbacks = models_from_env(args.model)
    chat = GeminiChat(
        client,
        toolbox,
        model=primary,
        fallback_models=fallbacks,
        on_tool_call=lambda name, tool_input: print(
            f"  · {name} {json.dumps(tool_input, ensure_ascii=False)}", file=sys.stderr
        ),
    )

    def ask(question: str) -> str:
        try:
            turn = chat.ask(question)
            text = turn.text + "".join(f"\n[근거] {e}" for e in turn.evidence)  # 개발용 CLI라 근거·모델도 보여 준다
            if chat.last_model and chat.last_model != chat.model:
                text += f"\n\n({chat.model}가 혼잡해 {chat.last_model}로 답했습니다)"
            return text
        except Exception as exc:  # noqa: BLE001 — 원인을 보여 주고 대화는 계속
            logging.getLogger(__name__).debug("gemini failed", exc_info=True)
            return f"⚠ {describe_error(exc)}"

    return ask


if __name__ == "__main__":
    sys.exit(main())
