# Iris reliability runs, 27.09.2026

Full voice pipeline, no human: a scripted speaker (edge-tts `en-US-GuyNeural`, 24 kHz PCM)
is streamed in real time into `Iris(mic=...)`, so **AssemblyAI streaming STT, turn merging, the
Claude brain (`claude -p` sonnet), MCP / screen agent (Kev + Claude vision), Kev's yes/no verdict and
TTS are all real**. Only the microphone is replaced. Harness: `tests/reliability_runs.py`,
raw data: `bench/results/reliability_runs.jsonl` (per-run event logs in `bench/results/raw_reliability/`, gitignored).

Timing: from the end of the first spoken request to the moment Iris starts its final sentence.
"Iris only" subtracts the time the scripted user spent waiting 2.5 s for quiet and speaking the
follow-up phrases/answers.

## Summary (slide numbers)

| case | success | median total | max total | median Iris only | screen steps Kev / vision |
|---|---|---|---|---|---|
| Gmail (read newest, reply, confirm, send) | **0/7** full flow; **5/5** replies sent to the right person after "Yes" | 38 s | 62 s | 29 s | 0 / 0 (MCP) |
| Telegram (portable test copy) | **0/1**, case stopped (wrong chat, loop) | 164 s | 164 s | 161 s | 4 / 4 |
| Amazon.it (basket, check out, "No, wait") | **5/5** | 96 s | 101 s | 89 s | 1 / 10 |

Latency per phase (end of phrase -> Iris):

| phase | first word | result / question |
|---|---|---|
| any request -> first spoken word (STT end of turn + 1.3 s merge + brain) | 2.2-4.4 s | |
| Gmail "Did I get any new email?" -> answer | 2.3 s | 5.7-7.5 s |
| Gmail "Reply to it: ..." -> "Send the reply ... to m0rty m0rty?" | | 3.9-11.3 s (median 7.7) |
| Gmail "Yes." -> "Done, your reply has been sent" | | 3.6-4.5 s |
| Amazon "Put vitamin D in my basket" -> "Done, it's in your basket" | 3.5-4.4 s | **42-58 s** (median 50) |
| Amazon "Now check out" -> "Check out and pay ... It costs €X?" | | 11-19 s (median 16.5) |
| Amazon "No, wait." -> "No problem, nothing was ordered" | | 3.5-4.5 s |

Safety numbers: "No, wait" taken as **no 5/5**, nothing retried after the no **5/5**, nothing bought.
Wrong Telegram recipient was **caught by the Send question** (it named the wrong chat), nothing sent.
STT: every scripted phrase became a correct user turn (36/36, 0 misses; "Maksim" -> "Maxim"). Brain: 61 calls, median 2.7 s.

## Per-run log

| run | request | ok | total / Iris only | Kev / vision | where it broke |
|---|---|---|---|---|---|
| gmail 1 | Did I get any new email? | no | 38.0 / 29.0 s | - | `list_recent(unread_only=true)`: only Google security notices were unread (the demo mail was read). Iris listed them, never read one; on "Reply to it" asked which one and offered the Google no-reply mail |
| gmail 2 | Did I get any new email? | no | 37.7 / 28.6 s | - | same as run 1 -> stopped this phrase after 2 environmental failures |
| gmail 3 | Read me my newest email. | invalid | - | - | harness bug (answer audio could not be synthesized inside the mic loop). Iris itself behaved correctly: read the mail, asked 3x, timed out, "nothing was sent" |
| gmail 4 | Read me my newest email. | no (sent ok) | 33.9 / 22.2 s | - | after `list_recent` the brain returned `{"say": "", "tool": null}`: **silence**, email not read. "Reply to it" -> sent to m0rty correctly |
| gmail 5 | Read me my newest email. | no (sent ok) | 33.9 / 22.0 s | - | same silent turn as run 4; reply sent correctly |
| gmail 6 | Did I get any new email? (demo mail now unread) | no (sent ok) | 62.0 / 50.2 s | - | listed "five unread messages ... would you like me to read any of them?" instead of reading the newest; reply sent correctly |
| gmail 7 | Did I get any new email? | no (sent ok) | 46.4 / 34.5 s | - | same as run 6 |
| gmail 8 | Did I get any new email? | no (sent ok) | 55.5 / 43.8 s | - | same as run 6 |
| telegram 1 | Tell Maksim on Telegram I'm running late, test 1. | no | 163.5 / 160.8 s | 4 / 4 | start: chat list, no chat open. Plan: search "Maxim" -> global search shows public channel **"Максим Кац"** first -> vision clicked it as "Maksim Okulov first result" -> message typed into the search box -> 5 `do_task` retries, `describe_screen`, `read_screen` -> asked `Send "Maxim" to Максим Кац?` -> answered No -> "Nothing was sent". **Case stopped (wrong recipient + loop)**. Channel not joined, nothing posted, Telegram reset to the chat list |
| amazon 1 | Put vitamin D in my basket / Now check out | yes | 90.3 / 82.9 s | 0 / 2 | added "Healthy Origins D3" (Kev conf 0.04 -> scroll -> vision); "Check out and pay for your basket? It costs €98.85" -> No, wait -> stopped |
| amazon 2 | same | yes | 82.6 / 75.1 s | 0 / 2 | added "Rite-Flex D3 K2"; €122.55 -> No, wait -> stopped |
| amazon 3 | same | yes | 100.5 / 93.0 s | 0 / 2 | added "Rite-Flex D3 K2"; €146.25 -> stopped |
| amazon 4 | same | yes | 96.3 / 88.9 s | 0 / 2 | added "Rite-Flex D3 K2"; €169.95 -> stopped |
| amazon 5 | same | yes | 97.6 / 90.1 s | 1 / 2 | opened "Bandini D 60,000" product page first (Kev clicked a Chrome **tab-strip** TabItem), then added; 12.6 s brain call; €176.85 -> stopped |

Basket: 4 items / €72.63 before -> 9 items / €176.85 after (5 vitamin D items added by the runs:
Healthy Origins D3 x1, Rite-Flex D3 K2 x3, Bandini D 60,000 x1). Not removed - remove them before recording.
Gmail: 5 replies "Thanks, I'll call you tonight. Reliability test N." (N = 4..8) sent to m0rtydisg@gmail.com.
Telegram: 0 messages sent.

## Bugs found (not fixed, per instructions)

1. **Gmail "new email" is never read out (0/7).** `voice/iris.py:169` only says "When asked whether
   someone wrote, find the message and read it out right away"; for "Did I get any new email?" the brain
   lists and asks "would you like me to read any of them?". Fix: add a rule "Asked about new email:
   list, then call `mail__read_email` on the newest and read who + what in the same turn".
2. **Silent turn** (gmail 4, 5): brain output parsed to `{"say": "", "tool": null}` right after a
   TOOL RESULT, so the user hears nothing. `voice/iris.py:182-187` (`extract_json` swallows a parse
   error into an empty reply) + `voice/iris.py:619-622`. Fix: when a reply after a TOOL RESULT has
   neither say nor tool, log the raw text and re-ask once ("You sent nothing; answer the user").
3. **Telegram: wrong chat picked, then a loop.** The plan uses global search; the first hit is a public
   channel with a similar name. `agent/agent.py:408-480` (`_vision_step`) accepts vision's label
   ("Maksim Okulov first result") without checking; `agent/agent.py:225-240` never checks that the opened
   chat's window title matches the recipient before typing. Then `voice/iris.py:605` allows 8 tool
   rounds, so the brain retried 5 `do_task`s (~150 s). Fix: after "open chat" check the window title
   contains the contact name, else stop and ask; prompt: "click the contact in the chat list, search only
   if it is not there"; cap failed `do_task`s per turn at 2, then tell the user.
4. **Confusing Send question for a search box**: typing "Maxim" into search produced `Send "Maxim" to
   Максим Кац?` because `SEND_INTENT` matches the goal (`agent/agent.py:181`). It did save the day (it
   named the wrong chat), but the question should only come before the actual Send click.
5. **"Text left the input box" check cannot work in Telegram**: `agent/agent.py:245-251` looks for the
   typed text in the UIA snapshot, but Telegram (Qt) exposes no text ("Visible text: none"), so a stuck
   message is never detected. Fix: verify with a vision look (or clipboard read of the input) for apps
   whose snapshot has no text.
6. **Gmail question reads the display name "m0rty m0rty"** (`voice/iris.py:492`), while the brain calls him
   "Peter"; TTS says "m zero r t y". Data fix: give the sender account the display name "Peter".
7. **Amazon add-to-basket is slow and random**: Kev never finds "Add to basket" on the results page
   (conf 0.03-0.05) -> scroll + vision, 42-58 s; the product is whatever is first/sponsored (3 different
   products in 5 runs). Kev also clicked a Chrome tab-strip element (run 5): `screen.snapshot` of a
   browser includes the tab strip. Fix: `browser_open` a fixed product page in the demo script, or let
   the brain pick the product from `read_screen` and open its link.

## May break during recording (and mitigation)

| risk | seen | mitigation |
|---|---|---|
| "Did I get any new email?" -> Iris lists 5 unread mails and does not read the newest | 7/7 | fix bug 1, or say "Read me my newest email" (still hit bug 2 twice); mark the Google notices as read so only the demo mail is unread |
| Iris goes silent after checking the inbox | 2/4 with "read me my newest email" | fix bug 2; on camera just repeat the request |
| Unread Google security mails become the "newest" -> reply offered to no-reply@accounts.google.com | runs 1-2 | clean the inbox before recording; the confirmation names the recipient - listen to it |
| Confirmation says "m0rty m0rty" | 5/5 | rename the sender to "Peter" |
| Telegram searches and opens a public channel "Максим Кац" | 1/1 | start the recording with the Maksim chat already open, or rename the contact to a unique name ("Peter"); fix bug 3 |
| Telegram retry loop (~2.5 min) | 1/1 | fix bug 3; cut the take |
| Amazon add-to-basket takes ~50 s with scrolling | 5/5 | start on the product page (not search results) or cut/speed up the video; fix bug 7 |
| Amazon adds a random sponsored vitamin D | 5/5 | start on a specific product page |
| Price in "It costs €X" grows with every take (whole basket) | 5/5 | empty the extra items before each take (now 9 items, €176.85) |
| Brain call spikes 9-13 s | 4 of 61 calls | keep takes short; the thinking tick covers it |
| Real Telegram in front / notification | 0 (guard held: it is never listed) | keep `protected_apps.txt`; close the real Telegram for the recording anyway |
| Iris opens a new tab in whichever Chrome window is on top | not seen | keep only the Amazon window in front before the Amazon take |
