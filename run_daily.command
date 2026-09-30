#!/bin/bash
# 더블클릭: 오늘 수집(랭킹 상위 1,000명 → 스쿼드·경기 기록 → 랭커 스탯 → 집계 → 시세 → 카드 상세)을 한 번 실행하고 상태를 보여 준다.
cd "$(dirname "$0")" || exit 1
source scripts/find_python.sh
if [ -z "$PY" ]; then read -r -p "엔터를 누르면 창이 닫힙니다" _; exit 1; fi

echo "파이썬: $PY"
echo "수집을 시작합니다. 처음 며칠은 1시간 가까이 걸릴 수 있습니다 (중간에 창을 닫으면 멈춤, 다시 실행하면 이어서 수집)."
echo ""
"$PY" -m fco_meta.daily run --top "${FCO_TOP:-1000}"
COLLECTED=$?
echo ""
echo "── 현재 상태 ──"
"$PY" -m fco_meta.daily status
echo ""

# 배포한 웹(Render)에 새 DB 올리기 — .env에 HF_TOKEN이 있을 때만 (python -m fco_meta.cloud push-db)
if grep -q '^HF_TOKEN=.' .env 2>/dev/null || [ -n "${HF_TOKEN:-}" ]; then
  if [ "$COLLECTED" -eq 0 ]; then
    echo "── 배포한 웹에 새 DB 올리기 ──"
    "$PY" -m fco_meta.cloud push-db || echo "DB를 올리지 못했습니다. 위 메시지를 확인해 주세요."
  else
    echo "수집이 끝까지 되지 않아 배포한 웹에는 올리지 않았습니다 (다시 실행하면 이어서 수집 후 올림)."
  fi
  echo ""
fi
read -r -p "엔터를 누르면 창이 닫힙니다" _
