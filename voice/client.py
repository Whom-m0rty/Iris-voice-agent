"""Voice front end: AssemblyAI Voice Agent API <-> the screen agent.

The Voice Agent API cannot speak while a tool runs, so `do_task` answers at once
("started") and the task runs in a background thread. Progress, confirmation
questions and the final result go back into the conversation with
`reply.create` (instructions), sent only when the agent is idle (after `reply.done`),
the same rule the API sets for `tool.result`. What happened is also appended to a task
log inside the system prompt (`session.update`), so the agent remembers it later.
(`conversation.message` is in the docs but is silently ignored by the live API, 26.09.)

Confirmation answers are judged on the user's exact words (`transcript.user`) by
Kev, not on the voice LLM's paraphrase.

Run:  python client.py            (needs ASSEMBLYAI_API_KEY in ../.env and Kev on :8009)
"""
import asyncio
import base64
import json
import os
import queue
import sys
import threading
import time

import sounddevice as sd
import websockets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
import env  # noqa: E402,F401  (loads ../.env)
import screen  # noqa: E402
import vault  # noqa: E402
import vision  # noqa: E402
from agent import Agent, interpret_confirmation  # noqa: E402
from mcp import StdioServerParameters  # noqa: E402
from mcp_bridge import MCPBridge  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# API-first abilities: each MCP server's tools become voice tools (<server>__<tool>)
MCP_SERVERS = {
    "mail": StdioServerParameters(command=sys.executable, args=[os.path.join(HERE, "mcp_servers", "mail.py")]),
}

URL = os.environ.get("VOICE_WS_URL", "wss://agents.assemblyai.com/v1/ws")
RATE = 24000                      # PCM16 mono 24 kHz both ways
MIC_BLOCK = RATE // 50            # 20 ms
VOICE = os.environ.get("VOICE", "ivy")
DEBUG_EVENTS = bool(os.environ.get("VOICE_DEBUG"))
# stored agent (setup_agent.py) whose LLM is Claude via AssemblyAI's gateway; empty = managed model.
# Measured 26.09: the managed model returned empty replies to some requests, Claude did not.
AGENT_ID = os.environ.get("VOICE_AGENT_ID", "")
MIN_SILENCE_MS = int(os.environ.get("MIN_SILENCE_MS", "900"))
MAX_SILENCE_MS = int(os.environ.get("MAX_SILENCE_MS", "2400"))

SYSTEM_PROMPT = """You are Iris, a calm voice assistant that operates a Windows PC for a person who
cannot see the screen or is not comfortable with computers. Speak in short, plain sentences.

Prefer direct tools (names like mail__list_recent) when one fits the request: they are fast
and reliable. Use do_task only for things no direct tool covers.
When the user asks whether someone wrote, find that message and read it out right away:
who it is from and what it says. Do not ask first whether to read it.
When the user dictates a reply, use their full words, including every part they said.

When the user wants something done on the screen, call do_task with:
- goal: what the user wants, in one sentence
- window: the app it happens in (for example "Telegram", "Gmail", "Amazon")
- steps: the plan as small single actions, each {"do": "..."}; if a step types text, add
  {"do": "...", "text": "exact text to type"}. One click or one text entry per step.
  The app is already open on screen: never add a step to open, launch or switch to it.
  Name the control to use, e.g. "press the 7 key", "click the Reply button",
  "type the message into the message box". To send, the last step is "click the Send
  button" - never press Enter. On keypads (calculator, phone dialer) press
  every key as its own step: "press the digit 1", "press the digit 2", "press multiply".
- passwords: never ask the user to say a password and never put one in "text". Use
  {"do": "enter the password", "secret": "<stored login name>"}; the app fills it itself.
  Stored login names: {SECRETS}.
do_task returns at once with "started". Then say one short sentence like "On it." and wait:
progress and the result arrive later as instructions and in the task log; relay them briefly.

Sometimes you are asked to confirm an action with the user. Ask one short
yes/no question that says plainly what will happen (who gets the message, what is paid). When the user answers such a question, call answer_confirmation and
say nothing else - the app judges the answer itself.

If the user says stop or cancel while a task runs, call stop_task.
Never claim something was done unless the task log says so."""

TOOLS = [
    {"type": "function", "name": "do_task",
     "description": "Start doing something on the user's computer. Returns immediately.",
     "parameters": {"type": "object", "properties": {
         "goal": {"type": "string"},
         "window": {"type": "string", "description": "App or window name, e.g. Telegram"},
         "steps": {"type": "array", "items": {"type": "object", "properties": {
             "do": {"type": "string"}, "text": {"type": "string"},
             "secret": {"type": "string", "description": "stored login name, for password fields"}},
             "required": ["do"]}}},
         "required": ["goal", "window", "steps"]},
     "execution_mode": "interactive", "timeout_seconds": 10},
    {"type": "function", "name": "answer_confirmation",
     "description": "Call when the user answers a yes/no question you asked about an action.",
     "parameters": {"type": "object", "properties": {}},
     "execution_mode": "interactive", "timeout_seconds": 10},
    {"type": "function", "name": "stop_task",
     "description": "Stop the running task.",
     "parameters": {"type": "object", "properties": {}},
     "execution_mode": "interactive", "timeout_seconds": 10},
]


def describe_mcp_action(tool: str, args: dict) -> str:
    """A confirmation question a person can answer, not a JSON dump."""
    if tool == "reply":
        return f'Send the reply "{args.get("text", "")}"?'
    if tool == "send_email":
        return f'Send an email to {args.get("to", "?")} saying "{args.get("text", "")}"?'
    shown = ", ".join(f"{k}: {v}" for k, v in args.items())
    return f"Run {tool.replace('_', ' ')} ({shown})?"


class Speaker:
    """Plays reply.audio; flush() drops what is queued when the user interrupts."""

    def __init__(self):
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.stream = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16",
                                         callback=self._cb, blocksize=MIC_BLOCK)
        self.stream.start()

    def _cb(self, out, frames, _t, _s):
        n = frames * 2
        with self.lock:
            chunk = bytes(self.buf[:n])
            del self.buf[:n]
        out[:] = chunk + b"\x00" * (n - len(chunk))

    def play(self, pcm: bytes):
        with self.lock:
            self.buf += pcm

    def flush(self):
        with self.lock:
            self.buf.clear()


class VoiceSession:
    def __init__(self, emit=print, mcp_servers=None):
        self.emit = emit
        self.bridge = MCPBridge(MCP_SERVERS if mcp_servers is None else mcp_servers).start()                      # event sink: stdout now, observer panel later
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws = None
        self.idle = True                      # no reply in progress
        self.outbox: list[dict] = []          # messages that must wait until idle
        self.answers: queue.Queue[str] = queue.Queue()
        self.awaiting_answer = False
        self.task_thread: threading.Thread | None = None
        self.agent: Agent | None = None
        self.stop_flag = threading.Event()
        self.speaker = Speaker()
        self.ready = asyncio.Event()          # no audio before session.ready
        self._reply_asked_at = 0.0
        self._last_reply_started = 0.0
        self._reply_had_content = False
        self._unanswered_user: str | None = None   # user text the agent has not reacted to yet
        self.empty_replies = 0
        self.log: list[str] = []              # task log kept in the system prompt

    # ---- sending ---------------------------------------------------------------

    async def _send(self, msg: dict):
        await self.ws.send(json.dumps(msg))

    async def _flush_if_idle(self):
        while self.idle and self.outbox:
            msg = self.outbox.pop(0)
            await self._send(msg)
            if msg["type"] == "reply.create":
                self.idle = False             # a reply is starting; wait for reply.done
                self._reply_asked_at = time.perf_counter()
                asyncio.ensure_future(self._unstick())

    async def _unstick(self, after: float = 4.0):
        """The live API sometimes drops a reply.create (e.g. while it answers the user itself).
        Without reply.started we would wait for a reply.done that never comes."""
        asked = self._reply_asked_at
        await asyncio.sleep(after)
        if not self.idle and not self._reply_started_since(asked):
            self.idle = True
            await self._flush_if_idle()

    def _reply_started_since(self, t: float) -> bool:
        return self._last_reply_started >= t

    def _queue(self, *msgs: dict):
        """Thread-safe: queue messages from the task thread and flush when idle."""
        def add():
            self.outbox.extend(msgs)
            asyncio.ensure_future(self._flush_if_idle())
        self.loop.call_soon_threadsafe(add)

    def _prompt(self) -> str:
        base = SYSTEM_PROMPT.replace("{SECRETS}", ", ".join(vault.names()) or "none yet")
        base += ("\n\nWindows open right now (use one of these names as `window`): "
                 + "; ".join(screen.open_windows()[:15]))
        if not self.log:
            return base
        return base + "\n\nTask log (what really happened, newest last):\n" + "\n".join(self.log[-15:])

    def tell(self, text: str, speak: str | None = None):
        """Record what happened in the task log and make the agent say it."""
        self.log.append(f"- {time.strftime('%H:%M')} {text}")
        self._queue({"type": "session.update", "session": {"system_prompt": self._prompt()}},
                    {"type": "reply.create", "instructions": speak or f"Briefly tell the user: {text}"})

    # ---- callbacks the screen agent uses ----------------------------------------

    def agent_say(self, text: str):
        self.emit({"kind": "progress", "text": text})
        self.tell(f"Progress: {text}", speak=f"In a few words, tell the user: {text}")

    def agent_confirm(self, question: str) -> str:
        """Blocks the task thread until the user answers out loud."""
        self.emit({"kind": "confirm", "question": question})
        while not self.answers.empty():
            self.answers.get_nowait()
        self.awaiting_answer = True
        self.tell(f"The task is paused for confirmation: {question}",
                  speak=("Ask the user one short, friendly yes/no question to confirm this action, "
                         f"naming what will happen: {question}"))
        try:
            said = self.answers.get(timeout=60)
        except queue.Empty:
            said = "no answer"
        self.awaiting_answer = False
        self.emit({"kind": "answer", "said": said})
        return said

    # ---- tools -----------------------------------------------------------------

    def _run_task(self, args: dict):
        agent = self.agent = Agent(say=self.agent_say, confirm=self.agent_confirm)
        agent.on_event = self.emit            # live steps, targets and clicks for panel + overlay
        window = screen.resolve_window(args.get("window", "")) if args.get("window") else None
        t = time.perf_counter()
        try:
            if window is None and args.get("window"):
                result_text = f"I could not find {args['window']} open on the screen."
                ok = False
            else:
                res = agent.run_plan(args["goal"], args["steps"], window)
                result_text, ok = res.summary, res.ok
                # report what the screen shows now, so the agent relays it instead of guessing
                result_text += f" Screen now shows: {screen.snapshot(window).state()[:400]}"
        except Exception as e:  # never leave the user hanging in silence
            result_text, ok = f"Something went wrong: {e}", False
        self.emit({"kind": "task_done", "ok": ok, "summary": result_text,
                   "seconds": round(time.perf_counter() - t, 1)})
        self.tell(f"Task finished. Result: {result_text}")

    def _run_risky_mcp(self, name: str, args: dict):
        """Risky MCP call in the background: ask out loud, act only on a clear yes."""
        bt = self.bridge.tools[name]
        question = describe_mcp_action(bt.name, args)
        answer = "unclear"
        for _ in range(3):
            said = self.agent_confirm(question)
            answer = interpret_confirmation(said, question)
            if answer != "unclear":
                break
        if answer != "yes":
            self.emit({"kind": "mcp", "tool": name, "ok": False, "result": "cancelled by user"})
            self.tell(f"The user did not confirm, so {name} was NOT done.")
            return
        t = time.perf_counter()
        result = self.bridge.call(name, args)
        self.emit({"kind": "mcp", "tool": name, "ok": not result.startswith("ERROR"),
                   "ms": round((time.perf_counter() - t) * 1000), "result": result[:300]})
        self.tell(f"Result of {name}: {result}")

    def _handle_tool(self, name: str, args: dict) -> str:
        if name in self.bridge.tools:
            if self.bridge.tools[name].risky:
                threading.Thread(target=self._run_risky_mcp, args=(name, args), daemon=True).start()
                return ("The app is asking the user to confirm this right now. Say nothing; "
                        "do not ask yourself. The result will follow.")
            t = time.perf_counter()
            result = self.bridge.call(name, args)
            self.emit({"kind": "mcp", "tool": name, "ok": not result.startswith("ERROR"),
                       "ms": round((time.perf_counter() - t) * 1000), "result": result[:300]})
            return result
        if name == "do_task":
            if self.task_thread and self.task_thread.is_alive():
                return "busy: another task is still running"
            self.emit({"kind": "task_start", "goal": args.get("goal"), "window": args.get("window"),
                       "steps": args.get("steps")})
            self.task_thread = threading.Thread(target=self._run_task, args=(args,), daemon=True)
            self.task_thread.start()
            return "started"
        if name == "answer_confirmation":
            return "noted"                    # the answer itself comes from transcript.user
        if name == "stop_task":
            # the agent checks this before every action, so it stops within one step
            if self.agent:
                self.agent.cancel.set()
            if self.awaiting_answer:
                self.answers.put("no, stop")
            self.emit({"kind": "stop"})
            return "stopping"
        return f"unknown tool {name}"

    # ---- main loops --------------------------------------------------------------

    async def _mic(self):
        q: asyncio.Queue[bytes] = asyncio.Queue()

        def cb(indata, _frames, _t, _s):
            self.loop.call_soon_threadsafe(q.put_nowait, bytes(indata))

        await self.ready.wait()
        with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=MIC_BLOCK, callback=cb):
            while True:
                pcm = await q.get()
                await self._send({"type": "input.audio", "audio": base64.b64encode(pcm).decode()})

    async def _events(self):
        async for raw in self.ws:
            ev = json.loads(raw)
            t = ev.get("type")
            if DEBUG_EVENTS and t not in ("reply.audio", "transcript.agent.delta", "transcript.user.delta"):
                print("   <<", json.dumps(ev, ensure_ascii=False)[:300], flush=True)
            if t == "reply.audio":
                self.speaker.play(base64.b64decode(ev["data"]))
            elif t == "reply.started":
                self.idle = False
                self._last_reply_started = time.perf_counter()
                self._reply_had_content = False
            elif t == "reply.done":
                if ev.get("status") == "interrupted":
                    self.speaker.flush()
                elif not self._reply_had_content and self._unanswered_user and not self.awaiting_answer:
                    # the live API sometimes ends a reply with no words and no tool call;
                    # ask once more with the user's exact words instead of going silent
                    self.empty_replies += 1
                    said, self._unanswered_user = self._unanswered_user, None
                    self.emit({"kind": "retry", "reason": "empty reply", "user": said})
                    self.outbox.append({"type": "reply.create",
                                        "instructions": f'The user said: "{said}". Respond to it now.'})
                self.idle = True
                await self._flush_if_idle()
            elif t == "transcript.user":
                self.emit({"kind": "user", "text": ev["text"]})
                self._unanswered_user = ev["text"]
                if self.awaiting_answer:
                    self.answers.put(ev["text"])
            elif t == "transcript.agent":
                self._reply_had_content = True
                self._unanswered_user = None
                self.emit({"kind": "agent", "text": ev["text"], "interrupted": ev.get("interrupted", False)})
            elif t == "tool.call":
                self._reply_had_content = True
                self._unanswered_user = None
                # off the event loop: MCP calls and window lookups can take a moment
                result = await asyncio.to_thread(self._handle_tool, ev["name"], ev.get("arguments") or {})
                self.emit({"kind": "tool", "name": ev["name"], "args": ev.get("arguments"), "result": result})
                # tool.result may only go out once the current reply is done
                self.outbox.insert(0, {"type": "tool.result", "call_id": ev["call_id"],
                                       "result": json.dumps(result)})
                await self._flush_if_idle()
            elif t == "session.error":
                self.emit({"kind": "error", "code": ev.get("code"), "message": ev.get("message")})
            elif t == "session.ready":
                self.ready.set()
                self.emit({"kind": "ready", "session_id": ev.get("session_id")})

    async def run(self):
        key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not key:
            raise SystemExit("ASSEMBLYAI_API_KEY is not set in voice-hack/.env")
        self.loop = asyncio.get_running_loop()
        # start the vision fallback now, so its first use is not a cold start
        threading.Thread(target=vision.backend().warm, daemon=True).start()
        url = f"{URL}?agent_id={AGENT_ID}" if AGENT_ID and URL.startswith("wss://agents.") else URL
        async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {key}"}) as ws:
            self.ws = ws
            await self._send({"type": "session.update", "session": {
                "system_prompt": self._prompt(),
                "greeting": "Hi, I'm Iris. What would you like to do?",
                "output": {"voice": VOICE},
                # older users pause mid-sentence; wait longer before ending their turn
                "input": {"turn_detection": {"min_silence": MIN_SILENCE_MS, "max_silence": MAX_SILENCE_MS}},
                "tools": TOOLS + self.bridge.voice_tools()}})
            mic = asyncio.ensure_future(self._mic())
            try:
                await self._events()          # returns when the server closes the session
            except websockets.ConnectionClosed:
                pass
            finally:
                mic.cancel()
                self.emit({"kind": "closed"})


def console_and_bus():
    """Events go to stdout and to the local bus (observer panel + cursor overlay)."""
    import events
    b = events.bus()

    def emit(e: dict):
        print(json.dumps(e, ensure_ascii=False)[:300], flush=True)
        b.publish(e)
    return emit


if __name__ == "__main__":
    try:
        asyncio.run(VoiceSession(emit=console_and_bus()).run())
    except KeyboardInterrupt:
        pass
