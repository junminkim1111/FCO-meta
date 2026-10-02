import pytest
from test_analytics import db  # noqa: F401  (아스널 4명 랭커, usage_stats 빌드 완료)

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fco_meta.chatbot import Toolbox  # noqa: E402
from fco_meta.web import create_app  # noqa: E402


def ask(client, message, session_id=None):
    """POST /api/chat and read the NDJSON stream → (answer as the page shows it, done event)."""
    import json

    res = client.post("/api/chat", json={"message": message, "session_id": session_id})
    assert res.status_code == 200 and res.headers["content-type"].startswith("application/x-ndjson")
    text, events = "", [json.loads(line) for line in res.text.splitlines()]
    assert events[0]["type"] == "start" and events[-1]["type"] == "done"
    for e in events:
        text = "" if e["type"] == "reset" else text + e.get("text", "")
    done = {**events[-1], "tools": [e for e in events if e["type"] == "tool"], "session_id": events[0]["session_id"]}
    return text, done


@pytest.fixture
def client(db, tmp_path):  # noqa: F811
    Toolbox(db.conn)  # 시세 테이블 생성
    db.conn.executemany(
        "INSERT INTO card_price (spid, grade, price, fetched_at) VALUES (?, 5, ?, '2026-09-28T12:00:00+00:00')",
        [(101000011, 900_000_000), (250000009, 300_000_000), (300000009, 50_000_000)],
    )
    db.conn.commit()
    return TestClient(create_app(tmp_path / "db.sqlite"))


def test_index_and_static(client):
    res = client.get("/")
    assert res.status_code == 200 and "<title>FCLM | FC Online 랭커 데이터 기반 AI</title>" in res.text and "/api/formation?" in res.text
    assert res.headers["cache-control"] == "no-cache"  # 수정한 화면이 바로 보이게
    assert client.get("/static/orb.js").headers["cache-control"] == "no-cache"
    examples = [q for q in client.get("/static/examples.txt").text.splitlines() if q.strip()]  # 입력창 예시 질문
    assert len(examples) >= 30


def test_meta(client):
    meta = client.get("/api/meta").json()
    assert meta["backend"] == "rules"
    assert set(meta) == {"backend", "daily_scope"}


def test_formations_and_errors(client):
    f = client.get("/api/formations", params={"team_color": "아스널"}).json()
    assert {x["formation"]: x["rankers"] for x in f["formations"]} == {"4-2-3-1": 3, "4-4-2": 1}
    bad = client.get("/api/formations", params={"team_color": "없는팀"})
    assert bad.status_code == 400 and "알 수 없는 팀컬러" in bad.json()["detail"]


def test_formation_overview(client):
    d = client.get("/api/formation", params={"name": "4231"}).json()
    assert d["formation"] == "4-2-3-1"
    # 테스트 DB는 필터 조회(아스널)만 있고 필터 없는 랭킹이 없음 → 랭킹 통계 없이 안내
    assert "note" in d
    assert client.get("/api/formation", params={"name": ""}).status_code == 422


def test_chat_rules(client):
    answer, done = ask(client, "아스날 볼란치 1명 추천")
    assert done["tool_calls"] == ["recommend_players"] and "볼란치R" in answer
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 501}).status_code == 422


def test_database_is_opened_read_only(client):
    import sqlite3

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        client.app.state.conn.execute("CREATE TABLE x (a)")


def test_missing_db(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_app(tmp_path / "missing.sqlite")


def test_gemini_errors_show_only_a_busy_notice(db, tmp_path, monkeypatch):  # noqa: F811
    from google.genai import errors

    import fco_meta.web.app as web_app

    class Broken:
        contents: list = []

        def ask_stream(self, message):
            yield "쓰다가 "  # 도중에 실패해도 쓰던 글은 지우고 안내로 바꾼다
            raise errors.ClientError(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})

    monkeypatch.setattr(web_app, "_gemini_chat", lambda toolbox, model: Broken())
    client = TestClient(create_app(tmp_path / "db.sqlite", backend="gemini"))
    answer, done = ask(client, "아스날 볼란치 1명 추천")
    assert answer == web_app.BUSY_MESSAGE  # 오류 원인·규칙 기반 답 없이 혼잡 안내만 (원인은 서버 로그)
    assert done["error"] == "unavailable"


def test_gemini_answer_hides_evidence_and_fallback_model(db, tmp_path, monkeypatch):  # noqa: F811
    import fco_meta.web.app as web_app
    from fco_meta.chatbot.gemini import RESET, GeminiTurn, ToolCall

    class Bot:
        model, last_model = "gemini-a", "gemini-b"  # 대체 모델로 답한 경우
        contents: list = []

        def ask_stream(self, message):
            yield from ["찾아볼게요.", RESET, ToolCall("recommend_players", {"role": "DM"}), "라이스가 ", "1순위입니다."]
            return GeminiTurn("라이스가 1순위입니다.", [("recommend_players", {})], "STOP", ["아스널 DM — 랭커 3명"])

    monkeypatch.setattr(web_app, "_gemini_chat", lambda toolbox, model: Bot())
    client = TestClient(create_app(tmp_path / "db.sqlite", backend="gemini"))
    answer, done = ask(client, "아스날 볼란치")
    assert answer == "라이스가 1순위입니다."  # 글자 단위로 흘러오고, 근거 줄·대체 모델 안내는 없다
    assert done["model"] == "gemini-b" and done["tool_calls"] == ["recommend_players"]
    assert done["tools"] == [{"type": "tool", "name": "recommend_players", "args": {"role": "DM"}}]  # 진행 문구용


def test_panels_answer_while_gemini_is_thinking(db, tmp_path, monkeypatch):  # noqa: F811
    import threading

    import fco_meta.web.app as web_app
    from fco_meta.chatbot.gemini import GeminiTurn

    thinking, release = threading.Event(), threading.Event()

    class SlowBot:
        model = last_model = "gemini-a"
        contents: list = []

        def __init__(self, tools):
            self.tools = tools

        def ask_stream(self, message):
            thinking.set()
            release.wait(5)  # 모델 응답을 기다리는 중
            content, _ = self.tools.run("list_formations", {})  # 도구는 그 뒤에도 실행된다
            yield content[:10]
            return GeminiTurn(content[:10])

    monkeypatch.setattr(web_app, "_gemini_chat", lambda tools, model: SlowBot(tools))
    client = TestClient(create_app(tmp_path / "db.sqlite", backend="gemini"))
    answers = []
    chat = threading.Thread(target=lambda: answers.append(ask(client, "q")[0]))
    chat.start()
    assert thinking.wait(5)
    panel = []  # 예전에는 답이 끝날 때까지 막혔다
    peek = threading.Thread(target=lambda: panel.append(client.get("/api/formations").status_code))
    peek.start()
    peek.join(2)
    release.set()
    assert panel == [200]
    chat.join(5)
    assert answers and answers[0]


def test_repeated_first_question_is_answered_from_cache(db, tmp_path, monkeypatch):  # noqa: F811
    import os

    import fco_meta.web.app as web_app
    from fco_meta.chatbot.gemini import GeminiTurn

    asked = []

    class Bot:
        model = last_model = "gemini-a"

        def __init__(self):
            self.contents = []

        def ask_stream(self, message):
            asked.append(message)
            answer = f"답 {len(asked)}"
            self.remember(message, answer)
            yield answer
            return GeminiTurn(answer, [], "STOP")

        def remember(self, question, answer):
            self.contents += [question, answer]

    monkeypatch.setattr(web_app, "_gemini_chat", lambda tools, model: Bot())
    db_file = tmp_path / "db.sqlite"
    client = TestClient(create_app(db_file, backend="gemini"))

    first, _ = ask(client, "레알 5억 미만 공격수 추천해줘")
    again, done = ask(client, "  레알 5억 미만   공격수 추천해줘?")  # 새 대화, 띄어쓰기·물음표만 다름
    assert (first, again, done.get("cached"), len(asked)) == ("답 1", "답 1", True, 1)  # Gemini를 다시 부르지 않음

    # 캐시로 답한 대화도 맥락이 남아, 이어지는 질문은 캐시 없이 모델이 답한다
    follow, done = ask(client, "레알 5억 미만 공격수 추천해줘", session_id=done["session_id"])
    assert follow == "답 2" and not done.get("cached")

    os.utime(db_file, ns=(0, db_file.stat().st_mtime_ns + 1))  # 매일 수집 등으로 DB가 바뀌면 새로 답한다
    fresh, done = ask(client, "레알 5억 미만 공격수 추천해줘")
    assert fresh == "답 3" and not done.get("cached")



def test_rate_limit_per_person_and_service():
    from fco_meta.web.app import RateLimit

    clock = [1_790_000_000.0]
    limit = RateLimit(per_minute=2, per_day=3, total_per_day=4, now=lambda: clock[0])
    assert limit.check("a") is None and limit.check("a") is None
    assert "1분에 2개" in limit.check("a")  # 분당 제한
    clock[0] += 61
    assert limit.check("a") is None
    assert "하루 질문 수(3개)" in limit.check("a")  # 하루 제한
    assert limit.check("b") is None
    assert "서비스 전체" in limit.check("c")  # 서비스 전체 하루 상한 (거절은 세지 않음)
    clock[0] += 24 * 3600  # 한국 시간 자정이 지나면 다시
    assert limit.check("a") is None and limit.check("c") is None


def test_chat_over_the_limit_gets_429(db, tmp_path):  # noqa: F811
    from fco_meta.web.app import RateLimit

    client = TestClient(create_app(tmp_path / "db.sqlite", limit=RateLimit(per_minute=1)))
    assert ask(client, "아스날 볼란치 1명 추천")[0]
    res = client.post("/api/chat", json={"message": "또"}, headers={"CF-Connecting-IP": "1.2.3.4"})
    assert res.status_code == 200  # 다른 사람(터널이 넘겨준 IP)은 따로 센다
    res = client.post("/api/chat", json={"message": "또"}, headers={"X-Forwarded-For": "5.6.7.8, 10.0.0.1"})
    assert res.status_code == 200  # Hugging Face 등 프록시 뒤: 첫 주소가 방문자
    res = client.post("/api/chat", json={"message": "또"})
    assert res.status_code == 429 and "1분에 1개" in res.json()["detail"]


def test_team_color_endpoints(client):
    d = client.get("/api/teamcolors").json()
    assert d["rows"] == [] and "note" in d  # 이 픽스처에는 필터 없는 랭킹이 없다
    detail = client.get("/api/teamcolor", params={"name": "아스날"})
    assert detail.status_code == 200 and detail.json()["team_color"] == "아스널"
    assert client.get("/api/teamcolor", params={"name": ""}).status_code == 422


def test_page_results_are_reused_until_the_db_changes(client, tmp_path, monkeypatch):
    import os

    from fco_meta.chatbot import Toolbox as ToolboxClass

    calls = []
    run = ToolboxClass.run
    monkeypatch.setattr(ToolboxClass, "run", lambda self, name, args: calls.append(name) or run(self, name, args))
    first = client.get("/api/formation", params={"name": "4231"}).json()
    assert client.get("/api/formation", params={"name": "4231"}).json() == first and calls == ["get_formation_overview"]
    assert client.get("/api/formations", params={"team_color": "없는팀"}).status_code == 400  # 오류는 담지 않는다
    assert client.get("/api/formations", params={"team_color": "없는팀"}).status_code == 400 and calls.count("list_formations") == 2
    db_file = tmp_path / "db.sqlite"
    os.utime(db_file, ns=(db_file.stat().st_atime_ns, db_file.stat().st_mtime_ns + 1_000_000_000))  # 수집으로 DB가 바뀜
    client.get("/api/formation", params={"name": "4231"})
    assert calls.count("get_formation_overview") == 2


def test_chat_outcomes_are_logged_for_the_admin_page(db, tmp_path, monkeypatch):  # noqa: F811
    from google.genai import errors

    import fco_meta.web.app as web_app
    from fco_meta.chatbot.gemini import GeminiTurn, ToolCall
    from fco_meta.web.app import RateLimit
    from fco_meta.web.chatlog import ChatLog

    class Bot:
        model = last_model = "gemini-a"

        def __init__(self):
            self.contents = []

        def ask_stream(self, message):
            if "고장" in message:
                yield "쓰다가 "
                raise errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
            yield ToolCall("recommend_players", {"role": "DM"})
            yield "라이스입니다."
            self.contents += [message, "라이스입니다."]
            return GeminiTurn("라이스입니다.", [("recommend_players", {"role": "DM"})], "STOP")

        def remember(self, question, answer):
            self.contents += [question, answer]

    monkeypatch.setattr(web_app, "_gemini_chat", lambda toolbox, model: Bot())
    log = ChatLog()
    app = create_app(tmp_path / "db.sqlite", backend="gemini", chat_log=log, admin_key="secret", limit=RateLimit(per_minute=3))
    client = TestClient(app)
    ask(client, "아스날 볼란치")
    ask(client, "아스날 볼란치")  # 새 대화의 같은 첫 질문 → 캐시
    ask(client, "고장 나는 질문")
    assert client.post("/api/chat", json={"message": "네 번째"}).status_code == 429

    rows = sorted(log.pending, key=lambda r: r["outcome"])
    assert [(r["outcome"], r["q"]) for r in rows] == [
        ("busy", "고장 나는 질문"), ("cached", "아스날 볼란치"), ("limited", "네 번째"), ("ok", "아스날 볼란치"),
    ]  # fmt: skip
    busy, _, limited, ok = rows
    assert "503" in busy["error"] and "1분에 3개" in limited["error"]
    assert ok["tools"] == ["recommend_players"] and ok["model"] == "gemini-a" and ok["ms"] >= 0

    # 관리자 페이지: 비밀번호가 맞아야 기록을 준다
    assert client.get("/admin").status_code == 200
    assert client.get("/api/admin/logs").status_code == 401
    assert client.get("/api/admin/logs", headers={"X-Admin-Key": "wrong"}).status_code == 401
    summary = client.get("/api/admin/logs", headers={"X-Admin-Key": "secret"}).json()
    assert summary["total"] == 4 and summary["by_outcome"]["busy"] == 1


def test_admin_page_is_hidden_without_a_key(client):
    assert client.get("/admin").status_code == 404
    assert client.get("/api/admin/logs", headers={"X-Admin-Key": ""}).status_code == 404


def test_admin_warns_from_the_third_wrong_password_and_blocks_the_ip_at_the_fifth(client, db, tmp_path):  # noqa: F811
    app = create_app(tmp_path / "db.sqlite", admin_key="secret")
    c = TestClient(app)

    def logs(key, ip="1.1.1.1"):
        return c.get("/api/admin/logs", headers={"X-Admin-Key": key, "X-Forwarded-For": ip})

    assert logs("").status_code == 401  # 입력 전은 횟수에 넣지 않는다
    for n in (1, 2):
        res = logs("wrong")
        assert res.status_code == 401 and "회 틀렸습니다" not in res.json()["detail"]
    for n in (3, 4):
        res = logs("wrong")
        assert res.status_code == 401 and f"{n}회 틀렸습니다" in res.json()["detail"] and "5회 틀리면" in res.json()["detail"]
    res = logs("wrong")
    assert res.status_code == 403 and "막혔습니다" in res.json()["detail"]
    assert logs("secret").status_code == 403  # 막힌 뒤에는 맞는 비밀번호도 안 된다
    assert c.get("/admin", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 403
    assert logs("secret", ip="2.2.2.2").status_code == 200  # 다른 IP는 그대로

    # 맞히면 틀린 횟수는 지운다 (2회 틀림 → 맞힘 → 다시 1회부터)
    for _ in range(2):
        logs("wrong", ip="3.3.3.3")
    assert logs("secret", ip="3.3.3.3").status_code == 200
    assert "회 틀렸습니다" not in logs("wrong", ip="3.3.3.3").json()["detail"]
