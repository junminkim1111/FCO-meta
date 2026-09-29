#!/bin/bash
# 더블클릭: 오늘 수집(랭킹 상위 300명 → 스쿼드·경기 기록 → 랭커 스탯 → 집계 → 시세 → 카드 상세)을 한 번 실행하고 상태를 보여 준다.
cd "$(dirname "$0")" || exit 1
source scripts/find_python.sh
if [ -z "$PY" ]; then read -r -p "엔터를 누르면 창이 닫힙니다" _; exit 1; fi

echo "파이썬: $PY"
echo "수집을 시작합니다. 처음 며칠은 1시간 가까이 걸릴 수 있습니다 (중간에 창을 닫으면 멈춤, 다시 실행하면 이어서 수집)."
echo ""
"$PY" -m fco_meta.daily run --top "${FCO_TOP:-300}"
echo ""
echo "── 현재 상태 ──"
"$PY" -m fco_meta.daily status
echo ""
read -r -p "엔터를 누르면 창이 닫힙니다" _
