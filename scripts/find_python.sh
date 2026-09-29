# shellcheck shell=bash
# 이 프로젝트를 설치한 파이썬을 찾아 $PY에 넣는다 (더블클릭 실행에서는 conda activate가 안 된 상태라서).
# 순서: FCO_PYTHON 환경 변수 → conda 환경 'fco-meta' → 저장소의 .venv → PATH의 python3
_fco_candidates=()
[ -n "${FCO_PYTHON:-}" ] && _fco_candidates+=("$FCO_PYTHON")
for base in "$HOME/anaconda3" "$HOME/miniconda3" "$HOME/miniforge3" "$HOME/mambaforge" \
            /opt/anaconda3 /opt/miniconda3 /opt/homebrew/anaconda3 /opt/homebrew/Caskroom/miniconda/base \
            /usr/local/anaconda3 /usr/local/miniconda3; do
  _fco_candidates+=("$base/envs/${FCO_CONDA_ENV:-fco-meta}/bin/python")
done
if command -v conda >/dev/null 2>&1; then
  _fco_candidates+=("$(conda info --base 2>/dev/null)/envs/${FCO_CONDA_ENV:-fco-meta}/bin/python")
fi
_fco_candidates+=("$PWD/.venv/bin/python" "$(command -v python3 2>/dev/null)")

PY=""
for c in "${_fco_candidates[@]}"; do
  if [ -n "$c" ] && [ -x "$c" ] && "$c" -c "import fco_meta" >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done
unset _fco_candidates

if [ -z "$PY" ]; then
  echo "fco_meta가 설치된 파이썬을 찾지 못했습니다."
  echo "  conda activate fco-meta && pip install -e \".[web]\"  를 먼저 실행하거나,"
  echo "  FCO_PYTHON=/경로/python 으로 직접 지정해 주세요."
fi
