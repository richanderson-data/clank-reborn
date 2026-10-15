# Clank Reborn — release validation, 2026-10-13

## Verdict: KEEP, with a deliberately small architecture

Keep the one-process browser/CLI, provider adapters, tool registry, and SQLite
store. The useful missing capabilities have been implemented without importing
the legacy orchestration system. This is a public-beta release candidate for
trusted local projects, not a claim of hardened unattended operation or perfect
model answers. No publication, push, or project commit was performed.

The MSI filesystem was directly accessible. Only Clank-Reborn was inspected;
legacy Clank repositories were not part of this work.

## Current functionality

- Local Ollama streaming in the browser and terminal; installed-model selection.
- Persisted SQLite conversations, resume, Markdown export, and damaged tool-call
  history recovery without rewriting the original database.
- Directory/file/search evidence with approved-root boundaries, junction checks,
  relative result paths, and exclusions for common credential filenames.
- Read-only Git status, working/staged diffs, and log.
- Public web search, HTML/text page reading with navigation links, and city weather.
  Web can be disabled in the browser, through `--offline`, or by configuration.
  Forecast days have explicit dates and weekday labels instead of ambiguous arrays.
- Actual retrieved page/weather sources are appended to answers and persisted,
  even when the model omits citations. This shows provenance, not proof that
  every model interpretation is correct.
- Opt-in exact edits/new files, recoverable backups, atomic undo, fixed tests,
  and normal argument-array commands for builds and targeted checks.
- Windows npm-style wrappers resolve through PATH. Fixed-command parsing preserves
  Windows paths and quoted arguments.
- Stop, bounded command output, configurable command deadlines, honest incomplete
  results, and rejection of incomplete provider streams.
- Optional existing DeepSeek provider with cloud consent. Public web access does
  not itself grant cloud project-file access.

## Tool-loop correction requested by the user

The old default of eight rounds has been replaced with **100 tool rounds**.
At 20, 40, 60, and 80 rounds, Clank adds a non-stopping progress instruction to
the next normal provider request. It includes recent failures and actual
backed-up changed paths/fixed-test status. The model is asked to identify blockers,
redirect ineffective approaches, and continue. There is no extra agent,
approval layer, queue, or separate review model call.

The ceiling is configurable: `--max-tool-rounds 300` or
`CLANK_MAX_TOOL_ROUNDS=300`. At that ceiling, pending calls are explicitly marked
unexecuted and Clank requests a tool-free summary. If the model gives no summary,
a factual incomplete-result message is saved instead of a blank answer. The
terminal ceiling no longer emits a contradictory “task continues” review.

A deterministic regression ran **41 tool rounds**, crossed the 20/40 checkpoints,
included an unknown-tool failure, and completed without early stopping. Tests
also check absence of checkpoint instructions between reviews, exact ceiling
execution counts, tool-free wrap-up, and empty-summary fallback. Scripted tests
prove loop behavior and prompt contents, not a model's ability to reason well.
An explicitly labelled deterministic browser fixture also completed 41 real
directory-tool calls, visibly displayed both checkpoints, and finished normally.
The screenshot is `CLANK_LOOP_PROOF.png`; it is not presented as a live-model run.

## Verified results

- Final full suite: **268 passed, 3 skipped in 77.63 seconds**, 271 tests collected.
- Six live acceptance checks passed: four real Ollama checks, one real-model
  edit/test/undo workflow, and one public search/weather/page check.
- Three skips: two optional live Clank DeepSeek API checks (no key configured),
  and a Windows file-symlink test requiring unavailable privilege. Junction
  boundary tests and deterministic DeepSeek HTTP/SSE tests ran.
- After final status/configuration edits: **39 passed in 5.83 seconds**.
- Final source-metadata/loop/web regression subset: **63 passed in 11.90 seconds**.
- Static checks: Ruff passed; browser JavaScript syntax passed.
- Repository hygiene: 87 source files scanned, no findings; all six sensitive
  ignore probes passed. This lightweight checker is not an exhaustive secret scan.
- Git has zero project commits. Temporary fixture repositories commit only fake
  test content; no project history was changed. GitHub CI is configured but has
  not been run remotely.
- Fresh wheel installed outside the checkout and imported under isolated Python:
  assets, HTTP streaming, SQLite/export, real edit and test execution, undo,
  command execution, internet/Git registration, and the 100-round default passed.
- Existing OpenCode DeepSeek Pro/Flash access was used for independent engineering
  reviews. Review findings were checked against code and actual tests, not treated
  as verification by themselves. No credentials were copied into Clank.
  The final focused review found no demonstrable blocking defect in loop/checkpoint,
  source persistence, or cloud/file separation. Its incorrect statement that
  Python's `dataclasses.replace` skips `__post_init__` was independently disproved
  using the actual runtime and was not accepted as a finding.

Wheel SHA-256:
`4f3e4f51f3a13d783ca6ab38cb0d72d39d4ab8238e0b144533270384768dc906`

## Remaining limitations and release positioning

1. **Model reliability:** small local models can make unsupported interpretations
   despite correct tools and stronger instructions. Earlier live attempts invented
   recurring event names and misread weather timing. Retrieved sources/evidence
   remain visible. Do not market this as guaranteed factual research or autonomous
   correctness. The original weather/events question now reaches tools and a final
   answer instead of an unavailable-tools refusal or eight-round cutoff.
   A qwen3.5:9b comparison avoided invented named festivals but followed an
   irrelevant airline result and still confused forecast-day ordering before
   explicit weekday/date output was added. No claim of fully solved factual
   reliability is made.
2. **Trusted code only:** command execution uses the user's OS permissions and
   environment. It is not an OS sandbox; commands can access credentials, modify
   files outside file-tool boundaries, and use the network. Command-created changes
   are not automatically backed up. Windows batch wrappers have shell semantics.
3. **Cancellation:** a silent provider can notice Stop only at an event/timeout.
   Child processes spawned by project commands are not an isolated process tree.
4. **Context:** history is bounded by message count, not tokens. A long active tool
   turn can exceed a model's context. Increasing the round budget cannot cure this.
5. **Web availability:** Bing RSS relevance varies; static page reading does not
   execute JavaScript, log in, bypass CAPTCHAs, or read PDFs. Weather is Open-Meteo
   model output, not an official alert service. Public addresses/redirects are
   checked and connections pinned, with byte/time limits.
6. **Data:** history, exports, and retained before/after backups can contain private
   project content. Common filename exclusions are not a comprehensive secret
   detector. No automatic backup pruning removes recovery data; monitor storage.
7. **Coverage:** live Clank DeepSeek API and manual Linux/macOS workflows remain
   unverified. Existing OpenCode DeepSeek review access is a separate integration.

For GitHub/LinkedIn, present the verified local-first workflow and its evidence,
not “production certified,” cross-platform verified, or hardened unattended
operation. Keep MIT, publish only source/public screenshots, and exclude `.env`,
databases, backups, caches, and private exports. Publication itself still requires
the user's explicit instruction.

## Prioritized next fixes

- Evaluate stronger installed local models against named-event, date, and weather
  interpretation tasks; keep failures in the evaluation record. Source links alone
  do not eliminate hallucinations.
- Exercise the optional Clank DeepSeek provider with a deliberately configured key
  if it is part of the release promise; run remote CI after authorized publication.
- Add token-aware context handling only if measured long-job failures justify it;
  do not rebuild a policy/orchestration framework to solve prompt quality.
- If untrusted unattended code execution becomes a requirement, use genuine OS
  isolation as a separately scoped feature rather than pretending directory checks
  provide it.

Older audit/publication reports and artifacts describe earlier checkpoints and
must not be used as proof of the current tool set. This report records the current
verified release candidate and explicitly preserves the remaining limits.
