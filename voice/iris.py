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
import screen  # noqa: E402
import vault  # noqa: E402
import vision  # noqa: E402
from agent import Agent, interpret_confirmation  # noqa: E402
from client import MCP_SERVERS, Speaker, describe_mcp_action  # noqa: E402
from mcp_bridge import MCPBridge  # noqa: E402

RATE = 24000
STT_URL = ("wss://streaming.assemblyai.com/v3/ws?sample_rate=24000&format_turns=true"
           f"&speech_model={os.environ.get('STT_MODEL', 'universal-3-6-pro')}")
CHUNK_MS = 100                                  # AssemblyAI wants 50-1000 ms per message
MERGE_S = float(os.environ.get("MERGE_PAUSE_S", "1.3"))   # a pause shorter than this continues the turn
BRAIN_MODEL = os.environ.get("BRAIN_MODEL", "haiku")
TTS_VOICE = os.environ.get("TTS_VOICE", "en-US-AvaMultilingualNeural")
STOP_WORDS = re.compile(r"^\W*(stop|cancel|wait|hold on|never mind)\b", re.I)

BRAIN_PROMPT = """You are the brain of Iris, a calm voice assistant that operates a Windows PC for a person who
cannot see the screen or is not comfortable with computers.

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
  The app is already open: never add a step to open it. Name the control: "press the digit 7",
  "click the Reply button", "type the message into the message box". Keypads: one key per step.
- stop_task(): stop the running task.

Rules:
- Prefer direct tools (mail__...) when one fits: they are faster and reliable.
- When asked whether someone wrote, find the message and read it out right away: who, and what it says.
- When the user dictates a message, use all of their words.
- Risky actions (sending, paying, deleting) are confirmed with the user by the app itself; just call the tool.
- Never claim something happened unless a TOOL RESULT says so.
- Stored login names: {SECRETS}
- Windows open right now: {WINDOWS}"""


def extract_json(text: str) -> dict:
    start = text.find("{")
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0] if start >= 0 else {"say": text.strip(), "tool": None}
    except ValueError:
        return {"say": "", "tool": None}


class Brain:
    """One warm `claude -p` session in stream-json mode; the conversation lives in its context."""

    def __init__(self, prompt: str):
        self.prompt_file = os.path.join(tempfile.gettempdir(), "iris_brain_prompt.txt")
        self.prompt = prompt
        self.proc = None
        self.turns = 0

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

    def ask(self, text: str) -> tuple[dict, float]:
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

    def ask(self, text: str) -> tuple[dict, float]:
        t = time.perf_counter()
        self.history.append({"role": "user", "content": text})
        r = self.client.messages.create(model=self.model, max_tokens=600, system=self.prompt,
                                        messages=self.history[-40:])
        reply = r.content[0].text
        self.history.append({"role": "assistant", "content": reply})
        return extract_json(reply), (time.perf_counter() - t) * 1000


def make_brain(prompt: str):
    return APIBrain(prompt) if os.environ.get("BRAIN_BACKEND") == "api" else Brain(prompt)


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
        self.brain = make_brain(self._prompt())
        self.turns: queue.Queue[str] = queue.Queue()      # finished user turns for the worker
        self.answers: queue.Queue[str] = queue.Queue()    # user turns while a yes/no is pending
        self.awaiting_answer = False
        self.speaking = False
        self.agent: Agent | None = None
        self.mic = mic                                    # None = real microphone
        threading.Thread(target=vision.backend().warm, daemon=True).start()

    # ---- prompt -----------------------------------------------------------------

    def _prompt(self) -> str:
        tools = "\n".join(f"- {t['name']}({', '.join((t['parameters'].get('properties') or {}).keys())}): "
                          f"{t['description']}" for t in self.bridge.voice_tools())
        return (BRAIN_PROMPT.replace("{TOOLS}", tools or "(no direct tools)")
                .replace("{SECRETS}", ", ".join(vault.names()) or "none")
                .replace("{WINDOWS}", "; ".join(screen.open_windows()[:15])))

    # ---- speaking and listening ---------------------------------------------------

    def say(self, text: str):
        if not text:
            return
        self.emit({"kind": "agent", "text": text})
        self.speaking = True
        try:
            self.voice.say(text)
        finally:
            self.speaking = False

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
            said = self.ask_user(question if attempt == 0 else f"{question} Please say yes or no.")
            verdict = interpret_confirmation(said, question)
            self.emit({"kind": "verdict", "said": said, "verdict": verdict})
            if verdict != "unclear":
                return verdict == "yes"
        return False

    def on_user_turn(self, text: str):
        """Called by the ears for every finished (merged) user turn."""
        self.emit({"kind": "user", "text": text})
        if self.awaiting_answer:
            self.answers.put(text)
        elif self.agent and STOP_WORDS.match(text):
            self.agent.cancel.set()
            self.emit({"kind": "stop"})
        else:
            self.turns.put(text)

    def on_speech_started(self):
        if self.speaking and not self.awaiting_answer:
            self.voice.stop()                 # barge-in: the user talks over Iris

    # ---- tools ----------------------------------------------------------------------

    def run_tool(self, name: str, args: dict) -> str:
        self.emit({"kind": "tool", "name": name, "args": args})
        if name == "do_task":
            return self.run_task(args)
        if name == "stop_task":
            if self.agent:
                self.agent.cancel.set()
            return "stopped"
        if name not in self.bridge.tools:
            return f"unknown tool {name}"
        bt = self.bridge.tools[name]
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

    def run_task(self, args: dict) -> str:
        window = screen.resolve_window(args.get("window", "")) if args.get("window") else None
        if args.get("window") and window is None:
            return f"{args['window']} is not open on the screen."
        self.emit({"kind": "task_start", "goal": args.get("goal"), "window": args.get("window"),
                   "steps": args.get("steps")})
        agent = self.agent = Agent(say=lambda s: self.say(s) if s != "Looking at the screen." else None,
                                   confirm=lambda q: self.ask_user(q))
        agent.on_event = self.emit
        t = time.perf_counter()
        try:
            res = agent.run_plan(args.get("goal", ""), args.get("steps", []), window)
            summary, ok = res.summary, res.ok
            summary += f" Screen now shows: {screen.snapshot(window or screen.foreground()).state()[:400]}"
        except Exception as e:
            summary, ok = f"Something went wrong: {e}", False
        finally:
            self.agent = None
        self.emit({"kind": "task_done", "ok": ok, "summary": summary, "seconds": round(time.perf_counter() - t, 1)})
        return summary

    # ---- the conversation worker ---------------------------------------------------------

    def converse(self):
        while True:
            text = self.turns.get()
            message = f"USER: {text}"
            for _ in range(8):                # a few tool rounds per user turn
                try:
                    out, ms = self.brain.ask(message)
                except Exception as e:
                    self.emit({"kind": "error", "message": f"brain: {e}"})
                    self.say("Sorry, I lost my train of thought. Could you say that again?")
                    break
                self.emit({"kind": "brain", "ms": round(ms), "out": out})
                tool = out.get("tool")
                if tool and out.get("say"):
                    threading.Thread(target=self.say, args=(out["say"],), daemon=True).start()
                else:
                    self.say(out.get("say", ""))
                if not tool:
                    break
                result = self.run_tool(tool.get("name", ""), tool.get("args") or {})
                message = f"TOOL RESULT {tool.get('name')}: {result}"

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
                        await ws.send(chunk)
                    return
                n = RATE * CHUNK_MS // 1000

                def cb(indata, *_):
                    loop.call_soon_threadsafe(audio_q.put_nowait, bytes(indata))
                with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=n, callback=cb):
                    while True:
                        await ws.send(await audio_q.get())

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
                                deadline = time.perf_counter() + MERGE_S
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


if __name__ == "__main__":
    try:
        Iris(emit=console_and_bus()).run()
    except KeyboardInterrupt:
        pass
