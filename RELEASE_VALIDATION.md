# Clank Reborn release validation

Updated 2026-10-17 for the Ollama-only public beta. This document supersedes
older checkpoint reports; it records only the current source tree and tests.

## Scope

- Ollama NDJSON chat streaming in the terminal and browser.
- SQLite sessions, exports, and recovery of existing conversation rows.
- Approved-root file/search/Git tools and public search/page/weather tools.
- Opt-in exact edits, commands, fixed tests, Stop, undo, and the 100-round
  tool budget with progress prompts every 20 rounds.
- Windows packaging, browser assets, hygiene scanning, and CI configuration.

The product has no cloud provider or API-key configuration. Commands run with
the user's OS permissions and are not an OS sandbox. Web results are external
evidence, not guaranteed truth. Live model behavior depends on the installed
Ollama model.

## Validation

Fresh release-candidate results:

- `CLANK_TEST_OLLAMA=1 CLANK_TEST_AUTONOMY=1 CLANK_TEST_WEB=1
  CLANK_TEST_MODEL=qwen3.5:4b .venv\Scripts\python.exe -m pytest -q`:
  **255 passed, 1 skipped** in 74.20 seconds. The skip is the Windows symlink
  test because symlinks are not permitted on this machine.
- `.venv\Scripts\ruff.exe check .`: **passed**.
- `node --check clank\web_assets\app.js`: **passed**.
- `.venv\Scripts\python.exe scripts\security_check.py`: **passed** with no
  findings; 76 working-tree files and 6 history patches scanned.

The test run included live Ollama, autonomy, and web opt-ins against the
installed `qwen3.5:4b` model. Deterministic local HTTP fixtures do not prove
live model quality beyond those exercised scenarios.

## Publication boundary

Publish source, MIT license, examples, and reviewed public documentation only.
Exclude credentials, databases, logs, model weights, backups, private exports,
and local control artifacts. Existing SQLite databases are retained in place;
no destructive history migration is performed.
