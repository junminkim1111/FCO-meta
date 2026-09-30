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
   - 확인 방법: `python -m fco_meta.daily status
python -m fco_meta.daily rank                                                 # 랭킹 상위 10,000명만 웹에서 (약 17분)`. 스냅샷, 수집 상태, 포메이션별 표본, 오늘 API 사용량, 중단 사유가 나옵니다.
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
3. **챗봇 답변 품질 개선 (2026-09-29 로컬 세션, 진행 중)**
   - 사용자 요청: 스쿼드 전체 추천, 선수 비교·대체, 예산·가성비, 메타 동향. 불만: 못 알아듣는 질문, 수치 신뢰. Gemini 우선.
   - 추가: `recommend_squad`, `get_meta_trends`, `recommend_players(sort="price")`, `get_player_detail` 범위·역할 요약·추이,
     Gemini 답 끝 `[근거]` 줄(도구 결과로 생성), 프롬프트 개편, 포메이션 `4231` 표기·역할 별칭, 규칙 기반 스쿼드·메타 의도.
   - 질문 유형을 늘리는 대신 **범용 조회 `query_squads`**(`analytics/query.py`)를 둠: 원본 선발 명단을 조건으로 거르고 묶어 집계.
     전용 도구는 계산 과정이 복잡한 것(스쿼드 예산 맞추기, 추이 비교)만. 자주 막히는 질문은 평가 보고서로 찾아 보강.
   - 이어서 추가: 팀컬러 별칭 파일(`data/team_color_aliases.json`, AC밀란 = 밀라노 FC는 사용자 확인), 묶음 포지션(윙어 = RW+LW …),
     급여(카드 table의 salary) 필터·합계 한도, `recommend_squad(slots=…)` 조합 최적화, 시즌 전적 승률(`season_win_rate`),
     포지션별 평균 시세·급여, 카드 상세 수집(`market/details.py`)과 능력치 조회(`stat`, `card_profiles`).
   - 사용자 결정: 시세 수집은 쓰인 선수 전부, 데이터센터에서는 최대한 많은 정보 수집. 최근 20경기 승률은 개발 키로 불가(랭커당 20회) → 시즌 전적 사용.
   - 수집 확대(사용자 결정): 매일 **상위 300명**(한도 여유), match-detail 전체 기록 저장(`match_player.stats`, `match_team.detail/shots`, JSON),
     Open API `ranker-stats`(TOP 10,000 랭커 20경기 평균, `pipeline/ranker_stats.py`, 하루 최대 30회·7일 유지) → `query_squads(match_stat)`, `top10000_stats`.
   - 범위 분리(사용자 결정): 랭킹은 웹에서 **상위 10,000명**(`--rank-top`, `daily rank`로 단독 실행), 스쿼드는 **상위 300명**(`--top`).
     긴 크롤링은 정각 갱신 시 처음부터 한 번 더(`crawl_rankings(restarts=1)`). 스쿼드 범위는 `squad_range()`(1위부터 연속 처리된 순위),
     랭킹 범위는 `unfiltered_coverage()`. 선수 없는 랭커 질문은 `query_rankers`(`analytics/rankers.py`).
   - 웹 개편(사용자 요청): 선수 추천 패널 제거(자연어 챗봇 중심), 포메이션 막대 클릭 → 상세 패널(`get_formation_overview`,
     `analytics/formation_view.py`: 최상위 랭커 스쿼드·베스트 11·상대 포메이션별 전적 + 10,000명 통계). 선수 사진 CDN 경로는 실제로 동작 확인.
   - **다음**: 매일 수집으로 실데이터가 쌓이면 `python -m fco_meta.chatbot.evaluate`로 `eval/questions.txt`(40개)를 돌려 보고서를 검토하고 다듬기.
     첫 실험에서 모델이 비율을 직접 더한 수치("약 87%")를 쓴 적이 있음 → 보고서의 "확인 필요 숫자"로 추적.
   - 작업 폴더는 `~/fco-meta` (DB·.env가 여기 있음). `~/Desktop/py/FCO_meta/fco-meta`는 예전 클론.
4. **남은 로드맵**
   - 알림(수집 실패 시)
   - 서비스 키로 전환하면 `--top`을 확대
   - 서비스로 공개하기 전 이용약관 확인

## 3. 구조 요약

```
fco_meta/
  crawler/     데이터센터 랭킹 크롤러 (rank_inner), 팀컬러 목록, membership.py(표시 팀컬러·엠블럼으로 소속 추정)
  market/      시세 크롤러 (선수 검색 PlayerList, 시세 이력), refresh.py(쓰인 선수 전부 시세·급여, 쓰인 시즌 카드만),
               details.py(카드 팝업 PlayerPreView → card_detail: 능력치·신체·특성, 30일 유지)
  openapi/     Open API 클라이언트 (초당 제한, CallBudget = api_usage 테이블에 KST 일자별 사용량, 재시도)
  pipeline/    닉네임→ouid→user/match→match-detail로 스쿼드 수집, 포지션 조합→포메이션 추론
  analytics/   usage.py: usage_sample / usage_stats 집계 (team_color_id 0 = 전체 랭커, formation '*' = 전체)
  chatbot/     tools.py(Toolbox: 도구 5개), rules.py(규칙 기반), gemini.py(Gemini 함수 호출), prompt.py
  web/         FastAPI + static/index.html (바닐라 JS). 외부 라이브러리는 static/vendor/에 복사 (thinking-orbs, marked, DOMPurify)
  daily.py     매일 작업: 크롤→소속→API 지연 대기→스쿼드→메타 갱신→집계→시세
  config.py    load_env(): cwd/.env와 프로젝트 .env 읽기 (기존 환경 변수 우선)
  storage.py   SQLite 스키마, unfiltered_coverage / latest_unfiltered_snapshot
tests/         pytest (네트워크 없음, fixtures/openapi/는 익명화된 실제 응답)
```

### 주요 명령

```bash
python -m fco_meta.daily run [--top 300 --no-wait --ranker-stats-calls 30 --price-players 2000 --detail-cards 600 -v]   # 매일 수집
python -m fco_meta.daily status
python -m fco_meta.daily schedule --at 00:00                                 # KST 자정마다 실행 (프로세스 상주)
python -m fco_meta.pipeline squads --top 300 | meta | budget | formations | ranker-stats
python -m fco_meta.analytics ...                                              # README '집계' 참고
python -m fco_meta.market used | details
python -m fco_meta.chatbot.evaluate [--only 1-10]                              # eval/questions.txt로 챗봇 평가 → reports/
python -m fco_meta.chatbot [--backend gemini|rules] [--list-models] [--ask "..."]
python -m fco_meta.web [--backend rules] [--port 8000]
```

## 4. 알아둘 사실과 함정

### 데이터
- 랭킹 페이지 스냅샷은 약 2.5시간 늦게 갱신되고, Open API 반영은 약 2시간 늦습니다.
  그래서 자정에 실행하면 대개 대기 없이 바로 수집됩니다.
- 랭커 1명당 호출: `id` 1회(ouid 캐시 후 0회) + `user/match` 1회 + `match-detail` 몇 회.
  하루 1,000회로 약 330명이 한계라 여유를 두고 300명을 수집합니다. 메타데이터 3회(META_RESERVE)와 랭커 스탯 몫(최대 30회, 남은 예산의 10% 이하)을 남겨 둡니다.
- matchId의 앞 4바이트는 경기 시작 시각(unix)입니다. 이 시각이 스냅샷 이후인 경기는 상세 호출 없이 건너뜁니다.
- 팀컬러 소속은 필터 조회 결과(`source='filter'`)를 표시 기반 추정(`'display'`)보다 우선합니다.
  같은 이름의 클럽/국가(예: 대한민국)는 데이터가 있는 쪽으로 해석하고 "(국가)/(클럽)"을 붙여 구분합니다.
- 표본이 MIN_SAMPLE(10) 미만이면 그 팀컬러의 전체 포메이션으로 폴백합니다. `strict`는 추론 포메이션이 페이지 포메이션과 일치하는 스쿼드만 씁니다.
- 시세는 데이터센터 검색이 spid로 조회되지 않아 선수 이름으로 검색합니다.
  응답에 모든 시즌 카드가 오지만 랭커가 쓴 카드만 저장하고, 20시간 안에 받은 것은 건너뜁니다.

### Gemini
- 기본 모델은 `gemini-3.8-flash`, 폴백은 `3.7-flash → 3.6-flash → 3.5-flash → 3.5-flash-lite`입니다 (무료 한도가 모델별이라 길게).
  한도 소진·혼잡 모델은 `_COOLDOWN`(프로세스 공유)에 기록해 건너뜁니다: 하루 한도는 태평양 자정까지, 분당·혼잡은 1~2분.
  `gemini-2.5-flash`는 신규 사용자에게 404가 납니다.
  환경 변수 `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`로 바꿀 수 있습니다.
- 오류 처리:
  - 503/500/504는 재시도한 뒤 다음 모델로 넘어갑니다.
  - 404는 바로 다음 모델로 넘어갑니다.
  - 429는 RetryInfo 대기 시간이 15초 이하면 한 번 기다리고, 일일 한도(PerDay)면 다음 모델로 넘어갑니다.
  - 설정된 모델이 모두 실패하면 `models.list()`로 찾은 모델을 최대 3개 더 시도합니다.
- 도구 호출은 최대 10라운드입니다. 넘으면 도구 없이 최종 답을 받고, 같은 도구 호출이 반복되면 실행하지 않습니다.
- 웹에서 Gemini가 실패하면 원인은 서버 로그에만 남기고 사용자에게는 혼잡 안내(`BUSY_MESSAGE`)만 보여 줍니다. HTTP 500은 내지 않습니다.
- Claude API 백엔드는 사용자 요청으로 제거했습니다.

### 개발
- `pkill -f`나 `pgrep -f`는 자기 셸 명령까지 잡을 수 있습니다. `ps aux | awk '/[p]attern/{print $2}'`를 쓰십시오.
- 웹 서버는 DB를 읽기 전용으로 엽니다. 테이블이 없는 DB에서도 `status`나 `market used`가 죽지 않도록 테이블 존재를 확인합니다.
- 차트 색은 dataviz 팔레트를 검증한 값을 씁니다(라이트 `#2a78d6`, 다크 `#3987e5`). 텍스트는 데이터 색을 쓰지 않습니다.

## 5. 최근 사용자 요청 흐름 (맥락)

2~6단계 구현 → Gemini로 전환 → `.env` 키 저장 → 매일 자정 상위 330명 수집 → 수집 중단 사유 안내
→ 많이 쓰인 선수 시세 갱신(쓰인 시즌만) → Mac 더블클릭 실행 → 웹 화면 개선(포메이션 분포, 선수 사진, 시즌 아이콘)
→ **다음: 사용자의 자정 이후 수집 결과 피드백 대응**
