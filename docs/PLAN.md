# FCO 랭커 메타 챗봇 — 설계 계획

> 목표: "아스널 팀컬러, 4-2-3-1 포메이션에 쓸 볼란치 2명 추천해줘" 같은 질문에
> **실제 랭커 경기 데이터 기반의 사용률·승률 근거**와 함께 선수를 추천하는 챗봇.

> ⚠️ 이 문서의 API 스펙은 NEXON Open API 문서(openapi.nexon.com/game/fconline)를 기준으로 작성했지만,
> 작성 환경에서 문서 사이트 접근이 막혀 있어 **파라미터·응답 필드·호출 한도는 구현 전에 실제 문서로 재확인**이 필요합니다.

---

## 1. 핵심 문제: API가 직접 주지 않는 정보

| 필요한 정보 | API 제공 여부 | 해결 방법 |
|---|---|---|
| 랭커가 누구인지 | ❌ 랭킹 목록 API 없음 | 공식경기(matchtype=50) 매치를 수집 → 참가 유저의 `maxdivision`으로 상위 등급(슈퍼챔피언스 등)만 필터 |
| 스쿼드에 쓰인 선수·포지션 | ✅ `match-detail`의 `player[]` (spId, spPosition, spGrade, status) | 그대로 사용 |
| 포메이션 | ❌ 필드 없음 | 선발 11명의 `spPosition` 조합으로 **추론** |
| 팀컬러 | ❌ 필드 없음, spid.json에도 소속팀 없음 | **선수→클럽 매핑 테이블을 별도 구축**해 스쿼드 구성으로 추론 (가장 큰 데이터 공백) |
| 선수 이름·시즌 | ✅ 메타데이터 (spid.json, seasonid.json, spposition.json) | 캐시 후 조인 |
| 랭커들의 선수별 평균 스탯 | ✅ `ranker-stats` (spid+포지션 지정 필요) | 추천 근거 보강용(태클·인터셉트 등) |

즉, **"수집 → 가공(포메이션/팀컬러 추론) → 집계 → LLM이 집계 DB를 도구로 조회"** 구조가 필요합니다.
LLM이 API를 실시간으로 직접 뒤지는 방식은 호출 한도·응답 속도·정확도 모두에서 불리합니다.

---

## 2. 사용할 API (재확인 필요)

공통: `https://open.api.nexon.com`, 헤더 `x-nxopen-api-key: <KEY>`

| 용도 | 엔드포인트 |
|---|---|
| 전체 매치 목록 | `GET /fconline/v1/match?matchtype=50&offset=&limit=&orderby=desc` |
| 매치 상세 | `GET /fconline/v1/match-detail?matchid=` |
| 유저 최고 등급 | `GET /fconline/v1/user/maxdivision?ouid=` |
| 유저 기본정보 | `GET /fconline/v1/user/basic?ouid=` |
| 닉네임→ouid | `GET /fconline/v1/id?nickname=` |
| 랭커 선수 평균 스탯 | `GET /fconline/v1/ranker-stats?matchtype=50&players=[{"id":spid,"po":pos}]` |
| 메타데이터 | `/static/fconline/meta/{spid,seasonid,spposition,matchtype,division}.json` |
| 선수 이미지 | `/static/fconline/...` (UI용, 선택) |

---

## 3. 전체 아키텍처

```
[Collector]  ──▶  [Raw Store]  ──▶  [Processor]  ──▶  [Analytics DB]  ◀──  [Chatbot API (LLM + Tools)]  ◀──  [Web UI]
 스케줄러로        match-detail       선발 추출            사용률/승률           Claude tool use                  채팅 화면
 매치 수집         JSON 원본 저장     포메이션·팀컬러 추론   집계 테이블           (조회 도구만 호출)
```

### 3.1 Collector (배치)
1. `match?matchtype=50`으로 최신 매치 ID를 주기적으로 수집 (이미 본 matchid는 skip).
2. `match-detail` 조회 → 원본 JSON 저장.
3. 매치 참가자 ouid별로 `user/maxdivision` 조회 (**ouid 단위 캐시, 하루 1회 갱신**)
   → 랭커 기준(예: 슈퍼챔피언스/챔피언스) 미달이면 해당 스쿼드는 집계 제외.
4. 호출 한도 대응: 토큰 버킷 rate limiter + 지수 백오프 + 재개 가능한 체크포인트.

### 3.2 Processor
스쿼드(매치×유저) 단위로:
- **선발 추출**: `spPosition != 28(SUB)` 인 11명.
- **포메이션 추론**: 포지션 코드 → 라인 그룹(GK/DF/DM/CM/AM/FW)으로 매핑 후 패턴 테이블과 매칭.
  - 예) `4-2-3-1` = GK + DF 4 + {RDM,LDM 또는 CDM 2} + {RAM,CAM,LAM 또는 RM,CAM,LM} + ST
  - FC온라인 포메이션은 세부 변형(4-2-3-1 와이드/내로우 등)이 있으므로 **사전 정의 패턴 테이블**을 두고, 미매칭은 `unknown`으로 저장 후 추후 보강.
- **팀컬러 추론**: 선수→클럽(이력) 매핑으로 선발 11명(+후보)의 클럽별 커버리지 계산 →
  기준치(예: 선발 전원 또는 N명 이상) 충족 클럽을 팀컬러로 태깅. 복수 태깅 허용.
- **시즌 분리**: `spId`에서 시즌ID(앞 3자리)와 선수 고유 pid 분리 → "같은 선수 다른 시즌" 비교 가능.
- 결과: `squad_player` 행 (matchid, ouid, spid, season, pid, position, grade(강화), rating, 결과(승/무/패), 포메이션, 팀컬러[])

### 3.3 Analytics DB (집계)
```
usage_stats(
  period, team_color, formation, position_role,   -- 예: '볼란치' = RDM/CDM/LDM
  spid, pid, season_id,
  squads_used, usage_rate, win_rate, avg_rating, avg_grade
)
```
- 기간(최근 7일/30일)별 스냅샷 유지 → "요즘 메타" 대응.
- 최소 표본 수(예: 20 스쿼드) 미달 조합은 "표본 부족" 플래그.

### 3.4 Chatbot (LLM + Tool Use)
LLM은 **자연어 해석 + 도구 호출 + 결과 설명**만 담당하고, 수치는 반드시 도구 결과에서만 인용.

도구 예시:
| 도구 | 설명 |
|---|---|
| `recommend_players(team_color, formation, role, top_n, sort_by, period)` | 핵심 추천 도구 |
| `get_player_detail(spid)` | 시즌·강화 분포, ranker-stats 평균 스탯 |
| `list_formations(team_color)` | 해당 팀컬러 랭커들이 많이 쓰는 포메이션 |
| `resolve_alias(text)` | "볼란치"→DM, "아스날/Arsenal"→아스널, "베카"→선수 등 별칭 해석 |

처리 흐름 (예시 질문):
1. "아스널 팀컬러, 4-2-3-1, 볼란치 2명" → `recommend_players(team_color="Arsenal", formation="4-2-3-1", role="DM", top_n=5)`
2. 결과: 선수명·시즌·사용률·승률·표본 수
3. LLM 답변: "랭커 N개 스쿼드 기준, 가장 많이 쓰인 조합은 A(사용률 x%), B(y%)…" + 대안/조합 설명

용어 사전(도메인 지식)을 시스템 프롬프트/도구로 제공:
- 볼란치 = DM(RDM/CDM/LDM), 풀백 = RB/LB(+WB), 윙어 = RW/LW/RM/LM, 원톱 = ST 1명 …

---

## 4. 기술 스택 (제안)

| 영역 | 선택 | 이유 |
|---|---|---|
| 언어 | Python 3.12 | 데이터 처리·LLM SDK 생태계 |
| HTTP | httpx (async) + tenacity | rate limit/재시도 |
| 저장 | SQLite(MVP) → PostgreSQL, 집계는 DuckDB도 고려 | 초기 간단, 확장 용이 |
| 스케줄러 | cron / GitHub Actions / APScheduler | 주기 수집 |
| LLM | Claude API (tool use) | 도구 호출 기반 답변 |
| 서버 | FastAPI | 챗봇 API |
| UI | Streamlit(MVP) → Next.js | 빠른 검증 후 확장 |

디렉터리 초안:
```
fco_meta/
  api/          # Nexon API 클라이언트 (rate limiter 포함)
  collector/    # 매치·유저 수집 잡
  processor/    # 포메이션/팀컬러 추론, 정규화
  analytics/    # 집계 쿼리
  data/         # 메타데이터 캐시, 선수-클럽 매핑, 포메이션 패턴, 용어 사전
  chatbot/      # LLM 도구 정의, 프롬프트, 대화 루프
  web/          # FastAPI / UI
tests/
```

---

## 5. 단계별 로드맵

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **0. 검증** | API 키 발급, 각 엔드포인트 실제 응답 확인, 호출 한도·데이터 보존 기간 확인 | 샘플 응답 JSON 저장, 스펙 문서 확정 |
| **1. 수집** | API 클라이언트 + 매치/유저 수집기 + 랭커 필터 | 하루치 랭커 스쿼드 수천 건 적재 |
| **2. 가공** | 포메이션 추론, 시즌 분리, 메타데이터 조인 | 포메이션 분류율 90%+ (수동 샘플 검수) |
| **3. 팀컬러** | 선수→클럽 매핑 구축(우선 인기 클럽 5~10개), 팀컬러 태깅 | 아스널 등 주요 팀컬러 스쿼드 식별 |
| **4. 집계** | usage_stats 생성, 기간별 스냅샷 | SQL로 예시 질문에 답 가능 |
| **5. 챗봇** | 도구 정의, 시스템 프롬프트, 용어 사전, CLI 챗봇 | 예시 질문 10개에 근거 있는 답변 |
| **6. UI/배포** | 웹 UI, 스케줄 수집 자동화, 모니터링 | 외부에서 사용 가능 |

MVP 범위: 공식경기(50) × 상위 등급 × 주요 팀컬러 몇 개 × CLI 챗봇.

---

## 6. 리스크 & 결정 필요 사항

1. **랭커 정의**: `maxdivision` 기반(슈퍼챔피언스만? 챔피언스까지?) vs 공식 랭킹 페이지 참고.
   랭킹 페이지 스크래핑은 약관 확인 필요 → 기본안은 `maxdivision` 기반.
2. **팀컬러 데이터 소스**: API에 없음. 선수 클럽 이력 데이터를 어디서 가져올지(수동 구축 / 공식 데이터센터 / 커뮤니티 데이터)가 최대 과제.
   대안: 초기엔 "선발 대다수가 같은 클럽 이력을 공유"하는 스쿼드를 클러스터링해 반자동 태깅.
   - 참고: 공식 데이터센터 랭킹 페이지(fconline.nexon.com/datacenter/rank)는 랭커별 **팀컬러·포메이션을 표시**함
     ("최근 공식경기 스쿼드 기준, 레벨 3 이상 팀컬러 적용 기준"). fc-rank.com 같은 외부 사이트는 Open API가 아니라
     이 페이지(또는 페이지가 호출하는 내부 요청)를 수집하는 것으로 추정됨 → 약관·robots 확인 후 활용 여부 결정.
   - 선수→클럽 매핑은 데이터센터 팀컬러 페이지(/datacenter/teamcolor)와 선수 상세 페이지가 후보 출처.
3. **호출 한도**: 개발 단계 키는 한도가 낮을 수 있음 → 수집량 설계에 직접 영향. 필요 시 서비스 단계 키 신청.
4. **포메이션 변형**: 세부 변형 매핑 테이블 필요, 유저가 경기 중 포메이션을 바꾸는 경우의 잡음.
5. **표본 편향**: 특정 팀컬러×포메이션 조합은 표본이 적을 수 있음 → 답변에 표본 수 명시, 부족 시 상위 조건 완화(팀컬러만/포메이션만) 폴백.
6. **이용 약관**: NEXON Open API 이용 약관의 출처 표기·상업적 이용 조건 확인.
