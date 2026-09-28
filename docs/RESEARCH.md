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

- 팀컬러는 행당 1개만 표시됨. **특수 팀컬러(예: "Winning Streak")가 적용된 랭커는 그 이름이 표시**되고,
  클럽 엠블럼(`crests/.../l1.png`)과 특수 팀컬러 아이콘(`teamcolorboost/.../4_l999848.png`)이 함께 나옴
  → 아스널 필터 결과 94명 중 1명이 "Winning Streak (10명)"으로 표시됨.
  **팀컬러 소속은 표시 이름이 아니라 "해당 팀컬러 필터 결과에 포함됐는지"로 판단해야 함**
- **스쿼드 선수 목록은 제공되지 않음** (프로필 툴팁 `/Profile/Common/ToolTip/{sn}`에도 없음) → Open API 필요

### 팀컬러 ID 목록
- 껍데기 페이지의 팀컬러 선택 목록에서 추출 → `data/teamcolors.json` (890개: 클럽 648, 국가 210, 특수 32)
  - 각 항목: `id`, `name`, `category`(club/nationality/special), `group_id`(리그 id / 대륙 id)
  - 같은 이름이 클럽·국가 양쪽에 있는 경우가 있음 (예: "잉글랜드" club 1318 / nationality 2006)
  - 일부 국가 항목은 id가 겹침(예: 여러 국가가 id 2) → 국가 팀컬러 필터는 사용 전 검증 필요
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

---

# 시세 데이터 조사 (2026-09-28)

## 결론
- **NEXON Open API에는 시세 데이터가 없음** (`user/trade`는 본인 거래 기록만 조회 가능)
- **공식 데이터센터 선수 검색/상세 페이지가 시세를 제공**하며, AJAX로 받아오는 HTML 조각을 그대로 수집할 수 있음 (robots.txt상 `/datacenter/*` 제한 없음)

## 1. 선수 목록 + 강화별 현재가: `POST /datacenter/PlayerList`
- 선수 검색 폼(`#form1`)을 그대로 POST. 응답은 카드 단위 HTML (`<div id="area_playerunit_{spid}">`)
- 카드마다: spid, 이름, 시즌 아이콘(`/season/26TOTY.png`), 대표 포지션, 급여(`.pay`), 평점(`.td_ar_score`, 예: `8.2 (49)`),
  **1~13강 현재가** (`.span_bp{n}[title]`, 예: `span_bp8 title="229,000,000"`)
- 주요 필터
  | 파라미터 | 의미 | 예 |
  |---|---|---|
  | `teamcolorid` | 팀컬러 id (`data/teamcolors.json`과 같은 체계) | `1004` (아스널) |
  | `strPosition` | 포지션 코드 목록(spposition), 쉼표로 감쌈 | `,9,10,11,` (RDM/CDM/LDM) |
  | `strPlayerName` | 선수명 | `사카` |
  | `n8PlayerGrade1Min/Max` | 가격 범위로 추정 (미검증) | |
  | `strSeason`, `n4LeagueId`, `n4TeamId`, `n4NationId`, `n4OvrMin/Max`, `n4SalaryMin/Max` 등 | 기타 검색 조건 | |
- 검증: `teamcolorid=1004&strPosition=,9,10,11,` → 아스널 팀컬러 해당 + 볼란치 가능 카드 (라이스, 비에이라, 기마랑이스 …)와 강화별 시세
- **요청당 최대 200장, 응답 약 1.8MB** → 200장에 걸리면 시즌·가격 등 조건을 쪼개서 조회해야 함 (페이지 파라미터 `n4PageNo`는 UI에서 항상 1로만 호출됨)
- 부가 효과: **"선수 → 해당 팀컬러" 매핑도 여기서 얻을 수 있음** (랭커가 안 쓰는 선수도 팀컬러 적격 여부 판단 가능)

## 2. 일별 시세 이력: `POST /datacenter/PlayerPriceGraph`
- 파라미터: `spid`, `n1strong`(강화 1~13)
- 응답 HTML 안의 `var json1 = { "time": [...], "value": [...] }` → **최근 365일 일별 시세** (JSON 끝에 trailing comma가 있어 정규식 파싱 필요)
- 현재가는 `title="3,170,000"` 속성
- 예: spid 100005471, 1강 → 365개 포인트, 현재가 3,170,000 BP

## 3. FC-RANK는 어디서 가져오나 (추정)
FC-RANK(fc-rank.com)는 Next.js(Vercel) + Supabase로 만든 사이트이고, 브라우저는 자체 API만 호출함:
- `/api/player-price-history?spid=&grade=` ↔ 공식 `PlayerPriceGraph(spid, n1strong)`와 파라미터 구조가 동일
- `/api/player-db/price-cache?spId=&spGrade=` → 응답에 `priceCache.price`, `fetched_at` → **서버가 시세를 받아 DB에 캐시**
- `/api/power-ranking/team-pack/prices?...&priceFormat=snapshot` → 팀 단위 시세 스냅샷
- 사이트 문구: "FC Online 데이터센터의 팀컬러·구단가치별 랭커 명단을 기준으로 수집", "NEXON Open API 및 공개 데이터를 기반으로 제공"

→ Open API에 시세가 없으므로, **서버에서 공식 데이터센터(PlayerList/PlayerPriceGraph)를 수집해 Supabase에 `fetched_at`과 함께 캐시**하는 구조로 보는 것이 가장 자연스러움.
서버 코드는 볼 수 없으므로 추정이며, 다른 공개 시세 출처는 확인되지 않음.

## 4. 구현하며 확인한 것 (시세 크롤러)
- `n4PageNo`는 서버에서 무시됨 (2페이지 요청 = 1페이지와 동일) → 200장 초과 시 조건 분할 필요
- `n8PlayerGrade1Min/Max`(가격 범위)도 무시됨 (UI 코드에서도 주석 처리)
- **동작하는 필터**: `teamcolorid`, `strPosition`, `strPlayerName`, `n4SalaryMin/Max`
- 정렬: `strOrderby=" n8playergrade1 descending"` (1강 가격 내림차순) 동작
- 분할 전략: 급여 구간 이분 → 급여 1개 구간에서도 넘치면 포지션 묶음(GK/DF/MF/FW)으로 분할.
  아스널 팀컬러 × 볼란치: 292장 / 8회 요청 / 약 15초
- 1강 가격이 `0`인 카드 존재 → 가격 정보 없음(None)으로 저장
- 시즌 코드가 `_B`로 끝나는 카드(예: `ICONTM_B`)는 강화와 관계없이 가격이 동일 → 거래 불가/귀속 카드로 추정 (미검증). 추천 시 제외 여부 결정 필요
- 시세 이력: 1년간 수십 배 상승한 카드가 있으나 하루 최대 상승폭은 1.3~1.7배 수준 → 단위 변경이 아닌 실제 시세 흐름으로 판단

## 5. 우리 설계에 반영할 점
- 시세 수집 = 데이터센터 `PlayerList` (팀컬러 × 포지션 단위로 조회하면 추천 후보 + 시세를 한 번에 확보)
- 이력 그래프가 필요할 때만 `PlayerPriceGraph` 개별 호출 (카드 × 강화 단위라 호출 수가 많음 → 온디맨드 + 캐시)
- 응답이 크므로(1.8MB) 캐시 기간을 두고, 요청 간격을 랭킹 크롤러와 같게(2초 이상) 유지
- 약관: 랭킹 페이지와 마찬가지로 넥슨 웹 이용약관 확인 필요 (FC-RANK도 같은 방식으로 운영 중인 것으로 보이나, 그것이 허용을 의미하지는 않음)
