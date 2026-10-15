import pytest

from clank import cli
from clank.db import Store
from clank.errors import ProviderConnectionError
from clank.providers.base import Done, TextDelta

from .conftest import FakeProvider


def run_cli(monkeypatch, provider: FakeProvider, argv: list[str], tmp_path, project_dir):
    monkeypatch.setattr(
        "clank.cli.make_provider",
        lambda settings, allow_missing_model=False: provider,
    )
    return cli.main(
        [
            "--db",
            str(tmp_path / "cli.db"),
            "--allow-dir",
            str(project_dir),
            "--model",
            "fake-model",
            *argv,
        ]
    )


def test_oneshot_prints_answer_and_persists(
    monkeypatch, capsys, tmp_path, project_dir
) -> None:
    provider = FakeProvider([[TextDelta("Hello "), TextDelta("from fake"), Done()]])
    rc = run_cli(monkeypatch, provider, ["-p", "hi"], tmp_path, project_dir)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Hello from fake" in out
    with Store(tmp_path / "cli.db") as store:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["message_count"] == 2


def test_oneshot_provider_error_returns_1(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider([ProviderConnectionError("ollama is down")])
    rc = run_cli(monkeypatch, provider, ["-p", "hi"], tmp_path, project_dir)
    out = capsys.readouterr().out
    assert rc == 1
    assert "ollama is down" in out


def test_oneshot_interrupted_returns_1(monkeypatch, capsys, tmp_path, project_dir) -> None:
    from clank.errors import StreamInterrupted

    def broken():
        yield TextDelta("partial")
        raise StreamInterrupted("connection lost")

    provider = FakeProvider([broken()])
    rc = run_cli(monkeypatch, provider, ["-p", "hi"], tmp_path, project_dir)
    out = capsys.readouterr().out
    assert rc == 1
    assert "connection lost" in out


def test_list_sessions_empty(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider([])
    rc = run_cli(monkeypatch, provider, ["--list-sessions"], tmp_path, project_dir)
    assert rc == 0
    assert "no sessions" in capsys.readouterr().out


def test_list_models(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider([])
    rc = run_cli(monkeypatch, provider, ["--list-models"], tmp_path, project_dir)
    assert rc == 0
    assert "fake-model" in capsys.readouterr().out


def test_version_exits(capsys) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0
    assert "clank-reborn" in capsys.readouterr().out


def test_unknown_session_returns_2(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider([])
    rc = run_cli(monkeypatch, provider, ["--session", "deadbeef"], tmp_path, project_dir)
    out = capsys.readouterr().out
    assert rc == 2
    assert "no stored session" in out


def feed_input(monkeypatch, lines: list[str]) -> None:
    iterator = iter(lines)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(iterator))


def test_repl_help_tools_exit(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider([])
    feed_input(monkeypatch, ["/help", "/tools", "", "/exit"])
    rc = run_cli(monkeypatch, provider, [], tmp_path, project_dir)
    out = capsys.readouterr().out
    assert rc == 0
    assert "commands:" in out
    assert "available tools:" in out
    assert "bye" in out


def test_repl_chat_then_exit(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider([[TextDelta("sure thing"), Done()]])
    feed_input(monkeypatch, ["hello clank", "/exit"])
    rc = run_cli(monkeypatch, provider, [], tmp_path, project_dir)
    out = capsys.readouterr().out
    assert rc == 0
    assert "sure thing" in out
    with Store(tmp_path / "cli.db") as store:
        sessions = store.list_sessions()
        assert sessions[0]["message_count"] == 2


def test_repl_new_session(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider([[TextDelta("one"), Done()]])
    feed_input(monkeypatch, ["/new", "/exit"])
    rc = run_cli(monkeypatch, provider, [], tmp_path, project_dir)
    assert rc == 0
    with Store(tmp_path / "cli.db") as store:
        assert len(store.list_sessions()) == 2


def test_repl_unknown_command(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider([])
    feed_input(monkeypatch, ["/frobnicate", "/exit"])
    rc = run_cli(monkeypatch, provider, [], tmp_path, project_dir)
    assert rc == 0
    assert "unknown command" in capsys.readouterr().out


def test_repl_history_and_sessions(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider([[TextDelta("stored reply"), Done()]])
    feed_input(monkeypatch, ["remember this", "/history", "/sessions", "/exit"])
    rc = run_cli(monkeypatch, provider, [], tmp_path, project_dir)
    out = capsys.readouterr().out
    assert rc == 0
    assert "remember this" in out
    assert "sessions in" in out


def test_error_in_repl_keeps_running(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider(
        [ProviderConnectionError("ollama is down"), [TextDelta("recovered"), Done()]]
    )
    feed_input(monkeypatch, ["first", "second", "/exit"])
    rc = run_cli(monkeypatch, provider, [], tmp_path, project_dir)
    out = capsys.readouterr().out
    assert rc == 0
    assert "ollama is down" in out
    assert "recovered" in out


def test_continue_resumes_latest(monkeypatch, capsys, tmp_path, project_dir) -> None:
    provider = FakeProvider([[TextDelta("first"), Done()], [TextDelta("second"), Done()]])
    run_cli(monkeypatch, provider, ["-p", "one"], tmp_path, project_dir)
    rc = run_cli(monkeypatch, provider, ["-p", "two", "--continue"], tmp_path, project_dir)
    assert rc == 0
    with Store(tmp_path / "cli.db") as store:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["message_count"] == 4
