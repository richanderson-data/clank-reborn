"""Read-only Git evidence; no commit/push or user shell involved."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from ..errors import ToolError


def git_info(sandbox, action="status", path="."):
    target = sandbox.resolve(path)
    if not target.is_dir():
        raise ToolError("Choose a project directory")
    commands = {
        "status": ["status", "--short", "--branch"],
        "diff": ["diff", "--no-ext-diff", "--no-textconv", "--", "."],
        "staged": ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--", "."],
        "log": ["log", "-8", "--format=%h %ad %s", "--date=short"],
    }
    if not isinstance(action, str) or action not in commands:
        raise ToolError("Choose status, diff, staged, or log; Git tools never commit or push")
    prefix = ["git", "--no-pager", "-c", f"safe.directory={target.as_posix()}", "-c", "core.fsmonitor=false"]
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        root = subprocess.run([*prefix, "rev-parse", "--show-toplevel"], cwd=target, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if root.returncode:
            raise ToolError("This directory is not an accessible Git repository")
        sandbox.resolve(Path(root.stdout.strip()))
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen([*prefix, *commands[action]], cwd=target, env=env, stdout=output, stderr=subprocess.STDOUT, shell=False)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
                raise ToolError("Git inspection timed out") from None
            output.seek(0)
            text = output.read(20001).decode("utf-8", errors="replace")
        if process.returncode:
            if action == "log" and "does not have any commits" in text:
                return "No commits yet."
            raise ToolError(text[:2000].strip())
        return text[:20000] + ("\n[truncated]" if len(text) > 20000 else "") if text else "No changes."
    except OSError as exc:
        raise ToolError(f"Git inspection unavailable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("Git inspection timed out") from exc
