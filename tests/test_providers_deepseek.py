import pytest

from clank.errors import (
    ConfigError,
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderProtocolError,
    StreamInterrupted,
)
from clank.providers.base import Done, TextDelta, ToolCall
from clank.providers.deepseek import DeepSeekProvider

from .conftest import FakeServer

MESSAGES = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hi"},
]


def make_provider(server: FakeServer, **kwargs) -> DeepSeekProvider:
    return DeepSeekProvider(
        kwargs.pop("model", "deepseek-chat"),
        api_key=kwargs.pop("api_key", "sk-test"),
        base_url=server.url,
        **kwargs,
    )


def collect(provider: DeepSeekProvider, messages=None, tools=None) -> list:
    return list(provider.chat_stream(messages or MESSAGES, tools=tools))


def delta(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}, "index": 0, "finish_reason": None}]}


def test_requires_api_key() -> None:
    with pytest.raises(ConfigError, match="API key"):
        DeepSeekProvider("deepseek-chat", api_key="  ")


def test_streams_text(deepseek_server: FakeServer) -> None:
    deepseek_server.enqueue(
        [
            delta("Hello"),
            delta(" world"),
            {"choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]},
            "[DONE]",
        ]
    )
    provider = make_provider(deepseek_server)
    events = collect(provider)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hello", " world"]
    assert events[-1] == Done("stop")
    request = deepseek_server.requests()[0]
    assert request["authorization"] == "Bearer sk-test"
    assert request["body"]["stream"] is True
    assert request["body"]["model"] == "deepseek-chat"


def test_accumulates_tool_call_deltas(deepseek_server: FakeServer) -> None:
    deepseek_server.enqueue(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"pa',
                                    },
                                }
                            ]
                        },
                        "index": 0,
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": 'th": "a.txt"}'}}
                            ]
                        },
                        "index": 0,
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {}, "index": 0, "finish_reason": "tool_calls"}]},
            "[DONE]",
        ]
    )
    provider = make_provider(deepseek_server)
    events = collect(provider, tools=[{"type": "function", "function": {"name": "read_file"}}])
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1
    assert calls[0].id == "call_abc"
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "a.txt"}
    assert calls[0].parse_error is None
    assert events[-1] == Done("tool_calls")
    assert deepseek_server.requests()[0]["body"]["tools"]


def test_invalid_tool_arguments_json(deepseek_server: FakeServer) -> None:
    deepseek_server.enqueue(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {"name": "read_file", "arguments": "not-json"},
                                }
                            ]
                        },
                        "index": 0,
                        "finish_reason": None,
                    }
                ]
            },
            "[DONE]",
        ]
    )
    provider = make_provider(deepseek_server)
    call = next(e for e in collect(provider) if isinstance(e, ToolCall))
    assert call.arguments == {}
    assert call.parse_error and "valid JSON" in call.parse_error


def test_translates_canonical_messages(deepseek_server: FakeServer) -> None:
    deepseek_server.enqueue([delta("ok"), "[DONE]"])
    provider = make_provider(deepseek_server)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "name": "read_file", "arguments": {"path": "a.txt"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "body"},
    ]
    collect(provider, messages=messages)
    sent = deepseek_server.requests()[0]["body"]["messages"]
    assert sent[2]["tool_calls"][0]["function"]["arguments"] == '{"path": "a.txt"}'
    assert sent[3] == {"role": "tool", "tool_call_id": "call_1", "content": "body"}


def test_401_hint(deepseek_server: FakeServer) -> None:
    deepseek_server.enqueue((401, {"error": {"message": "Authentication Fails"}}))
    provider = make_provider(deepseek_server)
    with pytest.raises(ProviderHTTPError) as info:
        collect(provider)
    assert info.value.status_code == 401
    assert "DEEPSEEK_API_KEY" in str(info.value)


def test_402_hint(deepseek_server: FakeServer) -> None:
    deepseek_server.enqueue((402, {"error": {"message": "Insufficient Balance"}}))
    provider = make_provider(deepseek_server)
    with pytest.raises(ProviderHTTPError, match="insufficient balance"):
        collect(provider)


def test_connection_refused(unused_port: int) -> None:
    provider = DeepSeekProvider(
        "deepseek-chat", api_key="sk-test", base_url=f"http://127.0.0.1:{unused_port}"
    )
    with pytest.raises(ProviderConnectionError, match="cannot reach the DeepSeek API"):
        collect(provider)


def test_mid_stream_error_event(deepseek_server: FakeServer) -> None:
    deepseek_server.enqueue(
        [
            delta("partial"),
            ("raw", 'data: {"error": {"message": "server exploded"}}'),
        ]
    )
    provider = make_provider(deepseek_server)
    with pytest.raises(ProviderProtocolError, match="server exploded"):
        collect(provider)


def test_interrupted_stream(deepseek_server: FakeServer) -> None:
    deepseek_server.enqueue(
        [delta("partial"), delta("more " * 140), ("sleep", 0.3), "__abort__"]
    )
    provider = make_provider(deepseek_server)
    events = []
    with pytest.raises(StreamInterrupted):
        for event in provider.chat_stream(MESSAGES):
            events.append(event)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["partial"]


def test_stalled_stream(deepseek_server: FakeServer) -> None:
    deepseek_server.enqueue([delta("start"), ("sleep", 0.6)])
    provider = make_provider(deepseek_server, timeout=(1.0, 0.2))
    with pytest.raises(StreamInterrupted, match="stalled"):
        collect(provider)


def test_ignores_comments_and_junk_lines(deepseek_server: FakeServer) -> None:
    deepseek_server.enqueue(
        [
            ("raw", ": keep-alive comment"),
            ("raw", "event: ping"),
            delta("fine"),
            "[DONE]",
        ]
    )
    provider = make_provider(deepseek_server)
    assert [e.text for e in collect(provider) if isinstance(e, TextDelta)] == ["fine"]


def test_list_models(deepseek_server: FakeServer) -> None:
    provider = make_provider(deepseek_server)
    models = provider.list_models()
    assert [m.name for m in models] == ["deepseek-chat"]


def test_list_models_unauthorized(deepseek_server: FakeServer) -> None:
    deepseek_server.state["models_status"] = 401
    provider = make_provider(deepseek_server)
    with pytest.raises(ProviderHTTPError) as info:
        provider.list_models()
    assert info.value.status_code == 401
