# FCO-meta

FC온라인 랭커 데이터를 기반으로 선수를 추천하는 챗봇 프로젝트입니다.
계획: [`docs/PLAN.md`](docs/PLAN.md) · 조사 결과: [`docs/RESEARCH.md`](docs/RESEARCH.md)

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

매일 한 번 **랭킹 상위 N명(기본 330명)** 을 수집하고 집계합니다. 챗봇·웹은 이 데이터로 답합니다.
개발 키(하루 1,000회)로 처음 받는 랭커는 1명당 약 3회라 하루 약 330명이 한계입니다.

```bash
export NEXON_API_KEY=...
python -m fco_meta.daily run --top 330               # 지금 한 번
python -m fco_meta.daily schedule --at 00:00         # 매일 00:00(KST)에 실행 — 이 프로세스를 켜 둔다
```

실행 순서:
1. 필터 없이 랭킹 상위 N명 크롤링 (17페이지, 약 40초)
2. 표시된 팀컬러·엠블럼으로 팀컬러 소속 기록 (클럽 = 엠블럼 있음, 국가 = 엠블럼 없음, 특수 팀컬러 = 엠블럼으로 원래 클럽 추정)
3. Open API 반영 지연(스냅샷 + 2시간)까지 대기 → 00:00 스냅샷이면 02:00쯤 수집 시작 (`--no-wait`로 생략)
4. 상위 랭커부터 스쿼드 수집 — 오늘 남은 호출 예산 안에서, 모자라면 중단하고 다음 실행 때 이어서.
   matchId에 들어 있는 경기 시작 시각이 스냅샷 이후인 경기는 상세를 받지 않고 건너뜀
5. 이름을 모르는 새 카드가 있으면 메타데이터 갱신 (3회 예비)
6. 이 스냅샷의 집계(usage_stats) 재계산: 팀컬러별 + **전체 랭커**(팀컬러 무관)

- 서버나 PC를 켜 두기 어렵다면 cron / 작업 스케줄러로 `run`을 부르면 됩니다: `5 0 * * * cd /path/fco-meta && .venv/bin/python -m fco_meta.daily run`
- 팀컬러를 말하지 않은 질문("4-2-3-1 볼란치 추천")은 전체 랭커 기준으로 답합니다. 상위 330명을 팀컬러별로 나누면 표본이 작으므로 답에 표본 수가 함께 나옵니다.

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
- 모델이 혼잡(503)하거나 한도(429)에 걸리면 1초·3초 뒤 다시 시도하고, 그래도 안 되면 대체 모델(기본 `gemini-3.5-flash`)로
  답합니다(답 끝에 표시). 이 키로 쓸 수 없는 모델(404)은 바로 다음 모델로 넘어갑니다.
  기본 모델은 `gemini-3.8-flash`이고, `.env`의 `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`로 바꿀 수 있습니다.
- 설정한 모델이 모두 혼잡하면 **이 키로 쓸 수 있는 모델 목록을 조회해** 다른 모델(최대 3개, 예: lite)을 한 번씩 더 시도합니다.
  목록 확인: `python -m fco_meta.chatbot --list-models`
- 그 밖의 Gemini 오류는 원인(키·권한·모델·한도)을 보여 주고, 웹에서는 그 질문을 규칙 기반으로 대신 답합니다.
- 규칙 기반 모드가 이해하는 질문:

| 예 | 동작 |
|---|---|
| 4-2-3-1 볼란치 2명 추천해줘 | 상위 랭커 전체 기준 사용률 TOP 2 + 다음 후보 |
| 아스날 4-2-3-1 볼란치 2명 추천해줘 | 팀컬러 지정 (별칭: 아스날·맨유·맨시티·레알·바르사·PSG …) |
| 아스널 볼란치 5억 이하로 3명 | 예산 필터 (랭커들이 주로 쓴 강화 단계 시세 기준) |
| 아스널 4-2-3-1 ST 엄격하게 | 실제 경기 배치가 포메이션과 일치한 스쿼드만 |
| 라이스 사용률 / 랭커 포메이션 / 어떤 데이터 있어? | 선수별 현황, 포메이션 분포, 수집 범위 |

- 포메이션 표본이 10명 미만이면 같은 팀컬러의 전체 포메이션으로 집계하고 그렇다고 표시합니다.
- 시세는 `python -m fco_meta.market cards --team-color 아스널 --role 볼란치`로 수집한 값만 씁니다.

## 웹 UI

추천 화면과 챗봇을 브라우저에서 씁니다. DB는 읽기 전용으로 엽니다.

```bash
pip install -e ".[web]"
python -m fco_meta.web                        # http://127.0.0.1:8000 (챗봇: Gemini, 키 없으면 규칙 기반)
python -m fco_meta.web --backend rules        # 챗봇을 규칙 기반으로
python -m fco_meta.web --host 0.0.0.0 --port 8080
```

- **선수 추천**: 팀컬러(집계된 조합 자동 완성)·포메이션·역할·인원·예산(예: `5억`)·배치 일치 여부 → 사용률 막대, 시즌 카드별 사용 수·주 강화·시세.
  선수 이름을 누르면 역할·시즌별 사용 현황 표. 표본 부족 폴백, 예산 기준을 안내 문구로 표시
- **챗봇**: Gemini(기본) 또는 규칙 기반. 예시 질문 버튼 제공
- 라이트/다크 모드, 모바일 폭 대응
- JSON API: `/api/meta`, `/api/recommend`, `/api/formations`, `/api/player`, `POST /api/chat` (문서: `/api/docs`)

## 테스트

```bash
pytest
```
