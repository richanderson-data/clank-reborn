"""Terminal rendering.

Plain ANSI escapes, no terminal framework. Color is used only when stdout
is a real terminal and NO_COLOR is not set. Windows consoles get virtual
terminal processing enabled explicitly so the output looks the same in
PowerShell, Windows Terminal, and cmd.exe on Windows 10+.
"""

from __future__ import annotations

import os
import sys
import textwrap
from typing import TextIO

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"


def _enable_windows_ansi() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def configure_stdio() -> None:
    """Use UTF-8 for console output so model text never crashes on Windows.

    Legacy Windows codepages (cp1252 and friends) cannot encode many
    characters that models emit. errors="replace" keeps output flowing even
    if the console font cannot render a glyph.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


class UI:
    """No-op interface so tests and scripts can ignore rendering."""

    def banner(self, **kwargs: object) -> None: ...

    def status(self, text: str) -> None: ...

    def info(self, text: str) -> None: ...

    def warning(self, text: str) -> None: ...

    def error(self, text: str) -> None: ...

    def assistant_delta(self, text: str) -> None: ...

    def assistant_end(self) -> None: ...

    def tool_result(
        self, name: str, arguments: dict, ok: bool, denied: bool, output: str
    ) -> None: ...

    def prompt(self) -> str:
        return input("you> ")


class NullUI(UI):
    """Silent UI used by tests."""


class ConsoleUI(UI):
    def __init__(self, stream: TextIO | None = None, color: bool | None = None):
        self.stream = stream if stream is not None else sys.stdout
        if color is None:
            color = self.stream.isatty() and not os.environ.get("NO_COLOR")
            if color and os.name == "nt":
                color = _enable_windows_ansi()
        self.color = bool(color)
        self._dirty_line = False

    def _c(self, code: str, text: str) -> str:
        if not self.color:
            return text
        return f"{code}{text}{RESET}"

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self.stream, "encoding", None) or "utf-8"
            safe = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            self.stream.write(safe)
        self.stream.flush()

    def banner(self, **kwargs: object) -> None:
        version = kwargs.get("version", "")
        provider = kwargs.get("provider", "")
        model = kwargs.get("model", "")
        session = kwargs.get("session", "")
        db_path = kwargs.get("db_path", "")
        roots = kwargs.get("roots", [])
        tools = kwargs.get("tools", True)
        lines = [
            f"Clank-Reborn v{version}",
            f"provider : {provider}  model: {model}",
            f"session  : {session}",
            f"history  : {db_path}",
            f"tools    : {'enabled' if tools else 'disabled'}",
        ]
        if tools:
            roots_text = ", ".join(str(r) for r in roots)  # type: ignore[union-attr]
            lines.append(f"allowed  : {roots_text}")
        lines.append("type /help for commands, /exit to quit")
        self._write(self._c(CYAN, "\n".join(lines)) + "\n\n")

    def status(self, text: str) -> None:
        self._write(self._c(DIM, text) + "\n")

    def info(self, text: str) -> None:
        self._write(self._c(BLUE, text) + "\n")

    def warning(self, text: str) -> None:
        self._write(self._c(YELLOW, f"warning: {text}") + "\n")

    def error(self, text: str) -> None:
        self._write(self._c(RED, f"error: {text}") + "\n")

    def assistant_delta(self, text: str) -> None:
        self._dirty_line = True
        self._write(text)

    def assistant_end(self) -> None:
        if self._dirty_line:
            self._write("\n")
            self._dirty_line = False

    def tool_result(
        self, name: str, arguments: dict, ok: bool, denied: bool, output: str
    ) -> None:
        self.assistant_end()
        args_text = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        if denied:
            label = self._c(YELLOW, "denied")
        elif ok:
            label = self._c(GREEN, "ok")
        else:
            label = self._c(RED, "failed")
        self._write(self._c(DIM, f"[tool] {name}({args_text}) -> ") + label + "\n")
        preview = output.strip()
        if preview:
            lines = preview.splitlines()[:8]
            clipped = len(preview.splitlines()) > 8 or len(preview) > 600
            shown = "\n".join(lines)
            if len(shown) > 600:
                shown = shown[:600]
            for line in textwrap.wrap(shown, subsequent_indent="    "):
                self._write(self._c(DIM, f"    {line}") + "\n")
            if clipped:
                self._write(self._c(DIM, "    ...") + "\n")

    def prompt(self) -> str:
        return input(self._c(BOLD, "you> "))
