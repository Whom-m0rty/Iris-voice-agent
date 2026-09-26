"""Live end-to-end test against the real AssemblyAI Voice Agent API, no human needed:
recorded phrases (tests/audio/*.wav, 24 kHz PCM16 mono) are streamed instead of the mic.

  python tests/live_tts_test.py calc.wav [yes.wav ...]
      the first phrase is the request; later phrases answer confirmation questions
  python tests/live_tts_test.py --seq a.wav b.wav c.wav
      a conversation with the real MCP servers: each next phrase is spoken once the agent
      has answered and stayed quiet for 2.5 s
"""
import asyncio
import base64
import json
import os
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import client  # noqa: E402

AUDIO = os.path.join(HERE, "audio")
CHUNK = client.MIC_BLOCK * 2               # 20 ms of PCM16
QUIET_S = 2.5


def pcm(name: str) -> bytes:
    with wave.open(os.path.join(AUDIO, name), "rb") as w:
        assert w.getframerate() == client.RATE and w.getsampwidth() == 2 and w.getnchannels() == 1
        return w.readframes(w.getnframes())


class ScriptedSession(client.VoiceSession):
    def __init__(self, request: str, answers: list[str], seq: bool = False, **kw):
        super().__init__(**kw)
        self.seq = seq
        self.queue_audio = [pcm(request)]
        self.answer_audio = [pcm(a) for a in answers]
        self.last_agent_speech = 0.0
        self.agent_spoke = False           # the agent answered since our last phrase

    def agent_confirm(self, question: str) -> str:
        if self.answer_audio and not self.seq:   # speak the next answer once the question is asked
            self.queue_audio.append(self.answer_audio.pop(0))
        return super().agent_confirm(question)

    def heard_agent(self):
        self.last_agent_speech = time.perf_counter()
        self.agent_spoke = True

    def _next_phrase_due(self) -> bool:
        return (self.seq and not self.queue_audio and self.answer_audio and self.idle and self.agent_spoke
                and time.perf_counter() - self.last_agent_speech > QUIET_S)

    def conversation_over(self) -> bool:
        return (not self.answer_audio and not self.queue_audio and self.idle and self.agent_spoke
                and time.perf_counter() - self.last_agent_speech > 6)

    async def _mic(self):
        await self.ready.wait()
        await asyncio.sleep(3.0)               # let the greeting finish
        silence = b"\x00" * CHUNK
        while True:
            if self._next_phrase_due():
                self.queue_audio.append(self.answer_audio.pop(0))
            if self.queue_audio and self.idle:
                self.agent_spoke = False
                data = self.queue_audio.pop(0) + silence * 60          # phrase + 1.2 s silence
                for i in range(0, len(data), CHUNK):
                    await self._send({"type": "input.audio", "audio": base64.b64encode(data[i:i + CHUNK]).decode()})
                    await asyncio.sleep(0.02)
            else:
                await self._send({"type": "input.audio", "audio": base64.b64encode(silence).decode()})
                await asyncio.sleep(0.02)


def main():
    args = sys.argv[1:] or ["calc.wav"]
    seq = args[0] == "--seq"
    request, *answers = args[1:] if seq else args
    t0 = time.perf_counter()
    state = {"done_at": None, "session": None}

    def emit(e):
        if e.get("kind") in ("target", "click", "overlay_hide"):
            return
        if e.get("kind") == "agent" and state["session"]:
            state["session"].heard_agent()
        print(f"{time.perf_counter() - t0:6.2f}s {json.dumps(e, ensure_ascii=False)[:260]}", flush=True)
        if e.get("kind") == "task_done":
            state["done_at"] = time.perf_counter()

    # --seq uses the real MCP servers (e.g. Gmail); the default keeps MCP off
    s = state["session"] = ScriptedSession(request, answers, seq=seq, emit=emit,
                                           **({} if seq else {"mcp_servers": {}}))

    async def run():
        task = asyncio.ensure_future(s.run())
        limit = 240 if seq else 120
        while not task.done():
            await asyncio.sleep(0.5)
            if seq and s.conversation_over():
                break
            if not seq and state["done_at"] and time.perf_counter() - state["done_at"] > 8:
                break                          # let the result be spoken, then stop
            if time.perf_counter() - t0 > limit:
                print("TIMEOUT")
                break
        task.cancel()
    try:
        asyncio.run(run())
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
