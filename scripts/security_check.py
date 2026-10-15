"""Repository hygiene check for Clank-Reborn.

Scans the working tree (and git history, when commits exist) for secrets,
credentials, personal information, and machine-specific paths. This is a
release gate, not a replacement for a real secret scanner.

Usage:
    python scripts/security_check.py            # scan working tree
    python scripts/security_check.py --verbose  # list files checked

Exit code 0 means no findings; 1 means findings were reported.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {
    ".clank-backups",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".eggs",
}

SKIP_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".log", ".pyc"}

MAX_FILE_BYTES = 2_000_000

PLACEHOLDERS = {
    "",
    "your-key",
    "your_api_key",
    "changeme",
    "replace-me",
    "sk-xxx",
    "sk-test",
    "sk-abc",
    "example",
    "xxx",
    "none",
    "null",
}

PLACEHOLDER_USERS = {
    "you",
    "user",
    "username",
    "yourname",
    "your-name",
    "name",
    "me",
    "demo",
    "example",
    "someone",
}


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]


RULES = [
    Rule("long sk- API key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    Rule("GitHub token", re.compile(r"\b(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,})\b")),
    Rule("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    Rule(
        "assigned secret",
        re.compile(
            r"(?i)\b(api[_-]?key|apikey|secret|password|passwd|token)\b\s*[:=]\s*"
            r"[\"']([^\"'\s]{8,})[\"']"
        ),
    ),
    Rule("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    Rule("absolute Windows user path", re.compile(r"(?i)\b[a-z]:[\\/]users[\\/][^\\/\s\"']+")),
    Rule("home directory path", re.compile(r"(?<![\w.])/(?:home|Users)/[A-Za-z0-9._-]+")),
    Rule(
        "email address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
]

EMAIL_ALLOWLIST = {
    "user@example.com",
    "you@example.com",
    "name@example.com",
    "example@example.com",
}

ALLOWLIST_MARKERS = ("pragma: allowlist secret", "security-check: ignore")


@dataclass(frozen=True)
class Finding:
    location: str
    rule: str
    excerpt: str


def _placeholder_username(line: str) -> bool:
    for match in re.finditer(r"(?i)[a-z]:[\\/]users[\\/]([^\\/\s\"']+)", line):
        if match.group(1).lower() in PLACEHOLDER_USERS:
            return True
    for match in re.finditer(r"/(?:home|Users)/([A-Za-z0-9._-]+)", line):
        if match.group(1).lower() in PLACEHOLDER_USERS:
            return True
    return False


def scan_text(text: str, location: str) -> list[Finding]:
    findings: list[Finding] = []
    path_rules = {"absolute Windows user path", "home directory path"}
    for lineno, line in enumerate(text.splitlines(), start=1):
        if any(marker in line for marker in ALLOWLIST_MARKERS):
            continue
        skip_paths = _placeholder_username(line)
        for rule in RULES:
            if skip_paths and rule.name in path_rules:
                continue
            for match in rule.pattern.finditer(line):
                if rule.name == "email address" and match.group(0) in EMAIL_ALLOWLIST:
                    continue
                if rule.name == "assigned secret":
                    value = match.group(2)
                    if value.lower() in PLACEHOLDERS:
                        continue
                excerpt = line.strip()
                if len(excerpt) > 120:
                    excerpt = excerpt[:120] + "..."
                findings.append(Finding(f"{location}:{lineno}", rule.name, excerpt))
    return findings


def _is_repo_file(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    return path.is_file()


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def tracked_files(root: Path) -> list[Path]:
    git = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if git.returncode == 0 and git.stdout:
        return [
            root / name
            for name in git.stdout.split("\x00")
            if name and _is_repo_file(root / name)
        ]
    return sorted(path for path in root.rglob("*") if _is_repo_file(path))


def scan_working_tree(root: Path, verbose: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    files = tracked_files(root)
    for path in files:
        relative = path.relative_to(root).as_posix()
        if verbose:
            print(f"  checked {relative}")
        findings.extend(scan_text(_read(path), relative))
    print(f"working tree: {len(files)} file(s) scanned")
    return findings


def scan_git_history(root: Path) -> list[Finding]:
    count = subprocess.run(
        ["git", "rev-list", "--all", "--count"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if count.returncode != 0:
        print("git history: not a git repository; skipped")
        return []
    commits = int((count.stdout or "0").strip() or 0)
    if commits == 0:
        print("git history: 0 commits; nothing to scan")
        return []
    diff = subprocess.run(
        ["git", "log", "-p", "--all", "--no-color"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    text = diff.stdout[:5_000_000]
    findings = scan_text(text, "git-history")
    print(f"git history: scanned {commits} commit(s) of patches")
    return findings


def check_ignored(root: Path) -> list[str]:
    probes = [".env", "secrets.db", "debug.log", ".venv/x", "private.db-wal",
              ".clank-backups/private.json"]
    failures = []
    if not (root / ".git").exists():
        print("gitignore check: no .git directory; skipped")
        return failures
    for probe in probes:
        result = subprocess.run(
            ["git", "check-ignore", "-q", probe],
            cwd=root,
            check=False,
        )
        if result.returncode != 0:
            failures.append(probe)
    if failures:
        print(f"gitignore check: NOT ignored: {', '.join(failures)}")
    else:
        print(f"gitignore check: all {len(probes)} sensitive probes are ignored")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--skip-history", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    print(f"scanning {root}")
    findings = scan_working_tree(root, verbose=args.verbose)
    if not args.skip_history:
        findings.extend(scan_git_history(root))
    ignore_failures = check_ignored(root)

    if ignore_failures:
        findings.append(
            Finding(".gitignore", "missing ignore rule", ", ".join(ignore_failures))
        )

    if findings:
        print(f"\n{len(findings)} finding(s):")
        for finding in findings:
            print(f"  [{finding.rule}] {finding.location}: {finding.excerpt}")
        return 1
    print("\nno findings: workspace looks safe to publish")
    return 0


if __name__ == "__main__":
    sys.exit(main())
