"""注册表名字 glob 与 MCP 合成名展开测试。

``ToolRegistry.glob``（三注册表同构门面的工具侧）与
``ToolRegistry.expand_mcp``（MCP 合成名 ``<声明名>--<server 名>`` 的
声明期异步展开）。MCP 用例经 ``tests/fixtures/mcp/demo_server.py``
的 stdio 形态真实往返。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from flowing.errors import ToolNotFoundError
from flowing.tool import Tool, ToolDefinition
from flowing.tool.registry import ToolRegistry

DEMO_SERVER = Path(__file__).parent.parent / "fixtures" / "mcp" / "demo_server.py"


def _plain(name: str) -> Tool:
    class _T(Tool):
        async def execute(self) -> str:
            return "ok"

    _t = _T()
    _t.definition = ToolDefinition(name=name, description="占位", params_schema={})
    return _t


def _write_mcp_group(project: Path, name: str = "demo") -> Path:
    """在 tmp 项目里写一份 stdio 形态的 MCP 组声明（demo/TOOL.fya）。"""
    d = project / name
    d.mkdir(parents=True)
    fya = d / "TOOL.fya"
    fya.write_text(
        f'name: {name}\ntype: mcp\ndescription: 演示 MCP 组。\n'
        f'command: {sys.executable}\nargs: ["{DEMO_SERVER}"]\n',
        encoding="utf-8")
    return fya


# ---------------------------------------------------------------------------
# glob() 门面
# ---------------------------------------------------------------------------

def test_glob_bare_view_and_qualified():
    """裸名模式查 default::/builtin:: 视图；限定模式查全键；零命中空列表。"""
    registry = ToolRegistry()
    registry.register(_plain("demo--list-prs"))
    registry.register(_plain("demo--create-issue"))
    registry.register(_plain("reader"), namespace="myplugin")
    # 裸名模式：default:: 视图
    assert registry.glob("demo--*") == [
        "default::demo--create-issue", "default::demo--list-prs"]   # 键排序稳定序
    # 限定模式：完整键匹配
    assert registry.glob("myplugin::*") == ["myplugin::reader"]
    assert registry.glob("*::reader") == ["myplugin::reader"]   # 跨命名空间
    # 裸名模式不吃其它命名空间
    assert registry.glob("read*") == []
    # 零命中 → 空列表
    assert registry.glob("no-such-*") == []


def test_glob_builtin_view():
    """builtin:: 键参与裸名视图（内置工具可被名字 glob 命中）。"""
    registry = ToolRegistry()
    registry.register(_plain("read"), namespace="builtin")
    registry.register(_plain("render"), namespace="builtin")
    assert registry.glob("re*") == ["builtin::read", "builtin::render"]
    assert registry.glob("builtin::rea?") == ["builtin::read"]


# ---------------------------------------------------------------------------
# expand_mcp
# ---------------------------------------------------------------------------

async def test_expand_mcp_file_anchor(tmp_path):
    """文件锚：demo/TOOL.fya（type: mcp）→ 精确合成名展开并注册子代理。"""
    _write_mcp_group(tmp_path)
    registry = ToolRegistry()
    assert await registry.expand_mcp("demo--list-prs", source_dir=tmp_path) is True
    tool = registry.get("demo--list-prs")
    assert tool.definition.name == "demo--list-prs"
    listed = await tool({"state": "open"})
    assert listed.status == "completed"
    assert listed.output["result"][0]["state"] == "open"   # outputSchema → structuredContent dict


async def test_expand_mcp_registry_anchor():
    """注册表锚：程序注册的组骨架（无文件）→ 展开。"""
    from flowing.tool.mcp import McpTool

    registry = ToolRegistry()
    group = McpTool(command=sys.executable, args=[str(DEMO_SERVER)],
                    definition=ToolDefinition(name="demo", description="组",
                                              params_schema={}))
    registry.register(group)
    assert await registry.expand_mcp("demo--create-issue") is True
    created = await registry.get("demo--create-issue")({"title": "t", "body": "b"})
    assert created.status == "completed"
    assert json.loads(created.output)["issue_id"] == 42


async def test_expand_mcp_file_anchor_wins(tmp_path):
    """双锚点同在 → 文件锚胜出（文件覆盖注册表同口径）：文件组只暴露子集。"""
    _write_mcp_group(tmp_path)   # 文件组：全量（list-prs + create-issue）
    from flowing.tool.mcp import McpTool

    registry = ToolRegistry()
    skeleton = McpTool(command=sys.executable, args=[str(DEMO_SERVER)],
                       definition=ToolDefinition(name="demo", description="注册表组",
                                                 params_schema={}),
                       tools=["create-issue"])   # 注册表组：子集仅 create-issue
    registry.register(skeleton)
    assert await registry.expand_mcp("demo--list-prs", source_dir=tmp_path) is True
    # 文件组是全量展开 → list-prs 在册（若注册表锚胜出则不在）
    assert "default::demo--list-prs" in registry._tools


async def test_expand_mcp_group_glob(tmp_path):
    """组内模式 demo--*：锚定组展开，该组全部合成名在册。"""
    _write_mcp_group(tmp_path)
    registry = ToolRegistry()
    assert await registry.expand_mcp("demo--*", source_dir=tmp_path) is True
    assert registry.glob("demo--*") == [
        "default::demo--create-issue", "default::demo--list-prs"]


async def test_expand_mcp_non_mcp_prefix(tmp_path):
    """前缀命中非 mcp 声明 → False、不连接（无子代理落账）。"""
    (tmp_path / "demo.py").write_text(
        "import flowing\n\n@flowing.flowing_tool(name='demo')\n"
        "def demo() -> str:\n    return 'x'\n", encoding="utf-8")
    registry = ToolRegistry()
    assert await registry.expand_mcp("demo--list-prs", source_dir=tmp_path) is False
    assert registry.glob("demo--*") == []
    with pytest.raises(ToolNotFoundError):
        registry.get("demo--list-prs")


async def test_expand_mcp_exact_occupied():
    """合成名已被普通工具精确占用 → 不展开（精确命中优先），返回 False。"""
    registry = ToolRegistry()
    mine = _plain("demo--list-prs")
    registry.register(mine)
    assert await registry.expand_mcp("demo--list-prs") is False
    assert registry.get("demo--list-prs") is mine


async def test_expand_mcp_idempotent(tmp_path):
    """组已展开 → 再次展开（含模式形态）不重复连接、不撞名。"""
    _write_mcp_group(tmp_path)
    registry = ToolRegistry()
    assert await registry.expand_mcp("demo--list-prs", source_dir=tmp_path) is True
    assert await registry.expand_mcp("demo--*", source_dir=tmp_path) is True
    assert await registry.expand_mcp("demo--create-issue", source_dir=tmp_path) is False
    assert len(registry.glob("demo--*")) == 2   # 无重复注册


async def test_expand_mcp_no_anchor(tmp_path):
    """无锚点（无文件、无注册骨架）→ False 不抛错。"""
    registry = ToolRegistry()
    assert await registry.expand_mcp("demo--list-prs", source_dir=tmp_path) is False
    # 无 -- 的名字 / 组名段含模式字符 → 同样 False
    assert await registry.expand_mcp("demo-list-prs", source_dir=tmp_path) is False
    assert await registry.expand_mcp("*--list-prs", source_dir=tmp_path) is False
