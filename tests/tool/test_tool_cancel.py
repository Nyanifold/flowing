"""工具取消竞速测试（Tool.__call__ 默认行为 + BashTool 特化 +
subagent-invoke 前台级联）。

竞速语义：在途 async execute 与取消信号（execution.cancel / 调用方
_turn_abort）竞速；信号先赢 → 中断在途执行 → ToolResult(status=
"cancelled")；工具捕获取消并返回部分产物的，产物带进 cancelled 结果。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from flowing.message import MessageKind, TextBlock, ToolCallBlock
from flowing.tool import Tool, ToolCall, ToolDefinition

from harness import make_runtime, add_fake_provider, script_provider, \
    text_response, tool_call_response


class SlowTool(Tool):
    """秒级睡眠的 script 工具（竞速靶子）。"""

    definition = ToolDefinition(name="slow", description="慢",
                                params_schema={})

    async def execute(self) -> str:
        await asyncio.sleep(30)
        return "done"


async def test_script_tool_race_cancel(tmp_path):
    """script 前台：abort 信号先置位 → 在途 execute 被中断，
    结果 status="cancelled"，不等 30s。"""
    rt = make_runtime(tmp_path)
    rt.register_tool(SlowTool())
    agent = await rt.create_agent("test-agent")
    agent.add_tool("slow")
    step1, calls = tool_call_response(("slow", {}))
    script_provider(add_fake_provider(rt), step1, text_response("ok"))

    async def _abort():
        await asyncio.sleep(0.1)
        agent.abort_turn()

    t = asyncio.ensure_future(_abort())
    r = await agent.query("go")
    await t
    assert r.status == "cancelled"
    results = {m.tool_call_id: m for m in agent._messages.values()
               if m.kind is MessageKind.TOOL}
    assert results[calls[0].id].tool_status == "cancelled"   # 竞速中断的产物
    await rt.shutdown()


async def test_script_tool_cancel_via_execution(tmp_path):
    """execution.cancel 通道（编程式直调）：置位 → cancelled。"""
    rt = make_runtime(tmp_path)
    tool = SlowTool()
    agent = await rt.create_agent("test-agent")
    from flowing.agent import Execution
    from datetime import datetime
    execution = Execution(id="x1", kind="tool", tags=[], started_at=datetime.now(),
                          cancel=asyncio.Event(), pause=asyncio.Event())
    call = ToolCall(id="c1", name="slow", args={})
    task = asyncio.ensure_future(tool({}, caller=agent, execution=execution,
                                      tool_call=call))
    await asyncio.sleep(0.1)
    execution.cancel.set()
    result = await task
    assert result.status == "cancelled"
    await rt.shutdown()


async def test_bash_cancel_kills_group_and_returns_partial(tmp_path):
    """bash 取消：进程组被杀、返回中断前的部分输出 + 中断提示行。"""
    rt = make_runtime(tmp_path)
    agent = await rt.create_agent("test-agent")
    agent.add_tool("bash")
    step1, calls = tool_call_response(
        ("bash", {"command": "echo 前半输出 && sleep 30 && echo 后半输出"}))
    script_provider(add_fake_provider(rt), step1, text_response("ok"))

    async def _abort():
        await asyncio.sleep(0.5)
        agent.abort_turn()

    t = asyncio.ensure_future(_abort())
    r = await agent.query("go")
    await t
    assert r.status == "cancelled"
    results = {m.tool_call_id: m for m in agent._messages.values()
               if m.kind is MessageKind.TOOL}
    msg = results[calls[0].id]
    assert msg.tool_status == "cancelled"
    text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
    assert "前半输出" in text, "中断前已收集的输出保留"
    assert "后半输出" not in text, "中断点之后的输出不存在"
    assert "CancelledError" in text, "中断提示行在"
    # 进程组已死：不再有 sleep 30 残留（杀组即回，不等 30s 即为证）
    await rt.shutdown()


async def test_subagent_invoke_foreground_cancel_cascade(tmp_path):
    """subagent-invoke 前台：父 abort → 等待中断 + 子 Agent 回合级联取消。"""
    from flowing.agent import Agent

    class Child(Agent):
        system_prompt = "子。"

        async def setup(self, **kwargs):
            self.add_tool("slow")   # 注册 ≠ 可见：子侧也要声明

    rt = make_runtime(tmp_path)
    rt.register_agent_type(Child, name="child")
    agent = await rt.create_agent("test-agent")
    agent.add_agent("child")
    agent.add_tool("subagent-invoke")

    # 父与子共用 default provider：脚本序 = 父的派单调用 → 子的慢工具调用
    provider = add_fake_provider(rt)
    step0, _ = tool_call_response(
        ("subagent-invoke", {"agent_type": "child", "prompt": "干活"}))
    step1, _ = tool_call_response(("slow", {}))
    script_provider(provider, step0, step1, text_response("兜底"))
    rt.register_tool(SlowTool())

    async def _abort():
        await asyncio.sleep(0.3)
        agent.abort_turn()

    t = asyncio.ensure_future(_abort())
    r = await agent.query("go")
    await t
    assert r.status == "cancelled"
    # 级联：子 Agent 的回合被取消，且其树内工具调用以 cancelled 封闭
    child = next(a for a in rt._nodes.values()
                 if isinstance(a, Agent) and a is not agent)
    # abort_turn 只是置位——等子的回合实际收尾（轮询，带截止）
    deadline = asyncio.get_running_loop().time() + 5
    while child.current_turn is not None:
        assert asyncio.get_running_loop().time() < deadline, "子回合未收尾"
        await asyncio.sleep(0.05)
    child_results = {m.tool_call_id: m for m in child._messages.values()
                     if m.kind is MessageKind.TOOL}
    assert child_results, "子的被取消工具调用应在树里"
    assert all(m.tool_status == "cancelled" for m in child_results.values())
    await rt.shutdown()
