"""Read API keys from a `.env` file so they don't have to be exported in every shell.

    # .env (프로젝트 폴더, git에는 올라가지 않음)
    NEXON_API_KEY=...
    GEMINI_API_KEY=...

Values already set in the environment win over the file. Values are never printed.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:  # 따옴표 없는 값 뒤의 주석
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


def load_env(*paths: Path) -> list[Path]:
    """Load `.env` from the current directory and the project folder (or `paths`). Returns files read."""
    candidates = list(paths) or [Path.cwd() / ".env", PROJECT_ROOT / ".env"]
    read: list[Path] = []
    for path in dict.fromkeys(p.resolve() for p in candidates):
        if not path.is_file():
            continue
        for key, value in _parse(path.read_text(encoding="utf-8")).items():
            if value and not os.environ.get(key):
                os.environ[key] = value
        read.append(path)
    return read
