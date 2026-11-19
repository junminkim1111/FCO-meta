"""클로즈드 베타 키 만들기 (키 원문은 이 CSV에만 있다 — 저장소·서버에는 해시만 남긴다).

    python -m fco_meta.beta_keys 50 --out beta_keys.csv

CSV(key,hash)를 쓰고, Render 환경 변수 BETA_KEY_HASHES에 넣을 한 줄을 출력한다.
해시 = SHA-256(키.strip().upper()) — 서버도 같은 식으로 계산한다 (손님이 소문자로 쳐도 맞게).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import secrets
import sys
from pathlib import Path

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 0/O/1/I 제외


def key_hash(key: str) -> str:
    return hashlib.sha256(key.strip().upper().encode()).hexdigest()


def make_key() -> str:
    pick = lambda: "".join(secrets.choice(ALPHABET) for _ in range(4))  # noqa: E731
    return f"fclm-{pick()}-{pick()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fco_meta.beta_keys")
    parser.add_argument("count", type=int)
    parser.add_argument("--out", type=Path, default=Path("beta_keys.csv"))
    args = parser.parse_args(argv)
    if args.out.exists():  # 앞에 나눠 준 키 원문을 잃지 않게 덮어쓰지 않는다
        print(f"{args.out}가 이미 있습니다. 다른 --out을 쓰거나 지우고 다시 하세요.", file=sys.stderr)
        return 2
    keys: set[str] = set()
    while len(keys) < args.count:
        keys.add(make_key())
    rows = [(k, key_hash(k)) for k in sorted(keys)]
    with args.out.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([("key", "hash"), *rows])
    print(f"{args.count}개를 {args.out}에 썼습니다 (git에 올리지 마세요). Render 환경 변수:")
    print("BETA_KEY_HASHES=" + ",".join(h for _, h in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
