# Iris — a voice agent that operates Windows for people who can't use the screen

Built for the lablab.ai × AssemblyAI Voice Agent Hackathon (September 2026). Work in progress.

You say what you want; Iris does it on your PC, tells you what happened, and asks out loud
before anything that can't be undone. It is meant for blind users — and for anyone who finds
a computer confusing.

**It works where a screen reader gives up.** Screen readers read the accessibility tree; when
an app doesn't label its buttons, they hear "button, button, button". Iris reads the tree when
it is there and looks at the pixels when it isn't.

## How it works

```
mic ─► AssemblyAI Voice Agent API (speech, turn-taking, voice; Claude via LLM Gateway plans)
          │ client-side tool do_task(goal, window, steps)   │ MCP tools (e.g. Gmail)
          ▼                                                   ▼
   Screen agent: Kev (local, ~230 ms) picks each control     API first, when one exists
   from UI Automation ──(nothing fits)──► Claude vision
          │
   Safety gate: word list + Kev ─► spoken "yes" required, judged on the user's exact words
```

- **Voice**: AssemblyAI Voice Agent API. Tools answer at once and the task runs in the
  background; progress and results are spoken via `reply.create`, and a task log lives in the
  system prompt.
- **Fast path**: [Kev](https://github.com/jaredpalmer/kev), an open-weights System One decision
  model (TypeSafe `/v1/systemone` API), runs locally and picks one control per planned step.
- **Vision fallback**: Claude looks at the window when the accessibility tree is not enough.
- **API first**: any MCP server becomes voice tools; the bundled Gmail server uses OAuth.
- **Passwords never reach a model**: stored logins live in Windows Credential Manager and are
  typed by code into fields UI Automation marks as password fields.

## Measured (26.09, RTX 5060 Ti) — `bench/bench_full.py`

| | Result | Latency |
|---|---|---|
| Spoken yes / no / unclear (60 answers) | 97%, **0 false "yes"** | 129 ms |
| Safety gate on an unseen set (40 actions) | **0 risky actions let through**, 3 extra questions | 137 ms |
| Kev picks the control (25 steps, ~37 options) | 92%; misses fall back to vision, not to a wrong click | 234 ms |
| Claude vision on unlabeled icon buttons | 10 / 10 | 3.2 s |

The test sets were written by us; action choice was measured on Windows Calculator and local
test pages.

## Run

Windows 10/11, Python 3.12+, an NVIDIA GPU with ~10 GB free for Kev.

1. `git clone https://github.com/jaredpalmer/kev` into `kev/`, then `uv sync --extra serve`
   and install a CUDA build of torch into `kev/.venv`.
2. `.env` in the repo root: `ASSEMBLYAI_API_KEY=...`; for vision either
   `CLAUDE_CODE_OAUTH_TOKEN=...` (local `claude -p`, personal use) or `VISION_BACKEND=api` with
   `ANTHROPIC_API_KEY=...`.
3. `python voice/setup_agent.py` — creates the AssemblyAI agent with Claude as its LLM.
4. Optional Gmail: put the Google OAuth client as `client_secret.json`, set `MAIL_ADDRESS`,
   run `python voice/mcp_servers/mail.py login`.
5. `run_demo.ps1` — starts Kev, the cursor overlay, the observer panel and the voice session.

## License

MIT. Kev is Apache-2.0 and is used as a separate dependency, not vendored.
