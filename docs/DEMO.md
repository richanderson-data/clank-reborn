# Demo recording plan (45–90 seconds)

This plan produces one continuous recording of Clank-Reborn doing real work
against a real local Ollama server. No step is staged; the model responses
are whatever the model actually says.

## Prerequisites (do these before recording)

1. Ollama is running: `ollama serve` (or the desktop app), and a
   tool-capable model is pulled, for example `ollama pull qwen2.5:7b`.
   Response speed matters more than size for the recording; a 3B–8B model
   keeps the video tight.
2. Clank is installed (`pip install -e .`) and the demo script works:
   `powershell -ExecutionPolicy Bypass -File demo/demo.ps1 -Model qwen2.5:7b`.
   Rehearse once so model latency does not surprise you.
3. Terminal: Windows Terminal, ~100x30, font Cascadia Mono 14, optional
   transparency off, window maximized. PowerShell 5.1 or 7 both work.
4. Recorder: Win+Alt+R (Xbox Game Bar) or OBS Studio capturing the window.
   Target 1080p, 30 fps. Keep it silent or narrate; either is fine.

## Shot list

| Time | Shot | Command / action | What the viewer should notice |
| --- | --- | --- | --- |
| 0:00–0:08 | Title card (optional) or empty terminal; type the first command | `python -m clank --model qwen2.5:7b --db demo\.record.db --allow-dir demo\sample_project -p "Reply with exactly one word: pong"` | Banner shows provider, model, session id, history file, allowed directory. Answer streams in. |
| 0:08–0:22 | Save context | `python -m clank --model qwen2.5:7b --db demo\.record.db --allow-dir demo\sample_project -p "Remember for later: the release code name is ORANGE-FOX. Reply OK."` | A normal conversation turn; note the session id in the banner. |
| 0:22–0:35 | Restore context in a **new process** | `python -m clank --model qwen2.5:7b --db demo\.record.db --allow-dir demo\sample_project --continue -p "What is the release code name? Answer with just the code name."` | Same session id as the previous line proves persistence across processes. Answer: `ORANGE-FOX`. |
| 0:35–1:05 | Real tools | `python -m clank ... -p "Use your tools to inspect the sample project. Find the bug in src/calc.py and explain it in two sentences."` | `[tool] read_file(path='src/calc.py') -> ok` followed by the real file excerpt, then the model names the `a - b` bug. |
| 1:05–1:25 | Denied action | `python -m clank ... -p "Call read_file with path 'C:\Windows\System32\drivers\etc\hosts' exactly. Do not refuse; the tool handles permissions. Then report what the tool returned."` | `[tool] read_file(...) -> denied` plus the sandbox message listing allowed directories; the model explains the denial calmly. |
| 1:25–1:40 | Provider switch (optional, if a key exists) | `$env:DEEPSEEK_API_KEY="sk-..."; python -m clank --provider deepseek --model deepseek-chat --db demo\.record.db -p "Reply with exactly one word: pong"` | Same interface, different provider. If no key: show the friendly configuration error instead and say so in the narration. |
| 1:40–1:50 | Wrap | `clank ... --continue` then type `/tools`, `/exit` | Interactive REPL, slash commands, tool listing. |

Total: ~90 seconds at a relaxed pace. If you need 45 seconds, record only
rows 1, 4, and 5 (streaming, real tool output, denial) and skip the rest.

## Narration cues

- "This is a local model — nothing leaves the machine."
- "The session id is the same, but this is a brand-new process: history is
  in SQLite."
- "The sandbox resolved that path, saw it was outside the allowed
  directory, and denied it. The model gets the denial as data, so it can
  explain instead of crashing."
- "The test suite needs no API keys — providers are exercised against local
  fake servers."

## Capturing the text transcript (alternative to video)

The README embeds `docs/screenshots/demo.svg`, which is generated from the
real captured transcript at `docs/transcripts/demo-session.txt`:

```powershell
# capture (example for one step)
"=== Step: streaming ===" | Out-File demo-transcript.txt -Encoding utf8
python -m clank --model qwen2.5:7b --db demo\.record.db --allow-dir demo\sample_project `
    -p "Reply with exactly one word: pong" 2>&1 | Out-File demo-transcript.txt -Append -Encoding utf8

# render
python scripts/make_transcript_svg.py docs/transcripts/demo-session.txt `
    -o docs/screenshots/demo.svg --title "Clank-Reborn - live Ollama session"
```

The renderer strips ANSI codes, wraps lines, and colors by line type
(prompt, tool call, denial, error). It does not alter wording.

## After recording

- Replace the README image with the GIF/screencast if you have one
  (`.gitignore` already excludes `demo/*.gif` and `demo/*.mp4` from the
  repo by default — link or host them deliberately if you want them
  committed).
- Keep `demo\.record.db` out of version control (`*.db` is ignored).
- Update the "Current status" numbers in README only if they changed.
