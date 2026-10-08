"""Claude backend with a fake client (no network): tool round → answer, append-only history, refusal."""

from types import SimpleNamespace as NS

from test_analytics import db  # noqa: F401
from test_chatbot import toolbox  # noqa: F401

from fco_meta.chatbot.llm import REFUSED, ClaudeChat
from fco_meta.chatbot.gemini import RESET, ToolCall


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

    def post(self, url, headers, timeout, json):
        self.requests.append({"url": url, **json, "messages": list(json["messages"])})
        return NS(raise_for_status=lambda: None, json=lambda: self.replies.pop(0))


def completion(content=None, tool_calls=None, finish="stop"):
    msg = {"role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})}
    return {"model": "qwen/qwen3.8-flash", "choices": [{"message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10}}  # fmt: skip


def test_openai_compatible_tool_round(toolbox):  # noqa: F811
    from fco_meta.chatbot.llm import OpenAIChat

    call = {"id": "c1", "type": "function", "function": {"name": "recommend_players", "arguments": '{"team_color": "아스널", "role": "DM"}'}}
    http = FakeHttp([completion(tool_calls=[call], finish="tool_calls"), completion("아스널 볼란치는 볼란치R입니다.")])
    chat = OpenAIChat(http, toolbox, model="qwen/qwen3.8-flash", base_url="https://example.test/v1", api_key="k")
    turn = chat.ask("아스널 볼란치 추천")
    assert turn.text == "아스널 볼란치는 볼란치R입니다." and turn.tool_calls == [("recommend_players", {"team_color": "아스널", "role": "DM"})]
    first, second = http.requests
    assert first["url"] == "https://example.test/v1/chat/completions" and first["messages"][0]["role"] == "system"
    assert first["tools"][0]["type"] == "function" and first["tool_choice"] == "auto"
    assert second["messages"][2]["tool_calls"] == [call] and second["messages"][3]["role"] == "tool"
    assert second["messages"][3]["tool_call_id"] == "c1" and chat.tokens == {"input": 200, "output": 20}


def test_openai_compatible_request_is_cut_at_the_deadline(toolbox, monkeypatch):  # noqa: F811
    import threading
    import time

    import pytest

    from fco_meta.chatbot import llm
    from fco_meta.chatbot.gemini import AnswerTimeout

    gate = threading.Event()

    class Hanging:  # 연결 유지 신호만 보내며 답하지 않는 서버 (읽기 타임아웃이 끝나지 않음)
        def post(self, *a, **k):
            gate.wait(5)

    monkeypatch.setattr(llm, "MIN_REQUEST_TIMEOUT", 0)
    chat = llm.OpenAIChat(Hanging(), toolbox, model="m", api_key="k")
    chat.deadline = 0.3
    started = time.monotonic()
    with pytest.raises(AnswerTimeout):
        chat.ask("q")
    assert time.monotonic() - started < 2  # 서버가 답하지 않아도 전체 시간에서 끊는다
    gate.set()
