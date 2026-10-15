import importlib.util
import sys
from pathlib import Path


def load_script(name: str, relative: str):
    path = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


security = load_script("security_check", "scripts/security_check.py")
svg_tool = load_script("make_transcript_svg", "scripts/make_transcript_svg.py")


def test_scanner_flags_real_looking_key() -> None:
    text = "DEEPSEEK_API_KEY = '" + "sk-" + "abcdefghijklmnopqrstuvwx" + "'\n"
    findings = security.scan_text(text, "x.py")
    assert any("API key" in f.rule for f in findings)


def test_scanner_flags_assigned_secret() -> None:
    text = "password = '" + "hunter2" + "hunter2" + "'\n"
    findings = security.scan_text(text, "x.py")
    assert any(f.rule == "assigned secret" for f in findings)


def test_scanner_ignores_placeholders() -> None:
    text = "\n".join(
        ["api_key = 'changeme'", "CLANK_DEEPSEEK_API_KEY=", "user@example.com"]
    )
    assert security.scan_text(text, "x.py") == []


def test_scanner_allows_example_emails() -> None:
    assert security.scan_text("contact user@example.com", "x.py") == []


def test_scanner_flags_user_path_and_email() -> None:
    path_line = "history: C:" + "\\" + "Users" + "\\richa\\clank.db"
    email = "someone" + "@" + "realdomain.dev"
    findings = security.scan_text("\n".join([path_line, email]), "x.txt")
    rules = {finding.rule for finding in findings}
    assert "absolute Windows user path" in rules
    assert "email address" in rules


def test_scanner_svg_text_is_clean() -> None:
    sample = "you> hello\n[tool] read_file(path='a') -> ok\nplain answer\n"
    assert security.scan_text(sample, "t.txt") == []


def test_svg_builds_and_escapes() -> None:
    svg = svg_tool.build_svg("you> hi <b>\nerror: boom", "title", "caption")
    assert svg.startswith("<svg")
    assert "hi &lt;b&gt;" in svg
    assert "boom" in svg


def test_svg_strips_ansi_and_wraps() -> None:
    line = "\x1b[31m" + "x" * 300 + "\x1b[0m"
    svg = svg_tool.build_svg(line, "t", "c", max_chars=100)
    assert "\x1b" not in svg
    assert svg.count("<text") > 3


def test_svg_classifies_prompts_and_tools() -> None:
    fill_prompt, _ = svg_tool.classify("you> hi")
    fill_ok, _ = svg_tool.classify("[tool] read_file() -> ok")
    fill_denied, _ = svg_tool.classify("[tool] read_file() -> denied")
    assert fill_prompt == svg_tool.CYAN
    assert fill_ok == svg_tool.GREEN
    assert fill_denied == svg_tool.YELLOW
