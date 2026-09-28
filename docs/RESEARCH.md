# 0단계 조사 결과 (2026-09-28)

## 1. 공식 데이터센터 랭킹 페이지

### robots.txt
```
User-agent: Bingbot
Disallow: /community/*
Disallow: /news/*
```
`/datacenter/*`에 대한 제한 없음. (NEXON 웹사이트 이용약관은 별도 확인 필요)

### 구조
- `GET /datacenter/rank?rt=1vs1` : 껍데기 페이지. 필터 UI, 팀컬러 ID 목록, **TOP 10 팀컬러/포메이션 픽률** 포함
- 목록은 AJAX로 로드: **`GET /datacenter/rank_inner?{params}`** → 서버 렌더링 HTML 조각 (JS 렌더링 불필요, httpx로 충분)
- 20명/페이지, 최대 500페이지 (= TOP 10,000)
- 데이터 갱신: **1시간 단위** ("2026-09-28 20:00:00 기준 데이터", KST로 보임)
- 안내 문구: "팀컬러 및 포메이션 정보는 가장 최근에 각 모드 공식경기에 사용한 스쿼드 정보 기준으로 수집됩니다."

### `rank_inner` 파라미터 (`goSearchDetail()` 분석)
| 파라미터 | 의미 | 기본값 |
|---|---|---|
| `rt` | 모드 (`1vs1`, `2vs2`, `manager`) | `1vs1` |
| `n4seasonno` | 시즌 (0 = 현재) | `0` |
| `n4pageno` | 페이지 | `1` |
| `tc_01`, `tc_02` | 팀컬러 ID (최대 2개 조건) | `0` |
| `tc_l_01`, `tc_l_02` | 리그 단위 팀컬러 ID | `0` |
| `tc_c_01`, `tc_c_02` | 대륙 단위 팀컬러 ID | `0` |
| `tc_01_cnt_s/e`, `tc_02_cnt_s/e` | 팀컬러 해당 선수 수 범위 | `1` / `11` |
| `formation_01`, `formation_02` | 포메이션 (`4-2-3-1`, 또는 `3`/`4`/`5` = n백 전체) | `-` |
| `cv_s`, `cv_e` | 구단가치 범위 | `0` / `18000000000000000000` |
| `tier_s`, `tier_e` | 등급 범위 (3100=유망주3부 … 900=챔피언스, 800=슈퍼챔피언스) | |
| `rank_s`, `rank_e` | 순위 범위 | `1` / `10000` |
| `strCharacterName` | 구단주명 검색 | |

**검증:** `tc_01=1004`(아스널) + `formation_01=4-2-3-1` → "94명의 구단주님이 검색 되었습니다." 
→ **팀컬러×포메이션 필터링을 공식 페이지가 직접 해줌.**

### 행(row) 필드
| 필드 | 셀렉터/예시 |
|---|---|
| 순위 | `.td.rank_no` → `1` |
| 구단주명 | `.name.profile_pointer` → 닉네임, `data-sn` = 넥슨 회원번호(Open API ouid와 다름) |
| 레벨 | `.lv .txt` |
| 구단가치 | `.price[title]` → `16,708,213,680` |
| 랭킹점수(ELO) | `.td.rank_r_win_point` → `3615.87` |
| 승률/승무패 | `.td.rank_before .top` / `.bottom` → `72.1%`, `142 | 0 | 55` |
| 팀컬러 | `.td.team_color .inner` → `아스널 <small>(11명)</small>`, 엠블럼 `crests/light/medium/l1.png` |
| 포메이션 | `.td.formation` → `4-2-3-1` |
| 최고 등급 | `.td.rank_best` 이미지(ico_rankN) |

- 팀컬러는 행당 1개만 표시됨 (샘플 20행 기준)
- **스쿼드 선수 목록은 제공되지 않음** (프로필 툴팁 `/Profile/Common/ToolTip/{sn}`에도 없음) → Open API 필요

### 팀컬러 ID 목록
- 껍데기 페이지의 `select_tc(id, 'name')` 호출에서 추출 → `data/teamcolors.json` (804개)
  - `< 1000`: 국가, `1000~`: 클럽(예: 1000 맨시티, 1004 아스널), `40000~`: 시즌/특수 팀컬러 등
- 팀컬러 이름 검색: `POST /datacenter/rank_tc` (`search_tc=아스널`) → `data-no="1004"`

## 2. NEXON Open API

스펙 원본: `docs/nexon-openapi-spec/*.yaml` (openapi.nexon.com이 내려주는 YAML)

| 엔드포인트 | 용도 |
|---|---|
| `GET /fconline/v1/id?nickname=` | 닉네임 → ouid |
| `GET /fconline/v1/user/basic?ouid=` | 기본 정보 |
| `GET /fconline/v1/user/maxdivision?ouid=` | 역대 최고 등급 |
| `GET /fconline/v1/user/match?ouid=&matchtype=&offset=&limit=` | 유저 매치 ID 목록 (최신순, limit 최대 100) |
| `GET /fconline/v1/match-detail?matchid=` | 매치 상세 (`matchInfo[].player[]`: spId, spPosition, spGrade, status.spRating 등) |
| `GET /fconline/v1/ranker-stats?matchtype=&players=` | TOP 10,000 랭커의 선수별 20경기 평균 스탯 (1회 50명 권장) |
| `/static/fconline/meta/*.json` | spid, seasonid, spposition, matchtype, division |

- ⚠️ v1 계획에서 가정한 **전체 매치 목록(`/fconline/v1/match`)은 현재 스펙에 없음** → 랭커 목록을 외부(랭킹 페이지)에서 얻어야 하는 이유가 더 분명해짐
- 인증 헤더: `x-nxopen-api-key`

### 호출 한도
| 키 타입 | 초당 | 일일 |
|---|---|---|
| 개발 단계 | 5 | **1,000** |
| 서비스 단계 | 500 | 20,000,000 |

에러: `429 OPENAPI00007` 호출량 초과, `400 OPENAPI00009` 데이터 준비 중, `400 OPENAPI00010` 게임 점검 중

## 3. 결론
- 랭커·팀컬러·포메이션: **`rank_inner` 크롤링** (필터 파라미터로 조합별 조회 가능)
- 선수 구성: 닉네임 → `id` → `user/match` → `match-detail`
- 개발 키 1,000건/일 제약 때문에 전수 수집보다 **조합 단위(팀컬러×포메이션) 수집 + 캐시**가 현실적
