# FCO 랭커 메타 챗봇 — 설계 계획 (v3: 0단계 조사 반영)

> 목표: "아스널 팀컬러, 4-2-3-1 포메이션에 쓸 볼란치 2명 추천해줘" 같은 질문에
> **실제 랭커 데이터 기반의 사용률·승률 근거**와 함께 선수를 추천하는 챗봇.

조사 상세: [`docs/RESEARCH.md`](RESEARCH.md) · API 스펙 원본: [`docs/nexon-openapi-spec/`](nexon-openapi-spec/)

---

## 1. 확정된 데이터 경로

| 필요한 정보 | 출처 | 비고 |
|---|---|---|
| 랭커 목록 (TOP 10,000) | 데이터센터 `GET /datacenter/rank_inner` | 서버 렌더링 HTML, 20명/페이지, 1시간 단위 갱신 |
| 팀컬러 | **팀컬러 필터(`tc_01`) 결과 포함 여부** | 행에 표시되는 이름은 1개뿐이고 특수 팀컬러가 우선 표시됨 → 소속은 `ranker_team_color` 테이블로 관리 |
| 포메이션 | 같은 행의 `.formation` | |
| **팀컬러×포메이션 필터** | `rank_inner?tc_01={id}&formation_01={포메이션}` | 공식 페이지가 직접 필터링 (아스널+4-2-3-1 = 94명 확인) |
| 선수 구성 | Open API: 닉네임 → `id` → `user/match` → `match-detail` | 랭킹 페이지에는 스쿼드 없음 |
| 선수 평균 스탯 | Open API `ranker-stats` | 추천 근거 보강 |
| 선수명·시즌·포지션 | `/static/fconline/meta/*.json` | |
| **선수 시세** | 데이터센터 `PlayerList`(강화별 현재가), `PlayerPriceGraph`(365일 이력) | Open API에는 없음. `fco_meta/market` |

기준 문구: "팀컬러 및 포메이션 정보는 가장 최근에 각 모드 공식경기에 사용한 스쿼드 정보 기준으로 수집됩니다."

---

## 2. 제약: Open API 호출 한도

| 키 타입 | 초당 | 일일 |
|---|---|---|
| 개발 단계 | 5 | **1,000** |
| 서비스 단계 | 500 | 20,000,000 |

랭커 1명당 호출: `id` 1회(캐시 후 0회) + `user/match` 1회 + `match-detail` K회.
TOP 10,000 전수는 개발 키로 불가 → **조합 단위 수집 + 캐시**로 설계하고, 서비스 공개 시 서비스 단계 키로 전환.

| 수집 전략 | 대상 | 예상 호출 (K=1) |
|---|---|---|
| **조합 단위 (MVP)** | 인기 팀컬러 TOP 10 × 해당 팀컬러 주요 포메이션 | 조합당 약 50~100명 × 2~3회 |
| 온디맨드 | 챗봇 질문 시 캐시 없는 조합만 수집 | 질문당 수백 회 (첫 질문만 느림) |
| 전수 | TOP 10,000 | 약 20,000~30,000회/일 → 서비스 키 필요 |

---

## 2-1. 운영 방식 (현재)
- 매일 00:00(KST) **랭킹 상위 330명** 스냅샷 → 02:00(반영 지연 후) 스쿼드 수집 → 집계 (`python -m fco_meta.daily schedule`)
- 330명 = 개발 키 하루 1,000회 ÷ 랭커당 약 3회. 서비스 키로 바꾸면 `--top 10000 --daily-limit …`로 확장
- 팀컬러 소속: 필터 없이 받은 행은 표시 팀컬러·엠블럼으로 추정(`source='display'`). 필터 조회 결과(`source='filter'`)가 있으면 그쪽이 우선
- 집계는 팀컬러별 + 전체 랭커(`team_color_id = 0`, 필터 없는 수집이 덮은 순위만)
- 시세: 매일 마지막 단계에서 사용 랭커가 많은 선수 150명을 데이터센터 선수 검색(이름)으로 갱신 (선수당 1요청, 20시간 캐시).
  실측: 150명 → 약 5분, 검색 응답 3,253장 중 랭커가 쓴 시즌 카드 549장만 저장 (강화 1~13 모두) → 전체 랭커 기준 예산 추천 가능
- 질문 시 수집하지 않음 — 챗봇·웹은 DB 조회만
- 실행: Mac은 `start_web.command`(웹) / `run_daily.command`(수집) 더블클릭

실제 1회차 (2026-09-29 00:26 KST 실행, 랭킹 스냅샷 2026-09-28 22:00):
- 랭킹 페이지 스냅샷 자체가 약 2.5시간 늦게 갱신됨 → 실행 시점에 API 반영 지연은 이미 지나 대기 없음
- 17페이지(340명) 크롤링, 팀컬러 추정 실패 3명
- 호출 1,000회로 306명 처리(스쿼드 303명, 스냅샷 이전 경기 없음 3명) 후 예산 소진
- 경기 상세 388회 중 99회가 스냅샷 이후 경기(버림) → matchId 앞 4바이트가 경기 시작 시각임을 확인(485건 모두
  `matchDate`보다 1~21분 이름)해, 그 시각이 스냅샷 이후면 상세 호출 없이 건너뛰도록 수정 → 이번 기준 82회 절약
- 이튿날부터는 ouid 캐시로 랭커당 약 2회 → 330명 여유 있게 수집 예상
- 추론 포메이션 = 페이지 포메이션: 220/303 (73%)

## 3. 파이프라인

```
① 랭킹 크롤러 (rank_inner)
     └▶ ranker_snapshot(snapshot_at, rank, nickname, elo, w/d/l, squad_value, team_color_id, tc_count, formation)
② 닉네임 → ouid (Open API /id, 캐시)
③ 스쿼드 수집: user/match(matchtype=50, limit=K) → match-detail → 해당 ouid의 player[]
④ 정합성 검증: 스냅샷 기준 시각 직전 경기인지, 추론 포메이션 = 페이지 포메이션인지
⑤ 집계: usage_stats (팀컬러 × 포메이션 × 역할 × 선수)
⑥ 챗봇: Gemini function calling(기본) 또는 규칙 기반으로 집계 DB 조회 → 근거 포함 답변
```

### ① 랭킹 크롤러
- `httpx`로 `rank_inner` 직접 호출, `selectolax`로 파싱 (JS 렌더링 불필요)
- 모드 A (전체 스냅샷): 500페이지 순회 → 팀컬러·포메이션 분포 파악 (2초 간격이면 약 17분)
- 모드 B (조합 조회): `tc_01` + `formation_01` 필터로 해당 조합 랭커만 수집
- 파싱 항목: `docs/RESEARCH.md` "행(row) 필드" 참고
- 테스트 fixture: `tests/fixtures/datacenter/*.html` (닉네임 익명화)

### ② 닉네임 → ouid
- 랭킹 페이지의 `data-sn`은 넥슨 회원번호로 Open API에서 사용 불가 → 닉네임으로 `/id` 조회
- ouid를 기본 키로 저장, 닉네임은 이력으로 보관 (닉네임 변경 대응)

### ③ 스쿼드 수집
- `user/match?matchtype=50&limit=K` → 최신 경기부터
- `match-detail`에서 `matchInfo[].ouid == 대상`의 `player[]` 추출
- 선발 = `spPosition != 28`
- 저장: matchId, matchDate, 결과, spId, 시즌ID, spPosition, spGrade(강화), spRating

### ④ 정합성 검증
- 페이지 팀컬러/포메이션은 "가장 최근 공식경기 스쿼드" 기준 → **스냅샷 시각 이전의 가장 최근 경기(K=1)**가 정답 스쿼드
- 추가 경기(K>1)는 포지션 조합으로 추론한 포메이션이 페이지 값과 같을 때만 반영
- `matchDate`는 UTC, 페이지 기준 시각은 KST로 보임 → 변환 후 비교
- 포메이션 추론 (`fco_meta/pipeline/formation.py`)
  1. 라인별 인원 수(수비 1–8 / DM 9–11 / MF 12–16 / AM 17–19 / RF·CF·LF 20–22 / 윙·ST 23–27)로 추론.
     실데이터에서 게임 포메이션 이름과 맞음 (RDM·LDM+RAM·CAM·LAM+ST = 4-2-3-1, RDM·LDM+CAM+RW·ST·LW = 4-2-1-3 등)
  2. "4-4-2(2)" 같은 변형 이름은 학습 표 `data/formations.json`으로만 구분 (`pipeline formations --save`).
     스냅샷 포메이션은 더 최신 경기 기준일 수 있으므로, 라인 모양이 이미 실제 포메이션 이름인데 관측값과 다르면 학습하지 않음
     (3회 이상·80% 이상 일치, 같은 라인 모양의 변형 이름이나 이름 없는 모양만 등록) → 여러 포메이션 스냅샷이 쌓인 뒤 생성
  3. 추가 경기는 추론값 = 스냅샷 포메이션이거나 선발 포지션 조합이 기본 스쿼드와 같으면 반영
- Open API 반영 지연(2시간): 기준 시각 + 2시간 전에 받은 경기 목록으로 만든 스쿼드는 `provisional`로 저장하고, 다음 실행에서 경기 목록만 다시 받아 확인 (match-detail은 캐시)

#### 검증 결과 (2026-09-28 21:00 KST 스냅샷, 아스널 × 4-2-3-1 = 94명)
- 94명 전원 스쿼드 수집, API 호출 285회 (id 94 + user/match 94 + match-detail 97 — 기준 시각 이후 경기 3건 포함), 랭커당 약 3회
- 기본 스쿼드 추론 포메이션 = 4-2-3-1: **77명(82%)**. 나머지 17명은 4-2-2-2·4-2-2-1-1·4-4-2·4-1-4-1 등 실제로 다른 배치
  → 기준 시각 + 2시간 뒤 재확인(경기 목록 94회 재조회)에서도 새 경기 없이 결과 동일 → API 반영 지연이 아니라
    랭커가 스쿼드를 여러 개 쓰는 경우로 판단. `analytics top --strict`로 일치 스쿼드만 집계 가능
- 기본 스쿼드 경기는 기준 시각 평균 33시간 전(최소 1분, 최대 10일)

### ⑤ 집계 (`fco_meta/analytics`)
```
usage_sample(data_as_of, mode, team_color_id, formation, strict, combo_rankers, squads)
usage_stats(
  data_as_of, mode, team_color_id, formation, strict, role,   -- role: market.roles (DM = RDM/CDM/LDM …)
  sp_id, pid, season_id,
  ranker_count, sample_size, usage_rate, win_rate, avg_rating, avg_grade, grade_dist,
  avg_elo, avg_ranker_win_rate
)
```
- 사용률 분모 = 해당 조합에서 기본 스쿼드가 수집된 랭커 수 (`usage_sample.squads`, 예: 아스널 4-2-3-1 = 94명)
- `formation = '*'`: 팀컬러 전체 포메이션 집계. 표본이 `MIN_SAMPLE`(10명) 미만이면 `top_players`가 여기로 폴백하고 `fallback`으로 표시
- `strict = 1`: 추론 포메이션 = 스냅샷 포메이션인 스쿼드만 (아스널 4-2-3-1: 94명 → 77명)
- 선수 단위(pid)는 카드 행을 합산하고 시즌별 내역을 함께 반환 → 챗봇이 "라이스 (25 UCL 23명, 26 TOTS 17명 …)"처럼 설명
- `grade_dist`(강화별 사용 랭커 수)는 시세(`market.card_price_latest`)와 결합해 예산별 추천에 사용 예정

#### 검증 결과 (아스널 × 4-2-3-1, 볼란치 = DM, 표본 94명)
| 순위 | 선수 | 사용 랭커 | 사용률 | 주요 시즌 |
|---|---|---|---|---|
| 1 | 데클런 라이스 | 53 | 56.4% | 25 UCL 23, 26 TOTS 17, PTG 9 |
| 2 | 수비멘디 | 33 | 35.1% | 25 UCL 18, PTG 13 |
| 3 | 파트리크 비에이라 | 26 | 27.7% | 26FSL 11, WS 4, SPT 4 |
| 4 | 브루누 기마랑이스 | 14 | 14.9% | PTG 5, SPT 5, 26 TOTS 4 |
| 5 | 미켈 메리노 | 13 | 13.8% | PTG 10 |

`--strict`(77명)에서도 순위 동일 (라이스 58.4%).

### ⑥ 챗봇 (`fco_meta/chatbot`)
두 가지 모드가 같은 도구(`tools.py`의 `Toolbox`)를 씁니다. 수치는 도구 결과에서만 나옵니다.

| 모드 | 방식 | 키 |
|---|---|---|
| **규칙 기반** `rules.py` (키 없을 때) | 질문에서 팀컬러(별칭 포함)·포메이션·역할·인원·예산·선수명을 뽑아 도구 1개 호출 → 정해진 형식으로 답변 | 불필요 |
| **Gemini (기본)** `gemini.py` | Gemini function calling 수동 루프 (기본 `gemini-3.8-flash`, 대체 `gemini-3.5-flash`), 자유 대화·복합 질문 | `GEMINI_API_KEY` (`.env`) |

| 도구 | 설명 |
|---|---|
| `resolve_terms(team_color, role)` | "아스날"→아스널(1004), "맨유"→맨체스터 유나이티드, "볼란치"→DM, 애매하면 후보 목록 |
| `list_available_data()` | 집계된 팀컬러×포메이션 조합, 표본 수, 기준 시각 |
| `list_formations(team_color)` | 스냅샷 포메이션 분포와 스쿼드 수집 여부 |
| `recommend_players(team_color, role, formation, top_n, strict, max_price_bp)` | 사용률 순위 + 시즌 카드별 사용 수·주 강화·그 강화의 시세, 예산 필터, 표본 부족 시 전체 포메이션 폴백 |
| `get_player_detail(name)` | 선수(부분 이름)의 팀컬러·포메이션·역할·시즌별 사용 현황과 시세 |

- 예산 비교 기준: 랭커들이 그 카드를 가장 많이 쓴 강화 단계의 최근 수집 시세
- 온디맨드 수집(데이터 없는 조합을 질문 시 바로 수집)은 아직 없음 → 수집 명령을 안내 (6단계 스케줄 수집과 함께 검토)
- `ranker-stats` 평균 스탯은 아직 미사용

---

## 4. 크롤링 운영 원칙
- robots.txt상 `/datacenter/*` 제한 없음 (확인 완료). NEXON 웹 이용약관은 서비스 공개 전 재확인
- 요청 간격 2초 이상, 동시 요청 1개, 데이터 갱신 주기(1시간)보다 자주 같은 페이지 요청 금지
- 원본 HTML 저장 → 재파싱 가능, 재요청 최소화
- 파서 테스트 + 파싱 성공률 급락 시 알림·수집 중단
- 서비스에는 집계 결과만 노출, 닉네임 목록 재배포 지양

---

## 5. 기술 스택
Python 3.12 · httpx · selectolax · tenacity · SQLite(MVP) → PostgreSQL · 규칙 기반 + Gemini API(function calling, 선택) · FastAPI + 정적 HTML/JS 웹 UI

```
fco_meta/
  crawler/      # rank_inner 수집·파싱
  openapi/      # Nexon Open API 클라이언트 (rate limiter: 초당 5, 일 1,000 예산 관리)
  pipeline/     # ouid 매핑, 스쿼드 수집, 정합성 검증
  analytics/    # 집계
  chatbot/      # 도구·프롬프트·대화 루프
  web/          # FastAPI / UI
data/           # teamcolors.json, 포지션·역할 사전, 별칭 사전
tests/fixtures/ # 랭킹 페이지 샘플
```

---

## 6. 로드맵

| 단계 | 내용 | 상태 |
|---|---|---|
| **0. 조사** | 랭킹 페이지 구조·필터·robots, Open API 스펙·한도 | ✅ 완료 |
| **1. 크롤러** | `rank_inner` 파서 + 조합 조회 + 스냅샷 저장 | ✅ 완료 (`fco_meta/crawler`) |
| **1-1. 시세 크롤러** | 데이터센터 선수 검색·시세 이력 수집 (팀컬러·포지션 필터, 강화별 현재가) | ✅ 완료 (`fco_meta/market`) |
| **2. Open API 클라이언트** | rate limiter·일일 예산, id/user/match/match-detail | ✅ 완료 (`fco_meta/openapi`) |
| **3. 파이프라인** | ouid 매핑, 스쿼드 수집, 정합성 검증 | ✅ 완료 (`fco_meta/pipeline`) |
| **4. 집계** | usage_stats, 아스널 4-2-3-1 볼란치로 end-to-end 검증 | ✅ 완료 (`fco_meta/analytics`) |
| **5. 챗봇** | 도구·프롬프트·별칭 사전, CLI 챗봇 | ✅ 완료 (`fco_meta/chatbot`, 규칙 기반 + Gemini) |
| **6. UI/운영** | 웹 UI, 스케줄 수집, 알림 | 웹 UI ✅ (`fco_meta/web`) · 매일 상위 330명 수집 ✅ (`fco_meta/daily.py`) · 알림 다음 |

---

## 7. 리스크
1. **API 한도**: 개발 키 1,000건/일 → 조합 단위 수집·캐시, 공개 시 서비스 키
2. **페이지 구조 변경**: 파서 테스트 + 원본 저장 + 알림
3. **시점 불일치**: ④ 정합성 검증
4. **닉네임 변경**: ouid 기준 저장, 조회 실패 시 다음 스냅샷에서 재시도
5. **표본 부족**: 표본 수 명시, 조건 완화 폴백
6. **이용약관**: 서비스 공개 전 NEXON 웹 약관·Open API 약관(출처 표기 등) 확인
