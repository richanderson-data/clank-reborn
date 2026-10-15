# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Local browser workspace with streaming answers, model selection, saved
  conversations, visible file evidence, Markdown rendering, and export.
- Browser authorization, host/origin checks, and explicit cloud consent.
- Refresh-connection control and launcher setup guidance with available-port selection.
- Release regressions for browser failures, exports, provider shapes, and persistence.
- Opt-in autonomous edit/test mode in the same session loop, exact text edits,
  new-file creation, private backups, conflict-aware undo, and Stop requests.
- Real-model autonomous fix/test/restore regression and a disposable demo fixture.

### Fixed

- Windows junction escape in recursive search.
- Provider switching now remembers per-provider models.
- Incomplete streams no longer report success.
- Tool calls/results persist atomically even when the browser disconnects.
- Bounded file reads, nonfinite timeout rejection, and actionable database errors.
- Same-second Python edits no longer reuse stale bytecode in the next test run.

## [0.1.0] - 2026-10-13

First release candidate. Local-first terminal assistant with streaming
providers, persistent sessions, and permission-controlled read-only tools.

### Added

- Ollama provider: streaming `/api/chat` (NDJSON), tool calling, model
  listing, model-not-found hints, and typed errors for connection failures,
  HTTP errors, malformed streams, mid-stream errors, stalls, and drops.
- DeepSeek provider: streaming OpenAI-compatible chat completions (SSE),
  tool-call delta accumulation, model listing, API-key configuration, and
  401/402-specific guidance.
- Persistent sessions in SQLite (`~/.clank-reborn/clank.db` by default):
  create, append, list, resume by id or unambiguous prefix, cascade delete,
  auto-derived titles, and JSON round-trips for tool calls.
- Session turn loop with tool execution: stream, persist, execute authorized
  tools, feed results back, bounded tool rounds (default 8).
- Read-only tools with JSON-schema specs: `list_directory`, `read_file`,
  `search_text`; size caps, binary detection, and safe skipping of heavy
  directories.
- Directory sandbox: symlink-resolving path authorization against
  `--allow-dir` roots; denies traversal, absolute paths, sibling-prefix
  escapes, and symlink escapes; denials become structured tool results.
- CLI: one-shot (`-p`), `--continue`, `--session`, `--allow-dir`
  (repeatable), `--no-tools`, `--list-models`, `--list-sessions`,
  `--provider`, `--model`, `--db`, `--host`, `--version`; interactive REPL
  with `/help`, `/new`, `/sessions`, `/history`, `/tools`, `/model`,
  `/provider`, `/exit`.
- Terminal UI: streaming token output, tool result previews, colored
  statuses, Windows VT enabling, `NO_COLOR`, UTF-8 stdio reconfiguration,
  and an encode-replace fallback for legacy codepages.
- Configuration via flags, environment variables, and an optional `.env`
  loader with no third-party dependency.
- Test suite: 128 tests, including fake NDJSON/SSE HTTP servers that
  exercise real streaming, abrupt connection resets, stalled reads,
  malformed payloads, and error-during-stream paths; opt-in live
  integration tests for Ollama and DeepSeek.
- Repository hygiene: `scripts/security_check.py` scans the working tree
  and git history for secrets, credentials, personal paths, and emails, and
  verifies `.gitignore` coverage for sensitive files.
- Demo: `demo/demo.ps1` runs the full capability tour against real Ollama;
  `demo/sample_project/` provides a safe project with a deliberate bug;
  `scripts/make_transcript_svg.py` renders captured real transcripts to SVG.
- Documentation: README with architecture diagram and verified setup,
  SHOWCASE.md (portfolio material), docs/DEMO.md (recording plan),
  PROGRESS.md (release checklist), CI workflows for lint, regression tests,
  and manual live integration runs.

### Fixed

- Console crash (`UnicodeEncodeError`) when model output contained
  characters outside the Windows console codepage; output now degrades to
  replacement characters instead of aborting.
- Read timeouts during streaming were reported as generic connection
  errors; stalls are now distinguished from drops in user-facing messages.

