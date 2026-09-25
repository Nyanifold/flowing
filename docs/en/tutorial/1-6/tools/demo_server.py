"""Self-written demo MCP server (official ``mcp`` SDK MCPServer) — the McpTool test server.

Test boundary: all connections are local loopback / local subprocess only;
no external real MCP server is contacted.

Usage::

    python demo_server.py                  # stdio (McpTool command form)
    python demo_server.py sse <port>       # SSE @ 127.0.0.1:<port>/sse
    python demo_server.py http <port>      # streamable HTTP @ 127.0.0.1:<port>/mcp

Exposes two tools (``create-issue`` has structured output — MCPServer
generates the ``outputSchema`` automatically, used by the "auto-filled
output_schema" assertion):

- ``create-issue(title, body="")`` → dict (issue_id/title/body/url)
- ``list-prs(state="open")`` → list[dict] (number/title/state)
"""

from __future__ import annotations

import sys

from mcp.server.mcpserver import MCPServer

_port = int(sys.argv[2]) if len(sys.argv) > 2 else 0
mcp = MCPServer("demo-github")


@mcp.tool(name="create-issue")
def create_issue(title: str, body: str = "") -> dict:
    """Create a GitHub issue."""
    return {"issue_id": 42, "title": title, "body": body,
            "url": "https://example.test/issues/42"}


@mcp.tool(name="list-prs")
def list_prs(state: str = "open") -> list[dict]:
    """List pull requests."""
    return [{"number": 1, "title": "fix typo", "state": state},
            {"number": 2, "title": "add feature", "state": state}]


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    transport = {"http": "streamable-http"}.get(mode, mode)
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport, host="127.0.0.1", port=_port)
