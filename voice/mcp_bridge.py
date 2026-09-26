"""Bridge MCP servers into the voice agent: every MCP tool becomes a client-side tool
of the AssemblyAI session. MCP sessions live on their own event loop in a background
thread, so calls can be made from any thread.

Safety: the server's own hints win (`readOnlyHint` = safe, `destructiveHint` = risky);
a tool without hints is risky if its name or description matches the always-confirm list. Risky calls need a spoken "yes".
"""
import asyncio
import json
import os
import sys
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
from agent import ALWAYS_CONFIRM  # noqa: E402

SEP = "__"   # voice tool name = <server>__<tool>


@dataclass
class BridgedTool:
    server: str
    name: str
    description: str
    schema: dict
    risky: bool

    @property
    def voice_name(self) -> str:
        return f"{self.server}{SEP}{self.name}"


class MCPBridge:
    def __init__(self, servers: dict[str, StdioServerParameters]):
        self.servers = servers
        self.sessions: dict[str, ClientSession] = {}
        self.tools: dict[str, BridgedTool] = {}
        self.loop = asyncio.new_event_loop()
        self._stack = AsyncExitStack()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def _run(self, coro, timeout=60):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _start(self):
        for name, params in self.servers.items():
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.sessions[name] = session
            for t in (await session.list_tools()).tools:
                ann = t.annotations
                if ann and ann.read_only_hint:        # the server says it only reads
                    risky = False
                elif ann and ann.destructive_hint:
                    risky = True
                else:                                 # no hints: fall back to the word list
                    risky = bool(ALWAYS_CONFIRM.search(f"{t.name.replace('_', ' ')} {t.description or ''}"))
                bt = BridgedTool(name, t.name, t.description or "", t.input_schema or {"type": "object"}, risky)
                self.tools[bt.voice_name] = bt

    def start(self) -> "MCPBridge":
        self._run(self._start())
        return self

    def voice_tools(self) -> list[dict]:
        """Tool definitions for the Voice Agent session.update."""
        out = []
        for bt in self.tools.values():
            desc = bt.description + (" This action needs the user's spoken confirmation; the app asks for it."
                                     if bt.risky else "")
            out.append({"type": "function", "name": bt.voice_name, "description": desc.strip(),
                        "parameters": bt.schema, "execution_mode": "interactive", "timeout_seconds": 30})
        return out

    def call(self, voice_name: str, args: dict) -> str:
        bt = self.tools[voice_name]
        res = self._run(self.sessions[bt.server].call_tool(bt.name, args))
        parts = [getattr(c, "text", "") for c in getattr(res, "content", []) or []]
        text = "\n".join(p for p in parts if p) or json.dumps(getattr(res, "structured_content", None))
        return ("ERROR: " + text) if getattr(res, "is_error", False) else text

    def close(self):
        try:
            self._run(self._stack.aclose(), timeout=10)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
