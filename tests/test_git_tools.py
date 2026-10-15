"""Git evidence from disposable repositories; no user history is changed."""
import subprocess

import pytest

from clank.errors import PermissionDenied, ToolError
from clank.tools.git import git_info
from clank.tools.permissions import Sandbox


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (root / "code.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(root), "add", "code.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=user@example.com", "commit", "-m", "fixture"], check=True, capture_output=True)
    return root, Sandbox([root])


def test_real_status_diff_log_do_not_change_worktree(repo):
    root, sandbox = repo
    (root / "code.txt").write_text("changed\n")
    before = (root / ".git" / "index").read_bytes()
    assert "code.txt" in git_info(sandbox)
    assert "+changed" in git_info(sandbox, "diff")
    assert "fixture" in git_info(sandbox, "log")
    assert (root / ".git" / "index").read_bytes() == before
    assert (root / "code.txt").read_text() == "changed\n"


def test_staged_diff(repo):
    root, sandbox = repo
    (root / "code.txt").write_text("staged\n")
    subprocess.run(["git", "-C", str(root), "add", "code.txt"], check=True, capture_output=True)
    assert "+staged" in git_info(sandbox, "staged")


def test_git_tool_never_writes(repo):
    _, sandbox = repo
    with pytest.raises(ToolError, match="never commit"):
        git_info(sandbox, "push")


def test_git_parent_repo_cannot_escape_approved_subdirectory(repo):
    root, _ = repo
    (root / "child").mkdir()
    with pytest.raises(PermissionDenied):
        git_info(Sandbox([root / "child"]))


def test_unborn_repo_and_non_repo_are_actionable(tmp_path):
    with pytest.raises(ToolError, match="Git repository"):
        git_info(Sandbox([tmp_path]))
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    assert git_info(Sandbox([tmp_path]), "log") == "No commits yet."
