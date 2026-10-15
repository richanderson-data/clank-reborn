import os
from pathlib import Path

import pytest

from clank.errors import PermissionDenied, ToolError
from clank.tools import build_default_registry, fs
from clank.tools.permissions import Sandbox


@pytest.fixture
def sandbox(project_dir: Path) -> Sandbox:
    return Sandbox([project_dir])


@pytest.fixture
def registry(sandbox: Sandbox):
    return build_default_registry(sandbox)


def test_sandbox_allows_inside(project_dir: Path, sandbox: Sandbox) -> None:
    assert sandbox.resolve("notes.txt") == (project_dir / "notes.txt").resolve()
    assert sandbox.resolve(".") == project_dir.resolve()
    assert sandbox.resolve(None) == project_dir.resolve()


def test_sandbox_denies_parent_escape(sandbox: Sandbox) -> None:
    with pytest.raises(PermissionDenied, match="outside the allowed"):
        sandbox.resolve("../outside.txt")


def test_sandbox_denies_absolute_outside(tmp_path: Path, sandbox: Sandbox) -> None:
    with pytest.raises(PermissionDenied):
        sandbox.resolve(tmp_path / "elsewhere.txt")


def test_sandbox_denies_sibling_prefix(project_dir: Path, sandbox: Sandbox) -> None:
    evil = project_dir.parent / (project_dir.name + "-evil")
    evil.mkdir()
    (evil / "secret.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(PermissionDenied):
        sandbox.resolve(f"../{project_dir.name}-evil/secret.txt")


def test_sandbox_denies_symlink_escape(project_dir: Path, sandbox: Sandbox) -> None:
    outside = project_dir.parent / "outside.txt"
    link = project_dir / "sneaky.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not permitted on this machine")
    with pytest.raises(PermissionDenied):
        sandbox.resolve("sneaky.txt")


def test_list_directory_formats_entries(sandbox: Sandbox) -> None:
    output = fs.list_directory(sandbox, ".")
    lines = output.splitlines()
    assert lines[0].startswith(". (")
    assert "src/" in lines[1]
    assert any(line.startswith("notes.txt") for line in lines)
    assert any("bytes" in line for line in lines)


def test_list_directory_truncates(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    root = sandbox.roots[0]
    for index in range(5):
        (root / f"file{index}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(fs, "MAX_LIST_ENTRIES", 3)
    output = fs.list_directory(sandbox, ".")
    assert "truncated at 3 of" in output


def test_list_directory_missing(sandbox: Sandbox) -> None:
    with pytest.raises(ToolError, match="not found"):
        fs.list_directory(sandbox, "nope")


def test_list_directory_on_file(sandbox: Sandbox) -> None:
    with pytest.raises(ToolError, match="not a directory"):
        fs.list_directory(sandbox, "notes.txt")


def test_read_file(sandbox: Sandbox) -> None:
    assert fs.read_file(sandbox, "notes.txt").startswith("hello clank")


def test_read_file_truncates(sandbox: Sandbox) -> None:
    root = sandbox.roots[0]
    (root / "big.txt").write_text("a" * 5000, encoding="utf-8")
    output = fs.read_file(sandbox, "big.txt", max_bytes=100)
    assert "[truncated" in output
    assert output.startswith("a" * 100)
    assert len(output) < 400


def test_read_file_rejects_binary(sandbox: Sandbox) -> None:
    root = sandbox.roots[0]
    (root / "blob.bin").write_bytes(b"\x00\x01\x02binary")
    with pytest.raises(ToolError, match="binary"):
        fs.read_file(sandbox, "blob.bin")


def test_read_file_missing(sandbox: Sandbox) -> None:
    with pytest.raises(ToolError, match="file not found"):
        fs.read_file(sandbox, "missing.txt")


def test_read_file_on_directory(sandbox: Sandbox) -> None:
    with pytest.raises(ToolError, match="is a directory"):
        fs.read_file(sandbox, "src")


def test_read_file_denied_outside(sandbox: Sandbox) -> None:
    with pytest.raises(PermissionDenied):
        fs.read_file(sandbox, "../outside.txt")


def test_search_text_finds_matches(sandbox: Sandbox) -> None:
    output = fs.search_text(sandbox, "return", ".")
    assert "src/calc.py:2" in output.replace("\\", "/")
    assert "capped" not in output


def test_search_text_case_insensitive(sandbox: Sandbox) -> None:
    assert "notes.txt" in fs.search_text(sandbox, "HELLO", ".")


def test_search_text_single_file(sandbox: Sandbox) -> None:
    output = fs.search_text(sandbox, "clank", "notes.txt")
    assert output.startswith("1 match(es)")


def test_search_text_caps_results(sandbox: Sandbox) -> None:
    root = sandbox.roots[0]
    (root / "many.txt").write_text("".join(f"needle {i}\n" for i in range(10)), encoding="utf-8")
    output = fs.search_text(sandbox, "needle", ".", max_results=3)
    assert "capped at 3" in output


def test_search_text_no_matches(sandbox: Sandbox) -> None:
    assert "no matches" in fs.search_text(sandbox, "zzz-not-there", ".")


def test_search_text_empty_query(sandbox: Sandbox) -> None:
    with pytest.raises(ToolError, match="non-empty"):
        fs.search_text(sandbox, "   ")


def test_search_text_skips_binary_and_git(sandbox: Sandbox) -> None:
    root = sandbox.roots[0]
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret-needle", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"\x00secret-needle")
    assert "no matches" in fs.search_text(sandbox, "secret-needle", ".")


def test_search_text_missing_path(sandbox: Sandbox) -> None:
    with pytest.raises(ToolError, match="path not found"):
        fs.search_text(sandbox, "x", "no/such/dir")


def test_registry_executes_tool(registry) -> None:
    result = registry.execute("read_file", {"path": "notes.txt"})
    assert result.ok and "hello clank" in result.output


def test_registry_unknown_tool(registry) -> None:
    result = registry.execute("rm_rf", {})
    assert not result.ok
    assert "unknown tool" in result.output
    assert "read_file" in result.output


def test_registry_non_dict_arguments(registry) -> None:
    result = registry.execute("list_directory", ["."])  # type: ignore[arg-type]
    assert not result.ok
    assert "JSON object" in result.output


def test_registry_missing_required_argument(registry) -> None:
    result = registry.execute("read_file", {})
    assert not result.ok
    assert "invalid arguments" in result.output


def test_registry_filters_unknown_arguments(registry) -> None:
    result = registry.execute("list_directory", {"path": ".", "surprise": True})
    assert result.ok


def test_registry_reports_denied(registry) -> None:
    result = registry.execute("read_file", {"path": "../outside.txt"})
    assert not result.ok
    assert result.denied
    assert result.output.startswith("DENIED")


def test_registry_reports_bad_value(registry) -> None:
    result = registry.execute("read_file", {"path": "notes.txt", "max_bytes": "huge"})
    assert not result.ok
    assert result.output.startswith("ERROR")


def test_registry_specs_are_wire_shaped(registry) -> None:
    specs = registry.specs()
    names = {spec["function"]["name"] for spec in specs}
    assert names == {"list_directory", "read_file", "search_text", "git_info"}
    for spec in specs:
        assert spec["type"] == "function"
        assert spec["function"]["parameters"]["type"] == "object"
