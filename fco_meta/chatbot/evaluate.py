"""Run a fixed question set through the chatbot and write a review report.

    python -m fco_meta.chatbot.evaluate                        # Gemini, eval/questions.txt → reports/chatbot-eval-*.md
    python -m fco_meta.chatbot.evaluate --backend rules
    python -m fco_meta.chatbot.evaluate --questions my.txt     # 한 줄에 "질문 || 기대 동작" (기대 동작은 생략 가능)
    python -m fco_meta.chatbot.evaluate --only 1-5,12          # 일부 질문만

For each question the report shows the tool calls, the answer, and numbers in the answer that no
tool result contains ("확인 필요") — a quick way to spot made-up figures.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import load_env
from .gemini import choose_backend
from .numbers import unsupported_numbers
from .prompt import SYSTEM_PROMPT
from .rules import RuleBot
from .tools import Toolbox

DEFAULT_DB = Path("data/fco_meta.sqlite")
DEFAULT_OUT_DIR = Path("reports")

DEFAULT_QUESTIONS = Path(__file__).resolve().parents[2] / "eval" / "questions.txt"


def load_questions(path: Path) -> list[tuple[str, str | None]]:
    """(질문, 기대 동작) per non-comment line of "질문 || 기대 동작"."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        question, _, expected = line.partition("||")
        out.append((question.strip(), expected.strip() or None))
    return out


def select(questions: list, only: str | None) -> list[tuple[int, Any]]:
    """1-based numbering kept; `only` like "1-5,12"."""
    numbered = list(enumerate(questions, 1))
    if not only:
        return numbered
    wanted: set[int] = set()
    for part in only.split(","):
        lo, _, hi = part.partition("-")
        wanted.update(range(int(lo), int(hi or lo) + 1))
    return [(i, q) for i, q in numbered if i in wanted]


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(prog="python -m fco_meta.chatbot.evaluate")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--backend", choices=["gemini", "claude", "openai", "rules"], default="gemini",
                        help="모델 비교용: claude (.env의 ANTHROPIC_API_KEY), openai = OpenAI 호환 API "
                             "(기본 OpenRouter, .env의 OPENROUTER_API_KEY; --model 예: qwen/qwen3.8-flash)")
    parser.add_argument("--model", help="모델 (기본: Gemini는 .env의 GEMINI_MODEL, Claude는 claude-sonnet-5-5)")
    parser.add_argument("--extra", type=json.loads, default=None,
                        help='openai: 요청 본문에 더할 JSON (예: \'{"reasoning": {"enabled": false}}\')')
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS, help="질문 파일 (기본 eval/questions.txt)")
    parser.add_argument("--only", help="이 번호의 질문만 (예: 1-5,12)")
    parser.add_argument("--out", type=Path, help=f"보고서 경로 (기본: {DEFAULT_OUT_DIR}/chatbot-eval-날짜.md)")
    parser.add_argument("--pause", type=float, default=4.0, help="질문 사이 대기(초) — Gemini 분당 한도 여유")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"DB가 없습니다: {args.db}", file=sys.stderr)
        return 2
    questions = select(load_questions(args.questions), args.only)

    toolbox = Toolbox(sqlite3.connect(str(args.db)))
    results: list[Any] = []  # 이번 질문의 도구 결과 (숫자 대조용)
    run_tool = toolbox.run

    def recording_run(name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        content, is_error = run_tool(name, tool_input)
        results.append(json.loads(content))
        return content, is_error

    toolbox.run = recording_run  # type: ignore[method-assign]

    backend, notice = choose_backend(args.backend)
    if notice:
        print(notice, file=sys.stderr)
    if backend == "gemini":
        from google import genai

        from .gemini import GeminiChat, describe_error, models_from_env

        primary, fallbacks = models_from_env(args.model)
        client = genai.Client()
    elif backend in ("claude", "openai"):
        import os

        from .gemini import describe_error
        from .llm import ClaudeChat, OpenAIChat

        if backend == "claude":
            if not os.environ.get("ANTHROPIC_API_KEY"):
                print(".env에 ANTHROPIC_API_KEY가 필요합니다 (https://console.anthropic.com 에서 발급)", file=sys.stderr)
                return 2
            import anthropic

            client = anthropic.Anthropic()
        else:
            if not args.model or not (os.environ.get("OPENAI_COMPAT_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
                print("--model과 .env의 OPENROUTER_API_KEY(또는 OPENAI_COMPAT_API_KEY)가 필요합니다", file=sys.stderr)
                return 2
            import httpx

            client = httpx.Client()
    tokens: Counter[str] = Counter()

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = args.out or DEFAULT_OUT_DIR / f"chatbot-eval-{stamp}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = [f"# 챗봇 평가 ({backend}, {datetime.now():%Y-%m-%d %H:%M})", "", f"DB: `{args.db}` · 질문 {len(questions)}개", ""]
    flagged = 0
    for n, (i, (question, expected)) in enumerate(questions, 1):
        results.clear()
        started = time.monotonic()
        calls: list[tuple[str, dict[str, Any]]] = []
        model, used = None, Counter()
        if backend in ("gemini", "claude", "openai"):
            if backend == "gemini":  # 질문마다 새 대화
                chat = GeminiChat(client, toolbox, model=primary, fallback_models=fallbacks)
            elif backend == "claude":
                chat = ClaudeChat(client, toolbox, model=args.model)
            else:
                chat = OpenAIChat(client, toolbox, model=args.model, extra=args.extra)
            try:
                turn = chat.ask(question)
                answer, calls, model = turn.text, turn.tool_calls, chat.last_model
            except Exception as exc:  # 한 질문 실패가 전체 평가를 멈추지 않게
                answer = f"⚠ {describe_error(exc)}"
            used = chat.tokens
            tokens.update(used)
        else:
            a = RuleBot(toolbox).ask(question)
            answer, calls = a.text, [(a.tool, a.tool_input)] if a.tool else []
        elapsed = time.monotonic() - started
        # 지시문의 예시 문장("레알 5억 미만 공격수…")을 그대로 쓴 안내는 지어낸 수치가 아니다
        missing = unsupported_numbers(answer, [*results, SYSTEM_PROMPT], question)
        flagged += bool(missing)
        print(f"[{n}/{len(questions)}] #{i} {question} — {elapsed:.1f}s, 도구 {len(calls)}회" + (f", 확인 필요 {missing}" if missing else ""))

        report += [f"## {i}. {question}", ""]
        meta = f"{elapsed:.1f}초" + (f" · {model}" if model else "")
        report.append(f"- {meta}")
        if used:
            report.append("- 토큰: " + ", ".join(f"{k} {v:,}" for k, v in used.items()))
        if expected:
            report.append(f"- 기대: {expected}")
        for name, tool_input in calls:
            report.append(f"- 도구 `{name}` `{json.dumps(tool_input, ensure_ascii=False)}`")
        report.append(f"- 확인 필요 숫자: {', '.join(missing) if missing else '없음'}")
        report += ["", "```text", answer, "```", ""]
        if backend == "gemini" and n < len(questions):
            time.sleep(args.pause)

    report.insert(3, f"확인 필요 숫자가 있는 답: {flagged}/{len(questions)}")
    if tokens:  # 비용 비교용 (모델 가격 × 토큰)
        report.insert(4, "토큰 합계: " + ", ".join(f"{k} {v:,}" for k, v in tokens.items()))
    out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"보고서: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
