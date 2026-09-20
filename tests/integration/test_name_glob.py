"""名字 glob 与 MCP 合成名的装配 e2e（真 Runtime + fya mount）。

覆盖：精确合成名声明、组内 glob、注册表名字 glob（裸名 / 限定模式）、
并集制判重与冲突优先级、路径 glob 扫入 MCP 组、subagents 名字 glob、
程序化路径的展开义务。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from flowing.errors import ToolNotFoundError

from harness import make_runtime

DEMO_SERVER = Path(__file__).parent.parent / "fixtures" / "mcp" / "demo_server.py"

ROOT_FYA = """\
description: 名字 glob 测试根。
model_tag: default
{tools_block}{subagents_block}---
$system_prompt:
你是测试助手。
"""

MCP_TOOL_FYA = (
    'name: demo\ntype: mcp\ndescription: 演示 MCP 组。\n'
    f'command: {sys.executable}\nargs: ["{DEMO_SERVER}"]\n')


def _project(tmp_path: Path, *, tools: str = "", subagents: str = "",
             with_mcp_group: bool = True) -> None:
    (tmp_path / "root.fya").write_text(
        ROOT_FYA.format(
            tools_block=f"tools:\n{tools}\n" if tools else "",
            subagents_block=f"subagents:\n{subagents}\n" if subagents else ""),
        encoding="utf-8")
    if with_mcp_group:
        (tmp_path / "demo").mkdir()
        (tmp_path / "demo" / "TOOL.fya").write_text(MCP_TOOL_FYA, encoding="utf-8")


async def test_exact_synthesized_name_mounts(tmp_path):
    """tools: [demo--list-prs] 精确合成名：挂载即展开，条目落账、可调用。"""
    _project(tmp_path, tools="  - demo--list-prs")
    runtime = make_runtime(tmp_path)
    agent = await runtime.mount("@/root.fya")
    assert "demo--list-prs" in agent._tool_entries
    tool = runtime.tool_registry.get("demo--list-prs")
    result = await tool({"state": "open"})
    assert result.status == "completed"


async def test_group_glob_mounts_all(tmp_path):
    """tools: [demo--*] 组内 glob：组展开，两个合成名都进绑定。"""
    _project(tmp_path, tools="  - demo--*")
    runtime = make_runtime(tmp_path)
    agent = await runtime.mount("@/root.fya")
    assert set(agent._tool_entries) == {"demo--create-issue", "demo--list-prs"}


async def test_registry_name_glob(tmp_path):
    """注册表名字 glob：裸名模式查 default::/builtin:: 视图，限定模式查全键。"""
    _project(tmp_path, tools="  - read*\n  - 'builtin::w*'")
    runtime = make_runtime(tmp_path)
    agent = await runtime.mount("@/root.fya")
    assert set(agent._tool_entries) == {"read", "write"}


async def test_union_dedup_same_resource(tmp_path):
    """并集判重：显式 read + 模式 read* 命中同一资源 → 单条目、无冲突。"""
    _project(tmp_path, tools="  - read\n  - 'read*'", with_mcp_group=False)
    runtime = make_runtime(tmp_path)
    agent = await runtime.mount("@/root.fya")
    assert list(agent._tool_entries) == ["read"]


async def test_conflict_local_file_wins(tmp_path, caplog):
    """glob 内部撞别名：本地文件工具 read 与内置 read 同被 read* 命中 →
    文件侧胜出 + warning（文件覆盖注册表的 glob 延伸）。"""
    (tmp_path / "read.py").write_text(
        "import flowing\n\n@flowing.flowing_tool(name='read')\n"
        "def local_read() -> str:\n    return 'local'\n", encoding="utf-8")
    _project(tmp_path, tools="  - read*", with_mcp_group=False)
    runtime = make_runtime(tmp_path)
    agent = await runtime.mount("@/root.fya")
    assert list(agent._tool_entries) == ["read"]
    assert agent._tool_entries["read"].name_ori != "read"   # 文件派生键落账
    assert "already claimed" in caplog.text   # 内置命中被跳过并告警


async def test_subagents_name_glob(tmp_path):
    """subagents: [helper-*] 名字 glob：注册表里的两个类型都进绑定。"""
    from flowing.agent import Agent

    class HelperA(Agent):
        system_prompt = None

    class HelperB(Agent):
        system_prompt = None

    _project(tmp_path, subagents="  - 'helper-*'", with_mcp_group=False)
    runtime = make_runtime(tmp_path)
    runtime.register_agent_type(HelperA, name="helper-a")
    runtime.register_agent_type(HelperB, name="helper-b")
    agent = await runtime.mount("@/root.fya")
    assert set(agent._subagent_entries) == {"helper-a", "helper-b"}


async def test_path_glob_sweeps_mcp_group(tmp_path):
    """路径 glob 命中 mcp 型组声明 → 转组内展开（不产骨架条目）。"""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "demo").mkdir()
    (tmp_path / "tools" / "demo" / "TOOL.fya").write_text(MCP_TOOL_FYA,
                                                          encoding="utf-8")
    (tmp_path / "tools" / "echo.py").write_text(
        "import flowing\n\n@flowing.flowing_tool(name='echo')\n"
        "def echo() -> str:\n    return 'x'\n", encoding="utf-8")
    _project(tmp_path, tools="  - ./tools/*", with_mcp_group=False)
    runtime = make_runtime(tmp_path)
    agent = await runtime.mount("@/root.fya")
    assert set(agent._tool_entries) == {
        "demo--create-issue", "demo--list-prs", "echo"}


async def test_zero_hit_is_empty(tmp_path):
    """双空间零命中 → 不报错、不产条目。"""
    _project(tmp_path, tools="  - 'zzz-*'", with_mcp_group=False)
    runtime = make_runtime(tmp_path)
    agent = await runtime.mount("@/root.fya")
    assert agent._tool_entries == {}


async def test_programmatic_add_tool_needs_expand(tmp_path):
    """程序化路径：add_tool 合成名未先展开 → ToolNotFoundError；
    await expand_mcp 后 → 成功。"""
    _project(tmp_path, with_mcp_group=True)
    runtime = make_runtime(tmp_path)
    agent = await runtime.mount("@/root.fya")
    with pytest.raises(ToolNotFoundError):
        agent.add_tool("demo--list-prs")
    assert await runtime.tool_registry.expand_mcp(
        "demo--list-prs", source_dir=tmp_path) is True
    agent.add_tool("demo--list-prs")
    assert "demo--list-prs" in agent._tool_entries
