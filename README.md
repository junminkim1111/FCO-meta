# FCO-meta

FC온라인 랭커 데이터를 기반으로 선수를 추천하는 챗봇 프로젝트입니다.
계획: [`docs/PLAN.md`](docs/PLAN.md) · 조사 결과: [`docs/RESEARCH.md`](docs/RESEARCH.md)

## 더블클릭 실행 (Mac)

설치(아래)를 한 번 마친 뒤에는 Finder에서 저장소 폴더의 파일을 더블클릭하면 됩니다.

| 파일 | 하는 일 |
|---|---|
| `start_web.command` | 웹 서버를 켜고 브라우저를 엶. 창을 닫으면 종료 |
| `run_daily.command` | 오늘 수집(랭킹 → 스쿼드 → 집계 → 시세)을 한 번 실행하고 상태 표시. `.env`에 `HF_TOKEN`이 있으면 끝나고 배포한 웹(Render)에 새 DB를 올림 |
| `start_share.command` | 웹 서버를 켜고 Cloudflare 임시 터널로 공유 주소(`https://….trycloudflare.com`)를 만듦 (지인 베타). 창을 닫으면 종료 |

### Render에 올리기 (무료 호스팅)

Render 무료 웹 서비스가 GitHub 코드로 빌드하고(`render.yaml`), 켜질 때 **비공개 Hugging Face 데이터셋**에서 DB를 받아 웹을 엽니다.
매일 수집은 맥에서 하고 DB만 올립니다. 무료 서비스는 15분 방문이 없으면 잠들고, 다음 방문 때 약 1분 걸려 다시 켜집니다.
(Hugging Face Space는 Docker 실행에 PRO 구독이 필요해졌고, Google Cloud e2-micro는 외부 IPv4가 월 약 3.7달러라 쓰지 않았습니다.)

```bash
pip install -e ".[cloud]"
python -m fco_meta.cloud push-db   # DB를 데이터셋에 올리고(.env의 HF_TOKEN, 쓰기) Render에 재배포 요청 (run_daily.command가 수집 뒤 자동 실행)
```

- Render에서 **New → Blueprint**로 이 저장소를 고르면 `render.yaml`대로 만들어지고, `HF_DATA_REPO`·`HF_TOKEN`(데이터셋 **쓰기** 토큰 — 답변 기록을 올림)·`GEMINI_API_KEY`·`ADMIN_KEY`를 묻습니다.
- **답변 기록**: `https://<서비스>/admin`에서 `ADMIN_KEY`로 들어가면 정상·캐시·혼잡 안내·질문 수 제한·정지 건수, 날짜별 표, 혼잡 안내가 나간 원인(오류)별 횟수, 최근 질문(원문·도구·모델·걸린 시간)을 봅니다.
  기록은 서버 메모리에 모았다가 10분마다(서버가 잠들 때도) 데이터셋의 `logs/날짜/*.jsonl`로 올립니다. 방문자 IP는 남기지 않습니다(`web/chatlog.py`).
  채팅창에 `/admin`을 입력해도 비밀번호 창이 뜹니다. 비밀번호를 3회 틀리면 경고, 5회 틀리면 그 IP는 관리자 페이지에 들어올 수 없습니다(서버 메모리 기준이라 재시작·재배포 때 풀림).
- **답하는 모델**: 기본은 Gemini 3.5 Flash-Lite. 스쿼드·복잡한 질문에 자주 나오는 말(짜줘·스쿼드·업그레이드·대신·케미·현역 …, `web/app.py`의 `HEAVY_WORDS`)이 있으면
  처음부터 3.5 Flash, 없더라도 Flash-Lite가 두 번째 도구를 부르면 그때부터 3.5 Flash가 받은 도구 결과로 이어서 답합니다.
- **채팅 명령어** (`/`로 시작하면 모델에 보내지 않고, 모르는 명령어는 *invalid command.*):
  `/admin` 기록 페이지 · `/admin --l` 비밀번호 확인 뒤 새로고침 전까지 모델 선택·생각·도구 결과를 답 위에 표시 ·
  `/deep` 다음 질문만 DeepSeek V4 Pro(생각 끔), `--r` 생각 켬, `--a` 새로고침 전까지 유지 ·
  `/compare` 다음 질문을 3.5 Flash·DeepSeek 생각 켬·끔이 전체 화면 세 칸에서 동시에 답함(IP당 1시간 2회) ·
  `/cancel` 위 명령어로 켠 것을 모두 끄고 기본 상태로.
  `/deep`·`/compare`는 질문당 상한 5분(일반 120초)이고, Render에 `OPENROUTER_API_KEY`가 있어야 DeepSeek이 답합니다.
- 만든 뒤 서비스 Settings의 **Deploy Hook** 주소를 맥 `.env`의 `RENDER_DEPLOY_HOOK`에 넣으면 push-db가 재배포까지 요청합니다.
- 코드는 GitHub에 푸시하면 Render가 자동으로 다시 빌드합니다. 질문 수 제한은 프록시가 넘겨주는 `X-Forwarded-For`의 첫 주소로 셉니다.

### 지인에게 공유하기 (베타)

- 처음 한 번 `brew install cloudflared`. 계정·도메인 없이 임시 주소가 생기며, **켤 때마다 주소가 바뀝니다**. 맥이 켜져 있을 때만 열립니다.
- 웹 서버는 이 맥 안(127.0.0.1)에서만 열리고 밖에서는 터널로만 들어옵니다. 방문자 IP는 터널이 넘겨주는 `CF-Connecting-IP`로 셉니다.
- 질문 수 제한(`web/app.py`의 `RateLimit`): 한 사람(IP)당 1분 6개 · 하루 60개, 서비스 전체 하루 300개(한국 시간 자정 초기화). 넘으면 429와 안내 문구.
- 화면 아래 `Data based on NEXON Open API` 표기는 넥슨 Open API 이용 조건입니다(지우지 마세요).
- 무료 Gemini 등급은 입력이 구글 제품 개선에 쓰일 수 있어, 화면에 "질문은 Google Gemini로 처리되며 서비스 개선을 위해 기록됩니다"를 적어 두었습니다(질문 원문을 답변 기록에 남기므로).
  공개 서비스로 넓힐 때는 유료 등급, 넥슨 서비스 단계 키(유효한 서비스 URL 필요), 상시 서버를 준비합니다.

- conda 환경 `fco-meta`의 파이썬을 자동으로 찾습니다 (`~/anaconda3`, `/opt/anaconda3`, miniconda·miniforge, `.venv` 순).
  다른 파이썬을 쓰려면 `FCO_PYTHON=/경로/python`, 다른 conda 환경 이름은 `FCO_CONDA_ENV=이름`.
- 처음 더블클릭할 때 macOS가 "확인되지 않은 개발자"라며 막으면: 파일을 **우클릭 → 열기**로 한 번 열어 주세요.
- 포트를 바꾸려면 `FCO_PORT=8080`.

## 설치

```bash
pip install -e ".[dev]"
cp .env.example .env   # .env에 NEXON_API_KEY, GEMINI_API_KEY를 적어 두면 모든 명령이 자동으로 읽음
```

## 랭킹 크롤러

공식 데이터센터 랭킹(TOP 10,000)에서 순위·팀컬러·포메이션을 수집해 SQLite(`data/fco_meta.sqlite`)에 저장합니다.
요청은 기본 2초 간격, 한 번에 하나씩 보냅니다.

```bash
# 팀컬러 목록 갱신 → data/teamcolors.json
python -m fco_meta.crawler catalog

# 관계·스페셜 팀컬러 효과와 적용 선수 → data/teamcolor_info.json (거의 안 바뀌어 한 번만, 약 1시간 반)
python -m fco_meta.crawler teamcolor-info

# TOP 10,000 팀컬러/포메이션 이용률 TOP 10
python -m fco_meta.crawler summary

# 아스널 팀컬러 + 4-2-3-1 랭커 수집
python -m fco_meta.crawler rank --team-color 아스널 --formation 4-2-3-1

# 필터 없이 상위 1,000명 (50페이지)
python -m fco_meta.crawler rank --max-pages 50

# 원본 HTML도 저장
python -m fco_meta.crawler --raw-dir raw rank --team-color 아스널
```

### 저장 구조
| 테이블 | 내용 |
|---|---|
| `crawl_run` | 수집 실행 기록 (조건, 기준 시각, 페이지/행 수, 상태) |
| `ranker_snapshot` | 기준 시각별 랭커 행 (순위, 닉네임, ELO, 승무패, 표시 팀컬러, 엠블럼, 포메이션 등) |
| `ranker_team_color` | 팀컬러 필터 결과에 포함된 랭커 = 해당 팀컬러 소속 |

팀컬러 소속은 `ranker_team_color`로 판단합니다. 랭킹 화면에는 팀컬러가 하나만 표시되고,
특수 팀컬러(예: "Winning Streak")가 있으면 그 이름이 우선 표시되기 때문입니다.

```sql
-- 아스널 팀컬러 × 4-2-3-1 랭커
SELECT s.rank, s.nickname, s.elo
FROM ranker_snapshot s
JOIN ranker_team_color m USING (data_as_of, mode, rank)
WHERE m.team_color_id = 1004 AND s.formation = '4-2-3-1'
ORDER BY s.rank;
```

## 매일 자동 수집 (권장)

매일 한 번 수집하고 집계합니다. 챗봇·웹은 이 데이터로 답합니다. 범위가 두 가지입니다.

| 범위 | 출처 | 내용 | 설정 |
|---|---|---|---|
| **랭킹 상위 10,000명** | 웹(데이터센터 랭킹) | 순위·팀컬러·포메이션·ELO·시즌 승/무/패·구단가치 | `--rank-top 10000` (500페이지 ≈ 17분) |
| **스쿼드 상위 10,000명** | 넥슨 Open API | 선발 명단·경기 기록, 그 카드들의 랭커 스탯·시세·급여·능력치 | `--top 10000` |

스쿼드는 랭커 1명당 2~3회 호출입니다. 서비스 키(하루 2천만 회·초당 500회)라 `.env`의 `NEXON_DAILY_LIMIT=50000`, `NEXON_RPS=20`으로 10,000명을 한 번에 받습니다 (첫날 약 3만 회, 이후 하루 2만여 회).
개발 키(하루 1,000회·초당 5회, 기본값)면 하루 약 300명씩 받고 나머지는 다음 날 이어서 받습니다.

```bash
export NEXON_API_KEY=...
python -m fco_meta.daily run --top 10000             # 지금 한 번
python -m fco_meta.daily schedule --at 00:00         # 매일 00:00(KST)에 실행 — 이 프로세스를 켜 둔다
python -m fco_meta.daily status                      # 수집 상태 (스냅샷, 처리한 랭커 수, 포메이션별 표본, API 사용량)
python -m fco_meta.daily rank                        # 랭킹 상위 10,000명만 웹에서 (API 키 불필요, 약 17분)
```

실행 순서:
1. 필터 없이 랭킹 상위 `--top`명 크롤링 (15페이지, 약 30초) — 스쿼드 대상
   1-1. 이어서 상위 `--rank-top`명(기본 10,000) 전체 크롤링 — 실패해도 스쿼드 수집은 계속, 도중에 정각 갱신이 일어나면 처음부터 한 번 더
2. 표시된 팀컬러·엠블럼으로 팀컬러 소속 기록 (클럽 = 엠블럼 있음, 국가 = 엠블럼 없음, 특수 팀컬러 = 엠블럼으로 원래 클럽 추정)
3. Open API 반영 지연(스냅샷 + 2시간)까지 대기 → 00:00 스냅샷이면 02:00쯤 수집 시작 (`--no-wait`로 생략)
4. 상위 랭커부터 스쿼드 수집 — 오늘 남은 호출 예산 안에서, 모자라면 중단하고 다음 실행 때 이어서.
   matchId에 들어 있는 경기 시작 시각이 스냅샷 이후인 경기는 상세를 받지 않고 건너뜀
5. 이름을 모르는 새 카드가 있으면 메타데이터 갱신 (3회 예비)
6. 이 스냅샷의 집계(usage_stats) 재계산: 팀컬러별 + **전체 랭커**(팀컬러 무관)
7. **쓰인 선수 시세·급여 갱신**: 집계에 나온 선수 전부(`--price-players`, 기본 2000 = 사실상 전부, 0이면 생략)를 이름으로 검색해
   **랭커들이 실제로 쓴 시즌 카드만** 1~13강 시세와 급여 저장 — 선수당 요청 1번(2초 간격, 수백 명이면 20~30분), 20시간 안에 받은 선수는 건너뜀.
   따로 실행: `python -m fco_meta.market used`
- 4단계에서 받는 경기 상세는 **전부 저장**합니다(추가 호출 없음): 선수별 골·도움·슈팅·패스·드리블·태클·가로채기·공중볼 등(`match_player.stats`),
  팀 점유율·슈팅·패스 종류별 기록(`match_team.detail`), 슈팅 위치·시간·도움(`match_team.shots`) — 모두 API 응답 JSON 그대로.
- 5-1. **랭커 스탯**(Open API `ranker-stats`): 우리 랭커들이 선발로 쓴 (카드, 포지션)마다 **TOP 10,000 랭커 최근 20경기 평균**(골·도움·슈팅·패스·드리블·태클·블록, 경기 수)
  — 1회 50쌍, 하루 최대 30회(`--ranker-stats-calls`), 7일 유지. 스쿼드 단계가 그 몫(남은 예산의 10% 이하)을 남겨 둡니다.
  따로 실행: `python -m fco_meta.pipeline ranker-stats --calls 30`
8. **카드 상세(능력치) 수집**: 쓰인 카드의 데이터센터 카드 팝업(`PlayerPreView`)에서 포지션별 능력치, 키·몸무게·체형, 개인기, 주발,
   특성, 요약 6개·세부 34개 능력치(1강 기준), 클럽 경력 저장 — 카드당 요청 1번, 한 번에 최대 600장(`--detail-cards`, 약 20분),
   받은 카드는 30일 유지(처음 며칠에 나눠 받고 이후엔 새 카드만). 따로 실행: `python -m fco_meta.market details`

- 서버나 PC를 켜 두기 어렵다면 cron / 작업 스케줄러로 `run`을 부르면 됩니다: `5 0 * * * cd /path/fco-meta && .venv/bin/python -m fco_meta.daily run`
- 팀컬러를 말하지 않은 질문("4-2-3-1 볼란치 추천")은 전체 랭커 기준으로 답합니다. 상위 300명을 팀컬러별로 나누면 표본이 작으므로 답에 표본 수가 함께 나옵니다.

## Open API 스쿼드 수집

랭킹 스냅샷의 랭커를 넥슨 Open API로 조회해 **스냅샷 기준 시각 직전 공식경기(matchtype 50)의 스쿼드**를 저장합니다.
`NEXON_API_KEY` 환경 변수(또는 `.env`를 셸에 로드)가 필요합니다.

```bash
# 1) 랭커 스냅샷 (위 크롤러)
python -m fco_meta.crawler rank --team-color 아스널 --formation 4-2-3-1

# 2) 메타데이터 (선수명·시즌·포지션) — 3회 호출
python -m fco_meta.pipeline meta

# 3) 스쿼드 수집: 이번 실행 최대 300회 호출 (중단돼도 같은 명령으로 이어서 수집)
python -m fco_meta.pipeline squads --team-color 아스널 --formation 4-2-3-1 --budget 300

python -m fco_meta.pipeline budget            # 오늘(KST) 호출 수
python -m fco_meta.pipeline formations --save # 포지션 조합 → 포메이션 표(data/formations.json) 갱신
```

- **호출 한도**: 초당 5회(1초 슬라이딩 윈도), 일일 1,000회(`api_usage` 테이블에 KST 날짜별 기록, 한도 도달 시 요청을 보내지 않고 중단).
  429(OPENAPI00007)·5xx는 지수 백오프 재시도, 400 OPENAPI00009(데이터 준비 중)는 해당 랭커만 건너뛰고, 점검(00010/00011)이면 중단합니다.
- **캐시**: 닉네임→ouid(실패 포함), 스냅샷 이후에 받은 경기 목록, matchId별 상세는 다시 호출하지 않습니다. 완료한 랭커도 건너뜁니다.
- **정합성**: 랭킹 페이지의 팀컬러·포메이션은 가장 최근 공식경기 스쿼드 기준 → `data_as_of`(KST) 이전의 가장 최근 경기(UTC `matchDate` 변환 후 비교)를 기본 스쿼드(`match_order` 0)로 씁니다.
  `--extra-matches N`으로 더 이전 경기를 보면, 추론 포메이션이 스냅샷 포메이션과 같거나 선발 포지션 조합이 기본 스쿼드와 같을 때만 `accepted=1`입니다.
- Open API는 매시 정각에 **2시간 전까지**의 경기를 반영합니다. 기준 시각 + 2시간 전에 수집한 스쿼드는 `provisional`로 저장되고,
  다음 실행에서 경기 목록만 다시 받아 확인합니다(match-detail은 캐시).
- 요청 URL에 닉네임·ouid가 들어가므로 httpx 요청 로그는 출력하지 않습니다.

| 테이블 | 내용 |
|---|---|
| `api_usage` | KST 날짜·엔드포인트별 호출 수 |
| `nickname_lookup` | 닉네임 → ouid 조회 결과 (ok / not_found) |
| `account`, `account_nickname` | ouid 기준 계정, 닉네임 이력 |
| `user_match_list` | ouid별 최신 공식경기 matchId 목록과 조회 시각 |
| `match`, `match_team`, `match_player` | 경기(UTC 일시), 참가자별 결과, 선수(spId, 시즌ID, pid, spPosition, 강화, 평점, 선발 여부) |
| `ranker_squad` | 스냅샷 랭커 ↔ 스쿼드 경기 (`match_order`, 추론 포메이션, 일치 여부, 반영 여부) |
| `ranker_squad_status` | 랭커별 수집 상태 (ok / provisional / nickname_not_found / no_match_before_snapshot / data_not_ready / error) |
| `meta_spid`, `meta_season`, `meta_position` | 메타데이터 |

## 집계 (usage_stats)

수집된 기본 스쿼드로 **팀컬러 × 포메이션 × 역할 × 카드**별 사용 현황을 계산합니다.
역할은 `fco_meta/market/roles.py`(시세 크롤러와 같은 정의: `DM`=RDM/CDM/LDM, `CAM`=RAM/CAM/LAM …)를 쓰고, 별칭(볼란치·공미 등)도 받습니다.

```bash
# 스쿼드 수집 후 재계산 (여러 번 실행해도 결과 동일)
python -m fco_meta.analytics build

# 볼란치 사용 선수 TOP 5 (시즌 합산 + 시즌별 내역)
python -m fco_meta.analytics top --team-color 아스널 --formation 4-2-3-1 --role 볼란치
#   --by sp_id  카드(시즌)별        --strict  추론 포메이션이 스냅샷과 같은 스쿼드만
#   --min-sample N  표본이 N명 미만이면 팀컬러 전체 포메이션으로 폴백 (기본 10)
```

| 테이블 | 내용 |
|---|---|
| `usage_sample` | 스냅샷 × 팀컬러 × 포메이션(`*` = 전체) × strict별 조합 랭커 수, 수집된 스쿼드 수(= 사용률 분모) |
| `usage_stats` | 위 단위 × 역할 × 카드(sp_id): 사용 랭커 수, 사용률, 강화 평균·분포, 평점 평균, 해당 경기 승률, 사용 랭커 평균 ELO·시즌 승률 |

- 선발(`spPosition != 28`)만 셉니다. 같은 선수의 다른 시즌 카드는 한 스쿼드에 함께 있을 수 없으므로 선수(pid) 단위 합산에 중복이 없습니다.
- 승률은 랭커당 기본 스쿼드 1경기 결과라 참고용입니다.
- 코드에서는 `fco_meta.analytics.top_players(conn, team_color_id, formation, role, ...)` → `UsageResult`

## 시세 크롤러

데이터센터 선수 검색(`/datacenter/PlayerList`)에서 카드 정보와 **1~13강 현재가**를,
`/datacenter/PlayerPriceGraph`에서 **365일 시세 이력**을 수집합니다. 랭킹 크롤러와 같은 DB 파일을 씁니다.

```bash
# 아스널 팀컬러 + 볼란치 가능 카드와 강화별 시세 수집
python -m fco_meta.market cards --team-color 아스널 --role 볼란치

# 저장된 시세에서 조건 검색 (5강 1억 이하, OVR 높은 순)
python -m fco_meta.market find --team-color 아스널 --role DM --grade 5 --max-price 1억 --sort ovr

# 카드·강화별 365일 시세 이력
python -m fco_meta.market history --spid 100001419 --grade 5
```

- 역할(`--role`): `GK CB RB LB RWB LWB DM CM CAM RM LM RW LW CF ST`, 별칭(`볼란치`, `수미`, `센터백` 등) 지원
- 검색 결과는 요청당 최대 200장이라, 넘치면 급여 구간을 나눠 다시 조회합니다(최대 60회). 그래도 넘치면 "일부 누락 가능"으로 표시합니다.

| 테이블 | 내용 |
|---|---|
| `card` | 카드 정보 (spid, 이름, 시즌, 대표 포지션, OVR, 급여, 유저 평점) |
| `card_price` / `card_price_latest` | 수집 시각별 강화 단계 현재가 / 카드·강화별 최신가 |
| `card_team_color` | 팀컬러 필터에 걸린 카드 = 팀컬러 적격 카드 |
| `card_role` | 포지션 필터에 걸린 카드 = 해당 역할 기용 가능 카드 |
| `price_history` | 카드·강화별 일별 시세 |

## 챗봇

집계(`usage_stats`)와 시세를 근거로 답합니다. 기본은 **Gemini**이고, `GEMINI_API_KEY`가 없으면 안내 후 **규칙 기반**으로 동작합니다.

```bash
python -m fco_meta.chatbot                                        # 대화형 (Gemini, 키 없으면 규칙 기반)
python -m fco_meta.chatbot --ask "4-2-3-1 볼란치 2명 추천해줘"
python -m fco_meta.chatbot --backend rules                        # 규칙 기반 (키 불필요)
python -m fco_meta.chatbot --model gemini-3.5-flash               # 다른 Gemini 모델

# 모델 없이 도구만 실행 (디버깅)
python -m fco_meta.chatbot --tool recommend_players '{"team_color": "아스널", "role": "DM", "max_price_bp": 500000000}'
```

- Gemini는 도구(`recommend_players` 등)로 DB를 조회해 답하므로, 수치는 규칙 기반과 같은 데이터에서 나옵니다.
- 모델이 혼잡(503)하거나 한도(429)에 걸리면 1초·3초 뒤 다시 시도하고, 그래도 안 되면 대체 모델로 답합니다(답 끝에 표시).
  무료 한도는 모델마다 따로라 대체 순서를 길게 둡니다: `gemini-3.7-flash` → `3.6-flash` → `3.5-flash` → `3.5-flash-lite`.
  이 키로 쓸 수 없는 모델(404)은 바로 다음 모델로 넘어갑니다.
- **한도가 소진된 모델은 기억해 두고 건너뜁니다**: 하루 한도 소진은 태평양 시간 자정(한도 초기화)까지, 분당 한도·혼잡은 1~2분.
  그래서 도구 호출마다 소진된 모델을 다시 두드리지 않습니다. 모두 소진되면 언제까지 건너뛰는지 안내합니다.
  기본 모델은 `gemini-3.5-flash-lite`(빠른 답을 위해)이고, `.env`의 `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`로 바꿀 수 있습니다.
- 설정한 모델이 모두 혼잡하면 **이 키로 쓸 수 있는 모델 목록을 조회해** 다른 모델(최대 3개, 예: lite)을 한 번씩 더 시도합니다.
  목록 확인: `python -m fco_meta.chatbot --list-models`
- 그 밖의 Gemini 오류: 터미널 챗봇은 원인(키·권한·모델·한도)을 보여 주고, 웹은 "지금 서버가 혼잡해…" 안내만 보여 줍니다(원인은 서버 로그).
- `[근거]` 줄(범위·표본 수·기준 시각)은 모델이 아니라 **도구 결과로 시스템이 만듭니다**. 웹 답에는 넣지 않고 서버 로그에만 남기며
  (대체 모델로 답했다는 안내도 마찬가지), 터미널 챗봇(`python -m fco_meta.chatbot`)에서는 답 끝에 보여 줍니다.
- 대화 기록: 지난 질문은 질문과 답 글만 남기고 도구 호출·결과는 다음 질문부터 보내지 않습니다(이어지는 질문의 사용량이 늘지 않도록).
  웹은 모델 응답을 기다리는 동안 DB 잠금을 잡지 않아, 챗봇이 생각하는 중에도 포메이션 패널이 바로 열립니다.
- 스트리밍: `/api/chat`은 답을 한 줄에 JSON 이벤트 하나(NDJSON: start → tool·delta… → done, reset = 도구를 부르기 전 글 지움)로
  흘려보내고, 화면은 첫 글자가 올 때까지 "랭커데이터를 살펴보고 있어요..."를 보여 준 뒤 글자 단위로 그립니다.
  기다리는 동안 모델이 도구를 부르면 문구가 그 도구에 맞게 바뀝니다(예: "아스널 랭커들이 쓰는 선수를 추리고 있어요...", index.html의 TOOL_STATUS).
  정지하면 받은 글까지 남기고, 그 질문은 대화 기록에서 빠집니다.
- 수치 검증: 도구로 조회한 답에 도구 결과(와 앞선 대화)에 없는 비율·금액·인원이 있으면(`chatbot/numbers.py`), 그 수치를 알려 주고
  도구 없이 **한 번** 다시 쓰게 합니다. 화면은 "수치를 다시 확인하고 있어요..."를 보여 주고, 기록에는 고친 답만 남습니다.
  다시 써도 남으면 그대로 두고 서버 로그에 남깁니다. 인사·범위 밖 질문처럼 도구 없이 답한 경우는 검사하지 않습니다.
- 답 캐시: 대화의 **첫 질문**은 같은 질문(띄어쓰기·대소문자·끝 문장부호만 무시)이 다시 오면 Gemini를 부르지 않고 저장해 둔 답을
  돌려줍니다. DB 파일이 바뀌면(매일 수집·시세 갱신) 비우고, 이어지는 질문은 대화에 따라 답이 달라 캐시하지 않습니다(웹 `AnswerCache`, 메모리 500개).
- 도구 9개:

| 도구 | 질문 예 | 내용 |
|---|---|---|
| `recommend_players` | 4-2-3-1 볼란치 2명 / 5억 이하 볼란치 / 가성비 센터백 | 역할별 사용률 순위. `max_price_bp`(한 장 예산), `sort="price"`(가성비: 사용률 3% 이상 중 시세 낮은 순) |
| `recommend_squad` | 아스널 4-2-3-1 스쿼드 / 총 50억으로 스쿼드 | 포메이션 선발 11명 + 자리별 대안 + 총액. 포지션 구성은 그 포메이션으로 실제 경기한 스쿼드에서, 총예산을 넘으면 사용률 손실이 적은 쪽부터 싼 선수·시즌으로 교체 |
| `get_player_detail` | 라이스 vs 수비멘디 / 라이스 대신 쓸 선수 | 역할별 사용률·역할 내 순위·평점·시즌 카드·시세, 날짜별 사용률 추이. 팀컬러·포메이션으로 범위 제한 가능 |
| `get_meta_trends` | 요즘 메타 / 많이 쓰는 팀컬러 / 뜨는 선수 | 팀컬러·포메이션 비율, 많이 쓰인 선수, 약 7일 전 스냅샷 대비 변화 (스냅샷이 쌓여야 변화가 나옴) |
| `query_squads` (범용 조회) | 평점 높은 볼란치 / 상위 100위는 뭐 써? / 라이스 파트너 / 승률 높은 포메이션 / 속력 좋은 볼란치 | 최신 스냅샷 기준 경기 선발 명단을 조건(팀컬러·포메이션·역할·순위 구간·ELO·특정 선수를 쓴 랭커·능력치 하한)으로 거르고 선수·카드·시즌·강화·팀컬러·포메이션·역할별로 묶어 사용률·평점·시즌 승률·강화·ELO·평균 시세·급여·능력치로 정렬. 평점 등으로 정렬하면 사용 랭커 3명 미만 항목은 기본 제외 |
| `query_rankers` (랭킹 10,000명) | 10,000명 중 많이 쓰는 팀컬러 / 승률 높은 포메이션 / 1,000~2,000위는 뭐 써? | 랭킹 페이지 정보만으로 팀컬러·포메이션·순위 구간별 랭커 수·비율·시즌 승률·ELO·구단가치 (선수 정보 없음) |
| `list_formations`, `list_available_data`, `resolve_terms` | 포메이션 분포 / 어떤 데이터 있어? | |
| `get_team_color_info` | 레드데블스 철벽라인 효과 / 박지성 들어간 관계 팀컬러 / 골 결정력 올려주는 팀컬러 | `data/teamcolor_info.json` (데이터센터) |

- 포메이션은 `4231`처럼 써도 되고, 역할 별칭(좌윙·우풀백·세컨톱 등)과 **묶음 포지션**(윙어 = RW+LW, 공격수, 풀백, 미드필더, 수비수 …)도 받습니다.
- 팀컬러 별칭(레알, 맨유, 뮌헨, AC밀란 → 밀라노 FC 등)은 [`data/team_color_aliases.json`](data/team_color_aliases.json)에 있습니다. 한 줄씩 추가하면 됩니다.
- 급여: `recommend_players(max_salary, sort="salary")`, 여러 자리 합계는 `recommend_squad(slots="DM,DM", max_total_salary=53)` —
  자리가 2~3개면 한도 안의 모든 조합을 비교해 사용률 합이 가장 큰 조합을 고릅니다.
- 승률: 랭킹 페이지의 **시즌 전적**(랭커별 승/무/패)을 합친 `season_win_rate`. 기준 경기 1경기 승률(`win_rate`)은 참고용.
  (최근 20경기 승패는 경기마다 상세 조회가 필요해 개발 키 한도로는 불가 — 서비스 키 전환 후 검토)
- 경기 성적: `query_squads(match_stat="goal", sort_by="match_stat")` — 기본은 TOP 10,000 랭커 20경기 평균(`match_stat_source="top10000"`),
  가로채기·공중볼 등은 우리 랭커 기준 경기 기록(`"rankers"`). 선수 한 명은 `get_player_detail`의 `top10000_stats`.
- 능력치: `query_squads(stat="속력", sort_by="stat")`, `stat_min`(예: 키 185 이상), `get_player_detail`의 `card_profiles` (1강 기준, 랭커가 쓴 카드만)
- 답변 점검: `python -m fco_meta.chatbot.evaluate` — 고정 질문 세트를 돌려 `reports/chatbot-eval-*.md`에
  도구 호출, 답, **도구 결과에 없는 숫자(확인 필요)** 를 적습니다. `--backend rules`, `--questions 파일`로 바꿀 수 있습니다.
- 규칙 기반 모드가 이해하는 질문:

| 예 | 동작 |
|---|---|
| 4-2-3-1 볼란치 2명 추천해줘 | 상위 랭커 전체 기준 사용률 TOP 2 + 다음 후보 |
| 아스날 4-2-3-1 볼란치 2명 추천해줘 | 팀컬러 지정 (별칭: 아스날·맨유·맨시티·레알·바르사·PSG …) |
| 아스널 볼란치 5억 이하로 3명 | 예산 필터 (랭커들이 주로 쓴 강화 단계 시세 기준) |
| 아스널 4-2-3-1 ST 엄격하게 | 실제 경기 배치가 포메이션과 일치한 스쿼드만 |
| 4231 스쿼드 30억 이하로 짜줘 | 선발 11명 추천 (총예산) |
| 요즘 메타 / 아스널 동향 | 팀컬러·포메이션·선수 사용률과 변화 |
| 라이스 사용률 / 랭커 포메이션 / 어떤 데이터 있어? | 선수별 현황, 포메이션 분포, 수집 범위 |

- 포메이션 표본이 10명 미만이면 같은 팀컬러의 전체 포메이션으로 집계하고 그렇다고 표시합니다.
- 시세는 `python -m fco_meta.market cards --team-color 아스널 --role 볼란치`로 수집한 값만 씁니다.

## 웹 UI

포메이션 정보를 둘러보면서 챗봇(Gemini, 자연어)에게 묻는 화면입니다. DB는 읽기 전용으로 엽니다.

```bash
pip install -e ".[web]"
python -m fco_meta.web                        # http://127.0.0.1:8000 (챗봇: Gemini, 키 없으면 규칙 기반)
python -m fco_meta.web --host 0.0.0.0 --port 8080
```

- **포메이션 분포**: 랭킹 상위 10,000명의 포메이션 상위 8개 막대. 누르면 아래에 그 포메이션 정보(처음에는 1위 포메이션):
  - 사용률 순위·비율, 시즌 승률(전체 평균 대비), 평균 ELO·구단가치, 최고 순위 — 랭킹 10,000명 기준
  - **스쿼드** 경기장 그림: 최상위 랭커 스쿼드(닉네임 없이 순위만) / 랭커 베스트 11(자리별 최다 사용), 선수 사진·시즌 아이콘·강화
  - **상대 포메이션별 전적**: 수집된 경기의 양쪽 선발 배치로 상대 포메이션을 추론한 승·무·패 (경기가 쌓일수록 정확)
  - 많이 쓰는 팀컬러, 순위 구간별 사용 비율
- **팀컬러 분포**: 위 전환 버튼(React Bits Rubber Segment를 옮긴 `static/segment.js`)으로 포메이션 분포와 바꿔 봄. 팀컬러 상위 8개 막대,
  누르면 같은 모양의 팀컬러 정보(`get_team_color_overview`): 사용률·시즌 승률·ELO·구단가치·최고 순위, 그 팀컬러 최상위 랭커 스쿼드 /
  베스트 11(포메이션 무관), 많이 쓰는 포메이션, 순위 구간별 비율. 랭커 한 명이 팀컬러 여럿일 수 있어 비율 합은 100%를 넘음
- **챗봇**: 자연어 질문. 답을 만드는 동안에도 포메이션을 둘러볼 수 있고, 넓은 화면에서는 챗봇이 화면에 고정됨
- 다크 테마, 모바일 폭 대응
- JSON API: `/api/meta`(수집 범위), `/api/formations`, `/api/formation?name=4-2-3-1`, `/api/teamcolors`, `/api/teamcolor?name=레알`,
  `POST /api/chat` (문서: `/api/docs`)

## 테스트

```bash
pytest
```
