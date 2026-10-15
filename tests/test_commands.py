"""Real subprocess acceptance tests for the normal development command tool."""
import sys
import threading
from threading import Event

import pytest

from clank.errors import PermissionDenied, ToolError
from clank.tools import build_default_registry
from clank.tools.autonomous import AutonomousTools
from clank.tools.permissions import Sandbox


@pytest.fixture
def actions(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return AutonomousTools(Sandbox([project]), tmp_path / "state", [sys.executable, "-c", "print('verified')"], Event())


def test_command_is_only_available_in_autonomous_mode(actions):
    assert "run_command" not in build_default_registry(actions.sandbox).names()
    assert "run_command" in build_default_registry(actions.sandbox, actions).names()


def test_actual_build_and_targeted_test(actions):
    script = actions.root / "build.py"
    script.write_text("from pathlib import Path\nPath('result.txt').write_text('built')\nprint('build complete')\n")
    result = actions.run_command([sys.executable, "build.py"])
    assert "exit code 0" in result and "build complete" in result
    assert (actions.root / "result.txt").read_text() == "built"
    assert "target check passed" in actions.run_command([sys.executable, "-c", "from pathlib import Path; assert Path('result.txt').read_text()=='built'; print('target check passed')"])
    assert actions.test_status == "not run"
    actions.run_tests()
    assert actions.test_status == "passed"


def test_command_never_certifies_an_earlier_test_pass(actions):
    actions.run_tests()
    actions.run_command([sys.executable, "-c", "print('lint only')"])
    assert actions.test_status == "not run"


def test_command_failure_has_real_output_and_exit_code(actions):
    with pytest.raises(ToolError, match="exit code 6") as error:
        actions.run_command([sys.executable, "-c", "import sys; print('build failed'); sys.exit(6)"])
    assert "build failed" in str(error.value)


def test_command_uses_approved_working_directory(actions):
    (actions.root / "src").mkdir()
    result = actions.run_command([sys.executable, "-c", "from pathlib import Path; print(Path.cwd().name)"], "src")
    assert "src" in result
    with pytest.raises(PermissionDenied):
        actions.run_command([sys.executable, "-c", "print('no')"], "..")


@pytest.mark.parametrize("command", ["python build.py", [], [3], [""], ["python", "bad\0arg"]])
def test_invalid_command_arguments_are_actionable(actions, command):
    with pytest.raises(ToolError, match="argument array"):
        actions.run_command(command)


def test_metacharacters_are_literal_not_shell(actions):
    result = actions.run_command([sys.executable, "-c", "import sys; print(sys.argv[1])", "hello & echo INJECTED"])
    assert "hello & echo INJECTED" in result


def test_stop_prevents_command(actions):
    actions.cancelled.set()
    with pytest.raises(ToolError, match="Stopped"):
        actions.run_command([sys.executable, "-c", "print('no')"])


def test_stop_ends_running_command(actions):
    timer = threading.Timer(0.2, actions.cancelled.set)
    timer.start()
    try:
        with pytest.raises(ToolError, match="stopped"):
            actions.run_command([sys.executable, "-c", "import time; time.sleep(20)"])
        assert actions.test_status == "not run"
    finally:
        timer.cancel()


def test_command_timeout_has_actual_deadline(actions):
    with pytest.raises(ToolError, match="timed out after 1 seconds"):
        actions.run_command([sys.executable, "-c", "import time; time.sleep(20)"], timeout_seconds=1)
    assert actions.test_status == "not run"


def test_command_output_limit(actions, monkeypatch):
    monkeypatch.setattr("clank.tools.autonomous.MAX_TEST_OUTPUT", 1000)
    with pytest.raises(ToolError, match="output limit"):
        actions.run_command([sys.executable, "-c", "import time; print('x'*5000, flush=True); time.sleep(20)"])
    assert actions.test_status == "not run"


def test_undo_invalidates_a_previous_test_pass(actions):
    actions.write_file("new.txt", "new")
    actions.run_tests()
    actions.restore(actions.changes()[0]["id"])
    assert actions.test_status == "not run"


def test_failed_replacement_leaves_no_phantom_change(actions, monkeypatch):
    def refused(*args):
        raise OSError("replacement refused")

    monkeypatch.setattr("clank.tools.autonomous.os.replace", refused)
    with pytest.raises(OSError, match="refused"):
        actions.write_file("new.txt", "new")
    assert actions.changes() == []
    assert not (actions.root / "new.txt").exists()


def test_deepseek_chat_still_requires_consent(tmp_path):
    from clank import cli

    assert cli.main(["--provider", "deepseek", "--db", str(tmp_path / "history.db"), "-p", "hello"]) == 2


def test_backup_order_is_reliable_with_identical_clock_ticks(actions, monkeypatch):
    monkeypatch.setattr("clank.tools.autonomous.time.time_ns", lambda: 5)
    actions.write_file("test.txt", "first")
    actions.edit_file("test.txt", "first", "second")
    changes = actions.changes()
    assert changes[0]["sequence"] > changes[1]["sequence"]
    actions.restore(changes[0]["id"])
    actions.restore(changes[1]["id"])
    assert not (actions.root / "test.txt").exists()


@pytest.mark.parametrize("reason", ["length", "max_tokens", "content_filter", "insufficient_system_resource", "unknown_failure"])
def test_incomplete_model_response_is_not_saved_or_executed(store, settings, reason):
    from clank.errors import StreamInterrupted
    from clank.providers.base import Done, TextDelta, ToolCall
    from clank.session import open_session
    from clank.ui import NullUI

    from .conftest import FakeProvider

    provider = FakeProvider([[TextDelta("unfinished"), ToolCall("partial", "read_file", {"path": "notes.txt"}), Done(reason)]])
    session = open_session(store, settings, NullUI(), provider)
    with pytest.raises(StreamInterrupted, match="Partial output was not saved"):
        session.send("do something")
    assert [m["role"] for m in store.messages(session.session_id)] == ["user"]


def test_deepseek_local_session_listing_needs_no_cloud_consent(tmp_path, monkeypatch, capsys):
    from clank import cli

    def unexpected_provider(*args, **kwargs):
        pytest.fail("Listing local history must not create a network provider")

    monkeypatch.setattr(cli, "make_provider", unexpected_provider)
    assert cli.main(["--provider", "deepseek", "--list-sessions", "--db", str(tmp_path / "history.db")]) == 0
    assert "consent" not in capsys.readouterr().err
