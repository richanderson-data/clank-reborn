"""Read-only filesystem tools.

These are deliberately read-only for v0.1.0: they demonstrate useful,
permission-controlled tool execution without letting a model modify the
user's files. All results are plain text, capped in size, and safe to feed
back to the model.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..errors import PermissionDenied, ToolError
from .permissions import Sandbox

MAX_LIST_ENTRIES = 200
DEFAULT_MAX_READ_BYTES = 20_000
MAX_READ_BYTES = 200_000
DEFAULT_MAX_RESULTS = 50
MAX_SEARCH_RESULTS = 200
MAX_SEARCH_FILE_BYTES = 1_000_000

SKIP_DIRS = {
    ".clank-backups",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
}


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return False


def _decode(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def sensitive(path: Path) -> bool:
    names = [part.lower() for part in path.parts]
    name = path.name.lower()
    return (
        any(part in {".git", ".clank-backups", ".ssh", ".aws", ".kube"} for part in names)
        or (name.startswith(".env") and name not in {".env.example", ".env.sample", ".env.template"})
        or name in {".npmrc", ".pypirc", "credentials", "id_rsa", "id_ed25519"}
        or name.startswith(("id_rsa.", "id_ed25519."))
        or path.suffix.lower() in {".pem", ".p12", ".pfx", ".key"}
    )


def relative_name(sandbox, target):
    for root in sandbox.roots:
        if target.is_relative_to(root):
            return target.relative_to(root).as_posix()
    return target.name


def readable(sandbox, path):
    target = sandbox.resolve(path)
    if sensitive(target):
        raise PermissionDenied("Credential files, Git metadata, and private Clank backups are not file-tool inputs")
    return target


def list_directory(sandbox: Sandbox, path: str = ".") -> str:
    target = readable(sandbox, path)
    label = relative_name(sandbox, target)
    if not target.exists():
        raise ToolError(f"directory not found: {label}")
    if not target.is_dir():
        raise ToolError(f"{label} is not a directory (use read_file)")
    entries = sorted(
        (p for p in target.iterdir() if not sensitive(p)), key=lambda p: (not p.is_dir(), p.name.lower())
    )
    lines: list[str] = []
    for entry in entries[:MAX_LIST_ENTRIES]:
        try:
            if entry.is_dir():
                lines.append(f"{entry.name}/")
            else:
                lines.append(f"{entry.name}  ({entry.stat().st_size} bytes)")
        except OSError as exc:
            lines.append(f"{entry.name}  (unreadable: {exc})")
    if not lines:
        return f"{label} is empty (credential/private paths are omitted)"
    header = f"{label} ({len(lines)} entries shown; credential/private paths omitted)"
    if len(entries) > MAX_LIST_ENTRIES:
        header += f", truncated at {MAX_LIST_ENTRIES} of {len(entries)}"
    return header + "\n" + "\n".join(lines)


def read_file(sandbox: Sandbox, path: str, max_bytes: int = DEFAULT_MAX_READ_BYTES) -> str:
    target = readable(sandbox, path)
    label = relative_name(sandbox, target)
    if not target.exists():
        raise ToolError(f"file not found: {label}")
    if target.is_dir():
        raise ToolError(f"{label} is a directory (use list_directory)")
    try:
        max_bytes = max(1, min(int(max_bytes), MAX_READ_BYTES))
    except (TypeError, ValueError) as exc:
        raise ToolError(f"max_bytes must be an integer, got {max_bytes!r}") from exc
    size = target.stat().st_size
    if _is_binary(target):
        raise ToolError(f"{label} looks binary; only text files can be read")
    # Bound the disk read itself, not merely the output after loading a huge file.
    with target.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    if len(text.encode("utf-8")) > max_bytes or size > max_bytes:
        clipped = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        return (
            clipped
            + f"\n\n[truncated: showing first {max_bytes} of {size} bytes; "
            f"call read_file again with a larger max_bytes or a more specific file]"
        )
    return text


def search_text(
    sandbox: Sandbox,
    query: str,
    path: str = ".",
    max_results: int = DEFAULT_MAX_RESULTS,
) -> str:
    if not query or not str(query).strip():
        raise ToolError("search_text requires a non-empty query")
    query = str(query)
    try:
        max_results = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
    except (TypeError, ValueError) as exc:
        raise ToolError(f"max_results must be an integer, got {max_results!r}") from exc
    target = readable(sandbox, path)
    if not target.exists():
        raise ToolError(f"path not found: {relative_name(sandbox, target)}")

    root = target if target.is_dir() else target.parent
    files: list[Path] = []
    if target.is_file():
        files = [target]
    else:
        for current, dirnames, filenames in os.walk(target):
            approved = []
            for directory in sorted(dirnames):
                if directory in SKIP_DIRS:
                    continue
                try:
                    readable(sandbox, Path(current) / directory)
                except (PermissionDenied, OSError, RuntimeError):
                    continue
                approved.append(directory)
            dirnames[:] = approved
            for name in sorted(filenames):
                files.append(Path(current) / name)

    needle = query.lower()
    matches: list[str] = []
    scanned = 0
    for file in files:
        try:
            file = readable(sandbox, file)
            if file.stat().st_size > MAX_SEARCH_FILE_BYTES or _is_binary(file):
                continue
        except (PermissionDenied, OSError, RuntimeError):
            continue
        scanned += 1
        try:
            text = _decode(file)
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                try:
                    rel = file.relative_to(root)
                except ValueError:
                    rel = file
                snippet = line.strip()
                if len(snippet) > 200:
                    snippet = snippet[:200] + "..."
                matches.append(f"{rel}:{lineno}: {snippet}")
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    if not matches:
        return f"no matches for {query!r} under {relative_name(sandbox, root)} ({scanned} text files scanned; credential/private paths skipped)"
    header = f"{len(matches)} match(es) for {query!r} under {relative_name(sandbox, root)}"
    if len(matches) >= max_results:
        header += f" (capped at {max_results})"
    return header + "\n" + "\n".join(matches)
