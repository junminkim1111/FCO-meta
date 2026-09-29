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


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


@pytest.fixture
def toolbox(db):  # noqa: F811
    return Toolbox(db.conn, min_sample=1)


def test_function_declarations_accepted_by_sdk():
    decls = {d.name: d for d in function_declarations()}
    assert set(decls) == {
        "resolve_terms", "list_available_data", "list_formations", "recommend_players", "recommend_squad",
        "get_player_detail", "get_meta_trends", "query_squads", "query_rankers", "get_formation_overview",
    }  # fmt: skip
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

    assert turn.text.startswith("라이스가 1순위입니다.") and turn.finish_reason == "STOP"
    # 근거 줄은 모델이 아니라 도구 결과에서 만든다
    assert turn.evidence == ["아스널 4-2-3-1 DM — 랭커 3명 스쿼드 (2026-09-28 20:00 기준)"]
    assert turn.text.endswith("\n\n[근거] 아스널 4-2-3-1 DM — 랭커 3명 스쿼드 (2026-09-28 20:00 기준)")
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
    from fco_meta.chatbot.gemini import DEFAULT_MODEL, models_from_env

    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_FALLBACK_MODELS", raising=False)
    assert models_from_env() == (DEFAULT_MODEL, ["gemini-3.5-flash"])
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
    chat = GeminiChat(client, toolbox, model="gemini-3.8-flash", fallback_models=["gemini-3.5-flash"], sleep=lambda s: None)
    assert chat.ask("질문").text == "lite 답" and chat.last_model == "gemini-3.8-flash-lite"
    # 목록은 한 번만 조회하고 재사용
    client.models.list = lambda: (_ for _ in ()).throw(AssertionError("listed twice"))
    client.models.outcomes["gemini-3.8-flash"] = [response([{"text": "복구"}])]
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
    for err in (_quota(delay=40), _quota(daily=True), _quota()):
        client = FakeClient([])
        client.models = ByModel({"a": [err], "b": [response([{"text": "b 답"}])]})
        slept = []
        chat = GeminiChat(client, toolbox, model="a", fallback_models=["b"], sleep=slept.append)
        assert chat.ask("질문").text == "b 답" and slept == []
        assert [r["model"] for r in client.models.requests] == ["a", "b"]  # 한도 걸린 모델은 다시 부르지 않음


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
