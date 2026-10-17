"""Release blockers reproduced with disposable files, subprocesses, and HTTP."""
import os

import pytest
import requests

from clank.command_line import split_command
from clank.errors import PermissionDenied
from clank.providers.base import Done, TextDelta
from clank.session import open_session
from clank.tools import ToolRegistry, ToolSpec, fs
from clank.tools.autonomous import AutonomousTools
from clank.tools.permissions import Sandbox
from clank.ui import NullUI

from .conftest import FakeProvider
from .test_workspace import auth
from .test_workspace import workspace as workspace_fixture

workspace = workspace_fixture


@pytest.mark.parametrize("name", [".env", ".env.local", "private.pem", "id_rsa", ".npmrc", ".pypirc", ".git/config", ".clank-backups/backup.json"])
def test_file_tools_never_read_or_search_credential_files(project_dir, name):
    file = project_dir / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("FAKE_CREDENTIAL_SENTINEL")
    sandbox = Sandbox([project_dir])
    with pytest.raises(PermissionDenied):
        fs.read_file(sandbox, name)
    assert "no matches" in fs.search_text(sandbox, "FAKE_CREDENTIAL_SENTINEL")
    assert name.split("/")[0] not in fs.list_directory(sandbox)


def test_env_examples_are_useful_and_paths_are_relative(project_dir):
    (project_dir / ".env.example").write_text("KEY=example")
    sandbox = Sandbox([project_dir])
    assert fs.read_file(sandbox, ".env.example") == "KEY=example"
    assert str(project_dir) not in fs.list_directory(sandbox)
    assert str(project_dir) not in fs.search_text(sandbox, "example")


def test_unexpected_tool_exception_returns_an_error_not_a_crash():
    def broken():
        raise ValueError("private internal detail")

    registry = ToolRegistry([ToolSpec("broken", "fixture", {"type": "object"}, broken)])
    result = registry.execute("broken", {})
    assert not result.ok and "ValueError" in result.output
    assert "private internal detail" not in result.output


def test_undo_replacement_failure_preserves_original_and_backup(project_dir, tmp_path, monkeypatch):
    actions = AutonomousTools(Sandbox([project_dir]), tmp_path, [])
    file = project_dir / "text.txt"
    file.write_text("original")
    actions.edit_file("text.txt", "original", "changed")
    backup = actions.changes()[0]

    def refused(*args):
        raise OSError("refused")

    monkeypatch.setattr("clank.tools.autonomous.os.replace", refused)
    with pytest.raises(OSError):
        actions.restore(backup["id"])
    assert file.read_text() == "changed"
    assert not actions.changes()[0]["restored"]


@pytest.mark.skipif(os.name != "nt", reason="Windows wrapper regression")
def test_windows_path_wrappers_are_resolved(project_dir, tmp_path, monkeypatch):
    wrapper = tmp_path / "fake-tool.cmd"
    wrapper.write_text('@echo off\r\necho wrapper:%~1\r\n')
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    actions = AutonomousTools(Sandbox([project_dir]), tmp_path / "state", [])
    assert "wrapper:normal argument" in actions.run_command(["fake-tool", "normal argument"])


@pytest.mark.skipif(os.name != "nt", reason="Windows parsing regression")
def test_windows_fixed_command_keeps_paths_and_quotes():
    assert split_command(r'"C:\Program Files\Python\python.exe" "C:\work\run tests.py"') == [r"C:\Program Files\Python\python.exe", r"C:\work\run tests.py"]
    assert split_command(r'C:\Tools\pytest.exe -q') == [r"C:\Tools\pytest.exe", "-q"]


def test_damaged_history_can_be_read_and_continued_without_mutating_database(store, settings):
    sid = store.create_session()
    store.append_many(sid, [{"role": "user", "content": "keep me"}, {"role": "assistant", "content": "damaged", "tool_calls": [{"id": "bad", "name": "read_file", "arguments": {}}]}, {"role": "tool", "tool_call_id": "bad", "content": "orphan"}])
    store._conn.execute("UPDATE messages SET tool_calls='{' WHERE role='assistant'")
    store._conn.commit()
    history = store.messages(sid)
    assert history[0]["content"] == "keep me"
    assert "History recovery" in history[1]["content"]
    assert all(message["role"] != "tool" for message in history)
    assert store._conn.execute("SELECT tool_calls FROM messages WHERE role='assistant'").fetchone()[0] == "{"
    session = open_session(store, settings, NullUI(), FakeProvider([[TextDelta("continued"), Done()]]), session_id=sid)
    assert session.send("continue").text == "continued"


def test_unexpected_stream_failure_emits_actionable_error(workspace, monkeypatch):
    def broken(*args):
        raise ValueError("do not expose internal data")

    monkeypatch.setattr("clank.session.ChatSession.send", broken)
    response = requests.post(workspace.url + "/api/chat", headers=auth(workspace), json={"prompt": "hello", "model": "fake"}, timeout=5)
    assert "event: error" in response.text and "Unexpected task failure" in response.text
    assert "do not expose internal data" not in response.text


def test_status_exposes_ollama_configuration_without_credentials(workspace):
    workspace.settings.model = "configured-local-model"
    response = requests.get(workspace.url + "/api/status", headers=auth(workspace), timeout=5)
    assert response.json()["model"] == "configured-local-model"
    assert "provider" not in response.json()


@pytest.mark.parametrize("web_enabled", [True, False])
def test_web_toggle_does_not_change_local_file_tools(workspace, monkeypatch, web_enabled):
    provider = FakeProvider([[TextDelta("local response"), Done()]])
    monkeypatch.setattr("clank.web.create_provider", lambda settings: provider)
    response = requests.post(workspace.url + "/api/chat", headers=auth(workspace),
        json={"prompt": "public question", "model": "fake", "web_enabled": web_enabled}, timeout=5)
    assert "event: done" in response.text
    tools = provider.calls[0]["tools"] or []
    names = {tool["function"]["name"] for tool in tools}
    expected = {"list_directory", "read_file", "search_text", "git_info"}
    if web_enabled:
        expected |= {"web_search", "read_webpage", "get_weather"}
    assert names == expected
