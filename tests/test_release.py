"""Release regressions: actual HTTP transport, persistence, and bounded reads."""

import sqlite3
from pathlib import Path

import pytest
import requests

from clank.config import Settings
from clank.db import Store
from clank.errors import ClankError, ConfigError, ProviderProtocolError
from clank.providers.base import Done, ToolCall
from clank.providers.deepseek import DeepSeekProvider
from clank.providers.ollama import OllamaProvider
from clank.session import SYSTEM_PROMPT, ChatSession
from clank.tools.fs import read_file
from clank.tools.permissions import Sandbox
from clank.ui import NullUI
from clank.web import export_markdown, serve

from .conftest import FakeProvider
from .test_workspace import auth
from .test_workspace import workspace as workspace_fixture

workspace = workspace_fixture


def test_disconnect_during_tool_results_keeps_all_pairs(store, settings):
    class ClosedBrowser(NullUI):
        def tool_result(self, *args, **kwargs):
            raise BrokenPipeError()

    calls = [
        ToolCall("a", "read_file", {"path": "notes.txt"}),
        ToolCall("b", "read_file", {"path": "other.txt"}),
    ]
    session = ChatSession(store, FakeProvider([[*calls, Done()]]), None, settings, ClosedBrowser())
    with pytest.raises(BrokenPipeError):
        session.send("read two files")
    history = store.messages(session.session_id)
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "tool"]
    assert [m["tool_call_id"] for m in history[-2:]] == ["a", "b"]


def test_message_batch_is_atomic(store):
    sid = store.create_session()
    with pytest.raises(sqlite3.IntegrityError):
        store.append_many(sid, [{"role": "user", "content": "must roll back"}, {"role": None}])
    assert store.messages(sid) == []


def test_unknown_database_version_preserves_history(tmp_path):
    db = tmp_path / "newer.db"
    with Store(db) as store:
        sid = store.create_session()
        store.append(sid, {"role": "user", "content": "keep me"})
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE schema_version SET version=999")
    with pytest.raises(ClankError, match="Unsupported"):
        Store(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT content FROM messages").fetchone()[0] == "keep me"


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_nonfinite_timeout_rejected(value):
    with pytest.raises(ConfigError):
        Settings.from_env({"CLANK_REQUEST_TIMEOUT": value})


def test_read_file_does_not_load_whole_file(project_dir, monkeypatch):
    path = project_dir / "large.txt"
    path.write_bytes(b"x" * 50_000)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unbounded read_text must not be used")

    monkeypatch.setattr(Path, "read_text", forbidden)
    result = read_file(Sandbox([project_dir]), "large.txt", max_bytes=32)
    assert "truncated" in result.lower()
    assert "x" * 32 in result
    assert "x" * 33 not in result


@pytest.mark.parametrize(
    "chunk",
    [
        {"message": [], "done": True},
        {"message": {"content": 17}, "done": True},
        {"message": {"tool_calls": ["bad"]}, "done": True},
    ],
)
def test_ollama_bad_shapes_become_protocol_errors(ollama_server, chunk):
    # Non-empty wrong shapes would formerly raise AttributeError/TypeError.
    if chunk["message"] == []:
        chunk["message"] = ["bad"]
    ollama_server.enqueue([chunk])
    provider = OllamaProvider("fake", ollama_server.url)
    with pytest.raises(ProviderProtocolError):
        events = list(provider.chat_stream([]))
        assert not events or not isinstance(events[0], Done)


@pytest.mark.parametrize(
    "chunk", [[], {"choices": ["bad"]}, {"choices": [{"delta": {"tool_calls": ["bad"]}}]}]
)
def test_deepseek_bad_shapes_become_protocol_errors(deepseek_server, chunk):
    deepseek_server.enqueue([chunk])
    provider = DeepSeekProvider(api_key="sk-test", base_url=deepseek_server.url)
    with pytest.raises(ProviderProtocolError):
        list(provider.chat_stream([]))


@pytest.mark.parametrize(
    "data", [[], {"models": "bad"}, {"models": ["bad"]}, {"models": [{"name": 42}]}]
)
def test_bad_model_lists_recover_cleanly(ollama_server, data):
    ollama_server.state["tags"] = data
    with pytest.raises(ProviderProtocolError):
        OllamaProvider("", ollama_server.url).list_models()


def test_export_includes_tool_arguments_and_unicode(workspace, ollama_server):
    ollama_server.enqueue([{"message": {"content": "A café answer"}, "done": True}])
    response = requests.post(
        workspace.url + "/api/chat",
        headers=auth(workspace),
        json={"prompt": "café", "model": "fake"},
    )
    assert "event: done" in response.text
    sid = requests.get(workspace.url + "/api/sessions", headers=auth(workspace)).json()["sessions"][
        0
    ]["id"]
    route = workspace.url + "/api/export?id=" + sid
    assert requests.get(route).status_code == 401
    assert "A café answer" in requests.get(route, headers=auth(workspace)).json()["markdown"]
    result = export_markdown(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "read_file", "arguments": {"path": "source.py"}}],
            }
        ]
    )
    assert "source.py" in result and "read_file" in result


def test_concurrent_request_is_clear_and_retryable(workspace, ollama_server):
    workspace.turn_lock.acquire()
    try:
        response = requests.post(
            workspace.url + "/api/chat",
            headers=auth(workspace),
            json={"prompt": "hello", "model": "fake"},
        )
        assert response.status_code == 409
    finally:
        workspace.turn_lock.release()
    ollama_server.enqueue([{"message": {"content": "recovered"}, "done": True}])
    assert (
        "recovered"
        in requests.post(
            workspace.url + "/api/chat",
            headers=auth(workspace),
            json={"prompt": "retry", "model": "fake"},
        ).text
    )


def test_http_provider_failure_does_not_lock_workspace(workspace, ollama_server):
    ollama_server.enqueue((404, {"error": "model not found"}))
    first = requests.post(
        workspace.url + "/api/chat",
        headers=auth(workspace),
        json={"prompt": "hello", "model": "missing"},
    )
    assert "event: error" in first.text and "Pull it first" in first.text
    ollama_server.enqueue([{"message": {"content": "next answer"}, "done": True}])
    second = requests.post(
        workspace.url + "/api/chat",
        headers=auth(workspace),
        json={"prompt": "retry", "model": "fake"},
    )
    assert "event: done" in second.text


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"prompt": []},
        {"prompt": "hello", "model": []},
        {"prompt": "hello", "model": "fake", "session_id": []},
    ],
)
def test_bad_browser_input_does_not_create_history(workspace, body):
    assert (
        requests.post(workspace.url + "/api/chat", headers=auth(workspace), json=body).status_code
        == 400
    )
    assert (
        requests.get(workspace.url + "/api/sessions", headers=auth(workspace)).json()["sessions"]
        == []
    )


def test_invalid_project_launch_is_actionable(settings, tmp_path, capsys):
    settings.allow_dirs = [tmp_path / "missing"]
    assert serve(settings, 0) == 2
    assert "does not exist" in capsys.readouterr().out


def test_prompt_is_coherent():
    assert "If a tool is denied or fails" in SYSTEM_PROMPT
    assert "If a tool is Read" not in SYSTEM_PROMPT
