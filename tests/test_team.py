"""@닉네임 team lookup (fake NEXON Open API, tiny DB)."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from fco_meta.openapi.errors import NotFoundError
from fco_meta.web.team import TeamNotFound, fetch_team

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def match_id(when: datetime, n: int) -> str:
    return f"{int(when.timestamp()):08x}{n:016x}"


def detail(mid: str, when: datetime, ouid: str, players: list[tuple[int, int, int]]) -> dict:
    return {
        "matchId": mid, "matchDate": when.strftime("%Y-%m-%dT%H:%M:%S"),
        "matchInfo": [{"ouid": "상대", "player": []},
                      {"ouid": ouid, "player": [{"spId": s, "spPosition": p, "spGrade": g} for s, p, g in players]}],
    }  # fmt: skip


class FakeApi:
    def __init__(self, matches: dict[int, tuple[datetime, list]]):
        self.matches = {mt: (match_id(when, mt), when, players) for mt, (when, players) in matches.items()}
        self.calls = []

    def get_ouid(self, nickname):
        self.calls.append("id")
        if nickname != "레몬":
            raise NotFoundError(400, "OPENAPI00004", "not found")
        return "ou1"

    def user_matches(self, ouid, matchtype, limit=10):
        self.calls.append(f"match{matchtype}")
        return [self.matches[matchtype][0]] if matchtype in self.matches else []

    def match_detail(self, mid):
        self.calls.append("detail")
        mt = next(k for k, v in self.matches.items() if v[0] == mid)
        return detail(mid, self.matches[mt][1], "ou1", self.matches[mt][2])


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE meta_spid (sp_id INTEGER, name TEXT);
        CREATE TABLE meta_season (season_id INTEGER, class_name TEXT);
        CREATE TABLE card (spid INTEGER, salary INTEGER);
        CREATE TABLE card_price_latest (spid INTEGER, grade INTEGER, price INTEGER);
        CREATE TABLE ranker_squad (ouid TEXT, match_id TEXT, match_order INTEGER);
        CREATE TABLE match (match_id TEXT, match_date TEXT);
        CREATE TABLE match_player (match_id TEXT, ouid TEXT, sp_id INTEGER, sp_position INTEGER, sp_grade INTEGER);
        CREATE TABLE card_detail (spid INTEGER, clubs TEXT, nation TEXT);
        INSERT INTO meta_spid VALUES (101000001, '골키퍼'), (101000002, '공격수'), (101000003, '교체');
        INSERT INTO meta_season VALUES (101, 'UC (Ultimate Champions)');
        INSERT INTO card VALUES (101000001, 15), (101000002, 30);
        INSERT INTO card_price_latest VALUES (101000002, 8, 500000000);
    """)
    return c


XI = [(101000002, 25, 8), (101000001, 0, 5), (101000003, 28, 1)]  # ST, GK, 교체


def test_newest_official_or_friendly_starting_eleven(conn):
    api = FakeApi({50: (NOW - timedelta(days=3), XI), 60: (NOW - timedelta(days=1), XI[:2])})
    team = fetch_team(api, conn, "레몬", now=NOW)
    assert team.source == "공식 친선" and not team.old  # 하루 전 친선이 사흘 전 공식경기보다 최근
    assert [(p["role"], p["player"], p["season"], p["grade"]) for p in team.lineup] == [("GK", "골키퍼", "UC", 5), ("ST", "공격수", "UC", 8)]
    assert team.totals == {"salary": 45, "price": "5억", "price_bp": 500000000, "unpriced": 1}
    block = team.for_model()
    assert block.startswith("[사용자 팀: @레몬 · ") and "공식" not in block.splitlines()[0] and "2026" not in block  # 경기 종류·날짜는 안 보낸다
    assert "팀컬러: 알 수 없음" in block and "ST 공격수 (UC, 8강, 급여 30, 5억)" in block
    assert "교체" not in block and api.calls == ["id", "match50", "match60", "match30", "detail"]


def test_old_match_ranker_squad_and_errors(conn):
    api = FakeApi({50: (NOW - timedelta(days=40), XI)})
    assert "30일 넘은 경기" in fetch_team(api, conn, "레몬", now=NOW).for_model()
    # 우리가 랭커로 모은 스쿼드가 더 최근이면 그것
    conn.execute("INSERT INTO ranker_squad VALUES ('ou1', 'r1', 0)")
    conn.execute("INSERT INTO match VALUES ('r1', ?)", ((NOW - timedelta(days=2)).isoformat(),))
    conn.execute("INSERT INTO match_player VALUES ('r1', 'ou1', 101000002, 25, 9)")
    team = fetch_team(api, conn, "레몬", now=NOW)
    assert team.source == "랭커 수집" and team.lineup[0]["grade"] == 9 and not team.old
    with pytest.raises(TeamNotFound, match="찾지 못했어요"):
        fetch_team(api, conn, "없는사람", now=NOW)
    conn.execute("DELETE FROM ranker_squad")
    with pytest.raises(TeamNotFound, match="경기 기록이 없어"):
        fetch_team(FakeApi({}), conn, "레몬", now=NOW)


def test_team_colors_from_club_careers_nations_seasons_and_chemistry(conn):
    from types import SimpleNamespace

    catalog = SimpleNamespace(entries=[SimpleNamespace(name="FC 바르셀로나", category="club"),
                                       SimpleNamespace(name="스페인", category="nationality")])  # fmt: skip
    xi = [(101000000 + i, 25, 8) for i in range(1, 12)]
    for sp_id, _, _ in xi:  # 11명 모두 바르셀로나 경력, 그중 4명만 스페인 (5명 미만은 알리지 않음)
        conn.execute("INSERT INTO card_detail VALUES (?, ?, ?)",
                     (sp_id, '[{"club": "FC 바르셀로나"}, {"club": "기타"}]', "스페인" if sp_id % 3 == 0 else "브라질"))  # fmt: skip
    info = [{"type": "special", "name": "Ultimate Champions", "levels": [{"players": 3}]},
            {"type": "relation", "name": "황금 세대", "levels": [{"players": 2}], "players": [{"pid": 1}, {"pid": 2}, {"pid": 99}]}]  # fmt: skip
    team = fetch_team(FakeApi({50: (NOW, xi)}), conn, "레몬", now=NOW, catalog=catalog, team_color_info=info)
    # 팀컬러는 가장 많이 겹치는 하나만 (같은 11명이면 클럽 쪽), 케미는 발동한 것을 덧붙인다
    assert team.colors == ["FC 바르셀로나 11명", "황금 세대 케미 2명"]
    assert "팀컬러: FC 바르셀로나 11명, 황금 세대 케미 2명" in team.for_model()
