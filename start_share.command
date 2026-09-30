#!/bin/bash
# 더블클릭: 웹 서버를 켜고 Cloudflare 임시 터널로 인터넷에 연다 (지인 베타용).
# 창을 닫거나 Ctrl+C로 둘 다 끈다. 켤 때마다 공유 주소가 바뀐다.
# 처음 한 번: 터미널에서 brew install cloudflared
cd "$(dirname "$0")" || exit 1
source scripts/find_python.sh
if [ -z "$PY" ]; then read -r -p "엔터를 누르면 창이 닫힙니다" _; exit 1; fi
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared가 없습니다. 터미널에서 먼저 설치하세요:  brew install cloudflared"
  read -r -p "엔터를 누르면 창이 닫힙니다" _
  exit 1
fi

PORT="${FCO_PORT:-8000}"
URL="http://127.0.0.1:$PORT"
SERVER=""
trap 'kill $SERVER 2>/dev/null' EXIT INT TERM

# 웹 서버는 이 맥 안(127.0.0.1)에서만 열고, 밖에서는 터널로만 들어온다
if ! curl -s -o /dev/null "$URL/api/meta"; then
  echo "파이썬: $PY"
  "$PY" -m fco_meta.web --port "$PORT" &
  SERVER=$!
  for _ in $(seq 1 60); do
    curl -s -o /dev/null "$URL/" && break
    kill -0 "$SERVER" 2>/dev/null || break
    sleep 0.5
  done
  if ! kill -0 "$SERVER" 2>/dev/null; then
    echo "웹을 켜지 못했습니다. 위 메시지를 확인해 주세요."
    read -r -p "엔터를 누르면 창이 닫힙니다" _
    exit 1
  fi
fi

echo "터널을 여는 중..."
cloudflared tunnel --no-autoupdate --url "$URL" 2>&1 | while IFS= read -r line; do
  if [[ "$line" =~ (https://[a-z0-9-]+\.trycloudflare\.com) ]]; then
    echo ""
    echo "공유 주소: ${BASH_REMATCH[1]}"
    echo "이 주소를 지인에게 보내면 됩니다. (이 창을 닫으면 닫힙니다)"
    echo ""
  fi
done
