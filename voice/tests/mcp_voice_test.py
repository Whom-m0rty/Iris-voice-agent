"""Voice client + MCP bridge against the mock server, with the echo MCP server."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp import StdioServerParameters
import client
servers = {"echo": StdioServerParameters(command=sys.executable, args=[os.path.join(os.path.dirname(__file__), "echo_server.py")])}
asyncio.run(client.VoiceSession(emit=lambda e: print(json.dumps(e, ensure_ascii=False)[:220], flush=True),
                                mcp_servers=servers).run())
