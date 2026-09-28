# FCO-meta

FC온라인 랭커 데이터를 기반으로 선수를 추천하는 챗봇 프로젝트입니다.
계획: [`docs/PLAN.md`](docs/PLAN.md) · 조사 결과: [`docs/RESEARCH.md`](docs/RESEARCH.md)

## 설치

```bash
pip install -e ".[dev]"
cp .env.example .env   # NEXON_API_KEY 입력 (Open API 단계부터 필요)
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

## 테스트

```bash
pytest
```
