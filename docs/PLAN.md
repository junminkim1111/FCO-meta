# FCO 랭커 메타 챗봇 — 설계 계획 (v3: 0단계 조사 반영)

> 목표: "아스널 팀컬러, 4-2-3-1 포메이션에 쓸 볼란치 2명 추천해줘" 같은 질문에
> **실제 랭커 데이터 기반의 사용률·승률 근거**와 함께 선수를 추천하는 챗봇.

조사 상세: [`docs/RESEARCH.md`](RESEARCH.md) · API 스펙 원본: [`docs/nexon-openapi-spec/`](nexon-openapi-spec/)

---

## 1. 확정된 데이터 경로

| 필요한 정보 | 출처 | 비고 |
|---|---|---|
| 랭커 목록 (TOP 10,000) | 데이터센터 `GET /datacenter/rank_inner` | 서버 렌더링 HTML, 20명/페이지, 1시간 단위 갱신 |
| 팀컬러 | 같은 행의 `.team_color` (이름 + 해당 선수 수) | 이름 → ID는 `data/teamcolors.json` (804개) |
| 포메이션 | 같은 행의 `.formation` | |
| **팀컬러×포메이션 필터** | `rank_inner?tc_01={id}&formation_01={포메이션}` | 공식 페이지가 직접 필터링 (아스널+4-2-3-1 = 94명 확인) |
| 선수 구성 | Open API: 닉네임 → `id` → `user/match` → `match-detail` | 랭킹 페이지에는 스쿼드 없음 |
| 선수 평균 스탯 | Open API `ranker-stats` | 추천 근거 보강 |
| 선수명·시즌·포지션 | `/static/fconline/meta/*.json` | |

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

## 3. 파이프라인

```
① 랭킹 크롤러 (rank_inner)
     └▶ ranker_snapshot(snapshot_at, rank, nickname, elo, w/d/l, squad_value, team_color_id, tc_count, formation)
② 닉네임 → ouid (Open API /id, 캐시)
③ 스쿼드 수집: user/match(matchtype=50, limit=K) → match-detail → 해당 ouid의 player[]
④ 정합성 검증: 스냅샷 기준 시각 직전 경기인지, 추론 포메이션 = 페이지 포메이션인지
⑤ 집계: usage_stats (팀컬러 × 포메이션 × 역할 × 선수)
⑥ 챗봇: Claude tool use로 집계 DB 조회 → 근거 포함 답변
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

### ⑤ 집계
```
usage_stats(
  snapshot_date, team_color_id, formation, role,   -- role: '볼란치' = RDM/CDM/LDM(9,10,11)
  spid, pid, season_id,
  ranker_count, usage_rate, win_rate, avg_rating, avg_grade
)
```
- 사용률 분모 = 해당 조합 랭커 수 (예: 아스널 4-2-3-1 = 94명)
- 표본 부족(예: 10명 미만) → 챗봇이 표본 수를 명시하고 조건 완화 폴백

### ⑥ 챗봇 (Claude tool use)
| 도구 | 설명 |
|---|---|
| `recommend_players(team_color, formation, role, top_n, sort_by)` | 핵심 추천 (캐시 없으면 온디맨드 수집) |
| `get_player_detail(spid)` | 시즌·강화 분포, ranker-stats 평균 스탯 |
| `list_formations(team_color)` | 팀컬러별 랭커 포메이션 분포 |
| `list_team_colors()` | 팀컬러 픽률 |
| `resolve_alias(text)` | "볼란치"→DM, "아스날"→아스널(1004) 등 |

LLM은 자연어 해석·도구 호출·결과 설명만 하고, 수치는 도구 결과에서만 인용.

---

## 4. 크롤링 운영 원칙
- robots.txt상 `/datacenter/*` 제한 없음 (확인 완료). NEXON 웹 이용약관은 서비스 공개 전 재확인
- 요청 간격 2초 이상, 동시 요청 1개, 데이터 갱신 주기(1시간)보다 자주 같은 페이지 요청 금지
- 원본 HTML 저장 → 재파싱 가능, 재요청 최소화
- 파서 테스트 + 파싱 성공률 급락 시 알림·수집 중단
- 서비스에는 집계 결과만 노출, 닉네임 목록 재배포 지양

---

## 5. 기술 스택
Python 3.12 · httpx · selectolax · tenacity · SQLite(MVP) → PostgreSQL · Claude API(tool use) · FastAPI + Streamlit(MVP)

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
| **1. 크롤러** | `rank_inner` 파서 + 조합 조회 + 스냅샷 저장 | 다음 |
| **2. Open API 클라이언트** | 키 발급, rate limiter·일일 예산, id/user/match/match-detail | **API 키 필요** |
| **3. 파이프라인** | ouid 매핑, 스쿼드 수집, 정합성 검증 | |
| **4. 집계** | usage_stats, 아스널 4-2-3-1 볼란치로 end-to-end 검증 | |
| **5. 챗봇** | 도구·프롬프트·별칭 사전, CLI 챗봇 | |
| **6. UI/운영** | 웹 UI, 스케줄 수집, 알림 | |

---

## 7. 리스크
1. **API 한도**: 개발 키 1,000건/일 → 조합 단위 수집·캐시, 공개 시 서비스 키
2. **페이지 구조 변경**: 파서 테스트 + 원본 저장 + 알림
3. **시점 불일치**: ④ 정합성 검증
4. **닉네임 변경**: ouid 기준 저장, 조회 실패 시 다음 스냅샷에서 재시도
5. **표본 부족**: 표본 수 명시, 조건 완화 폴백
6. **이용약관**: 서비스 공개 전 NEXON 웹 약관·Open API 약관(출처 표기 등) 확인
