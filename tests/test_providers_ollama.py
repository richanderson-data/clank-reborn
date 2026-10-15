import pytest

from clank.errors import (
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderProtocolError,
    StreamInterrupted,
)
from clank.providers.base import Done, TextDelta, ToolCall
from clank.providers.ollama import OllamaProvider

from .conftest import FakeServer

MESSAGES = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hi"},
]


def collect(provider: OllamaProvider, messages=None, tools=None) -> list:
    return list(provider.chat_stream(messages or MESSAGES, tools=tools))


def test_streams_text_deltas(ollama_server: FakeServer) -> None:
    ollama_server.enqueue(
        [
            {"message": {"role": "assistant", "content": "Hel"}, "done": False},
            {"message": {"role": "assistant", "content": "lo"}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"},
        ]
    )
    provider = OllamaProvider("qwen3.5:4b", ollama_server.url)
    events = collect(provider)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    assert isinstance(events[-1], Done)
    request = ollama_server.requests()[0]
    assert request["model"] == "qwen3.5:4b"
    assert request["stream"] is True
    assert request["messages"][1]["content"] == "hi"
    assert "tools" not in request


def test_passes_tools_when_given(ollama_server: FakeServer) -> None:
    ollama_server.enqueue([{"message": {"content": "ok"}, "done": True}])
    provider = OllamaProvider("m", ollama_server.url)
    specs = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    collect(provider, tools=specs)
    assert ollama_server.requests()[0]["tools"] == specs


def test_emits_tool_call(ollama_server: FakeServer) -> None:
    ollama_server.enqueue(
        [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "list_directory",
                                "arguments": {"path": "."},
                            }
                        }
                    ],
                },
                "done": False,
            },
            {"message": {"content": ""}, "done": True},
        ]
    )
    provider = OllamaProvider("m", ollama_server.url)
    calls = [e for e in collect(provider) if isinstance(e, ToolCall)]
    assert len(calls) == 1
    assert calls[0].name == "list_directory"
    assert calls[0].arguments == {"path": "."}
    assert calls[0].id
    assert calls[0].parse_error is None


def test_tool_call_with_non_object_arguments(ollama_server: FakeServer) -> None:
    ollama_server.enqueue(
        [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "read_file", "arguments": "not-an-object"}}
                    ],
                },
                "done": True,
            }
        ]
    )
    provider = OllamaProvider("m", ollama_server.url)
    call = next(e for e in collect(provider) if isinstance(e, ToolCall))
    assert call.arguments == {}
    assert call.parse_error and "JSON object" in call.parse_error


def test_translates_canonical_messages(ollama_server: FakeServer) -> None:
    ollama_server.enqueue([{"message": {"content": "ok"}, "done": True}])
    provider = OllamaProvider("m", ollama_server.url)
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
    sent = ollama_server.requests()[0]["messages"]
    assert sent[2]["tool_calls"][0]["function"] == {
        "name": "read_file",
        "arguments": {"path": "a.txt"},
    }
    assert sent[3] == {"role": "tool", "content": "body", "tool_name": "read_file"}


def test_http_error_includes_hint(ollama_server: FakeServer) -> None:
    ollama_server.enqueue((404, {"error": "model 'nope' not found, try pulling it first"}))
    provider = OllamaProvider("nope", ollama_server.url)
    with pytest.raises(ProviderHTTPError) as info:
        collect(provider)
    assert info.value.status_code == 404
    assert "ollama pull nope" in str(info.value)


def test_connection_refused(unused_port: int) -> None:
    provider = OllamaProvider("m", f"http://127.0.0.1:{unused_port}")
    with pytest.raises(ProviderConnectionError, match="cannot reach Ollama"):
        collect(provider)


def test_malformed_stream_line(ollama_server: FakeServer) -> None:
    ollama_server.enqueue([("raw", "this is not json")])
    provider = OllamaProvider("m", ollama_server.url)
    with pytest.raises(ProviderProtocolError, match="malformed stream line"):
        collect(provider)


def test_mid_stream_error_line(ollama_server: FakeServer) -> None:
    ollama_server.enqueue(
        [
            {"message": {"content": "partial"}, "done": False},
            {"error": "out of memory"},
        ]
    )
    provider = OllamaProvider("m", ollama_server.url)
    with pytest.raises(ProviderProtocolError, match="mid-stream"):
        collect(provider)


def test_interrupted_stream(ollama_server: FakeServer) -> None:
    ollama_server.enqueue(
        [
            {"message": {"content": "partial"}, "done": False},
            {"message": {"content": "more " * 140}, "done": False},
            ("sleep", 0.3),
            "__abort__",
        ]
    )
    provider = OllamaProvider("m", ollama_server.url)
    events = []
    with pytest.raises(StreamInterrupted):
        for event in provider.chat_stream(MESSAGES):
            events.append(event)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["partial"]


def test_stalled_stream(ollama_server: FakeServer) -> None:
    ollama_server.enqueue(
        [
            {"message": {"content": "start"}, "done": False},
            ("sleep", 0.6),
        ]
    )
    provider = OllamaProvider("m", ollama_server.url, timeout=(1.0, 0.2))
    with pytest.raises(StreamInterrupted, match="stalled"):
        collect(provider)


def test_list_models(ollama_server: FakeServer) -> None:
    provider = OllamaProvider("m", ollama_server.url)
    models = provider.list_models()
    assert [m.name for m in models] == ["qwen3.5:4b"]
    assert models[0].size == 2_500_000_000


def test_list_models_connection_error(unused_port: int) -> None:
    provider = OllamaProvider("m", f"http://127.0.0.1:{unused_port}")
    with pytest.raises(ProviderConnectionError):
        provider.list_models()


def test_empty_lines_are_ignored(ollama_server: FakeServer) -> None:
    ollama_server.enqueue(
        [("raw", ""), {"message": {"content": "after blank"}, "done": True}]
    )
    provider = OllamaProvider("m", ollama_server.url)
    deltas = [e.text for e in collect(provider) if isinstance(e, TextDelta)]
    assert deltas == ["after blank"]
