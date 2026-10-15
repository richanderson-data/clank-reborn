import os
import subprocess
import threading
from dataclasses import replace

import pytest
import requests

from clank.cli import handle_command
from clank.providers import create_provider
from clank.providers.base import Done, TextDelta
from clank.providers.deepseek import DeepSeekProvider
from clank.providers.ollama import OllamaProvider
from clank.session import ChatSession, open_session
from clank.tools import fs
from clank.tools.permissions import Sandbox
from clank.ui import NullUI
from clank.web import WorkspaceServer

from .conftest import FakeProvider


def test_search_denies_windows_junction(project_dir, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction regression")
    outside = tmp_path / "external"
    outside.mkdir()
    (outside / "private.txt").write_text("unique-boundary-sentinel")
    junction = project_dir / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                            capture_output=True, check=False)
    if result.returncode:
        pytest.fail("Cannot create Windows junction for boundary regression")
    try:
        assert "no matches" in fs.search_text(Sandbox([project_dir]), "unique-boundary-sentinel")
    finally:
        junction.rmdir()


def test_provider_switch_round_trip(store, settings):
    settings.deepseek_api_key = "test-placeholder"
    settings.cloud_consent = True
    settings.model = "qwen3.5:4b"
    session = open_session(store, settings, NullUI(), create_provider(settings))
    handle_command("/model qwen3.5:9b", session, settings, store, NullUI())
    handle_command("/provider deepseek", session, settings, store, NullUI())
    assert session.provider.name == "deepseek"
    handle_command("/provider ollama", session, settings, store, NullUI())
    assert session.provider.name == "ollama"
    assert session.provider.model == "qwen3.5:9b"
    assert settings.model == "qwen3.5:9b"


def test_failed_switch_preserves_current_provider(store, settings):
    session = open_session(store, settings, NullUI(), create_provider(settings))
    settings.cloud_consent = True
    current = session.provider
    handle_command("/provider deepseek", session, settings, store, NullUI())
    assert session.provider is current
    assert settings.provider == "ollama"


def test_context_keeps_entire_current_tool_turn(store, settings):
    session = ChatSession(store, FakeProvider([]), None, settings, NullUI())
    store.append(session.session_id, {"role": "user", "content": "read project"})
    for _ in range(6):
        store.append(session.session_id, {"role": "assistant", "content": "read"})
        store.append(session.session_id, {"role": "tool", "content": "file"})
    settings.max_history = 3
    assert session._wire_messages()[1]["role"] == "user"
    assert len(session._wire_messages()) == 14


@pytest.fixture
def workspace(settings, ollama_server):
    settings = replace(settings, ollama_host=ollama_server.url)
    server = WorkspaceServer(settings, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.url = f"http://127.0.0.1:{server.server_port}"
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def auth(server):
    return {"Authorization": "Bearer " + server.token}


def test_workspace_security_boundaries(workspace):
    assert requests.get(workspace.url + "/api/sessions").status_code == 401
    assert requests.get(workspace.url + "/", headers={"Host": "attacker.test"}).status_code == 403
    response = requests.post(workspace.url + "/api/chat", json={"prompt": "hello"},
                             headers={**auth(workspace), "Origin": "https://attacker.test"})
    assert response.status_code == 403
    page = requests.get(workspace.url + "/")
    assert page.status_code == 200
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert workspace.token not in page.text


def test_browser_stream_and_saved_history(workspace, ollama_server):
    ollama_server.enqueue([{"message": {"content": "Browser answer"}, "done": True}])
    response = requests.post(workspace.url + "/api/chat", headers=auth(workspace),
                             json={"prompt": "hello", "model": "fake-model"})
    assert response.status_code == 200
    assert "event: delta" in response.text
    assert "Browser answer" in response.text
    assert "event: done" in response.text
    sessions = requests.get(workspace.url + "/api/sessions", headers=auth(workspace)).json()
    sid = sessions["sessions"][0]["id"]
    messages = requests.get(workspace.url + "/api/history?id=" + sid,
                            headers=auth(workspace)).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_cloud_requires_explicit_consent(workspace):
    response = requests.post(workspace.url + "/api/chat", headers=auth(workspace),
                             json={"prompt": "hello", "provider": "deepseek",
                                   "model": "deepseek-chat"})
    assert response.status_code == 400
    assert "Confirm" in response.json()["error"]


def test_workspace_rejects_unknown_session(workspace):
    response = requests.post(workspace.url + "/api/chat", headers=auth(workspace),
                             json={"prompt": "hello", "session_id": "missing",
                                   "model": "fake-model"})
    assert response.status_code == 404


def test_tool_limit_does_not_leave_unanswered_tool_calls(store, settings, project_dir):
    from clank.providers.base import ToolCall

    settings.max_tool_rounds = 1
    call = ToolCall("one", "read_file", {"path": "notes.txt"})
    session = ChatSession(store, FakeProvider([[call, Done()], [call, Done()], [TextDelta("Work is incomplete."), Done()]]),
                          None, settings, NullUI())
    assert session.send("loop").stopped_early
    history = store.messages(session.session_id)
    assert history[-2]["role"] == "tool"
    assert "not executed" in history[-2]["content"]
    assert history[-1]["content"] == "Work is incomplete."


def test_browser_disconnect_leaves_no_partial_assistant(store, settings):
    class DisconnectedUI(NullUI):
        def assistant_delta(self, text):
            raise BrokenPipeError()

    session = ChatSession(store, FakeProvider([[TextDelta("partial"), Done()]]),
                          None, settings, DisconnectedUI())
    with pytest.raises(BrokenPipeError):
        session.send("hello")
    assert [m["role"] for m in store.messages(session.session_id)] == ["user"]


def test_ollama_eof_never_persists_partial(store, settings, ollama_server):
    from clank.errors import StreamInterrupted

    ollama_server.enqueue([{"message": {"content": "unfinished"}, "done": False}])
    provider = OllamaProvider("fake-model", ollama_server.url)
    session = ChatSession(store, provider, None, settings, NullUI())
    with pytest.raises(StreamInterrupted, match="completion marker"):
        session.send("hello")
    assert [m["role"] for m in store.messages(session.session_id)] == ["user"]


def test_deepseek_eof_never_persists_partial(store, settings, deepseek_server):
    from clank.errors import StreamInterrupted

    deepseek_server.enqueue([{"choices": [{"delta": {"content": "unfinished"}}]}])
    provider = DeepSeekProvider(api_key="sk-test", base_url=deepseek_server.url)
    session = ChatSession(store, provider, None, settings, NullUI())
    with pytest.raises(StreamInterrupted, match="completion marker"):
        session.send("hello")
    assert [m["role"] for m in store.messages(session.session_id)] == ["user"]
