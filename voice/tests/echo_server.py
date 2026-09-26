"""Tiny MCP server for bridge tests: one read-only tool, one destructive tool."""
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer("echo")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def lookup(word: str) -> str:
    """Look a word up."""
    return f"definition of {word}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def wipe(target: str) -> str:
    """Wipe something."""
    return f"wiped {target}"


@mcp.tool()
def share_link(url: str) -> str:
    """Post a link (unannotated, caught by the word list)."""
    return f"posted {url}"


if __name__ == "__main__":
    mcp.run("stdio")
