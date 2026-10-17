import importlib.util
import subprocess
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
    assert all("abcdefghijklmnopqrstuvwx" not in f.excerpt for f in findings)


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


def test_scanner_includes_untracked_files_after_index_has_files(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "README.md").write_text("Public project\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True, capture_output=True)
    (tmp_path / "new.py").write_text("password = '" + "fixture" * 3 + "'\n")
    findings = security.scan_working_tree(tmp_path)
    assert any(f.location == "new.py:1" and f.rule == "assigned secret" for f in findings)
    assert all("fixturefixturefixture" not in f.excerpt for f in findings)


def test_scanner_rejects_force_added_runtime_files_without_reading_them(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("*.gguf\n*.db\n.env\n")
    for name in ("model.gguf", "history.db", ".env"):
        (tmp_path / name).write_text("Private fixture content")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-f", name], check=True, capture_output=True)
    findings = security.scan_working_tree(tmp_path)
    assert {f.location for f in findings if "private/runtime" in f.rule} == {"model.gguf", "history.db", ".env"}


def test_scanner_empty_git_repo_does_not_scan_ignored_local_files(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text(".env\n")
    (tmp_path / ".env").write_text("password='" + "fixture" * 3 + "'")
    assert security.scan_working_tree(tmp_path) == []


def test_large_candidate_is_not_silently_certified(tmp_path, monkeypatch):
    (tmp_path / "large.txt").write_text("x" * 30)
    monkeypatch.setattr(security, "MAX_FILE_BYTES", 10)
    assert any("manual review" in f.rule for f in security.scan_working_tree(tmp_path))


def test_git_history_uses_explicit_utf8_decoding(tmp_path, monkeypatch):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "unicode.txt").write_text("Local assistant \u2192 evidence \u2014 verified\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "unicode.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=Fixture", "-c", "user.email=user@example.com",
         "commit", "-m", "Unicode fixture"], check=True, capture_output=True,
    )
    original_run = subprocess.run

    def checked_run(*args, **kwargs):
        if kwargs.get("text"):
            assert kwargs.get("encoding") == "utf-8"
            assert kwargs.get("errors") == "replace"
        return original_run(*args, **kwargs)

    monkeypatch.setattr(security.subprocess, "run", checked_run)
    assert security.scan_git_history(tmp_path) == []


def test_history_patch_failure_requires_review(tmp_path, monkeypatch):
    def failed_log(args, **kwargs):
        if "rev-list" in args:
            return subprocess.CompletedProcess(args, 0, stdout="1\n")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="fixture failure")

    monkeypatch.setattr(security.subprocess, "run", failed_log)
    assert any("history scan failed" in f.rule for f in security.scan_git_history(tmp_path))
