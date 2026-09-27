# Iris cloud mode (keyless default): fresh install + voice runs, 27.09.2026

Brain `qwen3.5-4b-32k-fast` via Iris Cloud (https://80-225-83-158.sslip.io), decisions Jev via
Iris Cloud, STT through a cloud token, no vision. Harness: `tests/reliability_runs.py --brain cloud`
(edge-tts speech as the mic, real STT/brain/tools/TTS; keys removed from the child's env, fresh
`LOCALAPPDATA` so a new device token is made). **Interrupted early** (the desktop was needed for the
demo recording): Amazon 0 runs, Gmail-in-browser 1 baseline run only, Telegram 2 of 3.

## Fresh install (as a judge)

| step | result |
|---|---|
| download + unpack main.zip | 5.5 s, 3.8 MB |
| `install.ps1 -NoShortcut` (cloud), warm uv cache | 3.2 s, exit 0, `.env` = `IRIS_BRAIN=cloud` |
| same, cold (empty uv cache, uv-managed Python downloaded) | 10.8 s, exit 0 (PySide6 73 MB is most of it) |
| first start, fresh LOCALAPPDATA, no keys | greeting spoken, `ready`, mic found; device token created |
| `bootstrap.ps1` (read, not run) | should work: `irm | iex` with its `param()` block tested OK; defaults to cloud |

What a judge hits:
1. **Rate limit of the LLM Gateway (the main problem).** The server's AssemblyAI key gets ~2 qwen
   requests per 40-60 s (measured directly: `200 200 429x8 200 200 429x10`, Retry-After up to 49 s).
   The server retries 15 s, the client up to 20 s more: a turn that hits it takes 20-46 s. The runs below got
   15 HTTP 429 responses over 34 brain calls. Only fixable on the server side (paid gateway tier, another
   model, or a queue); nothing in the client can remove it.
2. `LIMIT_DEVICES_PER_IP=5`: a venue or office behind one IP gets 5 installs a day; before the fix
   the 6th crashed at start with a Python traceback and said nothing.
3. `protected_apps.txt` is created from the example, which protects nothing (expected).
4. The mail MCP always started, so a keyless install was offered `mail__*` tools that can only fail
   (no Gmail login) - fixed.
5. "Ctrl+Alt+M is taken by another app" at start on this machine (the hotkey is held elsewhere here).

## Runs (cloud brain)

Baseline = `origin/main` 3eeb689 (installed copy). Fixed = branch `cloud-runs`.

| case | code | success | median total, s | brain calls: median ms (only calls that returned) | broken/empty replies | 429s | keyboard fallbacks |
|---|---|---|---|---|---|---|---|
| 1 calculator: open + 12+30 | baseline | 0/1 | 12.3 | 683 | 0 (but "I have opened the calculator" with no tool) | 0 | 0 |
| 1 calculator | fixed | **2/3** | 91.6 | 931 | 0 | 7 | 0 |
| 2 Telegram: open + tell Maksim | baseline | 0/1 | - | 683 | 1 empty after a 429 -> "I have sent the message" (false) | 1 | 0 |
| 2 Telegram | fixed | 0/2 | 49 | 738 | 0 | 2 | 0 |
| 3 Amazon basket / checkout / "No, wait" | - | not run | | | | | |
| 4 Gmail in the browser, no keys | baseline | 0/1 | - | 24848 (one call, with a 429) | invented "a message today from Peter" with no tool | 1 | 0 |
| 5 What's on my screen? | baseline | 0/1 | 1.6 | 791 | answered from the window list ("your email has new messages", browser in front) | 0 | 0 |
| 5 What's on my screen? | fixed | **3/3** | 27.0 | 713 | 0 | 4 | 0 |

`open_app` always picked the portable Telegram (`TelegramDesktopPortable was already open`), never
the protected one. No message was sent in any run (0 of max 3), nothing bought, no tabs left open.

Failures (fixed code):
- calc 2: brain said "Let me show you how", nudged, then answered "42" from its own head; the
  calculator was never pressed (the display showed 0). Correct number, wrong way.
- tgopen 1: `do_task(window="Telegram")` went to the Explorer window "Telegram Desktop - File
  Explorer" (title match beat the Telegram process, whose title is the chat name) - fixed after this
  run; also the plan had no Send step and dropped "test 1" from the text.
- tgopen 2: brain misheard its own job: "Tell Maxim I'm running late. Test 2." -> "I need to know
  which test you are referring to." (no tool). Small-model failure; not fixed.

Latency: when no 429 hits, a brain call is 0.35-0.8 s and "What's on my screen?" is answered in
1.9 s; calc with the six keypad presses (Jev, ~0.35 s each) is ~8 s of screen work. Everything else
in the medians above is the rate limit.

## Fixes on `cloud-runs`

| file | change | why |
|---|---|---|
| voice/iris.py | claim guard: "I have opened/sent/added/checked..." with no tool run in the turn -> ask once for the tool, never speak the claim | qwen said "I have opened the calculator" and "I have sent the message to Maxim" with no tool call |
| voice/iris.py | "what's on my screen / where am I / what can I do here / I'm lost" run describe_screen first and give the result to the brain in the same message | qwen described the screen from the window list (0/1 -> 3/3), and it saves a round trip |
| voice/iris.py | the same describe/read tool again with nothing done in between is dropped | qwen repeated describe_screen 2-3 times per turn (each one a rate-limited call) |
| voice/iris.py | `PROMISED` no longer matches "you are looking at..." | a correct answer was treated as a broken promise -> extra calls, 87 s |
| voice/iris.py | few-shot examples for non-Claude brains (open_app, calculator keypad, describe_screen, Telegram send, shop search, Gmail via browser) | a 4B model copies examples much better than it follows rules; Claude's prompt is unchanged |
| voice/iris.py | no mail tools -> prompt says: open Gmail with browser_open, read it with read_screen, newest at the top | cloud mode has no mail MCP; qwen invented an email |
| voice/iris.py | "Peter's message is on the screen now" example -> "the message ..." | qwen reused "Peter" as a made-up sender |
| voice/iris.py | mail MCP started only when a Gmail login is stored | keyless installs were offered mail tools that always fail |
| voice/iris.py | "tell/send/reply" plan that only types -> "click the Send button" appended (still confirmed) | qwen planned typing only; the message would sit unsent |
| voice/iris.py + agent/cloud.py | brain start failure (no network, device limit 429) is spoken, then a clean exit | was a silent traceback |
| voice/iris.py + agent/cloud.py | STT token refused (429) -> said once, retried every 60 s | `HTTPError` is an `OSError`: it looped every second in silence |
| voice/brains.py | `on_wait` hook: "One moment, I'm a little busy." on the first 429 | 20-46 s of ticks with no explanation |
| agent/screen.py | `resolve_window`: a window of the app named (process) beats a title that merely mentions it, for 1-2 word names | "Telegram" resolved to an Explorer folder window |
| agent/look.py | a Store app's own inner frame is not reported as a pop-up | Calculator was described as having a "Calculator" pop-up |
| tests/reliability_runs.py | `--brain cloud`, cases calc / tgopen / gmailweb / screen, 429 counting, Amazon in a new tab of its own, tabs opened in a run are closed after it, max 3 Telegram messages | the cloud-mode runs |

## Still broken / not done
- Gateway rate limit (server side): makes every multi-step task 1-2 minutes. Top priority.
- Amazon: not run in cloud mode. Gmail via browser: only the baseline run (the new prompt path is untested).
- Telegram 0/2: the resolve fix and the Send-step fix are untested end to end.
- qwen sometimes answers arithmetic itself instead of pressing the keypad (calc 2).
- The window list in the brain prompt is built once at start; windows opened later are unknown to it.
