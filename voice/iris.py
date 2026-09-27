"""Iris, main voice loop: AssemblyAI Universal-Streaming (ears) -> Claude (brain) -> neural TTS (voice).

This is the "Realtime Speech-to-Text + your own LLM and TTS" path of the hackathon. The
other backend, client.py, runs everything on AssemblyAI's Voice Agent API.

  ears   AssemblyAI streaming STT v3, formatted turns; short pauses are merged, because
         people - older people especially - pause mid-sentence
  brain  a warm `claude -p` session (local Claude Code login, personal use) or the Anthropic
         API (BRAIN_BACKEND=api); it answers with one JSON object per turn: what to say and
         which tool to call
  hands  MCP tools (Gmail...) when an API exists, otherwise the screen agent (Kev + Claude vision)
  voice  edge-tts neural voice, sentence by sentence; the user can talk over it

Safety: risky actions are confirmed out loud, and the answer is judged by Kev on the
user's exact words. Passwords never reach a model.

Accessibility: a soft tick while Iris thinks, a chime when an action is done, a low tone on
an error; "what's on my screen", "what can I do here", "I'm lost" and picture descriptions
(agent/look.py); "show me how" runs a task slowly, narrating each step; "say that again"
repeats the last reply without asking the brain; the pause that ends a turn is adjustable
by voice and remembered (iris_prefs.json).

Mute: Ctrl+Alt+M, the button on panel.html, or "stop listening". Muted, the mic sends silence
and any turn still in flight is dropped, so nothing reaches the brain and a pending yes/no can
only time out as a no. Iris keeps talking and a running task keeps going.

Run:  python iris.py
"""
import asyncio
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

import sounddevice as sd
import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent"))
import env  # noqa: E402,F401  (loads ../.env)
import apps  # noqa: E402
import browser  # noqa: E402
import screen  # noqa: E402
import vault  # noqa: E402
import look  # noqa: E402
import vision  # noqa: E402
from agent import Agent, interpret_confirmation  # noqa: E402
from client import (CHIME_LISTENING, CHIME_MUTED, MCP_SERVERS, Speaker, _chime,  # noqa: E402
                    describe_mcp_action, watch_hotkey)
from mcp_bridge import MCPBridge  # noqa: E402

RATE = 24000
STT_URL = ("wss://streaming.assemblyai.com/v3/ws?sample_rate=24000&format_turns=true"
           f"&speech_model={os.environ.get('STT_MODEL', 'universal-3-6-pro')}")
CHUNK_MS = 100                                  # AssemblyAI wants 50-1000 ms per message
MERGE_S = float(os.environ.get("MERGE_PAUSE_S", "1.3"))   # a pause shorter than this continues the turn
PAUSE_RANGE = (0.8, 4.0)                        # what "wait longer for me" may set it to
PREFS_FILE = os.path.join(HERE, "..", "iris_prefs.json")
# earcons: silence is confusing when you cannot see the screen
TICK = _chime((1320,), note_ms=28, volume=2600)            # soft, once a second while thinking
DONE = _chime((784, 988, 1175), note_ms=80, volume=6000)   # rising: the action is done
ERROR = _chime((262, 196), note_ms=160, volume=7000)       # low, falling: something went wrong
TICK_AFTER_S = 0.7                              # no tick for answers faster than this
ACTION_TOOLS = {"do_task", "browser_search", "browser_open", "browser_back", "switch_to", "open_app"}
FAILED = re.compile(r"^(ERROR|Something went wrong|Stopped at|No browser|I could not|.* is not open on the screen)", re.I)
# the whole turn must be the request: "pardon me, open my email" is a request, not a repeat
REPEAT = re.compile(r"^\W*((sorry|excuse me|pardon|what)\W+)?(say (that|it) again|repeat( that| it| again)*|"
                    r"what did you (just )?say|come again|pardon( me)?|i didn'?t (hear|catch) (that|you))"
                    r"\W*(please)?\W*$", re.I)
# sonnet: measured 26-27.09 - barely slower than haiku here, but haiku misread the screen
# ("12 x 3 = 36" while it showed 1 x 3 = 3) and made sloppier plans
BRAIN_MODEL = os.environ.get("BRAIN_MODEL", "sonnet")
TTS_VOICE = os.environ.get("TTS_VOICE", "en-US-AvaMultilingualNeural")
MIC_NAME = os.environ.get("MIC_NAME", "K66")        # input device, matched by name


def load_prefs() -> dict:
    try:
        with open(PREFS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_prefs(prefs: dict) -> None:
    with open(PREFS_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=1)


def mic_device() -> int | None:
    """Index of the input device whose name contains MIC_NAME; None = system default."""
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and MIC_NAME.lower() in d["name"].lower():
            return i
    return None
# tool results that mean the user refused; the turn ends there, whatever the brain wants next
USER_SAID_NO = re.compile(r"Cancelled: I did not|did not confirm|NOT done|Stopped because you asked", re.I)
STOP_WORDS = re.compile(r"^\W*(stop|cancel|wait|hold on|never mind)\b", re.I)

BRAIN_PROMPT = """You are the brain of Iris, a calm voice assistant that operates a Windows PC for a person who
cannot see the screen or is not comfortable with computers.

How you talk: short, warm, plain sentences, like a patient friend sitting next to them.
This is for "say" and "explain", the only text the user hears. Tool names, arguments and
steps keep the technical words (window, click, scroll): the user never hears those.
In what you say, never use computer words. Instead of window, app or browser, name the
thing: "your email", "the internet", "YouTube". Instead of scroll: "further down" or
"further up". Instead of menu: "the list of choices". Instead of pop-up or dialog: "a box
has come up asking...". Instead of field: "the box for your password". Never say click,
tab, icon, cursor or URL.
Say what happened to the person's things: "I opened your email", "Peter's message is on
the screen now", "I pressed Send".

Every reply is ONE JSON object and nothing else:
{"say": "<what to say now, one or two short plain sentences, or empty>",
 "tool": null or {"name": "<tool>", "args": {...}}}
After a tool runs you get a message starting with TOOL RESULT; answer it with the next JSON.
When the work is done, set "tool" to null and tell the user the outcome in "say".

Tools:
{TOOLS}
- do_task(goal, window, steps): act on the screen when no direct tool fits. window = one of the
  open windows below. steps = small single actions [{"do": "..."}], for typing add "text", for a
  stored password add "secret": "<login name>" (never put a password in text).
  The app must already be open (open_app first if it is not): never add a step to open it. Name the control: "press the digit 7",
  "click the Reply button", "type the message into the message box". A typing step always
  carries "text". To send, the last
  step is "click the Send button" - never press Enter. Keypads: one key per step.
- stop_task(): stop the running task.
- mute_microphone(): stop listening, when the user asks you to. After it you hear nothing until
  they hold Control and Alt, and press M.
- browser_search(site, query): open search results on a site in the user's browser. site is one
  of: google, youtube, amazon.it, amazon.com, wikipedia.
- browser_open(url): open an address in the user's browser (a product page, a known site).
- browser_back(): go back one page in the browser.
- read_screen(window): the text a window shows now, to answer questions about it ("how much is
  it?", "what does the page say?") without clicking anything.
- describe_screen(window, detail, question): tell the user what is in front of them.
  detail "brief" for "what's on my screen?" / "where am I?"; "actions" for "what can I do
  here?"; "pictures" for "what's in the photo?" (question = what they want to know);
  "lost" for "I'm lost" / "what happened?". window may be empty: the one in front.
- switch_to(window): bring one of the open windows to the front ("go back to my email").
- open_app(name): start a program by name ("Telegram", "calculator", "the browser", "Word"),
  or bring it to the front if it is already open. Use it whenever the app the user needs is
  not in the open windows below, then continue with do_task in its window.
- set_pause(seconds): how long you wait before deciding the user finished speaking (now
  {PAUSE} s, allowed 0.8 to 4). Raise it by about 1 s when they say you cut them off or they need
  more time; lower it when they say you are slow to answer.
- do_task can also teach: add "teach": true when the user asks to be shown how ("show me how
  to reply"), and give EVERY step an "explain" (required when teach is true): one plain
  sentence saying what you do and where it is ("Now I press Reply, the arrow at the top right of the message."). It runs slowly.

Rules:
- Prefer direct tools (mail__...) when one fits: they are faster and reliable.
- On the web, jump instead of clicking through menus: browser_search / browser_open first, then
  do_task only for the clicks that are left (open a result, add to basket, press play).
- To answer a question about what is on the screen, use read_screen, not do_task.
- Describing the screen: say where they are first, pop-ups before anything else, then at
  most three things that matter. Never read out menus or toolbars. Offer one next step.
  "What can I do here?": name the three most useful things, as things they can ask you for.
  "I'm lost": say calmly where they are; if a pop-up is open, say what it asks and offer to
  close it or answer it; offer to go back to one of their other windows. Never close or
  answer a pop-up without asking.
- When a message or page has a photo or picture, say so, and describe it with describe_screen
  "pictures" when the user asks or when the picture is the point of the message.
- Password fields: say there is one, never what is in it.
- Scrolling: a do_task step can be "scroll down", "scroll up", "scroll down to the bottom".
- When asked whether someone wrote, find the message and read it out right away: who, and what it says.
- When the user dictates a message, use all of their words.
- Risky actions (sending, paying, deleting) are confirmed with the user by the app itself; just call the tool.
- If the user said no, that is final: never retry the same thing another way. Confirm nothing was done.
- Results come from the screen: read numbers and outcomes from "Screen now shows" in the
  TOOL RESULT (the main display, not history lists). Never compute, remember or guess them.
  If the screen does not show what the user asked for, say so plainly.
- Never claim something happened unless a TOOL RESULT says so. If it says NOT sent or
  Stopped, tell the user plainly that it did not happen.
- Stored login names: {SECRETS}
- Windows open right now: {WINDOWS}"""


from brains import extract_json, normalize_tool  # noqa: E402  shared with the other brains

# the brain said it would act ("let me check") but sent no tool
PROMISED = re.compile(r"\b(let me|i'll|i will|i'm going to|checking|looking|one moment|opening)\b", re.I)


class Brain:
    """One warm `claude -p` session in stream-json mode; the conversation lives in its context."""

    def __init__(self, prompt: str):
        self.prompt_file = os.path.join(tempfile.gettempdir(), "iris_brain_prompt.txt")
        self.prompt = prompt
        self.proc = None
        self.turns = 0
        self.lock = threading.Lock()          # warm-up and the conversation share one process

    def _spawn(self):
        with open(self.prompt_file, "w", encoding="utf-8") as f:
            f.write(self.prompt)
        self.proc = subprocess.Popen(
            ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
             "--tools", "", "--model", BRAIN_MODEL, "--no-session-persistence",
             "--system-prompt-file", self.prompt_file],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", shell=True)
        self.turns = 0

    def warm(self):
        """Start the CLI and send one throwaway turn: the first real answer would otherwise
        take ~8 s instead of ~2.5 s."""
        if self.proc is None or self.proc.poll() is not None:
            self._spawn()
        try:
            self.ask('SYSTEM CHECK: reply exactly {"say": "", "tool": null}')
        except Exception:
            pass

    def ask(self, text: str) -> tuple[dict, float]:
        with self.lock:
            return self._ask(text)

    def _ask(self, text: str) -> tuple[dict, float]:
        if self.proc is None or self.proc.poll() is not None or self.turns > 60:
            self._spawn()
        t = time.perf_counter()
        self.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
        self.proc.stdin.flush()
        self.turns += 1
        for line in self.proc.stdout:
            ev = json.loads(line)
            if ev.get("type") == "result":
                if ev.get("is_error"):
                    raise RuntimeError(ev.get("result"))
                return extract_json(ev.get("result") or ""), (time.perf_counter() - t) * 1000
        raise RuntimeError("brain process exited")


class APIBrain:
    """Same contract as Brain, on the Anthropic API (BRAIN_BACKEND=api, ANTHROPIC_API_KEY)."""

    def __init__(self, prompt: str):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = os.environ.get("BRAIN_API_MODEL", "claude-haiku-4-5-20251001")
        self.prompt = prompt
        self.history: list[dict] = []

    def warm(self):
        pass

    def ask(self, text: str) -> tuple[dict, float]:
        t = time.perf_counter()
        self.history.append({"role": "user", "content": text})
        r = self.client.messages.create(model=self.model, max_tokens=600, system=self.prompt,
                                        messages=self.history[-40:])
        reply = r.content[0].text
        self.history.append({"role": "assistant", "content": reply})
        return extract_json(reply), (time.perf_counter() - t) * 1000


def make_brain(prompt: str):
    backend = os.environ.get("BRAIN_BACKEND", "cli")
    if backend == "openai":                   # any OpenAI-compatible endpoint, AssemblyAI LLM Gateway by default
        from brains import OpenAICompatibleBrain
        return OpenAICompatibleBrain(prompt)
    return APIBrain(prompt) if backend == "api" else Brain(prompt)


class Voice:
    """Neural TTS, sentence by sentence, so the first words play while the rest is synthesized."""

    def __init__(self, speaker: Speaker):
        self.speaker = speaker
        self.interrupted = threading.Event()

    def _synth(self, text: str) -> bytes:
        import edge_tts
        import miniaudio

        async def run():
            buf = b""
            async for chunk in edge_tts.Communicate(text, TTS_VOICE).stream():
                if chunk["type"] == "audio":
                    buf += chunk["data"]
            return buf
        mp3 = asyncio.run(run())
        pcm = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=RATE)
        return pcm.samples.tobytes()

    def say(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        self.interrupted.clear()
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s]
        audio: dict[int, bytes] = {}

        def synth(i, s):
            try:
                audio[i] = self._synth(s)
            except Exception:
                audio[i] = b""
        threads = [threading.Thread(target=synth, args=(i, s), daemon=True) for i, s in enumerate(sentences)]
        for th in threads:
            th.start()
        for i, th in enumerate(threads):
            th.join()
            if self.interrupted.is_set():
                return
            self.speaker.play(audio.get(i, b""))
            while self.speaker.buf and not self.interrupted.is_set():
                time.sleep(0.03)

    def stop(self):
        self.interrupted.set()
        self.speaker.flush()


class Iris:
    def __init__(self, emit=print, mcp_servers=None, mic=None):
        self.emit = emit
        self.bridge = MCPBridge(MCP_SERVERS if mcp_servers is None else mcp_servers).start()
        self.speaker = Speaker()
        self.voice = Voice(self.speaker)
        self.turns: queue.Queue[str] = queue.Queue()      # finished user turns for the worker
        self.answers: queue.Queue[str] = queue.Queue()    # user turns while a yes/no is pending
        self.awaiting_answer = False
        self.speaking = False
        self.agent: Agent | None = None
        self.mic = mic                                    # None = real microphone
        self.prefs = load_prefs()
        self.merge_s = float(self.prefs.get("pause_s", MERGE_S))
        self.last_said = ""                               # for "say that again"
        self.busy_since: float | None = None              # a user turn is being worked on
        self.last_ok = True                               # outcome of the last screen task
        self.muted = False
        self.mute_lock = threading.Lock()
        self.brain = make_brain(self._prompt())
        threading.Thread(target=vision.backend().warm, daemon=True).start()
        threading.Thread(target=self._ticker, daemon=True).start()

    # ---- prompt -----------------------------------------------------------------

    def _prompt(self) -> str:
        tools = "\n".join(f"- {t['name']}({', '.join((t['parameters'].get('properties') or {}).keys())}): "
                          f"{t['description']}" for t in self.bridge.voice_tools())
        return (BRAIN_PROMPT.replace("{TOOLS}", tools or "(no direct tools)")
                .replace("{SECRETS}", ", ".join(vault.names()) or "none")
                .replace("{WINDOWS}", "; ".join(screen.open_windows()[:15]))
                .replace("{PAUSE}", f"{self.merge_s:.1f}"))

    # ---- mute -------------------------------------------------------------------------

    def set_muted(self, muted: bool, source: str):
        """Thread-safe. The chime plays at once, then Iris says it in words."""
        with self.mute_lock:
            if muted == self.muted:
                return
            self.muted = muted
        self.speaker.chime(CHIME_MUTED if muted else CHIME_LISTENING)
        self.emit({"kind": "mute", "muted": muted, "source": source})
        self.say_async("I've stopped listening. To bring me back, hold Control and Alt, and press M."
                       if muted else "I'm listening.")

    def toggle_mute(self, source: str):
        self.set_muted(not self.muted, source)

    def on_command(self, cmd: str):
        """Commands from the observer panel (see events.py)."""
        if cmd == "toggle_mute":
            self.toggle_mute("panel")
        elif cmd in ("mute", "unmute"):
            self.set_muted(cmd == "mute", "panel")

    # ---- sounds -------------------------------------------------------------------------

    def _ticker(self):
        """A soft tick once a second while a turn is being worked on and nothing else plays."""
        last = 0.0
        while True:
            time.sleep(0.1)
            since = self.busy_since
            now = time.perf_counter()
            if (since is None or now - since < TICK_AFTER_S or now - last < 1.0 or self.speaking
                    or self.awaiting_answer or self.speaker.buf or self.speaker.chime_buf):
                continue
            self.speaker.chime(TICK)
            last = now

    def _outcome_sound(self, name: str, result: str, refused: bool):
        """Done / error earcon after a tool. A 'no' from the user is neither."""
        if refused:
            return
        if FAILED.match(result) or (name == "do_task" and not self.last_ok):
            self.speaker.chime(ERROR)
        elif name in ACTION_TOOLS or (name in self.bridge.tools and self.bridge.tools[name].risky):
            self.speaker.chime(DONE)

    # ---- speaking and listening ---------------------------------------------------

    def say(self, text: str):
        if not text:
            return
        self.last_said = text
        self.emit({"kind": "agent", "text": text})
        self.speaking = True
        try:
            self.voice.say(text)
        finally:
            self.speaking = False

    def say_async(self, text: str):
        if not self.speaking:                 # skip a progress line rather than talk over ourselves
            threading.Thread(target=self.say, args=(text,), daemon=True).start()

    def ask_user(self, question: str) -> str:
        """Speak a yes/no question and wait for the user's next turn (exact words)."""
        self.emit({"kind": "confirm", "question": question})
        while not self.answers.empty():
            self.answers.get_nowait()
        self.awaiting_answer = True
        self.say(question)
        try:
            said = self.answers.get(timeout=45)
        except queue.Empty:
            said = "no answer"
        self.awaiting_answer = False
        self.emit({"kind": "answer", "said": said})
        return said

    def confirmed(self, question: str) -> bool:
        for attempt in range(3):
            if self.muted:                    # nobody can answer: never act on silence
                self.emit({"kind": "verdict", "said": "(muted)", "verdict": "no"})
                return False
            said = self.ask_user(question if attempt == 0 else f"{question} Please say yes or no.")
            verdict = interpret_confirmation(said, question)
            self.emit({"kind": "verdict", "said": said, "verdict": verdict})
            if verdict != "unclear":
                return verdict == "yes"
        return False

    def on_user_turn(self, text: str):
        """Called by the ears for every finished (merged) user turn."""
        if self.muted:                        # said just before the mute: the user took it back
            return
        self.emit({"kind": "user", "text": text})
        if self.awaiting_answer:
            self.answers.put(text)
        elif REPEAT.match(text) and self.last_said:
            # word for word, at once: no brain round trip, no rephrasing
            threading.Thread(target=self.say, args=(self.last_said,), daemon=True).start()
        elif self.agent and STOP_WORDS.match(text):
            self.agent.cancel.set()
            self.emit({"kind": "stop"})
        else:
            self.turns.put(text)

    def on_speech_started(self):
        if self.muted:
            return
        if self.speaking and not self.awaiting_answer:
            self.voice.stop()                 # barge-in: the user talks over Iris

    # ---- tools ----------------------------------------------------------------------

    def run_tool(self, name: str, args: dict) -> str:
        self.emit({"kind": "tool", "name": name, "args": args})
        if name == "do_task":
            return self.run_task(args)
        if name in ("browser_search", "browser_open", "browser_back", "read_screen"):
            return self.run_browser(name, args)
        if name == "stop_task":
            if self.agent:
                self.agent.cancel.set()
            return "stopped"
        if name == "mute_microphone":
            self.set_muted(True, "voice")
            return "muted; the user unmutes by holding Control and Alt and pressing M"
        if name in ("describe_screen", "switch_to", "set_pause", "open_app"):
            return self.run_access(name, args)
        if name not in self.bridge.tools:
            return f"unknown tool {name}"
        bt = self.bridge.tools[name]
        if bt.name == "send_email" and "@" not in str(args.get("to", "")):
            # a name is not an address: never ask "send to Maksim Okulov?" and hope
            return (f"ERROR: '{args.get('to', '')}' is not an email address, so nothing was sent. "
                    "For Telegram or another messenger use do_task in that app's window.")
        question = describe_mcp_action(bt.name, args)
        if bt.name == "reply" and f"{bt.server}__read_email" in self.bridge.tools:
            # name the recipient: "who is it going to?" is the first thing people ask
            head = self.bridge.call(f"{bt.server}__read_email", {"email_id": args.get("email_id", "")})
            sender = re.sub(r"\s*<[^>]+>", "", head.splitlines()[0].removeprefix("From: ")) if head else ""
            if sender and not head.startswith("ERROR"):
                question = f'Send the reply "{args.get("text", "")}" to {sender}?'
        if bt.risky and not self.confirmed(question):
            self.emit({"kind": "mcp", "tool": name, "ok": False, "result": "cancelled by user"})
            return "The user did not confirm, so it was NOT done."
        t = time.perf_counter()
        result = self.bridge.call(name, args)
        self.emit({"kind": "mcp", "tool": name, "ok": not result.startswith("ERROR"),
                   "ms": round((time.perf_counter() - t) * 1000), "result": result[:300]})
        return result

    def run_browser(self, name: str, args: dict) -> str:
        t = time.perf_counter()
        try:
            if name == "browser_search":
                url = browser.search_url(args.get("site", "google"), args.get("query", ""))
                result = browser.open_url(url) if url else f"I don't know how to search {args.get('site')}."
            elif name == "browser_open":
                result = browser.open_url(args.get("url", ""))
            elif name == "browser_back":
                result = browser.back()
            else:
                w = screen.resolve_window(args.get("window", ""), 1) if args.get("window") else None
                result = browser.read(w)
        except Exception as e:
            result = f"ERROR: {e}"
        self.emit({"kind": "mcp", "tool": f"browser__{name.removeprefix('browser_')}",
                   "ok": not result.startswith(("ERROR", "No browser", "I could not")),
                   "ms": round((time.perf_counter() - t) * 1000), "result": result[:300]})
        return result

    def run_access(self, name: str, args: dict) -> str:
        """Tools that tell the user where they are, or change how Iris listens."""
        t = time.perf_counter()
        try:
            if name == "set_pause":
                lo, hi = PAUSE_RANGE
                self.merge_s = round(min(hi, max(lo, float(args.get("seconds", self.merge_s)))), 1)
                self.prefs["pause_s"] = self.merge_s
                save_prefs(self.prefs)
                result = f"I now wait {self.merge_s:g} seconds of silence before I answer."
            elif name == "open_app":
                result = apps.open_app(args.get("name") or args.get("app") or "")
            else:
                name_arg = (args.get("window") or "").strip()
                window = screen.resolve_window(name_arg, 1) if name_arg else screen.foreground()
                if window is None:
                    result = f"ERROR: {name_arg} is not open on the screen."
                elif name == "switch_to":
                    ok = screen.bring_to_front(window)
                    result = f"Now in front: {window.Name}" if ok else f"ERROR: I could not bring {window.Name} to the front."
                else:
                    result = look.overview(window, args.get("detail") or "brief", args.get("question") or "",
                                           emit=self.emit)
        except Exception as e:
            result = f"ERROR: {e}"
        self.emit({"kind": "mcp", "tool": f"screen__{name}", "ok": not result.startswith("ERROR"),
                   "ms": round((time.perf_counter() - t) * 1000), "result": result[:300]})
        return result

    def _explain(self, item: dict, agent: Agent):
        """Teach mode: say the step in plain words before it runs. If the user talked over it,
        wait for their turn to land: it may be "stop"."""
        if not item.get("explain"):
            return
        self.say(item["explain"])
        if self.voice.interrupted.is_set():
            end = time.perf_counter() + self.merge_s + 1.5
            while time.perf_counter() < end and not agent.cancel.is_set():
                time.sleep(0.05)

    def run_task(self, args: dict) -> str:
        window = screen.resolve_window(args.get("window", "")) if args.get("window") else None
        if args.get("window") and window is None:
            return f"{args['window']} is not open on the screen."
        self.emit({"kind": "task_start", "goal": args.get("goal"), "window": args.get("window"),
                   "steps": args.get("steps")})
        # progress lines are spoken in the background: the hands never wait for the voice
        agent = self.agent = Agent(say=lambda s: self.say_async(s) if s != "Looking at the screen." else None,
                                   confirm=lambda q: "no" if self.muted else self.ask_user(q))
        agent.on_event = self.emit
        if args.get("teach"):
            # "show me how": say each step before doing it, and let the cursor rest on the target
            agent.glide_s = 1.6
            # only the brain's plain "explain" is spoken; a raw step ("click the Reply button") never is
            agent.before_step = lambda item: self._explain(item, agent)
        t = time.perf_counter()
        try:
            res = agent.run_plan(args.get("goal", ""), args.get("steps", []), window)
            summary, ok = res.summary, res.ok
            summary += f" Screen now shows: {screen.snapshot(window or screen.foreground()).state()[:400]}"
        except Exception as e:
            summary, ok = f"Something went wrong: {e}", False
        finally:
            self.agent = None
        self.last_ok = ok
        self.emit({"kind": "task_done", "ok": ok, "summary": summary, "seconds": round(time.perf_counter() - t, 1)})
        return summary

    # ---- the conversation worker ---------------------------------------------------------

    def converse(self):
        while True:
            text = self.turns.get()
            self.busy_since = time.perf_counter()
            try:
                self._turn(text)
            except Exception as e:            # one bad turn must not end the conversation
                print("turn error:", repr(e))
                self.emit({"kind": "error", "message": f"turn: {e}"})
                self.say("Sorry, something went wrong on my side. Tell me what you'd like next.")
            finally:
                self.busy_since = None

    def _turn(self, text: str):
        message = f"USER: {text}"
        refused = False
        acted = False
        nudged = False
        failed_tasks = 0
        for _ in range(8):                # a few tool rounds per user turn
            try:
                out, ms = self.brain.ask(message)
            except Exception as e:
                self.emit({"kind": "error", "message": f"brain: {e}"})
                self.say("One moment.")
                time.sleep(2)
                try:
                    out, ms = self.brain.ask(message)
                except Exception as e2:
                    self.emit({"kind": "error", "message": f"brain: {e2}"})
                    self.speaker.chime(ERROR)
                    # after a tool ran, "say that again" could repeat an action (a second send)
                    self.say("Sorry, I can't think right now. " + ("Tell me what you'd like next." if acted
                                                                     else "Could you say that again?"))
                    break
            if not isinstance(out, dict):
                out = {"say": str(out), "tool": None}
            out["tool"] = normalize_tool(out.get("tool"))
            self.emit({"kind": "brain", "ms": round(ms), "out": out})
            tool = None if refused else out.get("tool")   # after a "no", no more actions this turn
            if not tool and not refused and not nudged and PROMISED.search(out.get("say") or ""):
                nudged = True             # "let me check" with no tool: ask once for the tool call
                message = ("You said you would do something but sent no tool. Reply again with the "
                           "tool call, or tell the user plainly that you can't do it.")
                continue
            if not (out.get("say") or tool):
                if not nudged:
                    nudged = True         # an empty reply: ask once more instead of going silent
                    message = "Your reply was empty. Answer the user now, as JSON with say and tool."
                    continue
                out["say"] = "Sorry, I didn't catch that. Could you say it again?"
            if tool and out.get("say") and not (tool.get("args") or {}).get("teach"):
                threading.Thread(target=self.say, args=(out["say"],), daemon=True).start()
            else:
                self.say(out.get("say", ""))
            if not tool:
                break
            args = tool["args"]
            result = self.run_tool(tool["name"], args)
            acted = True
            refused = bool(USER_SAID_NO.search(result))
            self._outcome_sound(tool["name"], result, refused)
            message = f"TOOL RESULT {tool['name']}: {result}"
            if refused:
                message += (" The user said no. Do not try again or find another way. "
                            "Reply with tool null and briefly confirm nothing was done.")
            elif tool["name"] == "do_task" and not self.last_ok:
                failed_tasks += 1
                if failed_tasks >= 2:     # two failed tries: stop and say so, don't loop for minutes
                    refused = True
                    message += (" This failed twice. Do not try again. Reply with tool null and tell "
                                "the user briefly what went wrong and what they could do.")

    # ---- ears: AssemblyAI streaming STT ------------------------------------------------

    async def listen(self):
        key = os.environ["ASSEMBLYAI_API_KEY"]
        loop = asyncio.get_running_loop()
        audio_q: asyncio.Queue[bytes] = asyncio.Queue()
        async with websockets.connect(STT_URL, additional_headers={"Authorization": key}) as ws:
            self.emit({"kind": "ready"})

            async def pump():
                if self.mic:                  # scripted phrases (tests)
                    async for chunk in self.mic(self):
                        await ws.send(bytes(len(chunk)) if self.muted else chunk)
                    return
                n = RATE * CHUNK_MS // 1000

                def cb(indata, *_):
                    loop.call_soon_threadsafe(audio_q.put_nowait, bytes(indata))
                device = mic_device()
                self.emit({"kind": "mic", "device": sd.query_devices(device, "input")["name"]})
                with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=n,
                                       callback=cb, device=device):
                    while True:
                        chunk = await audio_q.get()
                        # muted: keep the stream alive, but nothing of the user in it
                        await ws.send(bytes(len(chunk)) if self.muted else chunk)

            pump_task = asyncio.ensure_future(pump())
            parts: list[str] = []
            deadline = None
            try:
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=0.1)
                    except asyncio.TimeoutError:
                        raw = None
                    if raw:
                        ev = json.loads(raw)
                        if ev.get("type") == "SpeechStarted":
                            deadline = None if parts else deadline
                            self.on_speech_started()
                        elif ev.get("type") == "Turn" and ev.get("end_of_turn") and ev.get("turn_is_formatted"):
                            if ev.get("transcript", "").strip():
                                parts.append(ev["transcript"].strip())
                                deadline = time.perf_counter() + self.merge_s
                    if parts and deadline and time.perf_counter() > deadline:
                        self.on_user_turn(" ".join(parts))
                        parts, deadline = [], None
            finally:
                pump_task.cancel()

    async def listen_forever(self):
        """Reconnect the ears if the stream drops, so Iris never goes deaf."""
        while True:
            try:
                await self.listen()
                return                        # scripted mic finished
            except (websockets.ConnectionClosed, OSError) as e:
                self.emit({"kind": "error", "message": f"speech stream dropped ({e}); reconnecting"})
                await asyncio.sleep(1)

    def run(self):
        threading.Thread(target=self.brain.warm, daemon=True).start()
        threading.Thread(target=self.converse, daemon=True).start()
        threading.Thread(target=self.say, args=("Hi, I'm Iris. What would you like to do?",), daemon=True).start()
        asyncio.run(self.listen_forever())


def console_and_bus():
    import events
    b = events.bus()

    def emit(e: dict):
        if e.get("kind") not in ("target", "click", "overlay_hide"):
            print(json.dumps(e, ensure_ascii=False)[:300], flush=True)
        b.publish(e)
    return emit


def start(iris: Iris):
    """Wire the mute controls (panel button, global hotkey) and run."""
    import events
    events.bus().on_command(iris.on_command)
    if not watch_hotkey(lambda: iris.toggle_mute("hotkey")) and sys.platform == "win32":
        iris.emit({"kind": "error", "message": "Ctrl+Alt+M is taken by another app: mute only from the panel"})
    iris.emit({"kind": "mute", "muted": False, "source": "start"})
    iris.run()


if __name__ == "__main__":
    try:
        start(Iris(emit=console_and_bus()))
    except KeyboardInterrupt:
        pass
