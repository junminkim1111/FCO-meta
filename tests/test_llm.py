"""Claude backend with a fake client (no network): tool round → answer, append-only history, refusal."""

from types import SimpleNamespace as NS

import pytest

from test_analytics import db  # noqa: F401
from test_chatbot import toolbox  # noqa: F401

from fco_meta.chatbot.llm import REFUSED, RESET, ClaudeChat, ToolCall


def message(*blocks, stop="end_turn"):
    return NS(content=list(blocks), stop_reason=stop, model="claude-sonnet-5-5")


def text(t):
    return NS(type="text", text=t)


def thinking():
    return NS(type="thinking", thinking="")


def tool_use(name, args, id="t1"):
    return NS(type="tool_use", name=name, input=args, id=id)


class Stream:
    def __init__(self, msg):
        self.msg = msg
        self.text_stream = [b.text for b in msg.content if b.type == "text"]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.beta = NS(messages=NS(stream=self.stream))

    def with_options(self, **_):
        return self

    def stream(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return Stream(self.replies.pop(0))


def test_tool_round_then_answer_keeps_history_append_only(toolbox):  # noqa: F811
    asked = message(thinking(), text("찾아볼게요"), tool_use("recommend_players", {"team_color": "아스널", "role": "DM"}), stop="tool_use")
    client = FakeClient([asked, message(thinking(), text("아스널 볼란치는 볼란치R입니다."))])
    chat = ClaudeChat(client, toolbox)
    events = list(_events(chat.ask_stream("아스널 볼란치 추천")))
    assert any(isinstance(e, ToolCall) and e.name == "recommend_players" for e in events) and RESET in events
    first, second = client.requests
    assert first["tool_choice"] == {"type": "auto"} and first["fallbacks"] == "default"
    # 두 번째 요청: 첫 응답(생각 블록 포함)을 그대로 붙이고, 도구 결과를 한 user 메시지로
    assert second["messages"][1]["content"] is asked.content
    assert second["messages"][2]["content"][0]["type"] == "tool_result" and second["messages"][2]["content"][0]["tool_use_id"] == "t1"
    assert chat.history == [("아스널 볼란치 추천", "아스널 볼란치는 볼란치R입니다.")]

    # 다음 질문은 앞 질문·답 글만 보낸다 (도구 결과·생각 블록 없이)
    client.replies.append(message(text("네")))
    chat.ask("고마워")
    assert client.requests[-1]["messages"] == [
        {"role": "user", "content": "아스널 볼란치 추천"}, {"role": "assistant", "content": "아스널 볼란치는 볼란치R입니다."},
        {"role": "user", "content": "고마워"},
    ]  # fmt: skip


def test_refusal_is_answered_with_a_notice(toolbox):  # noqa: F811
    chat = ClaudeChat(FakeClient([message(stop="refusal")]), toolbox)
    assert chat.ask("x").text == REFUSED


def _events(gen):
    while True:
        try:
            yield next(gen)
        except StopIteration:
            return


class FakeHttp:
    """OpenAI-compatible /chat/completions."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def stream(self, method, url, headers, timeout, json):
        self.requests.append({"url": url, **json, "messages": list(json["messages"])})
        return SSE(self.replies.pop(0))


class SSE:
    """A streamed response: `lines` as the server sends them."""

    status_code = 200

    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter(self.lines)


def completion(content=None, tool_calls=None, finish="stop"):
    """SSE lines: the text in two pieces, each tool call's arguments in two pieces, then finish and usage."""
    import json

    def chunk(delta=None, **more):
        return "data: " + json.dumps({"model": "qwen/qwen3.8-flash", "choices": [{"delta": delta, **more}] if delta is not None else []} | (
            {"usage": more["usage"], "choices": []} if "usage" in more else {}))  # fmt: skip

    lines = [": OPENROUTER PROCESSING", ""]
    if content:
        half = len(content) // 2
        lines += [chunk({"content": content[:half]}), chunk({"content": content[half:]})]
    for i, call in enumerate(tool_calls or []):
        args = call["function"]["arguments"]
        lines.append(chunk({"tool_calls": [{"index": i, "id": call["id"], "type": "function", "function": {"name": call["function"]["name"], "arguments": args[:5]}}]}))
        lines.append(chunk({"tool_calls": [{"index": i, "function": {"arguments": args[5:]}}]}))
    return [*lines, chunk({}, finish_reason=finish), chunk(usage={"prompt_tokens": 100, "completion_tokens": 10}), "data: [DONE]"]


def test_openai_compatible_tool_round(toolbox):  # noqa: F811
    from fco_meta.chatbot.llm import OpenAIChat

    call = {"id": "c1", "type": "function", "function": {"name": "recommend_players", "arguments": '{"team_color": "아스널", "role": "DM"}'}}
    http = FakeHttp([completion(tool_calls=[call], finish="tool_calls"), completion("아스널 볼란치는 볼란치R입니다.")])
    chat = OpenAIChat(http, toolbox, model="qwen/qwen3.8-flash", base_url="https://example.test/v1", api_key="k")
    gen = chat.ask_stream("아스널 볼란치 추천")
    pieces = []
    while True:
        try:
            pieces.append(next(gen))
        except StopIteration as done:
            turn = done.value
            break
    assert [e for e in pieces if isinstance(e, str)] == ["아스널 볼란치는", " 볼란치R입니다."]  # 글은 오는 대로 흘려보낸다
    assert turn.text == "아스널 볼란치는 볼란치R입니다." and turn.tool_calls == [("recommend_players", {"team_color": "아스널", "role": "DM"})]
    first, second = http.requests
    assert first["url"] == "https://example.test/v1/chat/completions" and first["messages"][0]["role"] == "system"
    assert first["tools"][0]["type"] == "function" and first["tool_choice"] == "auto"
    assert second["messages"][2]["tool_calls"] == [call] and second["messages"][3]["role"] == "tool"
    assert second["messages"][3]["tool_call_id"] == "c1" and chat.tokens == {"input": 200, "output": 20}


def test_openai_compatible_thoughts_from_reasoning_content(toolbox):  # noqa: F811
    # OpenGateway(DeepSeek)는 생각을 delta.reasoning_content로 보낸다 (OpenRouter는 delta.reasoning)
    import json

    from fco_meta.chatbot.llm import OpenAIChat, Thought

    lines = completion("답입니다.")
    lines.insert(2, "data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "먼저 생각"}}]}))
    chat = OpenAIChat(FakeHttp([lines]), toolbox, model="deepseek/deepseek-v4.1-flash-eco", base_url="https://example.test/v1", api_key="k")
    gen, pieces = chat.ask_stream("질문", thoughts=True), []
    while True:
        try:
            pieces.append(next(gen))
        except StopIteration:
            break
    assert [p.text for p in pieces if isinstance(p, Thought)] == ["먼저 생각"]


def test_thoughts_stream_piece_by_piece_before_the_answer(toolbox):  # noqa: F811
    # 생각을 다 모았다 보내면 화면의 '생각함' 시간이 0.0초가 된다 → 조각마다 바로 보낸다
    import json

    from fco_meta.chatbot.llm import OpenAIChat, Thought

    lines = completion("답입니다.")
    for i, part in enumerate(["먼저 ", "생각"]):
        lines.insert(2 + i, "data: " + json.dumps({"choices": [{"delta": {"reasoning_content": part}}]}))
    chat = OpenAIChat(FakeHttp([lines]), toolbox, model="m", base_url="https://example.test/v1", api_key="k")
    pieces = list(chat.ask_stream("질문", thoughts=True))
    thoughts = [i for i, p in enumerate(pieces) if isinstance(p, Thought)]
    assert [pieces[i].text for i in thoughts] == ["먼저 ", "생각"]
    assert thoughts[-1] < next(i for i, p in enumerate(pieces) if isinstance(p, str))


def test_openai_compatible_request_is_cut_at_the_deadline(toolbox, monkeypatch):  # noqa: F811
    import time

    import pytest

    from fco_meta.chatbot import llm
    from fco_meta.chatbot.llm import AnswerTimeout

    def keepalive():  # 연결 유지 신호만 보내며 답하지 않는 서버 (읽기 타임아웃이 끝나지 않음)
        for _ in range(100):
            time.sleep(0.05)
            yield ": OPENROUTER PROCESSING"

    class Hanging:
        def stream(self, *a, **k):
            return SSE(keepalive())

    monkeypatch.setattr(llm, "MIN_REQUEST_TIMEOUT", 0)
    chat = llm.OpenAIChat(Hanging(), toolbox, model="m", api_key="k")
    chat.deadline = 0.3
    started = time.monotonic()
    with pytest.raises(AnswerTimeout):
        chat.ask("q")
    assert time.monotonic() - started < 2  # 서버가 답하지 않아도 전체 시간에서 끊는다


def test_a_figure_left_unverified_is_not_evidence_for_the_next_answer(toolbox):  # noqa: F811
    from fco_meta.chatbot.llm import OpenAIChat

    call = {"id": "c1", "type": "function", "function": {"name": "recommend_players", "arguments": '{"team_color": "아스널", "role": "DM"}'}}
    again = {**call, "id": "c2", "function": {**call["function"], "arguments": '{"team_color": "아스널", "role": "ST"}'}}
    http = FakeHttp([completion(tool_calls=[call], finish="tool_calls"), completion("볼란치R 사용률은 77.7%입니다."),
                     completion(tool_calls=[again], finish="tool_calls"), completion("아까 말한 대로 77.7%입니다.")])  # fmt: skip
    chat = OpenAIChat(http, toolbox, model="m", api_key="k")
    chat.ask("아스널 볼란치 추천")
    assert chat.unverified == ["77.7%"]  # 하나뿐이라 다시 쓰지는 않는다
    chat.ask("아스널 공격수는?")
    assert chat.unverified == ["77.7%"]  # 앞 답에 있었다는 이유로 확인된 수치가 되지 않는다


def test_squad_check_tracks_success_and_later_changes():
    from fco_meta.chatbot.llm import unchecked_squad

    assert unchecked_squad(False, "squad_candidates", {}, False)
    assert unchecked_squad(True, "check_squad", {"ok": False}, False)
    assert unchecked_squad(True, "check_squad", {"error": "bad picks"}, True)
    assert not unchecked_squad(True, "check_squad", {"ok": True}, False)
    assert unchecked_squad(False, "squad_candidates", {}, False)
    assert not unchecked_squad(True, "recommend_squad", {"lineup": [{}]}, False)


def test_prior_answer_is_not_numeric_evidence(toolbox):
    client = FakeClient([
        message(tool_use("list_formations", {}), stop="tool_use"),
        message(text("사용률은 77.7%입니다.")),
    ])
    chat = ClaudeChat(client, toolbox)
    chat.remember("이전 질문", "사용률은 77.7%입니다.")
    chat.ask("포메이션 사용률")
    assert chat.unverified == ["77.7%"]


def test_tool_calls_stop_at_the_cap_and_the_model_answers_with_what_it_has(toolbox):  # noqa: F811
    # 한 번에 여러 도구를 부르면 ROUNDS로는 못 막는다: MAX_TOOL_CALLS 넘는 호출은 실행하지 않고, 다음 번은 도구 없이 답만
    from fco_meta.chatbot.llm import MAX_TOOL_CALLS, OpenAIChat

    calls = [{"id": f"c{i}", "type": "function", "function": {"name": "recommend_players", "arguments": f'{{"team_color": "아스널", "role": "DM", "top_n": {i + 1}}}'}}
             for i in range(MAX_TOOL_CALLS + 5)]  # fmt: skip
    http = FakeHttp([completion(tool_calls=calls, finish="tool_calls"), completion("지금까지 결과로 답합니다.")])
    chat = OpenAIChat(http, toolbox, model="m", base_url="https://example.test/v1", api_key="k")
    gen = chat.ask_stream("팀컬러마다 다 찾아줘")
    while True:
        try:
            next(gen)
        except StopIteration as done:
            turn = done.value
            break
    assert len(turn.tool_calls) == MAX_TOOL_CALLS and turn.text == "지금까지 결과로 답합니다."
    second = http.requests[1]
    assert second["tool_choice"] == "none"  # 상한에 닿은 다음 번은 도구 없이
    refused = [m for m in second["messages"] if m["role"] == "tool" and "한도" in m["content"]]
    assert len(refused) == 5


def _drain(gen):
    while True:
        try:
            next(gen)
        except StopIteration as done:
            return done.value


def test_after_wrap_up_time_the_model_answers_without_tools_or_thinking(toolbox):  # noqa: F811
    # 마무리 시각(wrap_after)이 지나면 다음 요청은 도구 없이(tool_choice none), 생각도 끈 채(reasoning enabled false) 답만
    from fco_meta.chatbot.llm import OpenAIChat

    clock = [0.0]
    call = {"id": "c1", "type": "function", "function": {"name": "recommend_players", "arguments": '{"team_color": "아스널", "role": "DM"}'}}
    http = FakeHttp([completion(tool_calls=[call], finish="tool_calls"), completion("지금까지 결과로 답합니다.")])
    chat = OpenAIChat(http, toolbox, model="m", base_url="https://example.test/v1", api_key="k", now=lambda: clock[0])
    gen = chat.ask_stream("질문", deadline=360, wrap_after=300)
    next(gen)  # 첫 요청이 끝나고 도구를 부르는 동안 시간이 흐른다
    clock[0] = 301
    turn = _drain(gen)
    assert turn.text == "지금까지 결과로 답합니다."
    first, second = http.requests
    assert first["tool_choice"] == "auto" and "reasoning" not in first
    assert second["tool_choice"] == "none" and second["reasoning"] == {"enabled": False}


def test_thinking_past_wrap_up_time_is_dropped_for_a_plain_answer(toolbox):  # noqa: F811
    # 아직 생각만 하는 중에 마무리 시각을 넘기면 그 요청은 버리고 바로 답만 (답을 쓰기 시작했으면 끝까지 둔다)
    import json

    from fco_meta.chatbot.llm import OpenAIChat

    clock = [0.0]
    thinking = [": OPENROUTER PROCESSING"] + ["data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "생각"}}]})] * 3

    class SlowClock(FakeHttp):  # 첫 요청의 생각 줄을 받는 사이 305초가 된다
        def stream(self, method, url, headers, timeout, json):
            res = super().stream(method, url, headers=headers, timeout=timeout, json=json)
            clock[0] = 305
            return res

    http = SlowClock([thinking, completion("바로 답합니다.")])
    chat = OpenAIChat(http, toolbox, model="m", base_url="https://example.test/v1", api_key="k", now=lambda: clock[0])
    turn = _drain(chat.ask_stream("질문", deadline=360, wrap_after=300))
    assert turn.text == "바로 답합니다." and len(http.requests) == 2
    assert http.requests[1]["tool_choice"] == "none" and http.requests[1]["reasoning"] == {"enabled": False}


def test_timeout_message_names_the_deadline_that_applied(toolbox):  # noqa: F811
    from fco_meta.chatbot.llm import AnswerTimeout, describe_error

    assert "360초" in describe_error(AnswerTimeout("m 응답 없음 (상한 360초)")) and "120초" not in describe_error(AnswerTimeout("m 응답 없음 (상한 360초)"))


def test_cancel_stops_the_model_loop(toolbox):  # noqa: F811
    # 웹이 연결 끊김에 cancel을 켜면 모델 요청·도구를 더 진행하지 않는다
    import threading

    import pytest

    from fco_meta.chatbot.llm import Cancelled, OpenAIChat

    cancel = threading.Event()
    cancel.set()
    http = FakeHttp([completion("답")])
    chat = OpenAIChat(http, toolbox, model="m", base_url="https://example.test/v1", api_key="k")
    with pytest.raises(Cancelled):
        _drain(chat.ask_stream("질문", cancel=cancel))
    assert http.requests == []  # 요청을 보내기 전에 멈춘다


@pytest.mark.parametrize("base_url", ["https://apis.opengateway.ai/v1", "https://openrouter.ai/api/v1"])
def test_gateway_records_stream_cost_and_id_even_if_interrupted(toolbox, base_url):  # noqa: F811
    from fco_meta.chatbot.llm import OpenAIChat

    replies = [
        ['data: {"id":"gen-interrupted","choices":[{"delta":{"content":"답"}}]}'],
        ['data: {"id":"gen-free","usage":{"prompt_tokens":2,"completion_tokens":1,"cost":0},"choices":[]}'],
        ['data: {"id":"gen-paid","usage":{"prompt_tokens":2,"completion_tokens":1,"cost":0.000012},"choices":[]}'],
    ]
    chat = OpenAIChat(FakeHttp(replies), toolbox, model="m", base_url=base_url, api_key="test")
    stream = chat.ask_stream("질문")
    assert next(stream) == "답"
    stream.close()
    assert chat.cost_calls == [{"gateway": base_url, "id": "gen-interrupted", "cost": None}]
    chat.ask("다시 질문")
    assert chat.cost_calls == [{"gateway": base_url, "id": "gen-free", "cost": 0}]
    chat.ask("또 질문")
    assert chat.cost_calls == [{"gateway": base_url, "id": "gen-paid", "cost": 0.000012}]
