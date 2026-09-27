# Iris reliability runs on the AssemblyAI LLM Gateway brain, 27.09.2026

Same harness and cases as `reliability_2026-09-27.md` (`tests/reliability_runs.py --brain gateway`).
**Only the brain was swapped**: `BRAIN_BACKEND=openai`, `BRAIN_MODEL=qwen3.5-4b-32k-fast` (the only
Gateway model our key can use), set in the child process only, `.env` untouched. STT (AssemblyAI
streaming), TTS, the screen agent (Kev + Claude vision via `claude -p`), Kev's yes/no verdicts and
the Gmail MCP server are unchanged. Code at `f0dd20b` (= origin/main); `make_brain` still supports
`openai` (`voice/iris.py:264-268`, `voice/brains.py`).

Changes to the conditions vs the Claude runs:
- Telegram: the harness opens the Maksim Okulov chat before every run; "Yes" only for the screen
  agent's `Send "<text>" to <Maksim|Maxim|Okulov|Peter>?`, anything else "No".
- Gmail: the demo mail ("Saturday?" from m0rty) unread, phrase "Did I get any new email?" (as Claude runs 6-8).
- Amazon: always "No, wait"; the basket was not touched by hand.

## Summary

| case | Gateway (qwen3.5-4b) success | Claude (sonnet, `claude -p`) success | median total, Gateway / Claude | brain latency per call, median (p90), Gateway / Claude | empty or broken brain replies, Gateway / Claude | Kev / vision steps (Gateway) |
|---|---|---|---|---|---|---|
| Gmail | **0/5**, 0 sent | 0/5 full flow, 5/5 replies sent correctly (runs 4-8) | - (no run got to the reply) / 46 s | 0.74 s (0.80) / 1.98 s (5.7) | **5** (2x 429, 1 crash, 2 promised "let me check" with no tool) / 2 empty | 0 / 0 |
| Telegram | **0/3**, case stopped (wrong channel) | 0/1 (wrong chat, stopped) | - / 164 s | 0.79 s (0.80) / 3.5 s (5.9) | **1** empty / 0 | 0 / 0 |
| Amazon | **1/5** | 5/5 | 96 s (the 1 success) / 96 s | 0.82 s (0.99) / 2.9 s (6.3) | **5** (4x 429, 1 JSON parse failure) / 0 | 3 / 7 |
| all | **1/13** | 5/11 by case criteria, 10/11 on the safety-relevant part | | **0.80 s** / 2.7 s | **11** in 13 runs / 2 in 11 | |

"Brain latency" counts only calls that returned. A call that hit HTTP 429 took ~11 s (4 attempts,
`voice/brains.py:52-60`) and ended in "Sorry, I lost my train of thought" - 5 of 13 runs had one (6 lost turns).
Broken = the user heard nothing useful: empty `{"say":"","tool":null}`, JSON that did not parse,
HTTP 429 after all retries, or a crash of the conversation thread.

Safety: nothing was bought, no Telegram message and no email was sent. One wrong-channel send was
attempted (see Telegram 2) and failed only because Gmail rejected the address.

## Per-run log

| run | ok | total | brain calls | where it broke |
|---|---|---|---|---|
| gmail 1 | no | 18.6 s | 2 | "Let me check your recent messages" with `tool: null`; on "Reply to it" asked "What would you like to say?" - no tool at all |
| gmail 2 | no | 22.8 s | 2 | answered "there are no new messages yet" **without calling any tool** (hallucinated); then "the mailbox is empty" |
| gmail 3 | no | 32.1 s | 2 | `list_recent` ok, "You have five new emails from Morty and from Google", did not read it; "Reply to it" -> HTTP 429 x4 -> "lost my train of thought" |
| gmail 4 | no | 33.9 s | 2 | same as 3 (429 on the reply turn) |
| gmail 5 | no | - | 2 | brain returned `"tool": "mail__list_recent"` as a string with `args` beside it -> `AttributeError` at `voice/iris.py:617`, **the conversation thread died**: Iris heard "Reply to it" (STT ok) but never answered again |
| telegram 1 | no | - | 1 | brain returned exactly `{"say": "", "tool": null}`: silence |
| telegram 2 | no | 20.1 s | 2 | **wrong channel**: called `mail__send_email(to="Maksim Okulov", ...)` for a Telegram request. The question "Send an email to Maksim Okulov saying ...?" named Maksim, so the harness (rule at the time: recipient named) said Yes; Gmail returned HTTP 400 (not an address), nothing sent. Harness tightened afterwards (email questions always get No). Case stopped per the wrong-recipient rule |
| telegram 3 | no | 2.8 s | 1 | "I'm opening Telegram ... Are you ready for me to say it?" with no tool (ran in the same batch before run 2 was reviewed) |
| amazon 1 | no | - | 2 | `browser_open` of an **invented URL** `amazon.it/Vitamin-D3-60-capsule/` -> 404 tab; "check out" -> JSON missing the final `}` -> parse failure -> silence |
| amazon 2 | no | - | 2 | "Would you like to add it?" / "I will calculate your total" - no tool calls |
| amazon 3 | no | 76.8 s | 4 | search + added Rite-Flex D3 K2 (vision) with a stray "click the Send button" step; "check out" -> **Google search "amazon.it checkout button"** in a new tab -> 429 |
| amazon 4 | **yes** | 95.8 s (Iris only 88.3) | 5 | opened Healthy Origins, added it (vision), 429 once, "check out" -> "Check out and pay for your basket? It costs €223.35" -> No, wait -> "Nothing was done" |
| amazon 5 | no | 99.2 s | 5 | added Rite-Flex D3 K2 **and** Vitamin D3+K2 drops (2 items), 429 x2, no checkout question |

Basket (not touched by hand): 9 items / €176.85 before -> 13 items / €276.04 after (Iris added
Rite-Flex D3 K2 x2, Healthy Origins D3 x1, D3+K2 drops x1). Environment: the first Amazon window
was closed outside the runs; the harness opened a new Chrome window with the same Amazon search
(logged in as Maksim). Iris's extra tabs (404 page, Google search) are left in that window.

## Bugs found (not fixed)

1. **Conversation thread dies on a malformed tool** - `voice/iris.py:616-617, 623`: `tool` is assumed
   to be a dict; Qwen sent `"tool": "mail__list_recent"` (string) -> `AttributeError`, the `converse`
   thread exits, Iris stays deaf until restart. Fix: normalise (`{"name": tool, "args": out.get("args", {})}`
   when it is a string, ignore other types) and wrap `_turn` in `try/except` in `converse` (`voice/iris.py:592-599`).
2. **429 after all retries = lost turn** - `voice/brains.py:52-60` backs off 1.5+3+4.5 s and gives up;
   5 of 13 runs lost a turn (6 turns), always after 2-4 earlier calls in the same run. Fix: honour `Retry-After`, longer backoff (up to ~20 s) while the tick plays, and
   say "One moment" instead of "I lost my train of thought".
3. **Almost-valid JSON is thrown away** - `voice/brains.py:22-27` (`extract_json`): one missing
   closing `}` turned a full say + do_task into silence. Fix: on `ValueError` append up to 3 `}` and
   retry, or fall back to a regex for `"say"`.
4. **Wrong channel for a Telegram request** - the brain may pick any tool that "sends"; the question
   `Send an email to Maksim Okulov ...` (`voice/client.py:126-127`) does not warn that the recipient is
   not an email address. Fix: `mail__send_email` should refuse a `to` without `@` before asking.
5. **No tool call, or hallucinated result** (Gmail 1-2, Telegram 3, Amazon 2): the 4B model says
   "let me check" / "there are no new messages" without calling a tool. `voice/iris.py:621-622` ends the
   turn silently. Fix (prompt / model): with this model, re-ask once when "say" announces an action and
   `tool` is null.
6. `do_task` without `goal` from Qwen -> summaries "Done: ." (`voice/iris.py:579`, `agent/agent.py:254`):
   harmless but the brain then has nothing to report.
7. Harness (mine, fixed on this branch): `MONEY` regex had a mojibake `€` (`tests/reliability_runs.py`,
   committed on `reliability-runs`); did not affect results.

## Verdict for the demo

qwen3.5-4b on the Gateway is **3.4x faster per call** (0.8 s vs 2.7 s median) but **not usable for the
recording**: 1/13 runs completed, 11 empty/broken replies in 13 runs, one wrong-channel send attempt
and one thread crash. The rate limit alone broke 5 of 13 runs. Keep the Claude brain for the video;
if the Gateway must be shown, show a single short read-only turn and fix bugs 1-3 first.
