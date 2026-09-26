"""Local event bus: the agent publishes, the observer panel and the cursor overlay listen.

ws://127.0.0.1:8770 - every message is one JSON event {"kind": ..., "t": ...}.
A client that connects late gets the recent history first, so the panel can be reloaded.
publish() is thread-safe and never blocks the caller.

The panel can also send commands back ({"kind": "cmd", "cmd": "toggle_mute"}); they go to
the handler set with on_command(). Only pages opened from a file (the panel, Origin "null")
or local tools with no Origin may send them - not a website that happens to be open.
"""
import asyncio
import json
import threading
import time
from collections import deque

import websockets

HOST, PORT = "127.0.0.1", 8770


class EventBus:
    def __init__(self, history: int = 200):
        self.clients: set = set()
        self.history: deque[str] = deque(maxlen=history)
        self.command_handler = None
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()
        self._ready.wait(5)

    def _serve(self):
        asyncio.set_event_loop(self.loop)

        async def handler(ws):
            self.clients.add(ws)
            origin = ws.request.headers.get("Origin")
            trusted = origin in (None, "null")
            try:
                for msg in list(self.history):
                    await ws.send(msg)
                async for raw in ws:
                    if not trusted or self.command_handler is None:
                        continue
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and msg.get("kind") == "cmd":
                        # the handler may block (it talks to the voice session): keep it off this loop
                        threading.Thread(target=self.command_handler, args=(str(msg.get("cmd")),),
                                         daemon=True).start()
            except websockets.ConnectionClosed:
                pass
            finally:
                self.clients.discard(ws)

        async def main():
            async with websockets.serve(handler, HOST, PORT):
                self._ready.set()
                await asyncio.Future()

        self.loop.run_until_complete(main())

    async def _broadcast(self, msg: str):
        for ws in list(self.clients):
            try:
                await ws.send(msg)
            except Exception:
                self.clients.discard(ws)

    def publish(self, event: dict) -> None:
        msg = json.dumps({"t": round(time.time(), 3), **event}, ensure_ascii=False)
        # transient visuals are not replayed to late joiners
        if event.get("kind") not in ("cursor", "click", "target"):
            self.history.append(msg)
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self.loop)

    def on_command(self, handler) -> None:
        """handler(cmd: str) is called in its own thread for every command a trusted client sends."""
        self.command_handler = handler

    @property
    def listeners(self) -> int:
        return len(self.clients)


_bus: EventBus | None = None


def bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
