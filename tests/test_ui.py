import io

from clank.ui import ConsoleUI, configure_stdio


def make_ui(**kwargs) -> tuple[ConsoleUI, io.StringIO]:
    buffer = io.StringIO()
    return ConsoleUI(stream=buffer, color=kwargs.pop("color", False)), buffer


def test_unencodable_characters_do_not_crash() -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="")
    ui = ConsoleUI(stream=stream, color=False)
    ui.assistant_delta("warning \u26a0 kept flowing")
    ui.assistant_end()
    stream.flush()
    data = raw.getvalue()
    assert b"warning" in data
    assert b"kept flowing" in data


def test_banner_has_no_ansi_when_color_disabled() -> None:
    ui, buffer = make_ui()
    ui.banner(
        version="0.1.0",
        provider="ollama",
        model="qwen3.5:4b",
        session="abc12345",
        db_path="C:/db.sqlite",
        roots=["C:/project"],
        tools=True,
    )
    output = buffer.getvalue()
    assert "Clank-Reborn v0.1.0" in output
    assert "qwen3.5:4b" in output
    assert "\x1b[" not in output


def test_ansi_codes_used_when_color_enabled() -> None:
    ui, buffer = make_ui(color=True)
    ui.error("boom")
    assert "\x1b[31m" in buffer.getvalue()


def test_tool_result_render_states() -> None:
    ui, buffer = make_ui()
    ui.tool_result("read_file", {"path": "a.txt"}, ok=True, denied=False, output="body")
    ui.tool_result("read_file", {"path": ".."}, ok=False, denied=True, output="DENIED: nope")
    ui.tool_result("read_file", {"path": "x"}, ok=False, denied=False, output="ERROR: missing")
    output = buffer.getvalue()
    assert "[tool] read_file(path='a.txt') -> ok" in output
    assert "denied" in output
    assert "failed" in output


def test_tool_result_truncates_long_output() -> None:
    ui, buffer = make_ui()
    ui.tool_result("search", {}, ok=True, denied=False, output="line\n" * 100)
    output = buffer.getvalue()
    assert output.count("\n") < 20
    assert "..." in output


def test_configure_stdio_tolerates_streams_without_reconfigure(
    monkeypatch,
) -> None:
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    configure_stdio()
