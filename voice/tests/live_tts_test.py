"""Live end-to-end test against the real AssemblyAI Voice Agent API, no human needed:
recorded phrases (tests/audio/*.wav, 24 kHz PCM16 mono) are streamed instead of the mic.
The first phrase is the request; later phrases are played whenever the agent waits for a
confirmation. Prints the event log with timings.

  python tests/live_tts_test.py calc.wav [yes.wav ...]
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


def pcm(name: str) -> bytes:
    with wave.open(os.path.join(AUDIO, name), "rb") as w:
        assert w.getframerate() == client.RATE and w.getsampwidth() == 2 and w.getnchannels() == 1
        return w.readframes(w.getnframes())


class ScriptedSession(client.VoiceSession):
    def __init__(self, request: str, answers: list[str], **kw):
        super().__init__(**kw)
        self.queue_audio = [pcm(request)]
        self.answer_audio = [pcm(a) for a in answers]
        self.t0 = time.perf_counter()

    def agent_confirm(self, question: str) -> str:
        if self.answer_audio:                  # speak the next scripted answer once the question is asked
            self.queue_audio.append(self.answer_audio.pop(0))
        return super().agent_confirm(question)

    async def _mic(self):
        await self.ready.wait()
        await asyncio.sleep(3.0)               # let the greeting finish
        silence = b"\x00" * CHUNK
        while True:
            if self.queue_audio and self.idle:
                data = self.queue_audio.pop(0) + b"\x00" * (CHUNK * 60)   # phrase + 1.2 s silence
                for i in range(0, len(data), CHUNK):
                    await self._send({"type": "input.audio", "audio": base64.b64encode(data[i:i + CHUNK]).decode()})
                    await asyncio.sleep(0.02)
            else:
                await self._send({"type": "input.audio", "audio": base64.b64encode(silence).decode()})
                await asyncio.sleep(0.02)


def main():
    request, *answers = sys.argv[1:] or ["calc.wav"]
    t0 = time.perf_counter()

    def emit(e):
        if e.get("kind") in ("target", "click", "overlay_hide"):
            return
        print(f"{time.perf_counter() - t0:6.2f}s {json.dumps(e, ensure_ascii=False)[:230]}", flush=True)
        if e.get("kind") == "task_done":
            emit.done_at = time.perf_counter()

    emit.done_at = None
    s = ScriptedSession(request, answers, emit=emit, mcp_servers={})

    async def run():
        task = asyncio.ensure_future(s.run())
        while not task.done():
            await asyncio.sleep(0.5)
            if emit.done_at and time.perf_counter() - emit.done_at > 8:   # let the result be spoken
                task.cancel()
                break
            if time.perf_counter() - t0 > 120:
                print("TIMEOUT")
                task.cancel()
                break
    try:
        asyncio.run(run())
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
