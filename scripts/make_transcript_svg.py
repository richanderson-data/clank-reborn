"""Render a captured Clank terminal transcript into a self-contained SVG.

The input is real captured output (e.g. from running demo/demo.ps1 with
output redirected to a file). This script only wraps and colors the text;
it never invents content. ANSI escape codes are stripped.

Usage:
    python scripts/make_transcript_svg.py docs/transcripts/demo-session.txt \
        -o docs/screenshots/demo.svg \
        --title "Clank-Reborn - live Ollama session" \
        --caption "Captured from a real run"
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

BG = "#0d1117"
BAR = "#161b22"
BORDER = "#30363d"
DEFAULT = "#e6edf3"
DIM = "#8b949e"
CYAN = "#79c0ff"
GREEN = "#7ee787"
YELLOW = "#f2cc60"
RED = "#ff7b72"
PINK = "#ffa5d8"
WHITE = "#ffffff"

FONT = "'Cascadia Mono','Consolas','DejaVu Sans Mono',monospace"
FONT_SIZE = 13
CHAR_W = 7.85
LINE_H = 19
PAD = 24
TITLE_H = 36
MAX_CHARS = 112


def classify(line: str) -> tuple[str, bool]:
    stripped = line.strip()
    if stripped.startswith("==="):
        return PINK, True
    if line.startswith("you>"):
        return CYAN, False
    if line.startswith("[tool]"):
        if "-> ok" in line:
            return GREEN, False
        if "denied" in line:
            return YELLOW, False
        return RED, False
    if line.startswith("error:"):
        return RED, False
    if line.startswith("warning:"):
        return YELLOW, False
    if line.startswith("Clank-Reborn"):
        return CYAN, True
    if line.startswith(
        ("provider :", "session  :", "history  :", "tools    :", "allowed  :", "type ")
    ):
        return DIM, False
    if line.startswith("    "):
        return DIM, False
    return DEFAULT, False


def wrap_line(line: str, width: int = MAX_CHARS) -> list[str]:
    if len(line) <= width:
        return [line]
    wrapped = []
    indent = "    " if not line.startswith(" ") else "        "
    remaining = line
    first = True
    while remaining:
        room = width if first else width - len(indent)
        chunk = remaining[:room]
        wrapped.append(chunk if first else indent + chunk)
        remaining = remaining[room:]
        first = False
    return wrapped


def build_svg(
    text: str, title: str, caption: str, max_chars: int = MAX_CHARS
) -> str:
    raw_lines = [ANSI_RE.sub("", line).rstrip() for line in text.splitlines()]
    lines: list[tuple[str, str, bool]] = []
    for line in raw_lines:
        for piece in wrap_line(line, max_chars):
            fill, bold = classify(piece)
            lines.append((piece, fill, bold))

    width = int(PAD * 2 + max_chars * CHAR_W)
    body_top = TITLE_H + PAD
    height = body_top + len(lines) * LINE_H + PAD + 28

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        f'font-family="{FONT}" font-size="{FONT_SIZE}">'
    )
    parts.append(
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="10" '
        f'fill="{BG}" stroke="{BORDER}"/>'
    )
    parts.append(f'<rect x="0" y="0" width="{width}" height="{TITLE_H}" rx="10" fill="{BAR}"/>')
    parts.append(
        f'<rect x="0" y="{TITLE_H - 10}" width="{width}" height="10" fill="{BAR}"/>'
    )
    for index, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        parts.append(
            f'<circle cx="{PAD + index * 20}" cy="{TITLE_H / 2}" r="6" fill="{color}"/>'
        )
    parts.append(
        f'<text x="{width / 2}" y="{TITLE_H / 2 + 4}" fill="{DIM}" '
        f'text-anchor="middle">{escape(title)}</text>'
    )

    y = body_top + FONT_SIZE
    for text_line, fill, bold in lines:
        weight = ' font-weight="bold"' if bold else ""
        content = escape(text_line) if text_line else " "
        parts.append(
            f'<text x="{PAD}" y="{y:.1f}" fill="{fill}"{weight} '
            f'xml:space="preserve">{content}</text>'
        )
        y += LINE_H

    footer = f"{caption} - generated {date.today().isoformat()} by scripts/make_transcript_svg.py"
    parts.append(
        f'<text x="{PAD}" y="{height - 14}" fill="{DIM}" font-size="11">'
        f"{escape(footer)}</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transcripts", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--title", default="Clank-Reborn terminal session")
    parser.add_argument(
        "--caption",
        default="Captured from a real run; text is unedited, only colors and wrapping are added",
    )
    parser.add_argument("--max-chars", type=int, default=MAX_CHARS)
    args = parser.parse_args()

    chunks = []
    for path in args.transcripts:
        chunk = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
        chunks.append(chunk.rstrip())
    text = "\n".join(chunks)

    svg = build_svg(text, args.title, args.caption, args.max_chars)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8")
    print(f"wrote {args.output} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
