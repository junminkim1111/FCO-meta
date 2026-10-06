"""Relation/special team colors from the datacenter team color page, and the chatbot tool over them."""

import json
import sqlite3

import httpx

from fco_meta.chatbot import Toolbox
from fco_meta.crawler import DatacenterClient
from fco_meta.crawler import teamcolor_info as tci


def client(handler):
    http = httpx.Client(base_url="https://fconline.nexon.com", transport=httpx.MockTransport(handler))
    return DatacenterClient(http, sleep=lambda s: None)


def test_parse_list_and_detail(fixture_html):
    assert tci.parse_list(fixture_html("teamcolor_list_relation_first2.html")) == [
        (30007, "01-03 성남FC 리그3연패 베스트"), (30003, "02 수원삼성 더블"),
    ]  # fmt: skip
    d = tci.parse_detail(fixture_html("teamcolor_detail_special_40149.html"))
    assert d["description"] == "19 UEFA Champions League 클래스 선수들로 구성된 팀컬러입니다."
    assert d["levels"] == [
        {"level": 1, "players": 3, "effects": ["전체 능력치 +1"]},
        {"level": 2, "players": 6, "effects": ["전체 능력치 +2"]},
        {"level": 3, "players": 8, "effects": ["전체 능력치 +3"]},
    ]


def test_players_are_grouped_by_player_and_split_at_the_cap(fixture_html):
    cards = json.loads(fixture_html("teamcolor_players_40019.json"))["players"]
    with client(lambda r: httpx.Response(200, text=json.dumps({"players": cards}))) as c:
        players, complete = tci.fetch_players(c, 40019)
    assert complete and len(players) == len({p["pid"] for p in cards}) == 3  # 카드 8장 → 선수 3명
    assert players[0] == {"pid": cards[0]["pid"], "name": cards[0]["name"]}

    # 100장에 걸리면 포지션 묶음으로 나누고, 묶음도 걸리면 능력치 낮은 순으로 한 번 더. 그래도 걸리면 일부만
    asked = []

    def capped(request):
        pos, order = request.url.params.get("strPosition", ""), request.url.params["strOrderby"].split()[-1]
        asked.append((pos, order))
        full = pos in ("", ",0,") or (pos.startswith(",1,4,") and order == "descending")  # 전체·GK 묶음, DF 위쪽이 100장
        cards = [{"pid": len(asked) * 1000 + i, "name": f"p{i}"} for i in range(tci.PLAYER_CAP if full else 1)]
        return httpx.Response(200, text=json.dumps({"players": cards}))

    with client(capped) as c:
        players, complete = tci.fetch_players(c, 1)
    assert not complete  # GK는 낮은 순으로도 100장
    assert [o for p, o in asked if p.startswith(",1,4,")] == ["descending", "ascending"]  # DF는 아래쪽 100장 미만
    assert len(asked) == 7 and len(players) == 100 * 4 + 1 * 3  # 전체·GK 2번·DF 2번·MF·FW


def test_collect_relation_with_players_and_special_without(fixture_html, tmp_path):
    def handler(request):
        path, params = request.url.path, request.url.params
        if path == "/datacenter/teamcolor":
            if params["strTeamColorType"] == ",relation,":
                return httpx.Response(200, text=fixture_html("teamcolor_list_relation_first2.html"))
            return httpx.Response(200, text='<div class="teamcolor_item"><a class="btn_detail_link" onclick="DataCenter.GetTeamColorDetail(40149); return false;"></a><div class="name">19 UEFA Champions League</div></div>')
        if path == "/datacenter/TeamColorDetail":
            return httpx.Response(200, text=fixture_html("teamcolor_detail_special_40149.html"))
        assert params["teamcolorid"] != "40149"  # 스페셜은 선수 목록을 받지 않는다
        return httpx.Response(200, text=fixture_html("teamcolor_players_40019.json"))

    with client(handler) as c:
        entries = tci.collect(c)
    assert [(e["id"], e["type"]) for e in entries] == [(30007, "relation"), (30003, "relation"), (40149, "special")]
    assert len(entries[0]["players"]) == 3 and entries[0]["players_complete"] and "players" not in entries[2]

    # 파일을 주면 받는 대로 저장하고, 다시 실행하면 이미 받은 팀컬러는 건너뛴다 (목록만 다시 받음)
    out = tmp_path / "info.json"
    with client(handler) as c:
        tci.collect(c, out)
    seen = []
    with client(lambda r: seen.append(r.url.path) or handler(r)) as c:
        assert len(tci.collect(c, out)) == 3
    assert set(seen) == {"/datacenter/teamcolor"}


INFO = [
    {"id": 40019, "name": "레드데블스 철벽라인", "type": "relation", "description": "맨체스터 유나이티드의 철벽수비라인",
     "levels": [{"level": 1, "players": 3, "effects": ["적극성 +2", "대인 수비 +1"]}],
     "players": [{"pid": 1, "name": "리오 퍼디난드"}, {"pid": 2, "name": "네마냐 비디치"}], "players_complete": True},
    {"id": 40020, "name": "레드데블스 공격라인", "type": "relation", "description": "",
     "levels": [{"level": 1, "players": 3, "effects": ["골 결정력 +2"]}],
     "players": [{"pid": 3, "name": "박지성"}], "players_complete": True},
    {"id": 40149, "name": "19 UEFA Champions League", "type": "special", "description": "19 UCL 클래스",
     "levels": [{"level": 1, "players": 3, "effects": ["전체 능력치 +1"]}]},
]  # fmt: skip


def run(tool_input, info=INFO):
    content, is_error = Toolbox(sqlite3.connect(":memory:"), team_color_info=info).run("get_team_color_info", tool_input)
    return json.loads(content), is_error


def test_team_color_info_tool():
    d, _ = run({"team_color": "철벽 라인"})  # 일부·띄어쓰기 무시
    (tc,) = d["team_colors"]
    assert tc["type"] == "관계" and tc["players"] == ["리오 퍼디난드", "네마냐 비디치"] and tc["levels"][0]["players"] == 3

    d, _ = run({"team_color": "레드데블스"})  # 둘 다
    assert d["matches"] == 2
    assert [t["name"] for t in run({"player": "박지성"})[0]["team_colors"]] == ["레드데블스 공격라인"]
    assert [t["name"] for t in run({"effect": "골결정력"})[0]["team_colors"]] == ["레드데블스 공격라인"]
    (special,) = run({"team_color": "19 uefa"})[0]["team_colors"]
    assert special["type"] == "스페셜" and special["players"] == "해당 시즌 클래스 카드 전부"

    assert "get_team_color_overview" in run({"team_color": "아스널"})[0]["hint"]  # 클럽 팀컬러
    assert run({})[1]  # 조건 없음 → 오류
    assert "names" in run({"effect": "+"}, INFO * 5)[0]  # 많으면 이름만
    assert "note" in run({"team_color": "x"}, [])[0]  # 수집 전
