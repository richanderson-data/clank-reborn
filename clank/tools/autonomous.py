"""Small opt-in edit/test toolset. This is not an operating-system sandbox."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from ..errors import PermissionDenied, ToolError
from .permissions import Sandbox

MAX_EDIT_BYTES = 200_000
MAX_TEST_OUTPUT = 1_000_000
TEST_TIMEOUT = 90


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AutonomousTools:
    def __init__(
        self, sandbox: Sandbox, state_dir: Path, command: list[str], cancelled: Event | None = None
    ):
        if len(sandbox.roots) != 1:
            raise ToolError("Autonomous mode needs exactly one project")
        self.sandbox = sandbox
        self.root = sandbox.roots[0]
        self.backups = state_dir / ".clank-backups" / digest(str(self.root).encode())[:16]
        self.command = command
        self.cancelled = cancelled or Event()
        self.changed_files: list[str] = []
        self.test_status = "not run"
        self.sequence = 0
        if self.backups.exists():
            for file in self.backups.glob("*.json"):
                try:
                    self.sequence = max(self.sequence, int(json.loads(file.read_text(encoding="utf-8")).get("sequence", 0)))
                except (OSError, ValueError, TypeError):
                    continue

    def target(self, path: str) -> Path:
        target = self.sandbox.resolve(path)
        relative = target.relative_to(self.root)
        if ".git" in relative.parts or target.is_relative_to(self.backups.resolve()):
            raise PermissionDenied("Git metadata and Clank backups are not project edit targets")
        if target == self.root or target.is_dir():
            raise ToolError("Choose a text file, not a directory")
        return target

    def _commit(self, target: Path, before: bytes | None, after: bytes) -> str:
        if self.cancelled.is_set():
            raise ToolError("Stopped; no file was changed")
        if len(after) > MAX_EDIT_BYTES:
            raise ToolError("Edited files are limited to 200000 UTF-8 bytes")
        if before == after:
            return "No change needed"
        self.backups.mkdir(parents=True, exist_ok=True)
        backup_id = uuid.uuid4().hex
        self.sequence = max(time.time_ns(), self.sequence + 1)
        relative = str(target.relative_to(self.root))
        record = {
            "id": backup_id,
            "sequence": self.sequence,
            "path": relative,
            "root": str(self.root),
            "created_at": datetime.now(UTC).isoformat(timespec="microseconds"),
            "before": None if before is None else base64.b64encode(before).decode(),
            "after": base64.b64encode(after).decode(),
            "after_hash": digest(after),
            "restored": False,
        }
        # Save recoverable content before replacing the project file.
        record_path = self.backups / (backup_id + ".json")
        record_path.write_text(json.dumps(record), encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        committed = False
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(after)
            if self.sandbox.resolve(target) != target:
                raise PermissionDenied("The edit target changed during this operation")
            current = target.read_bytes() if target.exists() else None
            if current != before:
                raise ToolError("File changed since it was read; reread it before editing")
            if before is not None:
                os.chmod(temporary, target.stat().st_mode)
            os.replace(temporary, target)
            committed = True
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
            if not committed:
                record_path.unlink(missing_ok=True)
        self.changed_files.append(relative)
        self._invalidate_bytecode(target)
        return f"Changed {relative}; recoverable backup {backup_id}"

    def _invalidate_bytecode(self, target: Path):
        # Same-size edits within one second can otherwise reuse old Python bytecode.
        # Only generated cache files for this exact source file are removed.
        cache = target.parent / "__pycache__"
        if target.suffix == ".py" and cache.is_dir():
            for candidate in cache.iterdir():
                if candidate.name.startswith(target.stem + ".") and candidate.suffix == ".pyc":
                    try:
                        self.sandbox.resolve(candidate)
                        candidate.unlink()
                    except OSError:
                        pass
                    except PermissionDenied:
                        pass

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
            raise ToolError("Provide non-empty old_text and string new_text")
        target = self.target(path)
        if not target.is_file() or target.stat().st_size > MAX_EDIT_BYTES:
            raise ToolError("Existing text file required, at most 200000 bytes")
        before = target.read_bytes()
        try:
            text = before.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("Only UTF-8 text files can be edited") from exc
        if "\0" in text:
            raise ToolError("Binary files cannot be edited")
        if "\r\n" in text:
            old_text = old_text.replace("\r\n", "\n").replace("\n", "\r\n")
            new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
        if text.count(old_text) != 1:
            raise ToolError(
                "old_text must match exactly once; read the file and use unique context"
            )
        return self._commit(target, before, text.replace(old_text, new_text, 1).encode("utf-8"))

    def write_file(self, path: str, content: str) -> str:
        target = self.target(path)
        if target.exists():
            raise ToolError("File already exists; use edit_file with exact original text")
        if not isinstance(content, str) or "\0" in content:
            raise ToolError("New files must contain UTF-8 text")
        return self._commit(target, None, content.encode("utf-8"))

    def changes(self) -> list[dict]:
        if not self.backups.exists():
            return []
        records = []
        for file in self.backups.glob("*.json"):
            try:
                record = json.loads(file.read_text(encoding="utf-8"))
                if record["root"] == str(self.root):
                    records.append(
                        {**{key: record[key] for key in ("id", "path", "created_at", "restored")}, "sequence": record.get("sequence", 0)}
                    )
            except (OSError, ValueError, KeyError):
                continue
        return sorted(records, key=lambda record: (record["sequence"], record["created_at"]), reverse=True)[:50]

    def restore(self, backup_id: str) -> str:
        if (
            not isinstance(backup_id, str)
            or len(backup_id) != 32
            or any(c not in "0123456789abcdef" for c in backup_id)
        ):
            raise ToolError("Invalid backup ID")
        file = self.backups / (backup_id + ".json")
        try:
            record = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolError("Backup not found or unreadable") from exc
        if record["root"] != str(self.root) or record["restored"]:
            raise ToolError("Backup is for another project or already restored")
        target = self.target(record["path"])
        if not target.is_file() or digest(target.read_bytes()) != record["after_hash"]:
            raise ToolError("File changed after this backup; undo refused to preserve later work")
        if record["before"] is None:
            target.unlink()  # Its generated contents remain recoverable in the backup record.
        else:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(base64.b64decode(record["before"], validate=True))
                os.chmod(temporary, target.stat().st_mode)
                if self.sandbox.resolve(target) != target or digest(target.read_bytes()) != record["after_hash"]:
                    raise ToolError("File changed during undo; later work was preserved")
                os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        self._invalidate_bytecode(target)
        self.test_status = "not run"
        record["restored"] = True
        file.write_text(json.dumps(record), encoding="utf-8")
        return f"Restored {record['path']}"

    def run_tests(self) -> str:
        if not self.command:
            raise ToolError("No fixed test command was configured")
        return self._run(self.command, self.root, tests=True)

    def run_command(self, command: list[str], cwd: str = ".", timeout_seconds: int = 300) -> str:
        """Run explicit argv in the selected project, with the user's OS permissions."""
        if (
            not isinstance(command, list)
            or not 1 <= len(command) <= 100
            or any(not isinstance(arg, str) or "\0" in arg for arg in command)
            or not command[0].strip()
            or sum(len(arg) for arg in command) > 16000
        ):
            raise ToolError("command must be a non-empty argument array, e.g. ['npm', 'run', 'build']")
        directory = self.sandbox.resolve(cwd)
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
            raise ToolError("timeout_seconds must be an integer from 1 to 600")
        if not directory.is_dir():
            raise ToolError("Command working directory must be a project directory")
        # Commands may change files. A prior green test cannot certify their effects.
        self.test_status = "not run"
        return self._run(command, directory, tests=False, timeout=timeout_seconds)

    def _run(self, command, directory, *, tests, timeout=None):
        timeout = TEST_TIMEOUT if timeout is None else timeout
        command = list(command)
        if os.name == "nt":
            # CreateProcess does not search PATHEXT for bare npm/npx names.
            # Let Python launch the resolved wrapper; do not concatenate a shell string.
            command[0] = shutil.which(command[0]) or command[0]
        if self.cancelled.is_set():
            raise ToolError("Stopped; command was not started")
        if tests:
            self.test_status = "running"
        reason = ""
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=directory,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    shell=False,
                )
            except OSError as exc:
                if tests:
                    self.test_status = "could not start"
                raise ToolError(f"Could not start {'the fixed test command' if tests else 'command'}: {exc}") from exc
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if self.cancelled.is_set():
                        reason = "stopped"
                    elif time.monotonic() >= deadline:
                        reason = f"timed out after {timeout:g} seconds"
                    elif os.fstat(output.fileno()).st_size > MAX_TEST_OUTPUT:
                        reason = "output limit exceeded"
                    if reason:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                        break
                    self.cancelled.wait(0.1)
                # A fast process can exit between polls after writing too much.
                # Check the final size before reporting a successful verification.
                if not reason and os.fstat(output.fileno()).st_size > MAX_TEST_OUTPUT:
                    reason = "output limit exceeded"
                output.seek(0)
                text = output.read(20_000).decode("utf-8", errors="replace")
                if os.fstat(output.fileno()).st_size > 20_000:
                    text += "\n[output truncated]"
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)
        outcome = reason or ("passed" if process.returncode == 0 else "failed")
        if tests:
            self.test_status = outcome
        result = f"{'Tests' if tests else 'Command'} {outcome}; exit code {process.returncode}\n{text}"
        if reason or process.returncode != 0:
            raise ToolError(result)
        return result


def tool_specs(actions):
    from . import ToolSpec

    return [
        ToolSpec(
            "run_command",
            "Run a build, targeted test, lint, or development command in the project. "
            "Provide an argument array, not a shell string, e.g. ['npm','run','build']. "
            "Runs with the user's OS permissions: NOT a sandbox. Commands may change files; "
            "only edit_file/write_file changes get backups. Do not delete, publish, push, "
            "install dependencies, or start persistent services unless the user requested that. "
            "Use run_tests after changes for the project's verification result.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "array", "items": {"type": "string"}},
                    "cwd": {"type": "string", "description": "Project-relative working directory; default '.'"},
                    "timeout_seconds": {"type": "integer", "description": "Command deadline, 1–600 seconds; default 300"},
                },
                "required": ["command"],
            },
            actions.run_command,
        ),
        ToolSpec(
            "edit_file",
            "Replace one exact, unique text block in an existing project file. "
            "Read it first. A recoverable backup is saved.",
            {
                "type": "object",
                "properties": {key: {"type": "string"} for key in ("path", "old_text", "new_text")},
                "required": ["path", "old_text", "new_text"],
            },
            actions.edit_file,
        ),
        ToolSpec(
            "write_file",
            "Create a new UTF-8 project file. Refuses to overwrite existing files.",
            {
                "type": "object",
                "properties": {key: {"type": "string"} for key in ("path", "content")},
                "required": ["path", "content"],
            },
            actions.write_file,
        ),
        ToolSpec(
            "run_tests",
            "Run the fixed project test command. Read the actual exit code/output. "
            "After edits, run tests and repair failures; never invent a pass.",
            {"type": "object", "properties": {}},
            actions.run_tests,
        ),
    ]
