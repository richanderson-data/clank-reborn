"""Opt-in autonomy, real subprocesses, recoverable edits, and browser gates."""

import os
import subprocess
import sys
import threading
import time
from dataclasses import replace

import pytest
import requests

from clank.errors import ConfigError, PermissionDenied, StreamInterrupted, ToolError
from clank.providers.base import Done, TextDelta, ToolCall
from clank.session import open_session
from clank.tools.autonomous import AutonomousTools
from clank.tools.permissions import Sandbox
from clank.ui import NullUI

from .conftest import FakeProvider
from .test_workspace import auth
from .test_workspace import workspace as workspace_fixture

workspace = workspace_fixture


@pytest.fixture
def actions(project_dir, tmp_path):
    return AutonomousTools(
        Sandbox([project_dir]),
        tmp_path / "state",
        [sys.executable, "-c", "print('real command passed')"],
    )


def test_exact_edit_and_undo_preserve_bytes(actions, project_dir):
    file = project_dir / "calc.py"
    original = b"def add(a, b):\r\n    return a - b\r\n"
    file.write_bytes(original)
    assert "recoverable backup" in actions.edit_file("calc.py", "return a - b", "return a + b")
    assert b"return a + b\r\n" in file.read_bytes()
    change = actions.changes()[0]
    assert "before" not in change and "after" not in change
    actions.restore(change["id"])
    assert file.read_bytes() == original
    assert actions.changes()[0]["restored"]


def test_undo_refuses_to_clobber_later_work(actions, project_dir):
    actions.write_file("new.txt", "generated")
    (project_dir / "new.txt").write_text("later user work")
    with pytest.raises(ToolError, match="later work"):
        actions.restore(actions.changes()[0]["id"])
    assert (project_dir / "new.txt").read_text() == "later user work"


def test_new_file_undo_retains_recoverable_record(actions, project_dir):
    actions.write_file("src/new.py", "print('hello')\n")
    backup = actions.changes()[0]
    actions.restore(backup["id"])
    assert not (project_dir / "src/new.py").exists()
    assert (actions.backups / (backup["id"] + ".json")).exists()


def test_existing_files_cannot_be_overwritten(actions, project_dir):
    original = (project_dir / "notes.txt").read_bytes()
    with pytest.raises(ToolError, match="already exists"):
        actions.write_file("notes.txt", "replacement")
    assert (project_dir / "notes.txt").read_bytes() == original


@pytest.mark.parametrize("path", ["../outside.txt", ".git/config"])
def test_edit_boundaries(actions, path):
    with pytest.raises(PermissionDenied):
        actions.write_file(path, "no")


def test_backup_directory_is_not_editable(actions):
    with pytest.raises(PermissionDenied):
        actions.write_file(str(actions.backups / "bad.json"), "no")


def test_ambiguous_or_oversized_edits_do_not_change_files(actions, project_dir):
    file = project_dir / "twice.txt"
    file.write_text("same same")
    with pytest.raises(ToolError, match="exactly once"):
        actions.edit_file("twice.txt", "same", "different")
    with pytest.raises(ToolError, match="200000"):
        actions.edit_file("twice.txt", "same same", "x" * 200001)
    assert file.read_text() == "same same"


def test_tests_execute_real_command(actions):
    assert "real command passed" in actions.run_tests()
    assert actions.test_status == "passed"


def test_failed_tests_report_exit_and_output(actions):
    actions.command = [sys.executable, "-c", "import sys; print('failure evidence'); sys.exit(7)"]
    with pytest.raises(ToolError, match="exit code 7") as exc:
        actions.run_tests()
    assert "failure evidence" in str(exc.value)
    assert actions.test_status == "failed"


def test_missing_test_command_is_not_a_pass(actions):
    actions.command = ["clank-nonexistent-test-executable"]
    with pytest.raises(ToolError, match="Could not start"):
        actions.run_tests()
    assert actions.test_status == "could not start"


def test_test_timeout_is_bounded(actions, monkeypatch):
    monkeypatch.setattr("clank.tools.autonomous.TEST_TIMEOUT", 0.1)
    actions.command = [sys.executable, "-c", "import time; time.sleep(20)"]
    started = time.monotonic()
    with pytest.raises(ToolError, match="timed out"):
        actions.run_tests()
    assert time.monotonic() - started < 5


def test_stop_ends_running_test_process(actions):
    actions.command = [sys.executable, "-c", "import time; time.sleep(20)"]
    timer = threading.Timer(0.2, actions.cancelled.set)
    timer.start()
    try:
        with pytest.raises(ToolError, match="stopped"):
            actions.run_tests()
    finally:
        timer.cancel()
    assert actions.test_status == "stopped"


def test_test_output_is_bounded(actions, monkeypatch):
    monkeypatch.setattr("clank.tools.autonomous.MAX_TEST_OUTPUT", 10000)
    actions.command = [
        sys.executable,
        "-c",
        "import time; print('x'*50000, flush=True); time.sleep(20)",
    ]
    with pytest.raises(ToolError, match="output limit exceeded") as exc:
        actions.run_tests()
    assert len(str(exc.value)) < 21000


def test_stop_prevents_next_edit(actions, project_dir):
    actions.cancelled.set()
    with pytest.raises(ToolError, match="Stopped"):
        actions.write_file("never.txt", "no")
    assert not (project_dir / "never.txt").exists()


def test_default_mode_exposes_no_write_or_test_tools(store, settings):
    session = open_session(store, settings, NullUI(), FakeProvider([]))
    assert {"list_directory", "read_file", "search_text", "git_info", "web_search", "read_webpage", "get_weather"} == set(session.registry.names())
    assert not session.registry.execute("write_file", {"path": "x", "content": "y"}).ok


def test_edit_batch_always_runs_tests_and_empty_final_gets_factual_summary(
    store, settings, project_dir
):
    (project_dir / "calc.py").write_text("def add(a,b): return a-b\n")
    settings = replace(
        settings,
        autonomous=True,
        test_command=[
            sys.executable,
            "-c",
            "import calc; assert calc.add(2,3)==5; print('verified')",
        ],
    )
    provider = FakeProvider(
        [
            [
                ToolCall(
                    "edit",
                    "edit_file",
                    {"path": "calc.py", "old_text": "return a-b", "new_text": "return a+b"},
                ),
                Done(),
            ],
            [Done()],
        ]
    )
    session = open_session(store, settings, NullUI(), provider)
    result = session.send("fix addition")
    assert result.test_status == "passed"
    assert "Task summary from actual tool results" in result.text
    assert any(m.get("name") == "run_tests" for m in store.messages(session.session_id))


def test_empty_model_reply_has_one_retry_and_no_false_success(store, settings):
    from clank.errors import ProviderProtocolError

    session = open_session(store, settings, NullUI(), FakeProvider([[Done()], [Done()]]))
    with pytest.raises(ProviderProtocolError, match="twice"):
        session.send("hello")
    assert [m["role"] for m in store.messages(session.session_id)] == ["user"]


def test_failed_test_edit_retest_loop_is_real(store, settings, project_dir):
    file = project_dir / "calc.py"
    file.write_text("def add(a, b):\n    return a - b\n")
    settings = replace(
        settings,
        autonomous=True,
        test_command=[
            sys.executable,
            "-c",
            "import calc; assert calc.add(2,3)==5; print('actual add assertion passed')",
        ],
    )
    provider = FakeProvider(
        [
            [ToolCall("test1", "run_tests", {}), Done()],
            [ToolCall("read", "read_file", {"path": "calc.py"}), Done()],
            [
                ToolCall(
                    "edit",
                    "edit_file",
                    {"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"},
                ),
                Done(),
            ],
            [ToolCall("test2", "run_tests", {}), Done()],
            [TextDelta("Fixed and verified"), Done()],
        ]
    )
    session = open_session(store, settings, NullUI(), provider)
    result = session.send("Fix add and run tests")
    history = store.messages(session.session_id)
    assert any("Tests failed" in m["content"] for m in history)
    assert any("actual add assertion passed" in m["content"] for m in history)
    assert result.changed_files == ("calc.py",) and result.test_status == "passed"
    assert "return a + b" in file.read_text()


def test_cancel_stops_provider_before_any_tool_executes(store, settings, project_dir):
    cancelled = threading.Event()

    class CancellingProvider(FakeProvider):
        def chat_stream(self, messages, tools=None):
            cancelled.set()
            yield ToolCall("write", "write_file", {"path": "never.txt", "content": "no"})

    settings = replace(settings, autonomous=True)
    session = open_session(store, settings, NullUI(), CancellingProvider([]), cancelled=cancelled)
    with pytest.raises(StreamInterrupted, match="Stopped"):
        session.send("do work")
    assert not (project_dir / "never.txt").exists()


def test_autonomy_requires_one_project_and_tools(settings, tmp_path):
    with pytest.raises(ConfigError):
        replace(settings, autonomous=True, allow_dirs=[tmp_path, tmp_path / "other"])
    with pytest.raises(ConfigError):
        replace(settings, autonomous=True, tools_enabled=False)


def test_browser_cannot_enable_autonomy_without_launch_flag(workspace):
    response = requests.post(
        workspace.url + "/api/chat",
        headers=auth(workspace),
        json={"prompt": "edit", "model": "fake", "autonomous": True},
    )
    assert response.status_code == 400
    assert (
        requests.post(
            workspace.url + "/api/restore", headers=auth(workspace), json={"id": "0" * 32}
        ).status_code
        == 400
    )
    assert requests.post(workspace.url + "/api/cancel", json={}).status_code == 401


def test_browser_opt_in_is_per_turn(workspace, ollama_server):
    workspace.settings.autonomous = True
    for enabled, count in [(False, 7), (True, 11)]:
        ollama_server.enqueue([{"message": {"content": "okay"}, "done": True}])
        response = requests.post(
            workspace.url + "/api/chat",
            headers=auth(workspace),
            json={"prompt": "hello", "model": "fake", "autonomous": enabled},
        )
        assert "event: done" in response.text
        assert len(ollama_server.state["requests"][-1]["tools"]) == count


def test_browser_undo_is_authenticated_and_refuses_during_work(workspace, project_dir):
    workspace.settings.autonomous = True
    actions = AutonomousTools(Sandbox([project_dir]), workspace.settings.db_path.parent, [])
    actions.write_file("generated.txt", "new")
    backup_id = actions.changes()[0]["id"]
    assert requests.get(workspace.url + "/api/changes").status_code == 401
    workspace.turn_lock.acquire()
    try:
        assert (
            requests.post(
                workspace.url + "/api/restore", headers=auth(workspace), json={"id": backup_id}
            ).status_code
            == 409
        )
    finally:
        workspace.turn_lock.release()
    assert (
        requests.post(
            workspace.url + "/api/restore", headers=auth(workspace), json={"id": backup_id}
        ).status_code
        == 200
    )
    assert not (project_dir / "generated.txt").exists()


def test_autonomous_write_denies_windows_junction(actions, project_dir, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction regression")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = project_dir / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False
    )
    assert result.returncode == 0
    try:
        with pytest.raises(PermissionDenied):
            actions.write_file("junction/private.txt", "no")
        assert not (outside / "private.txt").exists()
    finally:
        link.rmdir()
