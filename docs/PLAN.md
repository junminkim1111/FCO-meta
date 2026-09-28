# FCO 랭커 메타 챗봇 — 설계 계획 (v2: 공식 랭킹 페이지 크롤링 방식)

> 목표: "아스널 팀컬러, 4-2-3-1 포메이션에 쓸 볼란치 2명 추천해줘" 같은 질문에
> **실제 랭커 데이터 기반의 사용률·승률 근거**와 함께 선수를 추천하는 챗봇.

> ⚠️ 작성 환경에서 fconline.nexon.com / openapi.nexon.com 접근이 막혀 있어, 페이지 구조·API 스펙은 직접 확인하지 못했습니다.
> **0단계(구조 조사)** 에서 확정한 뒤 이 문서를 갱신합니다. `[확인 필요]` 표시가 그 항목입니다.
> 0단계 진행 상황과 조사 스크립트 사용법은 [6.1](#61-0단계-구조-조사-진행-상황)을 참고하세요.

---

## 1. 방식 변경 요약

| 항목 | v1 (Open API만 사용) | **v2 (공식 랭킹 페이지 크롤링 + Open API)** |
|---|---|---|
| 랭커 목록 | 매치 수집 후 maxdivision으로 필터 | **랭킹 페이지에서 순위·닉네임 직접 수집** |
| 팀컬러 | 선수→클럽 매핑을 직접 구축해 추론 | **랭킹 페이지의 팀컬러 값 사용** |
| 포메이션 | 포지션 조합으로 추론 | **랭킹 페이지의 포메이션 값 사용** (추론은 검증용) |
| 선수 구성 | match-detail | 랭킹 페이지에 스쿼드가 있으면 그대로 사용, 없으면 **Open API match-detail** |

공식 데이터센터 안내 문구:
> 팀컬러·포메이션 정보는 모드별 최근 공식경기에 사용한 스쿼드 기준으로 수집되며, 팀컬러는 레벨 3 이상 적용 기준.

---

## 2. 데이터 출처

### 2.1 공식 데이터센터 (크롤링)
| 페이지 | 얻을 정보 |
|---|---|
| `fconline.nexon.com/datacenter/rank` (1vs1 공식경기) | 순위, 닉네임, 팀컬러(+레벨), 포메이션, 승률·랭킹포인트 등 `[확인 필요: 표시 항목, 페이지당 인원, 최대 페이지]` |
| 랭커 상세(클릭 시 팝업/요청) | 스쿼드 선수 목록 제공 여부 `[확인 필요]` |
| `/datacenter/teamcolor` | 팀컬러별 소속 선수 (검증·UI용, 선택) |

### 2.2 NEXON Open API (`https://open.api.nexon.com`, 헤더 `x-nxopen-api-key`)
| 용도 | 엔드포인트 |
|---|---|
| 닉네임 → ouid | `GET /fconline/v1/id?nickname=` |
| 유저 매치 목록 | `GET /fconline/v1/user/match?ouid=&matchtype=50&offset=0&limit=` |
| 매치 상세 (선수·포지션·강화·평점) | `GET /fconline/v1/match-detail?matchid=` |
| 랭커 선수 평균 스탯 | `GET /fconline/v1/ranker-stats?matchtype=50&players=[...]` |
| 메타데이터 | `/static/fconline/meta/{spid,seasonid,spposition,division}.json` |

---

## 3. 파이프라인

```
① 랭킹 크롤러 ──▶ ranker_snapshot (순위, 닉네임, 팀컬러, 포메이션, 수집시각)
                         │
② 닉네임→ouid (Open API, 캐시)
                         │
③ 스쿼드 수집 ──▶ (A) 랭킹 페이지에 스쿼드 있음 → 그대로 저장
                  (B) 없음 → user/match → match-detail → 해당 유저의 선발 11명
                         │
④ 정합성 검증 ──▶ 매치 스쿼드와 랭킹 페이지 팀컬러/포메이션이 일치하는지 확인
                         │
⑤ 집계 ──▶ usage_stats (팀컬러 × 포메이션 × 역할 × 선수)
                         │
⑥ 챗봇 (Claude tool use) ──▶ 집계 DB 조회 → 근거 포함 답변
```

### ① 랭킹 크롤러
- 수집 경로 결정 (0단계에서 확정):
  1. 페이지가 내부 XHR/Fetch로 HTML 조각·JSON을 받아오면 → **그 요청을 httpx로 직접 호출** (가장 가볍고 안정적)
  2. 서버 렌더링 HTML이면 → httpx + selectolax/BeautifulSoup 파싱
  3. JS 렌더링 필수면 → Playwright (최후 수단)
- 수집 범위: 1vs1 공식경기 상위 N명 (MVP: 상위 1,000명 → 확장 시 10,000명)
- 주기: 하루 1회. 스냅샷을 날짜별로 누적 저장 → 기간별 메타 변화 분석
- 팀컬러 표기 정규화: `"아스널"/"Arsenal"/"아스날"` → 표준 키, 레벨 분리 저장. 복수 팀컬러(클럽+국가 등)면 모두 저장 `[확인 필요: 표기 형식]`

### ② 닉네임 → ouid
- 닉네임은 바뀔 수 있으므로 **ouid를 기본 키**로 사용, 닉네임은 이력으로 보관
- 캐시: 이미 매핑된 닉네임은 재조회하지 않음, 조회 실패(닉변) 시 다음 스냅샷에서 재시도

### ③ 스쿼드 수집 (경로 B 기준)
- 랭커별 최근 공식경기 K개(예: 5경기) 조회 → match-detail에서 해당 ouid의 `player[]` 추출
- 선발 = `spPosition != 28 (SUB)`
- `spId` → 시즌ID(앞 3자리) + 선수 고유 ID 분리, 메타데이터로 이름·시즌명 조인
- 경기 결과(승/무/패), 선수 평점, 강화 단계(`spGrade`)도 저장

### ④ 정합성 검증 (중요)
랭킹 페이지의 팀컬러·포메이션은 "최근 공식경기 스쿼드" 기준이지만, 수집한 매치는 그보다 이전일 수 있습니다(스쿼드 변경).
- 포지션 조합으로 추론한 포메이션 ≠ 페이지 포메이션이면 해당 매치 제외
- 크롤링 시각과 가장 가까운 매치에 가중치, 오래된 매치(예: 3일 이상)는 제외
- (선택) teamcolor 페이지로 만든 선수→클럽 매핑으로 팀컬러 충족 여부 재확인

### ⑤ 집계
```
usage_stats(
  snapshot_date, period,            -- 최근 1일/7일/30일
  team_color, formation, role,      -- role 예: '볼란치' = RDM/CDM/LDM
  spid, pid, season_id,
  ranker_count, usage_rate, win_rate, avg_rating, avg_grade
)
```
- 사용률 분모 = 해당 (팀컬러 × 포메이션)을 쓰는 랭커 수
- 표본 부족(예: 랭커 10명 미만) 조합은 플래그 → 챗봇이 조건 완화 폴백

### ⑥ 챗봇 (LLM + Tool Use)
LLM은 **자연어 해석 + 도구 호출 + 결과 설명**만 하고, 수치는 도구 결과에서만 인용.

| 도구 | 설명 |
|---|---|
| `recommend_players(team_color, formation, role, top_n, sort_by, period)` | 핵심 추천 |
| `get_player_detail(spid)` | 시즌·강화 분포, ranker-stats 평균 스탯 |
| `list_formations(team_color)` | 팀컬러별 랭커 포메이션 분포 |
| `list_team_colors(formation?)` | 팀컬러 픽률 |
| `resolve_alias(text)` | "볼란치"→DM, "아스날"→아스널 등 별칭 해석 |

예시 흐름:
1. "아스널 팀컬러, 4-2-3-1, 볼란치 2명" → `recommend_players("아스널", "4-2-3-1", "DM", top_n=5)`
2. 결과: 선수·시즌·사용률·승률·랭커 수
3. 답변: "아스널 4-2-3-1 랭커 N명 기준, 볼란치로 A(x%)·B(y%)가 가장 많이 쓰였습니다…"

---

## 4. 크롤링 운영 원칙

- **약관·robots.txt 확인 후 진행** (0단계). 금지 범위면 크롤링 경로를 포기하고 v1 방식으로 회귀
- 요청 간격 최소 1~2초, 동시 요청 1개, 하루 1회 배치
- 응답 원본(HTML/JSON)을 저장해 재파싱 가능하게 → 같은 페이지 재요청 최소화
- **구조 변경 감지**: 저장한 샘플 페이지로 파서 테스트 작성, 파싱 성공률이 급락하면 알림 후 수집 중단
- 수집 데이터는 집계 결과만 서비스에 노출, 개인 닉네임 목록 재배포는 지양

---

## 5. 기술 스택 (제안)

| 영역 | 선택 |
|---|---|
| 언어 | Python 3.12 |
| 크롤링 | httpx + selectolax (필요 시 Playwright) |
| Open API | httpx(async) + tenacity (rate limit·재시도) |
| 저장 | SQLite (MVP) → PostgreSQL |
| 스케줄 | cron / GitHub Actions |
| LLM | Claude API (tool use) |
| 서버/UI | FastAPI + Streamlit (MVP) |

디렉터리 초안:
```
fco_meta/
  crawler/      # 랭킹 페이지 수집·파싱
  openapi/      # Nexon Open API 클라이언트
  pipeline/     # ouid 매핑, 스쿼드 수집, 정합성 검증
  analytics/    # 집계
  data/         # 메타데이터 캐시, 팀컬러 별칭, 포지션·역할 사전, 포메이션 패턴
  chatbot/      # 도구 정의, 프롬프트, 대화 루프
  web/          # FastAPI / UI
tests/fixtures/ # 저장된 랭킹 페이지 샘플
```

---

## 6. 로드맵

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **0. 조사** | 랭킹 페이지 Network 분석(XHR/HTML), 표시 항목·페이지네이션·스쿼드 제공 여부, 약관·robots 확인, Open API 키 발급 | 수집 경로 확정, 샘플 응답 저장 |
| **1. 크롤러** | 랭킹 수집·파싱·정규화, 스냅샷 저장 | 상위 1,000명 팀컬러·포메이션 적재 |
| **2. 스쿼드** | ouid 매핑, match-detail 수집(또는 페이지 스쿼드 파싱) | 랭커 90% 이상 스쿼드 확보 |
| **3. 검증·집계** | 포메이션 정합성 검증, usage_stats 생성 | SQL로 예시 질문에 답 가능 |
| **4. 챗봇** | 도구·프롬프트·용어 사전, CLI 챗봇 | 예시 질문 10개에 근거 있는 답변 |
| **5. UI/운영** | 웹 UI, 일일 배치 자동화, 구조 변경 알림 | 외부에서 사용 가능 |

### 6.1 0단계 구조 조사 진행 상황

**상태: 미완료 — 조사 스크립트만 준비됨 (2026-09-28)**

클라우드 세션의 네트워크 정책이 `fconline.nexon.com`(및 `nexon.com` 계열) 접속을 차단(프록시 403)해서
robots.txt·약관·페이지 구조를 직접 확인하지 못했습니다. 접속 가능한 환경(로컬 PC 또는 해당 도메인을 허용한 환경)에서
아래 스크립트를 실행하면 조사 결과가 `tests/fixtures/datacenter/`에 저장됩니다.

```bash
pip install playwright && playwright install chromium   # 이미 설치돼 있으면 생략
python scripts/survey_datacenter.py                      # 결과: tests/fixtures/datacenter/
python scripts/survey_datacenter.py --headed             # 셀렉터가 안 맞을 때 화면 보면서 확인
```

스크립트가 수행하는 단계 (단계 사이 2초 대기, 동시 요청 1개):

| 단계 | 동작 | 기록 |
|---|---|---|
| 01_rank_initial | `/datacenter/rank` 접속 | 최초 로딩 시 요청, DOM, 스크린샷 |
| 02_official_1v1 | "1vs1 공식경기" 선택 | 모드 선택 요청 |
| 03_page_2 | 페이지 2 클릭 | 페이지 넘김 요청 (URL·메서드·파라미터) |
| 04_mode_switch / 05_back_to_1v1 | 다른 모드 전환 후 복귀 | 모드 파라미터 |
| 06_ranker_click | 1위 랭커 클릭 (팝업 포함) | 랭커 상세 요청, 스쿼드 제공 여부 |
| 07_teamcolor | `/datacenter/teamcolor` 접속 | 팀컬러 페이지 구조 |

각 단계 폴더에 `requests.jsonl`(URL, 메서드, 쿼리/POST 파라미터, 상태, content-type), XHR/Fetch/문서 응답 본문(`bodies/`),
`page.html`, `screenshot.png`, 랭킹 영역 텍스트(`text.txt`)가 남고, `summary.json`에 셀렉터 탐색 성공/실패가 기록됩니다.
셀렉터는 구조 미확인 상태의 후보값이므로, 실패한 단계는 `--headed`로 확인해 스크립트 상단 후보 목록에 추가합니다.

조사 후 채울 항목 (현재 모두 `[확인 필요]`):

- [ ] robots.txt에서 `/datacenter/` 경로 허용 여부 (`tests/fixtures/datacenter/robots.txt`)
- [ ] 이용약관·운영정책의 자동 수집(크롤링·매크로) 관련 조항 — 스크립트로 확인 불가, 넥슨 약관 페이지를 사람이 직접 확인
- [ ] 랭킹 데이터 전달 방식: XHR(JSON/HTML 조각) / 서버 렌더링 / JS 렌더링 → ① 수집 경로 1·2·3 중 결정
- [ ] 페이지 넘김·모드 전환 요청의 URL·메서드·파라미터, 페이지당 인원, 최대 페이지
- [ ] 랭커 1명당 표시 항목: 팀컬러 표기 형식·레벨·복수 여부, 포메이션, 승률·랭킹포인트 등
- [ ] 랭커 클릭 시 스쿼드 선수 목록 제공 여부 → ③ 경로 A/B 결정
- [ ] `/datacenter/teamcolor` 구조 (팀컬러 목록, 소속 선수 제공 방식)

---

## 7. 리스크

1. **약관/차단**: 크롤링 제한 시 v1(Open API + 자체 추론)로 회귀 가능하도록 파이프라인 분리
2. **페이지 구조 변경**: 파서 테스트 + 원본 저장 + 알림
3. **시점 불일치**: 페이지 팀컬러/포메이션과 수집 매치 스쿼드가 다를 수 있음 → ④ 정합성 검증
4. **닉네임 변경**: ouid 기준 저장, 매핑 실패 재시도
5. **표본 부족**: 비인기 팀컬러×포메이션 조합 → 표본 수 명시, 조건 완화 폴백
6. **Open API 호출 한도**: 랭커 1,000명 × (1 + K)회 호출 → 키 등급에 맞게 K·N 조정
