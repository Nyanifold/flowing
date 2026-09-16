"""McpTool 测试：测试清单 50–51。

三种连接方式全覆盖：stdio（``command``）/ SSE / streamable HTTP（后两者
``url``，本地回环自研 demo server，不起真实外网）。覆盖：来源互斥
（AmbiguousMcpSourceError / MissingMcpSourceError）、list_tools() 装配
（合成名 / tools 子集 / overrides / outputSchema 自动填 output_schema）、
execute 真实往返、同名冲突 ToolNameConflictError。
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from flowing.errors import (
    AmbiguousMcpSourceError,
    MissingMcpSourceError,
    ToolNameConflictError,
)
from flowing.tool import McpTool, ToolDefinition, ToolRegistry

DEMO_SERVER = Path(__file__).parent.parent / "fixtures" / "mcp" / "demo_server.py"
FIXTURES = Path(__file__).parent.parent / "fixtures"


def _declaration(**kwargs) -> McpTool:
    """声明实例（组代理）：骨架 definition + 连接配置。"""
    return McpTool(
        definition=ToolDefinition(name="github", description="", params_schema={}),
        **kwargs)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_port(port: int, proc: subprocess.Popen, timeout: float = 15.0) -> None:
    """轮询端口就绪；子进程提前退出则带 stderr 内容失败。"""
    for _ in range(int(timeout * 20)):
        if proc.poll() is not None:
            raise RuntimeError(
                f"demo MCP server 提前退出（code={proc.returncode}）: "
                f"{proc.stderr.read().decode(errors='replace') if proc.stderr else ''}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            await asyncio.sleep(0.05)
    raise TimeoutError(f"demo MCP server 端口 {port} 未就绪")


def _start_server(mode: str) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(DEMO_SERVER), mode, str(port)],
                            stderr=subprocess.PIPE)
    return proc, port


# ---------------------------------------------------------------------------
# 清单 50：来源互斥
# ---------------------------------------------------------------------------


def test_mcp_source_mutex():
    with pytest.raises(AmbiguousMcpSourceError):
        _declaration(command="npx", url="http://127.0.0.1:1/sse")
    with pytest.raises(MissingMcpSourceError):
        _declaration()


def test_mcp_url_env_missing_is_format_error(monkeypatch):
    """url 的 ``{{ env.X }}`` 装配期渲染缺失变量 → FormatError（与
    env/headers 通道同口径，不外泄 jinja2.UndefinedError）。"""
    from flowing.errors import FormatError

    monkeypatch.delenv("FLOWING_TEST_GHOST", raising=False)
    with pytest.raises(FormatError, match="environment variable referenced by template"):
        _declaration(url="http://127.0.0.1:{{ env.FLOWING_TEST_GHOST }}/sse")


# ---------------------------------------------------------------------------
# 清单 51 · stdio（command 形态，直接构造）
# ---------------------------------------------------------------------------


async def test_mcp_stdio_roundtrip():
    decl = _declaration(command=sys.executable, args=[str(DEMO_SERVER)])
    tools = await decl.list_tools()
    assert {t.definition.name for t in tools} == {
        "github-create-issue", "github-list-prs"}   # 合成名 <声明名>-<server 名>
    by_name = {t.definition.name: t for t in tools}
    # outputSchema 自动填 output_schema（list-prs 服务端带 outputSchema）
    assert by_name["github-list-prs"].definition.output_schema is not None
    registry = ToolRegistry()
    for t in tools:
        registry.register(t)
    # 同名重复注册 → ToolNameConflictError
    with pytest.raises(ToolNameConflictError):
        registry.register(by_name["github-create-issue"])
    # execute 真实往返（惰性连接）。create-issue 无 outputSchema → 非结构化
    # 文本（JSON 字符串）；list-prs 有 outputSchema → structuredContent dict
    created = await by_name["github-create-issue"]({"title": "标题", "body": "正文"})
    assert created.status == "completed"
    created_out = json.loads(created.output)
    assert created_out["issue_id"] == 42 and created_out["title"] == "标题"
    listed = await by_name["github-list-prs"]({"state": "closed"})
    assert listed.status == "completed"
    assert listed.output["result"][0]["state"] == "closed"


# ---------------------------------------------------------------------------
# 清单 51 · SSE（fixtures fya/tools/github/TOOL.fya + 注册表链）
# ---------------------------------------------------------------------------


@pytest.fixture
async def sse_server():
    proc, port = _start_server("sse")
    await _wait_port(port, proc)
    yield port
    proc.terminate()
    proc.wait(timeout=10)


async def test_mcp_sse_via_fya_fixture(sse_server, fixtures_dir, monkeypatch):
    monkeypatch.setenv("DEMO_MCP_PORT", str(sse_server))
    registry = ToolRegistry()
    decl = registry.get("github", source_dir=fixtures_dir / "fya" / "tools")
    assert isinstance(decl, McpTool) and decl.url.endswith("/sse")
    tools = await decl.list_tools()
    # tools 子集：只暴露 create-issue，list-prs 不暴露
    assert [t.definition.name for t in tools] == ["github-create-issue"]
    tool = tools[0]
    # overrides：description 整体替换 + args 稀疏覆写
    assert tool.definition.description == "创建 GitHub Issue（覆写描述）。"
    assert tool.definition.params_schema["title"]["description"] == \
        "Issue 标题，不超过 80 字符。"
    registry.register(tool)
    assert registry.get("github-create-issue") is tool
    result = await tool({"title": "经 fya 装配"})
    assert result.status == "completed", result.error
    assert json.loads(result.output)["url"] == "https://example.test/issues/42"


# ---------------------------------------------------------------------------
# 清单 51 · streamable HTTP（url 形态，直接构造）
# ---------------------------------------------------------------------------


@pytest.fixture
async def http_mcp_server():
    proc, port = _start_server("http")
    await _wait_port(port, proc)
    yield port
    proc.terminate()
    proc.wait(timeout=10)


async def test_mcp_streamable_http_roundtrip(http_mcp_server):
    decl = _declaration(url=f"http://127.0.0.1:{http_mcp_server}/mcp")
    tools = await decl.list_tools()
    by_name = {t.definition.name: t for t in tools}
    assert set(by_name) == {"github-create-issue", "github-list-prs"}
    result = await by_name["github-create-issue"]({"title": "streamable"})
    assert result.status == "completed", result.error
    assert json.loads(result.output)["title"] == "streamable"


# ---------------------------------------------------------------------------
# 负例：声明实例不可直接执行；连接失败 → error 结果
# ---------------------------------------------------------------------------


async def test_mcp_declaration_instance_not_executable():
    decl = _declaration(command=sys.executable, args=[str(DEMO_SERVER)])
    result = await decl({"title": "x"})
    assert result.status == "error"
    assert "synthetic name" in result.error


async def test_mcp_connection_failure_is_error_result():
    decl = _declaration(command=sys.executable,
                        args=["-c", "import sys; sys.exit(1)"])
    tools = None
    try:
        tools = await decl.list_tools()
    except Exception:
        pass   # list_tools 连接失败上抛（装配期 fail fast，无重试）
    assert tools is None or tools == []
    # execute 路径：连接失败 → status="error" 结果（不抛给调用方）
    tool = _declaration(command=sys.executable,
                        args=["-c", "import sys; sys.exit(1)"])
    tool._server_tool_name = "ghost"
    result = await tool({"x": 1})
    assert result.status == "error"
