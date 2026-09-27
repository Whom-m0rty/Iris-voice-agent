# Iris — a voice agent that operates Windows for people who can't use the screen

Built for the lablab.ai × AssemblyAI Voice Agent Hackathon (September 2026).
**Site:** https://whom-m0rty.github.io/Iris-voice-agent/ · **Deck:** [slides](https://whom-m0rty.github.io/Iris-voice-agent/slides/) ([PDF](https://whom-m0rty.github.io/Iris-voice-agent/slides/iris-pitch.pdf))

You say what you want; Iris does it on your PC, tells you what happened, and asks out loud
before anything that can't be undone. It is meant for blind users, and for anyone who finds
a computer confusing.

**It works where a screen reader gives up.** Screen readers read the accessibility tree; when
an app doesn't label its buttons, they hear "button, button, button". Iris needs nothing from
the app's developer: it uses an API when one exists, otherwise the controls Windows can see
and the keyboard, and, when you bring Claude, the pixels themselves.

## How it works

Out of the box (`IRIS_BRAIN=cloud`) your PC needs no keys and no GPU:

```
 Your PC (Windows)                    Iris Cloud (Oracle Ampere A1)         AssemblyAI
 ─────────────────                    ─────────────────────────────         ──────────
 Iris ── asks for a speech token ───► /v1/stt-token (holds the keys)
  │  ◄───────────── 10-minute token ──┘
  mic ── audio, straight to AssemblyAI ────────────────────────────────►  Universal-Streaming
  │  ◄──────────────────────────────── text, as you talk ──────────────────┘
  │
  ├─ plan ──────────────────────────► /v1/chat/completions ────────────►  LLM Gateway: Qwen 3.5 4B
  │  ◄──── one JSON decision per turn: what to say, which tool ◄──────────┘
  │
  ├─ API first: MCP tools (Gmail), opening apps, browser shortcuts
  ├─ the screen: UI Automation; a decision model picks each control
  │     ──────────────────────────────► /v1/systemone ─────────────────►  Jev (TypeSafe)
  │     (or Kev on your own GPU, ~230 ms; Claude vision only when Claude is the brain)
  ├─ the keyboard when nothing fits: Enter sends, Escape closes, Alt+Left goes back
  │
  ├─ safety gate: word list + the decision model ─► a spoken "yes", judged on your exact words
  └─ neural TTS back to you, sentence by sentence (you can talk over it)
```

- **Ears: AssemblyAI Universal-Streaming** (`voice/iris.py`, the main loop, used in the demo).
  The hackathon's "Realtime Speech-to-Text + your own LLM and TTS" path. Formatted turns;
  short pauses are merged into one turn, because people (older people especially) pause
  mid-sentence; how long a pause may be is set by voice (0.8–4 s) and remembered.
- **Brain: Qwen 3.5 4B on AssemblyAI's LLM Gateway** (`qwen3.5-4b-32k-fast`, ~0.8 s per plan),
  reached through Iris Cloud. One JSON decision per turn: what to say and which tool to call.
  Small-model replies are hardened: broken JSON is repaired, empty replies are asked again, rate
  limits back off. One line in `.env` swaps in Claude or any other Gateway model (below).
- **Iris Cloud** (`server/app.py`, FastAPI behind Caddy on an Oracle Ampere A1, aarch64) keeps
  our keys on the server. Each PC gets an anonymous token and a daily allowance; the server
  hands out short-lived AssemblyAI speech tokens and forwards brain and decision calls. Your
  audio goes straight to AssemblyAI, and the server stores usage counters, never what you say
  or what is on your screen. No model runs on it. Deploy your own with `server/deploy.sh`.
- **Hands, API first**: any MCP server becomes voice tools; the bundled Gmail server uses OAuth,
  and servers' own `readOnlyHint` / `destructiveHint` feed the safety gate. Iris also opens apps
  by name and jumps around the web with browser shortcuts.
- **Hands, on the screen**: UI Automation lists the controls; a System One decision model picks
  one per planned step: **Jev** (TypeSafe, through Iris Cloud) by default, or
  [**Kev**](https://github.com/jaredpalmer/kev), open weights, on your own NVIDIA GPU. With no
  vision, the keyboard covers the rest. With Claude as the brain, **Claude vision** looks at the
  window when the accessibility tree is not enough.
- **Second voice backend: the AssemblyAI Voice Agent API** (`voice/client.py`): speech,
  turn-taking, voice and the LLM in one connection, JSON-Schema client tools (`do_task`,
  `answer_confirmation`, `stop_task`, `mute_microphone`), Claude through the LLM Gateway in a
  stored agent. It works, but in our tests the live API sometimes ended a reply with no words
  and no tool call, so the demo runs on the streaming backend.

Around all of it:

- **Asks before acting**: anything that sends, pays, deletes or shares waits for a spoken yes,
  judged on your exact words; "uh… who is it for?" gets the question again, and a "no" ends the
  task.
- **Passwords never reach a model**: stored logins live in Windows Credential Manager and are
  typed by code into fields UI Automation marks as password fields.
- **Apps it never touches**: list them in `protected_apps.txt`; Iris won't read, type into or
  click them.
- **Made for people who cannot see the screen**: a soft tick while Iris thinks, a chime when
  an action is done, a low tone on an error. Ask "what's on my screen?", "what can I do here?",
  "what's in the photo?" (with Claude) or "I'm lost" (pop-ups are named first, never closed
  without asking). "Show me how" does a task slowly and explains each step; "say that again"
  repeats the last reply word for word. Iris speaks without computer words: "I opened your
  email", not "focused the tab".
- **Mute**: Ctrl+Alt+M from any app, the button on `voice/panel.html`, or "stop listening".
  A chime and a spoken line say which state you are in. Muted, the mic sends silence, so a
  pending yes/no can only end as a no. Unmuting needs the key or the button.

## Build it your way

Every part is one line in `.env`:

| Part | Options | Status |
|---|---|---|
| Ears | AssemblyAI Universal-Streaming (`voice/iris.py`) or the AssemblyAI Voice Agent API (`voice/client.py`); your own `ASSEMBLYAI_API_KEY`, or a token from Iris Cloud | both run; demo on streaming |
| Brain | `IRIS_BRAIN=cloud` (default: Qwen on the Gateway through Iris Cloud), `claude-code` (your Claude Code login), `anthropic` (your API key), `gateway` (your AssemblyAI key, any Gateway model via `BRAIN_MODEL`: Claude, GPT, Gemini, Qwen, DeepSeek… 47 listed), `openai` (any OpenAI-compatible endpoint: Ollama, vLLM) | Qwen: default; Claude: most reliable (see runs below) |
| Decisions | `IRIS_DECISIONS=auto` (local Kev if it runs, else Jev through Iris Cloud), `kev`, `jev` (your `JEV_KEY`), `cloud`; `SYSTEMONE_URL` overrides | Kev measured; Jev API-compatible, not benchmarked by us |
| Eyes | Claude vision, on automatically with `IRIS_BRAIN=claude-code` or `anthropic` (`VISION_BACKEND` overrides); none by default | measured with Claude |
| Hands | any MCP server becomes voice tools; the screen and the keyboard otherwise | Gmail tested |
| Voice | edge-tts neural voices, or AssemblyAI Voice Agent voices | both run |

## Measured (26.09, RTX 5060 Ti) — `bench/bench_full.py`

| | Result | Latency |
|---|---|---|
| Spoken yes / no / unclear (60 answers) | 97%, **0 false "yes"** | 129 ms |
| Safety gate on an unseen set (40 actions) | **0 risky actions let through**, 3 extra questions | 137 ms |
| Kev picks the control (25 steps, ~37 options) | 92%; misses fall back to vision, not to a wrong click | 234 ms |
| Claude vision on unlabeled icon buttons | 10 / 10 | 3.2 s |

The test sets were written by us; action choice was measured on Windows Calculator and local
test pages. These numbers measure the safety gate and the screen layer with Kev on a local GPU
and Claude vision; the default cloud setup uses Jev and no vision, and Jev has not been
benchmarked by us.

## End-to-end runs (27.09) — `tests/reliability_runs.py`

Whole voice loop on real services: synthesized speech into AssemblyAI streaming, the brain,
Gmail over MCP, Kev and Claude vision on the screen, spoken confirmations. Only the microphone
is replaced.

| Brain | Amazon: basket, checkout, "No, wait" | Gmail: read, reply, "Yes" | Telegram | Brain reply (median) |
|---|---|---|---|---|
| Claude (Claude Code) | **5 / 5**, nothing bought | reply reached the right person 5 / 5 | 0 / 1 (picked the wrong chat, asked, stopped on "no") | 2.7 s |
| AssemblyAI LLM Gateway, `qwen3.5-4b-32k-fast` | 1 / 5 | 0 / 5 | 0 / 3 | **0.8 s** |

> **What these runs are for.** They show that the whole pipeline works end to end, and they
> give anyone who installs Iris a way to try it on their own machine. They are not a claim of
> production reliability. Success depends mostly on the brain: a 4B model on the Gateway (the
> only one our hackathon account could use) is fast but often sends broken or empty replies;
> use Claude, or a larger Gateway model if your AssemblyAI plan includes one. Details, failure
> points and fixes: `bench/results/reliability_2026-09-27.md`,
> `bench/results/reliability_gateway_2026-09-27.md`. The failures found here are now handled:
> broken JSON is repaired, empty replies and "let me check" without a tool are asked again,
> rate limits back off, two failed screen tasks stop the turn, and a name is never accepted as
> an email address (`tests/brain_robustness_test.py`).

## Run

Windows 10/11 and a microphone. No keys and no GPU needed. In PowerShell:

```powershell
irm https://raw.githubusercontent.com/Whom-m0rty/Iris-voice-agent/main/bootstrap.ps1 | iex
```

This downloads Iris to `%LOCALAPPDATA%\Iris\app`, installs it (about 2 minutes) and puts an
**Iris** shortcut on the desktop. Start it, put on headphones, wait for "Hi, I'm Iris", and talk:
"Open the calculator", "What's on my screen?", "Find a video on how to bake bread".

### Pick the brain: one line in `.env`

| `IRIS_BRAIN=` | What runs | You need |
|---|---|---|
| `cloud` (default) | Qwen on the AssemblyAI LLM Gateway, Jev decisions, AssemblyAI speech, all through **Iris Cloud**. No vision: Iris works from the accessibility tree and the keyboard | nothing |
| `claude-code` | Claude as the brain and the eyes, through your Claude Code login (`claude -p`, personal use) | Claude Code |
| `anthropic` | Claude as the brain and the eyes, through the Anthropic API | `ANTHROPIC_API_KEY` |
| `gateway` | any LLM Gateway model your AssemblyAI plan includes (`BRAIN_MODEL=`) | `ASSEMBLYAI_API_KEY` |

Or at install time: `install.ps1 -Brain claude-code`. Decisions: `IRIS_DECISIONS=auto` uses Kev
when it runs locally (`install.ps1 -Local`, NVIDIA GPU with ~10 GB), else Jev through Iris
Cloud; `jev` with your own `JEV_KEY`. Details in `agent/cloud.py`.

**Iris Cloud** (`server/`, FastAPI behind Caddy) keeps our keys on the server. Each PC gets an
anonymous token and a daily allowance (40 voice sessions, 600 brain calls, 3000 decisions).
Your speech goes straight to AssemblyAI with a short-lived token; the server stores counters,
never what you say or what is on your screen. Deploy your own with `server/deploy.sh`.

### From source

`git clone` this repo, then `powershell -ExecutionPolicy Bypass -File install.ps1` (same
options) and `run_demo.ps1`. Optional: Gmail over MCP (put the Google OAuth client as
`client_secret.json`, set `MAIL_ADDRESS`, run `python voice/mcp_servers/mail.py login`); the
Voice Agent API backend (`python voice/setup_agent.py`, then `voice/client.py`).

## License

MIT. Kev is Apache-2.0 and is used as a separate dependency, not vendored.
