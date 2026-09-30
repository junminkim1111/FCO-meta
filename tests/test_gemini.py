"""Gemini loop with a scripted client. Responses are built from the SDK's own types."""

import json

import pytest
from test_analytics import db  # noqa: F401

pytest.importorskip("google.genai")
from google.genai import types  # noqa: E402

from fco_meta.chatbot import Toolbox  # noqa: E402
from fco_meta.chatbot.gemini import GeminiChat, GeminiUnavailable, describe_error, function_declarations  # noqa: E402


def response(parts, finish="STOP"):
    return types.GenerateContentResponse.model_validate(
        {"candidates": [{"content": {"role": "model", "parts": parts}, "finish_reason": finish}]}
    )


class FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def generate_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        return self.responses.pop(0)

    def generate_content_stream(self, **kwargs):  # 한 조각짜리 스트림 (오류는 실제 SDK처럼 첫 조각을 받을 때)
        yield self.generate_content(**kwargs)


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


@pytest.fixture
def toolbox(db):  # noqa: F811
    return Toolbox(db.conn, min_sample=1)


@pytest.fixture(autouse=True)
def fresh_cooldowns():
    """한도 소진 기록은 프로세스 전체가 공유한다 — 테스트마다 비운다."""
    from fco_meta.chatbot import gemini

    gemini._COOLDOWN.clear()
    yield
    gemini._COOLDOWN.clear()


def test_function_declarations_accepted_by_sdk():
    decls = {d.name: d for d in function_declarations()}
    assert set(decls) == {
        "resolve_terms", "list_available_data", "list_formations", "recommend_players", "recommend_squad",
        "get_player_detail", "get_meta_trends", "query_squads", "query_rankers", "get_formation_overview",
    }  # fmt: skip
    assert decls["recommend_players"].parameters_json_schema["required"] == ["role"]  # 팀컬러 생략 = 전체 랭커


class Chunked(FakeModels):
    """Each scripted response is a list of chunks (a real stream sends the answer in pieces)."""

    def generate_content_stream(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        yield from self.responses.pop(0)


def events_of(stream):
    """All events of an ask_stream generator and the GeminiTurn it returns."""
    events = []
    while True:
        try:
            events.append(next(stream))
        except StopIteration as stop:
            return events, stop.value


def test_made_up_figures_are_rewritten_once(toolbox):
    from fco_meta.chatbot.gemini import RESET, Recheck, ToolCall

    args = {"team_color": "아스날", "formation": "4-2-3-1", "role": "볼란치"}
    call = {"function_call": {"name": "recommend_players", "args": args}}
    rankers = json.loads(toolbox.run("recommend_players", args)[0])["players"][0]["rankers"]
    client = FakeClient([
        response([call]),
        response([{"text": f"4-2-3-1이 99.9%, 랭커 {rankers}명입니다."}]),  # 99.9%는 조회 결과에 없음
        response([{"text": f"4-2-3-1이 1위, 랭커 {rankers}명입니다."}]),
    ])  # fmt: skip
    chat = GeminiChat(client, toolbox, model="gemini-test")
    events, turn = events_of(chat.ask_stream("포메이션 순위"))

    assert events[:2] == [ToolCall("recommend_players", args), f"4-2-3-1이 99.9%, 랭커 {rankers}명입니다."]
    assert events[2:] == [RESET, Recheck(["99.9%"]), f"4-2-3-1이 1위, 랭커 {rankers}명입니다."]
    assert turn.text == f"4-2-3-1이 1위, 랭커 {rankers}명입니다."
    request = client.models.requests[2]
    assert "99.9%" in request["contents"][-1].parts[0].text  # 어떤 수치가 없는지 알려 준다
    assert request["config"].tool_config.function_calling_config.mode == "NONE"  # 다시 쓸 때는 도구 없이
    # 기록에는 고친 답만 남는다 (처음 답과 고쳐 달라는 요청은 빠짐)
    assert chat.contents[-1].parts[0].text == turn.text
    assert not any("99.9%" in (p.text or "") for c in chat.contents for p in c.parts or [])


def test_backed_figures_and_tool_free_answers_are_not_rechecked(toolbox):
    args = {"team_color": "아스날", "formation": "4-2-3-1", "role": "볼란치"}
    call = {"function_call": {"name": "recommend_players", "args": args}}
    rate = json.loads(toolbox.run("recommend_players", args)[0])["players"][0]["usage_rate"]
    client = FakeClient([
        response([call]), response([{"text": f"사용률 {rate * 100:.1f}%입니다."}]),  # 결과에 있는 비율
        response([{"text": "안녕하세요! 예: 레알 5억 미만 공격수 추천해줘"}]),  # 도구 없이 답한 인사
    ])  # fmt: skip
    chat = GeminiChat(client, toolbox, model="gemini-test")
    chat.ask("포메이션 순위")
    chat.ask("안녕")
    assert len(client.models.requests) == 3  # 다시 쓰기 요청 없음


def test_answer_streams_in_pieces_and_preamble_is_reset(toolbox):
    from fco_meta.chatbot.gemini import RESET, ToolCall

    call = {"function_call": {"name": "list_formations", "args": {}}, "thought_signature": b"sig-1"}
    client = FakeClient([])
    client.models = Chunked([
        [response([{"text": "찾아볼게요."}]), response([call])],  # 도구 부르기 전 글 → 답이 아님
        [response([{"text": "4-2-3-1이 "}], finish=None), response([{"text": "1위입니다."}])],
    ])  # fmt: skip
    chat = GeminiChat(client, toolbox, model="gemini-test")
    stream = chat.ask_stream("포메이션 순위")
    events = []
    while True:
        try:
            events.append(next(stream))
        except StopIteration as stop:
            turn = stop.value
            break
    # 도구를 부르기 전 글은 RESET으로 지우고, 도구를 부를 때 ToolCall을 알린다 (화면의 진행 문구)
    assert events == ["찾아볼게요.", RESET, ToolCall("list_formations", {}), "4-2-3-1이 ", "1위입니다."]
    assert turn.text == "4-2-3-1이 1위입니다." and turn.tool_calls == [("list_formations", {})]
    # 조각들은 한 응답으로 기록되고, 도구 호출의 서명도 그대로 남는다
    first = chat.contents[1]
    assert first.role == "model" and first.parts[1].thought_signature == b"sig-1"
    assert [p.text for p in chat.contents[-1].parts] == ["4-2-3-1이 ", "1위입니다."]


def test_stopping_a_stream_drops_the_question(toolbox):
    client = FakeClient([])
    client.models = Chunked([[response([{"text": "첫 답"}])], [response([{"text": "길게 "}], finish=None), response([{"text": "…"}])]])
    chat = GeminiChat(client, toolbox, model="gemini-test")
    chat.ask("첫 질문")
    stream = chat.ask_stream("두 번째")
    assert next(stream) == "길게 "
    stream.close()  # 사용자가 정지
    assert [c.parts[0].text for c in chat.contents] == ["첫 질문", "첫 답"]


def test_remembered_answer_is_context_for_follow_ups(toolbox):
    client = FakeClient([response([{"text": "2위는 4-1-2-3입니다."}])])
    chat = GeminiChat(client, toolbox, model="gemini-test")
    chat.remember("포메이션 순위", "4-2-3-1이 1위입니다.")  # 캐시로 답한 첫 질문
    chat.ask("2위는?")
    sent = client.models.requests[0]["contents"]
    assert [(c.role, c.parts[0].text) for c in sent] == [
        ("user", "포메이션 순위"), ("model", "4-2-3-1이 1위입니다."), ("user", "2위는?"),
    ]  # fmt: skip


def test_follow_up_resends_only_questions_and_answers(toolbox):
    """지난 질문의 도구 호출·결과(큰 JSON)는 다음 질문부터 보내지 않는다 — 대화가 길어져도 요청 크기가 거의 그대로."""
    call = {"function_call": {"name": "list_formations", "args": {}}, "thought_signature": b"sig-1"}
    client = FakeClient([
        response([call]), response([{"text": "4-2-3-1이 1위입니다.", "thought_signature": b"sig-2"}]),
        response([{"text": "그중 2위는 4-1-2-3입니다."}]),
    ])  # fmt: skip
    chat = GeminiChat(client, toolbox, model="gemini-test")
    chat.ask("포메이션 순위")
    chat.ask("2위는?")

    sent = client.models.requests[-1]["contents"]
    assert [(c.role, c.parts[0].text) for c in sent] == [
        ("user", "포메이션 순위"), ("model", "4-2-3-1이 1위입니다."), ("user", "2위는?"),
    ]  # fmt: skip
    assert sent[1].parts[0].thought_signature == b"sig-2"  # 답 글의 서명은 그대로
    # 이번 질문 안의 도구 결과는 그대로 보낸다
    assert any(p.function_response for c in client.models.requests[1]["contents"] for p in c.parts)


def test_tool_round_trip(toolbox):
    call = {
        "function_call": {
            "id": "call-1",
            "name": "recommend_players",
            # Gemini는 정수도 실수로 보낼 수 있다
            "args": {"team_color": "아스날", "formation": "4-2-3-1", "role": "볼란치", "top_n": 2.0},
        },
        "thought_signature": b"sig-1",
    }
    client = FakeClient([response([call]), response([{"text": "라이스가 1순위입니다."}])])
    seen = []
    chat = GeminiChat(client, toolbox, model="gemini-test", on_tool_call=lambda n, a: seen.append(n))

    turn = chat.ask("아스날 4-2-3-1 볼란치 2명 추천")

    assert turn.text.startswith("라이스가 1순위입니다.") and turn.finish_reason == "STOP"
    # 근거 줄은 모델이 아니라 도구 결과에서 만들고, 답 본문에는 넣지 않는다
    assert turn.text == "라이스가 1순위입니다."
    assert turn.evidence == ["아스널 4-2-3-1 DM — 랭커 3명 스쿼드 (2026-09-28 20:00 기준)"]
    assert turn.tool_calls[0][0] == "recommend_players" and seen == ["recommend_players"]
    first, second = client.models.requests
    assert first["model"] == "gemini-test"
    assert first["config"].automatic_function_calling.disable is True
    assert "존댓말" in first["config"].system_instruction

    # 두 번째 요청: user 질문 → 모델 content(서명 그대로) → 함수 결과
    user, model_turn, tool_turn = second["contents"]
    assert model_turn.parts[0].thought_signature == b"sig-1"
    fr = tool_turn.parts[0].function_response
    assert (fr.name, fr.id) == ("recommend_players", "call-1")
    result = fr.response["result"]
    assert result["team_color"] == "아스널" and [p["name"] for p in result["players"]] == ["볼란치R", "볼란치L"]


def test_parallel_calls_and_errors_return_in_one_message(toolbox):
    calls = [
        {"function_call": {"id": "a", "name": "resolve_terms", "args": {"team_color": "맨유"}}},
        {"function_call": {"id": "b", "name": "list_formations", "args": {"team_color": "없는팀"}}},
    ]
    client = FakeClient([response(calls), response([{"text": "확인했습니다."}])])
    turn = GeminiChat(client, toolbox).ask("맨유랑 없는팀 포메이션")

    assert turn.text == "확인했습니다."
    tool_turn = client.models.requests[1]["contents"][-1]
    ok, err = (p.function_response for p in tool_turn.parts)
    assert ok.response["result"]["team_color"]["name"] == "맨체스터 유나이티드"
    assert "알 수 없는 팀컬러" in err.response["error"]


def test_history_accumulates_and_thoughts_are_hidden(toolbox):
    client = FakeClient([
        response([{"text": "생각 중", "thought": True}, {"text": "안녕하세요."}]),
        response([{"text": "네."}]),
    ])  # fmt: skip
    chat = GeminiChat(client, toolbox)
    assert chat.ask("안녕").text == "안녕하세요."
    assert chat.ask("고마워").text == "네."
    assert len(client.models.requests[1]["contents"]) == 3  # user, model, user


def test_empty_candidate_and_tool_limit(toolbox):
    blocked = types.GenerateContentResponse.model_validate({"prompt_feedback": {"block_reason": "SAFETY"}})
    assert "응답을 받지 못했습니다" in GeminiChat(FakeClient([blocked]), toolbox).ask("?").text

    # 도구 호출 한도 → 도구를 끈 마지막 요청으로 답을 받는다
    from fco_meta.chatbot.gemini import MAX_TOOL_ROUNDS

    calls = [
        response([{"function_call": {"name": "list_formations", "args": {"team_color": f"팀{i}"}}}]) for i in range(MAX_TOOL_ROUNDS)
    ]
    client = FakeClient(calls + [response([{"text": "지금까지 결과로 답합니다."}])])
    turn = GeminiChat(client, toolbox).ask("계속")
    assert turn.text == "지금까지 결과로 답합니다." and len(turn.tool_calls) == MAX_TOOL_ROUNDS
    last = client.models.requests[-1]["config"]
    assert last.tool_config.function_calling_config.mode.value == "NONE"
    assert client.models.requests[0]["config"].tool_config is None


def test_failed_turn_is_rolled_back(toolbox):
    from google.genai import errors

    class Flaky(FakeModels):
        def generate_content(self, *, model, contents, config):
            if not self.responses:
                raise errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
            return super().generate_content(model=model, contents=contents, config=config)

    client = FakeClient([])
    client.models = Flaky([response([{"text": "첫 답"}])])
    chat = GeminiChat(client, toolbox, sleep=lambda s: None)
    assert chat.ask("첫 질문").text == "첫 답"
    with pytest.raises(GeminiUnavailable):
        chat.ask("두 번째")
    assert len(chat.contents) == 2  # 실패한 질문은 기록에서 빠짐 (user, model만 남음)


def test_describe_error():
    from google.genai import errors

    from fco_meta.chatbot.gemini import describe_error

    e = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    assert describe_error(e) == "Gemini API 오류 429 RESOURCE_EXHAUSTED: quota — 사용량 한도 초과 (잠시 후 다시 시도)"
    assert describe_error(ValueError("bad")) == "Gemini 처리 중 오류 (ValueError): bad"


class ByModel(FakeModels):
    """Each model name maps to a list of outcomes (Exception to raise or response to return)."""

    def __init__(self, outcomes):
        super().__init__([])
        self.outcomes = {m: list(o) for m, o in outcomes.items()}

    def generate_content(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        outcome = self.outcomes[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _unavailable():
    from google.genai import errors

    return errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})


def test_retries_then_succeeds_on_same_model(toolbox):
    client = FakeClient([])
    client.models = ByModel({"primary": [_unavailable(), response([{"text": "답"}])]})
    slept = []
    chat = GeminiChat(client, toolbox, model="primary", fallback_models=["backup"], sleep=slept.append)
    assert chat.ask("질문").text == "답"
    assert slept == [1.0] and chat.last_model == "primary"


def test_falls_back_to_next_model_after_retries(toolbox):
    client = FakeClient([])
    client.models = ByModel({
        "primary": [_unavailable()] * 3,
        "backup": [response([{"text": "대체 모델 답"}])],
    })  # fmt: skip
    slept = []
    chat = GeminiChat(client, toolbox, model="primary", fallback_models=["backup"], sleep=slept.append)
    assert chat.ask("질문").text == "대체 모델 답"
    assert [r["model"] for r in client.models.requests] == ["primary"] * 3 + ["backup"]
    assert slept == [1.0, 3.0] and chat.last_model == "backup"


def test_non_retryable_errors_raise_immediately(toolbox):
    from google.genai import errors

    bad_key = errors.ClientError(400, {"error": {"code": 400, "message": "API key not valid", "status": "INVALID_ARGUMENT"}})
    client = FakeClient([])
    client.models = ByModel({"primary": [bad_key]})
    chat = GeminiChat(client, toolbox, model="primary", fallback_models=["backup"], sleep=lambda s: None)
    with pytest.raises(errors.ClientError):
        chat.ask("질문")
    assert len(client.models.requests) == 1


def test_all_models_unavailable_raises_last_error(toolbox):
    from google.genai import errors

    client = FakeClient([])
    client.models = ByModel({"primary": [_unavailable()] * 3, "backup": [_unavailable()] * 3})
    chat = GeminiChat(client, toolbox, model="primary", fallback_models=["backup"], sleep=lambda s: None)
    with pytest.raises(GeminiUnavailable) as info:
        chat.ask("질문")
    assert [m for m, _ in info.value.failures] == ["primary", "backup"]
    assert chat.contents == []  # 기록도 되돌림


def test_models_from_env(monkeypatch):
    from fco_meta.chatbot.gemini import DEFAULT_FALLBACK_MODELS, DEFAULT_MODEL, models_from_env

    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_FALLBACK_MODELS", raising=False)
    assert models_from_env() == (DEFAULT_MODEL, list(DEFAULT_FALLBACK_MODELS))
    monkeypatch.setenv("GEMINI_MODEL", "m1")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "m2, m3")
    assert models_from_env() == ("m1", ["m2", "m3"])
    assert models_from_env("cli") == ("cli", ["m2", "m3"])
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    assert models_from_env()[1] == []  # 빈 값 = 대체 모델 없음


def _not_found(model):
    from google.genai import errors

    return errors.ClientError(404, {"error": {"code": 404, "status": "NOT_FOUND",
        "message": f"This model models/{model} is no longer available to new users. Please update your code."}})  # fmt: skip


def test_overloaded_primary_then_unavailable_fallback_reports_both(toolbox):
    """실제 사례: 기본 모델 503 연속 → 대체 모델 404. 두 원인을 모두 보여 준다."""
    client = FakeClient([])
    client.models = ByModel({"gemini-3.5-flash": [_unavailable()] * 3, "gemini-2.5-flash": [_not_found("gemini-2.5-flash")]})
    chat = GeminiChat(client, toolbox, model="gemini-3.5-flash", fallback_models=["gemini-2.5-flash"], sleep=lambda s: None)
    with pytest.raises(GeminiUnavailable) as info:
        chat.ask("질문")
    assert len(client.models.requests) == 4  # 503 x3 + 404 x1 (404는 재시도 안 함)
    text = describe_error(info.value)
    assert text.startswith("Gemini 모델을 모두 쓸 수 없습니다")
    assert "gemini-3.5-flash: 503 UNAVAILABLE: high demand — 구글 서버 혼잡" in text
    assert "gemini-2.5-flash: 404 NOT_FOUND: This model models/gemini-2.5-flash is no longer available to new users" in text


def test_unavailable_primary_moves_on_without_retry(toolbox):
    client = FakeClient([])
    client.models = ByModel({"old": [_not_found("old")], "new": [response([{"text": "새 모델 답"}])]})
    slept = []
    chat = GeminiChat(client, toolbox, model="old", fallback_models=["new"], sleep=slept.append)
    assert chat.ask("질문").text == "새 모델 답" and chat.last_model == "new" and slept == []


class Listed:
    def __init__(self, name, actions=("generateContent", "countTokens")):
        self.name = f"models/{name}"
        self.supported_actions = list(actions)


def test_available_models_filters_and_orders():
    from fco_meta.chatbot.gemini import available_models

    class C:
        class models:  # noqa: N801
            @staticmethod
            def list():
                return [
                    Listed("gemini-3.5-flash"), Listed("gemini-3.8-flash-lite"), Listed("gemini-3.8-flash"),
                    Listed("gemini-3.8-flash-image"), Listed("text-embedding-004", ["embedContent"]),
                    Listed("gemini-3.8-pro"), Listed("gemini-3.9-flash-preview"), Listed("gemini-3.8-flash-tts"),
                ]  # fmt: skip

    assert available_models(C()) == [
        "gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-3.8-pro", "gemini-3.5-flash", "gemini-3.9-flash-preview",
    ]  # fmt: skip


def test_discovers_other_models_when_configured_ones_are_overloaded(toolbox):
    client = FakeClient([])
    client.models = ByModel({
        "gemini-3.8-flash": [_unavailable()] * 3,
        "gemini-3.5-flash": [_unavailable()] * 3,
        "gemini-3.8-flash-lite": [response([{"text": "lite 답"}])],
    })  # fmt: skip
    client.models.list = lambda: [Listed("gemini-3.8-flash"), Listed("gemini-3.5-flash"), Listed("gemini-3.8-flash-lite")]
    clock = [1000.0]
    chat = GeminiChat(client, toolbox, model="gemini-3.8-flash", fallback_models=["gemini-3.5-flash"], sleep=lambda s: None,
                      now=lambda: clock[0])  # fmt: skip
    assert chat.ask("질문").text == "lite 답" and chat.last_model == "gemini-3.8-flash-lite"
    # 목록은 한 번만 조회하고 재사용, 혼잡했던 모델은 잠시 뒤 다시 쓴다
    client.models.list = lambda: (_ for _ in ()).throw(AssertionError("listed twice"))
    client.models.outcomes["gemini-3.8-flash"] = [response([{"text": "복구"}])]
    clock[0] += 121
    assert chat.ask("다시").text == "복구"


def test_model_list_failure_keeps_original_errors(toolbox):
    client = FakeClient([])
    client.models = ByModel({"a": [_unavailable()] * 3})

    def broken_list():
        raise RuntimeError("list failed")

    client.models.list = broken_list
    chat = GeminiChat(client, toolbox, model="a", fallback_models=[], sleep=lambda s: None)
    with pytest.raises(GeminiUnavailable) as info:
        chat.ask("질문")
    assert [m for m, _ in info.value.failures] == ["a"]


def _quota(delay=None, daily=False):
    from google.genai import errors

    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{
        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if daily else "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
    }]}]  # fmt: skip
    if delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": f"{delay}s"})
    return errors.ClientError(429, {"error": {"code": 429, "message": "Resource exhausted.", "status": "RESOURCE_EXHAUSTED", "details": details}})


def test_quota_short_delay_waits_once_then_retries(toolbox):
    client = FakeClient([])
    client.models = ByModel({"a": [_quota(delay=7), response([{"text": "답"}])]})
    slept = []
    chat = GeminiChat(client, toolbox, model="a", fallback_models=["b"], sleep=slept.append)
    assert chat.ask("질문").text == "답" and slept == [7.0]


def test_quota_long_delay_or_daily_moves_to_next_model_without_waiting(toolbox):
    from fco_meta.chatbot import gemini

    for err in (_quota(delay=40), _quota(daily=True), _quota()):
        gemini._COOLDOWN.clear()
        client = FakeClient([])
        client.models = ByModel({"a": [err], "b": [response([{"text": "b 답"}])]})
        slept = []
        chat = GeminiChat(client, toolbox, model="a", fallback_models=["b"], sleep=slept.append)
        assert chat.ask("질문").text == "b 답" and slept == []
        assert [r["model"] for r in client.models.requests] == ["a", "b"]  # 한도 걸린 모델은 다시 부르지 않음


def test_exhausted_model_is_skipped_until_quota_resets(toolbox):
    from datetime import datetime

    from fco_meta.chatbot.gemini import QUOTA_RESET_TZ

    call = {"function_call": {"id": "c", "name": "list_available_data", "args": {}}}
    client = FakeClient([])
    client.models = ByModel({"a": [_quota(daily=True)], "b": [response([call]), response([{"text": "b 답"}]), response([{"text": "또 b"}])]})
    now = datetime(2026, 9, 30, 12, 0, tzinfo=QUOTA_RESET_TZ).timestamp()
    clock = [now]
    chat = GeminiChat(client, toolbox, model="a", fallback_models=["b"], sleep=lambda s: None, now=lambda: clock[0])
    assert chat.ask("질문").text.startswith("b 답")
    assert chat.ask("다음").text == "또 b"
    # 하루 한도가 소진된 a는 도구 호출 다음 라운드에도, 다음 질문에도 다시 부르지 않는다
    assert [r["model"] for r in client.models.requests] == ["a", "b", "b", "b"]

    # 태평양 시간 자정이 지나면 다시 시도한다
    client.models.outcomes["a"] = [response([{"text": "a 복구"}])]
    clock[0] = datetime(2026, 10, 1, 0, 1, tzinfo=QUOTA_RESET_TZ).timestamp()
    assert chat.ask("내일").text == "a 복구"


def test_all_models_cooling_reports_when(toolbox):
    client = FakeClient([])
    client.models = ByModel({"a": [_quota(daily=True)]})
    chat = GeminiChat(client, toolbox, model="a", fallback_models=[], sleep=lambda s: None)
    chat.discover = False
    with pytest.raises(GeminiUnavailable):
        chat.ask("질문")
    with pytest.raises(GeminiUnavailable) as info:
        chat.ask("다시")  # 요청을 보내지 않고 바로 알린다
    assert "KST까지 건너뜀" in describe_error(info.value) and len(client.models.requests) == 1


def test_quota_retried_only_once(toolbox):
    client = FakeClient([])
    client.models = ByModel({"a": [_quota(delay=5), _quota(delay=5)], "b": [response([{"text": "b 답"}])]})
    slept = []
    chat = GeminiChat(client, toolbox, model="a", fallback_models=["b"], sleep=slept.append)
    assert chat.ask("질문").text == "b 답" and slept == [5.0]


def test_quota_error_text():
    assert "약 37초 후 다시 시도" in describe_error(_quota(delay=37))
    assert "오늘 무료 사용량 소진" in describe_error(_quota(daily=True))



def test_repeated_identical_calls_are_not_executed(toolbox):
    same = {"function_call": {"name": "list_available_data", "args": {}}}
    client = FakeClient([response([same]), response([same]), response([{"text": "끝"}])])
    runs = []
    chat = GeminiChat(client, toolbox, on_tool_call=lambda n, a: runs.append(n))
    turn = chat.ask("데이터?")
    assert turn.text == "끝" and runs == ["list_available_data"]  # 두 번째는 실행하지 않음
    repeated = client.models.requests[2]["contents"][-1].parts[0].function_response.response
    assert "이미 호출했습니다" in repeated["result"]["note"]


def test_final_round_without_text(toolbox):
    from fco_meta.chatbot.gemini import MAX_TOOL_ROUNDS

    loop = response([{"function_call": {"name": "list_formations", "args": {"team_color": "x"}}}])
    turn = GeminiChat(FakeClient([loop] * (MAX_TOOL_ROUNDS + 1)), toolbox).ask("?")
    assert turn.finish_reason == "tool_limit" and "답을 만들지 못했습니다" in turn.text
