"""Gemini loop with a scripted client. Responses are built from the SDK's own types."""

import json

import pytest
from test_analytics import db  # noqa: F401

pytest.importorskip("google.genai")
from google.genai import types  # noqa: E402

from fco_meta.chatbot import Toolbox  # noqa: E402
from fco_meta.chatbot.gemini import GeminiChat, function_declarations  # noqa: E402


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


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


@pytest.fixture
def toolbox(db):  # noqa: F811
    return Toolbox(db.conn, min_sample=1)


def test_function_declarations_accepted_by_sdk():
    decls = {d.name: d for d in function_declarations()}
    assert set(decls) == {"resolve_terms", "list_available_data", "list_formations", "recommend_players", "get_player_detail"}
    assert decls["recommend_players"].parameters_json_schema["required"] == ["role"]  # 팀컬러 생략 = 전체 랭커


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

    assert turn.text == "라이스가 1순위입니다." and turn.finish_reason == "STOP"
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

    loop = response([{"function_call": {"name": "list_available_data", "args": {}}}])
    turn = GeminiChat(FakeClient([loop] * 8), toolbox).ask("계속")
    assert turn.finish_reason == "tool_limit" and len(turn.tool_calls) == 8
    assert json.loads(Toolbox.run(toolbox, "list_available_data", {})[0])["combos"]


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
    with pytest.raises(errors.ServerError):
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
    with pytest.raises(errors.ServerError):
        chat.ask("질문")
    assert chat.contents == []  # 기록도 되돌림


def test_models_from_env(monkeypatch):
    from fco_meta.chatbot.gemini import DEFAULT_MODEL, models_from_env

    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_FALLBACK_MODELS", raising=False)
    assert models_from_env() == (DEFAULT_MODEL, ["gemini-2.5-flash"])
    monkeypatch.setenv("GEMINI_MODEL", "m1")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "m2, m3")
    assert models_from_env() == ("m1", ["m2", "m3"])
    assert models_from_env("cli") == ("cli", ["m2", "m3"])
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "")
    assert models_from_env()[1] == []  # 빈 값 = 대체 모델 없음
