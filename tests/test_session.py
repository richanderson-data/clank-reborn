from pathlib import Path

import pytest

from clank.db import Store
from clank.errors import ProviderConnectionError, StreamInterrupted
from clank.providers.base import Done, TextDelta, ToolCall
from clank.session import ChatSession
from clank.tools import ToolRegistry, ToolSpec, build_default_registry
from clank.tools.permissions import Sandbox

from .conftest import FakeProvider, RecordingUI


def make_session(
    store: Store,
    settings,
    project_dir: Path,
    scripts: list,
    tools: bool = True,
):
    provider = FakeProvider(scripts)
    registry = (
        build_default_registry(Sandbox([project_dir])) if tools else None
    )
    ui = RecordingUI()
    session = ChatSession(store, provider, registry, settings, ui)
    return session, provider, ui


def test_happy_path_streams_and_persists(store, settings, project_dir) -> None:
    session, _, ui = make_session(
        store, settings, project_dir, [[TextDelta("Hello"), TextDelta(" world"), Done()]]
    )
    result = session.send("hi there")
    assert result.text == "Hello world"
    assert result.tool_calls == 0
    messages = store.messages(session.session_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "hi there"
    assert messages[1]["content"] == "Hello world"
    assert ("delta", "Hello") in ui.events
    assert ui.kinds().count("delta") == 2


def test_title_is_set_from_first_message(store, settings, project_dir) -> None:
    session, _, _ = make_session(store, settings, project_dir, [[TextDelta("ok"), Done()]])
    session.send("what does calc.py do?")
    assert store.get_session(session.session_id)["title"] == "what does calc.py do?"


def test_tool_round_trip(store, settings, project_dir) -> None:
    call = ToolCall(id="call_1", name="list_directory", arguments={"path": "."})
    session, provider, ui = make_session(
        store,
        settings,
        project_dir,
        [
            [call, Done()],
            [TextDelta("You have notes.txt and src/."), Done()],
        ],
    )
    result = session.send("what files are here?")
    assert result.tool_calls == 1
    messages = store.messages(session.session_id)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[1]["tool_calls"][0]["name"] == "list_directory"
    assert "notes.txt" in messages[2]["content"]
    assert messages[2]["tool_call_id"] == "call_1"
    assert messages[3]["content"].startswith("You have")
    tool_events = [e for e in ui.events if e[0] == "tool"]
    assert tool_events and tool_events[0][2] is True and tool_events[0][3] is False
    second_request = provider.calls[1]
    assert second_request["tools"] is not None
    roles = [m["role"] for m in second_request["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]


def test_denied_tool_is_fed_back(store, settings, project_dir) -> None:
    call = ToolCall(id="c1", name="read_file", arguments={"path": "../outside.txt"})
    session, _, ui = make_session(
        store,
        settings,
        project_dir,
        [[call, Done()], [TextDelta("I was denied, sorry."), Done()]],
    )
    session.send("read the secret file")
    messages = store.messages(session.session_id)
    assert messages[2]["content"].startswith("DENIED")
    tool_events = [e for e in ui.events if e[0] == "tool"]
    assert tool_events[0][3] is True


def test_unknown_tool_from_model_is_recoverable(store, settings, project_dir) -> None:
    call = ToolCall(id="c1", name="launch_rocket", arguments={})
    session, _, _ = make_session(
        store,
        settings,
        project_dir,
        [[call, Done()], [TextDelta("I cannot do that."), Done()]],
    )
    result = session.send("launch a rocket")
    messages = store.messages(session.session_id)
    assert "unknown tool" in messages[2]["content"]
    assert result.text == "I cannot do that."


def test_parse_error_is_reported_without_execution(store, settings, project_dir) -> None:
    call = ToolCall(id="c1", name="read_file", arguments={}, parse_error="broken JSON")
    session, _, _ = make_session(
        store,
        settings,
        project_dir,
        [[call, Done()], [TextDelta("Sorry."), Done()]],
    )
    session.send("read it")
    messages = store.messages(session.session_id)
    assert "could not parse arguments" in messages[2]["content"]


def test_provider_error_keeps_history_consistent(store, settings, project_dir) -> None:
    session, _, _ = make_session(
        store, settings, project_dir, [ProviderConnectionError("ollama is down")]
    )
    with pytest.raises(ProviderConnectionError):
        session.send("hello")
    messages = store.messages(session.session_id)
    assert [m["role"] for m in messages] == ["user"]


def test_interrupted_stream_does_not_persist_partial(store, settings, project_dir) -> None:
    def broken():
        yield TextDelta("partial answer")
        raise StreamInterrupted("connection lost")

    session, _, ui = make_session(store, settings, project_dir, [broken()])
    with pytest.raises(StreamInterrupted):
        session.send("hello")
    messages = store.messages(session.session_id)
    assert [m["role"] for m in messages] == ["user"]
    assert ("delta", "partial answer") in ui.events


def test_interrupted_after_tool_keeps_completed_messages(store, settings, project_dir) -> None:
    call = ToolCall(id="c1", name="list_directory", arguments={"path": "."})

    def after_tool():
        yield TextDelta("reading...")
        raise StreamInterrupted("dropped")

    session, _, _ = make_session(
        store, settings, project_dir, [[call, Done()], after_tool()]
    )
    with pytest.raises(StreamInterrupted):
        session.send("list files")
    roles = [m["role"] for m in store.messages(session.session_id)]
    assert roles == ["user", "assistant", "tool"]


def test_max_tool_rounds_stops_loop(store, settings, project_dir) -> None:
    settings.max_tool_rounds = 1
    call = ToolCall(id="c1", name="list_directory", arguments={"path": "."})
    session, provider, ui = make_session(
        store,
        settings,
        project_dir,
        [[call, Done()], [call, Done()], [TextDelta("I listed the directory; further exploration was stopped."), Done()]],
    )
    result = session.send("loop please")
    assert result.stopped_early is True
    assert len(provider.calls) == 3
    assert provider.calls[-1]["tools"] is None
    assert "listed the directory" in result.text
    assert any(kind == "error" for kind in ui.kinds())


def test_empty_input_is_ignored(store, settings, project_dir) -> None:
    session, provider, _ = make_session(store, settings, project_dir, [])
    result = session.send("   ")
    assert result.text == ""
    assert store.messages(session.session_id) == []
    assert provider.calls == []


def test_long_task_continues_through_progress_reviews(store, settings, project_dir):
    assert settings.max_tool_rounds == 100
    scripts = []
    for index in range(41):
        name = "unknown_action" if index == 19 else "list_directory"
        scripts.append([ToolCall(id=f"c{index}", name=name, arguments={"path": "."}), Done()])
    scripts.append([TextDelta("Completed after inspecting and redirecting."), Done()])
    session, provider, ui = make_session(store, settings, project_dir, scripts)
    result = session.send("Work through a substantial task")
    assert result.tool_calls == 41
    assert not result.stopped_early
    assert len(provider.calls) == 42
    assert "This is NOT a stop" in provider.calls[20]["messages"][0]["content"]
    assert "unknown tool" in provider.calls[20]["messages"][0]["content"]
    assert "This is NOT a stop" in provider.calls[40]["messages"][0]["content"]
    for index in (19, 21, 39, 41):
        assert "This is NOT a stop" not in provider.calls[index]["messages"][0]["content"]
    reviews = [event for event in ui.events if event[0] == "info" and "Progress review" in event[1]]
    assert len(reviews) == 2
    assert all(call["tools"] is not None for call in provider.calls)


@pytest.mark.parametrize("summary", ["Final summary: unresolved blocker", ""])
def test_budget_terminal_round_summarizes_without_continue_notice(store, settings, project_dir, summary):
    settings.max_tool_rounds = 20
    call = ToolCall("one", "list_directory", {"path": "."})
    scripts = [[call, Done()] for _ in range(20)]
    scripts += [[TextDelta("I will inspect another file."), call, Done()], [TextDelta(summary), Done()]]
    session, provider, ui = make_session(store, settings, project_dir, scripts)
    result = session.send("long task")
    assert result.tool_calls == 20 and result.stopped_early
    assert provider.calls[-1]["tools"] is None
    assert "This is NOT a stop" not in provider.calls[20]["messages"][0]["content"]
    assert not any(event[0] == "info" and "task continues" in event[1] for event in ui.events)
    if summary:
        assert result.text == summary
    else:
        assert "remains incomplete" in result.text


def test_text_and_tool_call_in_same_message(store, settings, project_dir) -> None:
    call = ToolCall(id="c1", name="list_directory", arguments={"path": "."})
    session, _, _ = make_session(
        store,
        settings,
        project_dir,
        [[TextDelta("Checking the files. "), call, Done()], [TextDelta("done"), Done()]],
    )
    session.send("check")
    first_assistant = store.messages(session.session_id)[1]
    assert first_assistant["content"] == "Checking the files. "
    assert first_assistant["tool_calls"][0]["id"] == "c1"


def test_tools_disabled_rejects_tool_calls(store, settings, project_dir) -> None:
    settings.tools_enabled = False
    call = ToolCall(id="c1", name="list_directory", arguments={"path": "."})
    session, provider, _ = make_session(
        store, settings, project_dir, [[call, Done()], [TextDelta("no tools"), Done()]], tools=False
    )
    session.send("list files")
    assert provider.calls[0]["tools"] is None
    messages = store.messages(session.session_id)
    assert "tools are disabled" in messages[2]["content"]


def test_wire_messages_truncates_at_user_boundary(store, settings, project_dir) -> None:
    session, _, _ = make_session(store, settings, project_dir, [])
    sid = session.session_id
    for index in range(10):
        role = "user" if index % 2 == 0 else "assistant"
        store.append(sid, {"role": role, "content": f"m{index}"})
    settings.max_history = 4
    wire = session._wire_messages()
    assert wire[0]["role"] == "system"
    assert len(wire) == 5
    assert wire[1]["role"] == "user"
    assert wire[-1]["content"] == "m9"


def test_wire_messages_short_history_untouched(store, settings, project_dir) -> None:
    session, _, _ = make_session(store, settings, project_dir, [])
    store.append(session.session_id, {"role": "user", "content": "only"})
    wire = session._wire_messages()
    assert [m["role"] for m in wire] == ["system", "user"]


def test_retrieved_sources_persist_even_when_model_omits_citations(store, settings, project_dir):
    call = ToolCall("page", "read_webpage", {"url": "https://example.com/events"})
    session, _, ui = make_session(store, settings, project_dir, [[call, Done()], [TextDelta("Verified activity."), Done()], [TextDelta("Next answer."), Done()]])
    session.registry = ToolRegistry([ToolSpec("read_webpage", "fixture", {"properties": {"url": {"type": "string"}}}, lambda url: "Source: https://example.com/events\nActual page")])
    result = session.send("Find an activity")
    assert "Sources actually retrieved" in result.text
    assert "[example.com](https://example.com/events)" in result.text
    assert store.messages(session.session_id)[-1]["content"] == result.text
    assert any(event[0] == "delta" and "Sources actually retrieved" in event[1] for event in ui.events)
    assert "Sources actually retrieved" not in session.send("Unrelated follow-up").text


@pytest.mark.parametrize("output", ['[]', 'null', '42', '"text"', '{', '{"source": 42}'])
def test_unusable_weather_source_metadata_does_not_abort_task(store, settings, project_dir, output):
    call = ToolCall("weather", "get_weather", {})
    session, _, _ = make_session(store, settings, project_dir, [[call, Done()], [TextDelta("Completed."), Done()]])
    session.registry = ToolRegistry([ToolSpec("get_weather", "fixture", {}, lambda: output)])
    assert session.send("check").text == "Completed."
