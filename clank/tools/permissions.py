"""Directory sandbox for tools.

Every path a tool touches is resolved to a real path first and then checked
against the list of allowed project directories. Relative paths are rooted
at the first allowed directory. Prefix collisions such as ``C:\\project``
vs ``C:\\project-evil`` are rejected, and symlinks cannot escape because
resolution happens before the check.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from ..errors import PermissionDenied


def _within(child: Path, parent: Path) -> bool:
    child_s = os.path.normcase(str(child))
    parent_s = os.path.normcase(str(parent))
    try:
        return os.path.commonpath([child_s, parent_s]) == parent_s
    except ValueError:
        return False


class Sandbox:
    def __init__(self, roots: Iterable[str | Path]):
        resolved: list[Path] = []
        for root in roots:
            candidate = Path(root).expanduser()
            try:
                candidate = candidate.resolve()
            except OSError:
                candidate = candidate.absolute()
            resolved.append(candidate)
        if not resolved:
            resolved = [Path.cwd().resolve()]
        self.roots: list[Path] = resolved

    def describe(self) -> str:
        return ", ".join(str(r) for r in self.roots)

    def resolve(self, path: str | Path | None) -> Path:
        """Return the real path for *path* or raise PermissionDenied."""
        if path is None or str(path).strip() == "":
            candidate = self.roots[0]
        else:
            candidate = Path(str(path)).expanduser()
            if not candidate.is_absolute():
                candidate = self.roots[0] / candidate
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise PermissionDenied(f"could not resolve path {path!r}: {exc}") from exc
        if any(_within(resolved, root) for root in self.roots):
            return resolved
        raise PermissionDenied(
            f"path {path!r} is outside the allowed project directories "
            f"({self.describe()})"
        )
