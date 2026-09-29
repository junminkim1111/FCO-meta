# 인수인계 — FCO 랭커 메타 챗봇

> 다음 Claude Code 세션에게: 이 파일을 먼저 끝까지 읽고 이어서 작업하십시오.
> 설계 상세는 `docs/PLAN.md`, 조사 자료는 `docs/RESEARCH.md`, 사용법은 `README.md`에 있습니다.

작성: 2026-09-29 (클라우드 세션 → 로컬 Mac Claude Code로 이전)
브랜치: `claude/fco-ranker-chatbot-plan-tyzgns` (마지막 커밋 `d8fa335` 웹 화면 개선) · 테스트 166개 통과

---

## 0. 작업 규칙 (사용자 요청 사항)

- **사용자에게는 항상 한국어 존댓말로 답합니다.** (반말 금지)
- **API 키 값을 출력하거나 커밋하지 않습니다.** 키는 `.env`(gitignore됨)나 환경 변수에만 둡니다.
  - `NEXON_API_KEY`: 넥슨 Open API (개발 키: 초당 5회, **하루 1,000회**)
  - `GEMINI_API_KEY` (또는 `GOOGLE_API_KEY`): 챗봇
- **Nexon Open API를 함부로 호출하지 않습니다.** 하루 1,000회 한도를 사용자와 같이 씁니다.
  클라우드 세션에서 테스트하느라 한도를 다 써서 사용자의 수집이 막힌 적이 있습니다.
  실제 호출이 필요하면 먼저 물어보십시오. 개발과 검증은 fixture와 `httpx.MockTransport` 테스트로 합니다.
- 데이터센터 페이지(fconline.nexon.com) 크롤링은 요청 간격을 지키면 괜찮습니다.
- fixture에 넣는 실제 응답은 닉네임과 ouid를 **익명화**합니다. DB(`data/fco_meta.sqlite`)는 커밋하지 않습니다.
- 커밋하기 전에 `set -o pipefail; python -m pytest -q | tail -1`로 테스트를 돌립니다.
  파이프만 쓰면 실패가 가려져서 깨진 커밋을 올린 적이 있습니다.
- PR은 사용자가 요청할 때만 만듭니다.

## 1. 사용자 환경

- Mac에서 conda 환경 `fco-meta`(`/opt/anaconda3`)를 씁니다. 필요한 패키지는 `pip install -e ".[web,dev]"`로 설치합니다.
- 더블클릭 실행 파일은 `scripts/find_python.sh`로 conda 파이썬을 찾습니다. `FCO_PYTHON` 환경 변수로 경로를 직접 지정할 수도 있습니다.
  - `start_web.command`: 웹 서버를 띄우고 브라우저를 엽니다.
  - `run_daily.command`: 매일 수집(`daily run`)과 상태 확인(`daily status`)을 차례로 실행합니다.
- 사용자는 명령어 입력보다 더블클릭 실행을 선호합니다.
- 한때 저장소가 아닌 폴더(`FCO_MATA`)에서 실행해 `No module named 'fco_meta'`와 `fatal: 깃 저장소가 아닙니다` 오류가 났습니다.
  반드시 clone한 저장소 폴더 안에서 실행해야 합니다.

## 2. 현재 상태와 바로 할 일

1. **사용자의 매일 수집 결과 확인 (최우선)**
   - 사용자 DB는 스쿼드가 1/340명만 수집된 상태입니다. 원인은 클라우드 세션이 같은 키의 한도를 소진한 것입니다(`stopped_rate_limit`).
   - 사용자가 KST 자정 이후 `run_daily.command`를 다시 돌리고 결과를 알려주기로 했습니다.
   - 확인 방법: `python -m fco_meta.daily status`. 스냅샷, 수집 상태, 포메이션별 표본, 오늘 API 사용량, 중단 사유가 나옵니다.
   - 문제가 있으면 `STOP_HINTS`(pipeline/squads.py)의 설명과 로그를 보고 대응합니다.
2. **웹 화면 이미지 확인**
   - 선수 사진과 시즌 아이콘은 클라우드 환경에서 넥슨 CDN이 막혀 확인하지 못했습니다.
   - 시즌 아이콘: 메타데이터의 `season_img` 값(`ssl.nexon.com/.../season/*.png`)이라 맞을 가능성이 높습니다.
   - 선수 사진: 관례적인 경로라 틀릴 수 있습니다.
     - 1순위 `https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/playersAction/p{spid}.png`
     - 실패하면 `.../common/players/p{pid}.png`
     - 그것도 실패하면 이름 첫 글자를 표시합니다.
   - 관련 코드: `fco_meta/web/static/index.html`의 `avatar()`.
   - 로컬에서 사진이 안 뜨면 올바른 경로를 찾아 고칩니다.
3. **남은 로드맵**
   - 알림(수집 실패 시)
   - 서비스 키로 전환하면 `--top`을 확대
   - 서비스로 공개하기 전 이용약관 확인

## 3. 구조 요약

```
fco_meta/
  crawler/     데이터센터 랭킹 크롤러 (rank_inner), 팀컬러 목록, membership.py(표시 팀컬러·엠블럼으로 소속 추정)
  market/      시세 크롤러 (선수 검색 PlayerList, 시세 이력), refresh.py(많이 쓰인 선수 150명 시세 갱신, 쓰인 시즌 카드만 저장)
  openapi/     Open API 클라이언트 (초당 제한, CallBudget = api_usage 테이블에 KST 일자별 사용량, 재시도)
  pipeline/    닉네임→ouid→user/match→match-detail로 스쿼드 수집, 포지션 조합→포메이션 추론
  analytics/   usage.py: usage_sample / usage_stats 집계 (team_color_id 0 = 전체 랭커, formation '*' = 전체)
  chatbot/     tools.py(Toolbox: 도구 5개), rules.py(규칙 기반), gemini.py(Gemini 함수 호출), prompt.py
  web/         FastAPI + static/index.html (바닐라 JS)
  daily.py     매일 작업: 크롤→소속→API 지연 대기→스쿼드→메타 갱신→집계→시세
  config.py    load_env(): cwd/.env와 프로젝트 .env 읽기 (기존 환경 변수 우선)
  storage.py   SQLite 스키마, unfiltered_coverage / latest_unfiltered_snapshot
tests/         pytest (네트워크 없음, fixtures/openapi/는 익명화된 실제 응답)
```

### 주요 명령

```bash
python -m fco_meta.daily run [--top 330 --no-wait --price-players 150 -v]   # 매일 수집 (옵션은 서브커맨드 뒤)
python -m fco_meta.daily status
python -m fco_meta.daily schedule --at 00:00                                 # KST 자정마다 실행 (프로세스 상주)
python -m fco_meta.pipeline squads --top 330 | meta | budget | formations
python -m fco_meta.analytics ...                                              # README '집계' 참고
python -m fco_meta.market used --limit 150
python -m fco_meta.chatbot [--backend gemini|rules] [--list-models] [--ask "..."]
python -m fco_meta.web [--backend rules] [--port 8000]
```

## 4. 알아둘 사실과 함정

### 데이터
- 랭킹 페이지 스냅샷은 약 2.5시간 늦게 갱신되고, Open API 반영은 약 2시간 늦습니다.
  그래서 자정에 실행하면 대개 대기 없이 바로 수집됩니다.
- 랭커 1명당 호출: `id` 1회(ouid 캐시 후 0회) + `user/match` 1회 + `match-detail` 몇 회.
  하루 1,000회로 약 330명을 수집합니다. 일일 수집에서는 메타데이터 갱신용 3회를 남겨 둡니다(META_RESERVE).
- matchId의 앞 4바이트는 경기 시작 시각(unix)입니다. 이 시각이 스냅샷 이후인 경기는 상세 호출 없이 건너뜁니다.
- 팀컬러 소속은 필터 조회 결과(`source='filter'`)를 표시 기반 추정(`'display'`)보다 우선합니다.
  같은 이름의 클럽/국가(예: 대한민국)는 데이터가 있는 쪽으로 해석하고 "(국가)/(클럽)"을 붙여 구분합니다.
- 표본이 MIN_SAMPLE(10) 미만이면 그 팀컬러의 전체 포메이션으로 폴백합니다. `strict`는 추론 포메이션이 페이지 포메이션과 일치하는 스쿼드만 씁니다.
- 시세는 데이터센터 검색이 spid로 조회되지 않아 선수 이름으로 검색합니다.
  응답에 모든 시즌 카드가 오지만 랭커가 쓴 카드만 저장하고, 20시간 안에 받은 것은 건너뜁니다.

### Gemini
- 기본 모델은 `gemini-3.8-flash`, 폴백은 `gemini-3.5-flash`입니다.
  `gemini-2.5-flash`는 신규 사용자에게 404가 납니다.
  환경 변수 `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`로 바꿀 수 있습니다.
- 오류 처리:
  - 503/500/504는 재시도한 뒤 다음 모델로 넘어갑니다.
  - 404는 바로 다음 모델로 넘어갑니다.
  - 429는 RetryInfo 대기 시간이 15초 이하면 한 번 기다리고, 일일 한도(PerDay)면 다음 모델로 넘어갑니다.
  - 설정된 모델이 모두 실패하면 `models.list()`로 찾은 모델을 최대 3개 더 시도합니다.
- 도구 호출은 최대 10라운드입니다. 넘으면 도구 없이 최종 답을 받고, 같은 도구 호출이 반복되면 실행하지 않습니다.
- 웹에서 Gemini가 실패하면 "⚠ 원인"을 보여 주고 규칙 기반으로 답합니다. HTTP 500은 내지 않습니다.
- Claude API 백엔드는 사용자 요청으로 제거했습니다.

### 개발
- `pkill -f`나 `pgrep -f`는 자기 셸 명령까지 잡을 수 있습니다. `ps aux | awk '/[p]attern/{print $2}'`를 쓰십시오.
- 웹 서버는 DB를 읽기 전용으로 엽니다. 테이블이 없는 DB에서도 `status`나 `market used`가 죽지 않도록 테이블 존재를 확인합니다.
- 차트 색은 dataviz 팔레트를 검증한 값을 씁니다(라이트 `#2a78d6`, 다크 `#3987e5`). 텍스트는 데이터 색을 쓰지 않습니다.

## 5. 최근 사용자 요청 흐름 (맥락)

2~6단계 구현 → Gemini로 전환 → `.env` 키 저장 → 매일 자정 상위 330명 수집 → 수집 중단 사유 안내
→ 많이 쓰인 선수 시세 갱신(쓰인 시즌만) → Mac 더블클릭 실행 → 웹 화면 개선(포메이션 분포, 선수 사진, 시즌 아이콘)
→ **다음: 사용자의 자정 이후 수집 결과 피드백 대응**
