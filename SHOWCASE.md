# Clank Reborn — the project story

Clank Reborn is a small local-first project assistant built around a simple
question: can local AI help explain a codebase without becoming a complicated
orchestration system?

The answer is a single Python process with browser and terminal entry points,
streaming Ollama chat, SQLite conversation history, and three read-only file
tools. Opt-in autonomy adds exact edits, new files, and a fixed test command
to that same turn loop. The browser shows the evidence and recoverable changes.

## Engineering choices

- Standard-library browser server and packaged HTML/CSS/JavaScript: no frontend build chain.
- One runtime dependency, requests: provider transport remains easy to inspect.
- Plain SQLite tables: history belongs to the user, not a hosted account.
- Explicit local/cloud distinction: optional DeepSeek requires consent.
- Narrow project roots: tools resolve paths before reading, including junctions.
- Atomic tool-call/result batches: browser disconnects cannot strand unmatched calls.
- Completion markers: a broken provider stream does not masquerade as a saved answer.

## Evidence, not marketing

See COMPLETION.md for the verified tests and real-model observations.
Scripted-provider tests exercise real HTTP transport but are not live-model tests.
The local browser was exercised with qwen3.5:4b. DeepSeek live behavior remains
unverified without a key. CI is configured, not claimed to have run remotely.

The model can be wrong. It found the sample bug correctly but made an incorrect
numerical illustration in one run. That limitation is part of the project story.

## What this is not

No model-chosen arbitrary shell commands, agent hierarchy, telemetry, or claim
of production-hardening. Autonomous project tests execute code with the user's
OS permissions; the directory boundary is not an OS sandbox. This is an inspectable
early release. Improvements should follow observed friction, not a speculative roadmap.
