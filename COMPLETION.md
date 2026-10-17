# Clank Reborn — historical validation checkpoint

Publication-ready claim retracted. The results below describe the earlier
checkpoint, not validation of the current development changes. See PROGRESS.md.

Verified locally on 2026-10-13: Windows, Python 3.14.5, Ollama 0.34.1,
qwen3.5:4b. Nothing committed, pushed, or published.

## What is complete

Local browser/terminal chat, SQLite history, file evidence, Markdown export,
and opt-in autonomous edits/tests in the same small session loop.
Autonomy adds exact edits, new files, a fixed test command, private backups,
conflict-aware undo, and Stop requests. Every edit batch automatically runs tests.
There is no agent hierarchy, queue, or policy service.

## Verified results

- Final full installed-checkout suite: **189 passed, 3 skipped** in 56.64 seconds.
- The passing suite includes four real Ollama chat tests and one real-model
  autonomous repair/test/restore test; the remaining tests are offline regressions.
- Skips: optional live Ollama/web/autonomy checks and a Windows file-symlink test
  requiring extra OS permission. Separate real Windows junction tests passed.
- Offline autonomy regressions cover real subprocess success/failure, stop,
  timeout/output bounds, write boundaries, atomic history, backup/undo conflicts,
  automatic verification, empty completions, and read-only default behavior.
- Lint and JavaScript syntax checks passed.
- Runtime dependency audit found no known vulnerabilities in the five tested
  base runtime packages. This is a point-in-time database check, not a security guarantee.
- Real browser project reading, history restoration, Markdown export content,
  clipboard fallback, narrow-screen history access, autonomous repair, and undo
  were exercised. Native embedded-browser file download completion remains unconfirmed.
- Clean-install package smoke and final archive checks are documented in the
  accompanying publication report.

## Important limits

Project tests run with the user's OS permissions and environment, not inside
an OS sandbox. Only use autonomy with trusted project code. Tests can be edited
by the model, so inspect changed files. Stop on a silent provider can wait until
its next event/read timeout; project-spawned descendants are not isolated.
Eight tool rounds bound a task; incomplete tasks are reported rather than
continued in the background. Models can still be wrong.

Repeated real-model checks found a model forgetting to rerun tests and returning
an empty explanation. Automatic verification now prevents a skipped test step;
a factual tool-derived summary handles an empty autonomous final answer.
A blank ordinary chat gets one retry, then a visible error.

Live Ollama behavior and manual macOS/Linux operation remain unverified.
GitHub CI is configured for Windows/Linux and Python 3.11/3.12/3.14, including
installed-wheel checks, but has not run remotely. Context uses message-count
trimming rather than token counting.

## Release position

**Ready for an honest early GitHub release**, not a production-hardened
unattended agent claim. Existing MIT license retained with owner approval;
placeholder repository links removed. README documents setup, privacy, autonomy,
failure behavior, and limits. Publish only the sanitized source archive or
reviewed source files—not history, backups, environments, or private startup links.
