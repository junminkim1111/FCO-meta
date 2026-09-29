#!/bin/bash
# 더블클릭: 웹 서버를 켜고 브라우저를 연다. 창을 닫거나 Ctrl+C로 종료.
cd "$(dirname "$0")" || exit 1
source scripts/find_python.sh
if [ -z "$PY" ]; then read -r -p "엔터를 누르면 창이 닫힙니다" _; exit 1; fi

PORT="${FCO_PORT:-8000}"
URL="http://127.0.0.1:$PORT"
open_browser() {
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1
  else echo "브라우저에서 $URL 을 여세요"; fi
}

# 이미 켜져 있으면 브라우저만 연다
if curl -s -o /dev/null "$URL/api/meta"; then
  echo "이미 실행 중입니다: $URL"
  open_browser
  exit 0
fi

echo "파이썬: $PY"
"$PY" -m fco_meta.web --port "$PORT" &
SERVER=$!
trap 'kill "$SERVER" 2>/dev/null' EXIT INT TERM

for _ in $(seq 1 60); do
  curl -s -o /dev/null "$URL/" && break
  kill -0 "$SERVER" 2>/dev/null || break
  sleep 0.5
done

if kill -0 "$SERVER" 2>/dev/null; then
  open_browser
  echo ""
  echo "웹이 켜졌습니다: $URL  (끝내려면 이 창을 닫거나 Ctrl+C)"
  wait "$SERVER"
else
  echo ""
  echo "웹을 켜지 못했습니다. 위 메시지를 확인해 주세요."
  echo "(DB가 없다면 먼저 run_daily.command로 수집)"
  read -r -p "엔터를 누르면 창이 닫힙니다" _
fi
