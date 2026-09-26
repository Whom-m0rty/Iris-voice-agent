"""Live test of iris.py (AssemblyAI STT -> Claude -> TTS) with recorded phrases instead of the mic.
Each phrase is spoken once Iris has finished talking and stayed quiet for QUIET_S.

  python tests/iris_seq_test.py mail_check.wav mail_reply.wav mail_unsure.wav mail_yes.wav
"""
import asyncio
import json
import os
import sys
import threading
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import iris  # noqa: E402

QUIET_S = 2.5
CHUNK = iris.RATE * iris.CHUNK_MS // 1000 * 2          # bytes per 100 ms


def pcm(name):
    with wave.open(os.path.join(HERE, "audio", name), "rb") as w:
        return w.readframes(w.getnframes())


def main():
    phrases = [pcm(p) for p in sys.argv[1:]]
    t0 = time.perf_counter()
    state = {"last_speech": time.perf_counter(), "busy": False}

    import events
    bus = events.bus()                     # the overlay and the observer panel listen here

    def emit(e):
        bus.publish(e)
        k = e.get("kind")
        if k in ("target", "click", "overlay_hide"):
            return
        if k == "agent":
            state["last_speech"] = time.perf_counter()
        print(f"{time.perf_counter() - t0:6.2f}s {json.dumps(e, ensure_ascii=False)[:250]}", flush=True)

    async def scripted_mic(app):
        silence = b"\x00" * CHUNK
        await asyncio.sleep(1.0)
        while phrases:
            quiet = (not app.speaking and app.turns.empty() and time.perf_counter() - state["last_speech"] > QUIET_S
                     and (app.awaiting_answer or not state["busy"]))
            if quiet:
                data = phrases.pop(0)
                data += silence[:-len(data) % CHUNK] + silence * 25   # whole 100 ms chunks only
                for i in range(0, len(data), CHUNK):
                    yield data[i:i + CHUNK]
                    await asyncio.sleep(iris.CHUNK_MS / 1000)
                state["last_speech"] = time.perf_counter() + 3     # give Iris time to react
            else:
                yield silence
                await asyncio.sleep(iris.CHUNK_MS / 1000)
        for _ in range(250):                                       # 25 s to finish the last answer
            yield silence
            await asyncio.sleep(iris.CHUNK_MS / 1000)
        os._exit(0)

    app = iris.Iris(emit=emit, mic=scripted_mic)
    # track whether the worker is mid-turn, so the next phrase waits for the answer
    orig_ask = app.brain.ask

    def ask(msg):
        state["busy"] = True
        try:
            return orig_ask(msg)
        finally:
            threading.Timer(0.5, lambda: state.update(busy=False)).start()
    app.brain.ask = ask
    threading.Timer(240, lambda: os._exit(1)).start()
    app.run()


if __name__ == "__main__":
    main()
