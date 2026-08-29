"""自研 demo MCP server（官方 ``mcp`` SDK FastMCP）——McpTool 测试服务端。

阶段 3 边界：全部连接仅本地回环 / 本地子进程，不接外部真实 MCP 服务器。

用法::

    python demo_server.py                  # stdio（McpTool command 形态）
    python demo_server.py sse <port>       # SSE @ 127.0.0.1:<port>/sse
    python demo_server.py http <port>      # streamable HTTP @ 127.0.0.1:<port>/mcp

暴露两个工具（``create-issue`` 带结构化输出——FastMCP 自动生成
``outputSchema``，供「自动填 output_schema」断言）：

- ``create-issue(title, body="")`` → dict（issue_id/title/body/url）
- ``list-prs(state="open")`` → list[dict]（number/title/state）
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

_port = int(sys.argv[2]) if len(sys.argv) > 2 else 0
mcp = FastMCP("demo-github", host="127.0.0.1", port=_port)


@mcp.tool(name="create-issue")
def create_issue(title: str, body: str = "") -> dict:
    """创建 GitHub Issue。"""
    return {"issue_id": 42, "title": title, "body": body,
            "url": "https://example.test/issues/42"}


@mcp.tool(name="list-prs")
def list_prs(state: str = "open") -> list[dict]:
    """列出 Pull Request。"""
    return [{"number": 1, "title": "fix typo", "state": state},
            {"number": 2, "title": "add feature", "state": state}]


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    mcp.run(transport={"http": "streamable-http"}.get(mode, mode))
