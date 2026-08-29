"""阶段 4 cron 默认执行器测试（W20–W21）：测试清单 T54–T56。

均以 FakeClock 手动跳变 + 直接 ``_fire`` 驱动；EVENT 消息经
``CronAgent.captured``（after_enqueue 钩子）观测。
"""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from flowing.message import MessageKind, MessagePriority, TextBlock
from flowing.plugins.cron import CronAction
from flowing.tool import Tool, ToolDefinition

from cron_support import CronAgent, FakeClock, make_cron_harness, next_minute


class EchoTool(Tool):
    """回声工具：记录调用、返回 ``echo:<text>``。"""

    definition = ToolDefinition(
        name="echo", description="回声",
        params_schema={"text": {"type": "string"}})

    calls: ClassVar[list[dict]] = []

    async def execute(self, text: str = "") -> str:
        type(self).calls.append({"text": text})
        return f"echo:{text}"


class BoomTool(Tool):
    """业务异常工具：execute 恒抛（Tool.__call__ 包装为 error ToolResult）。"""

    definition = ToolDefinition(name="boom", description="爆炸")

    async def execute(self) -> str:
        raise RuntimeError("工具内部炸了")


def _events():
    return [m for m in CronAgent.captured if m.kind is MessageKind.EVENT]


async def _schedule_and_fire(runtime, scheduler, action, *, jump_seconds=61,
                             job_id="j"):
    """注册任务并以 FakeClock 推进一个理想点后直接 _fire；返回 job_id。"""
    CronAgent.captured = []
    agent = await runtime.create_agent("cron-agent", start_loop=False)
    jid = scheduler.schedule(agent.node_id, "* * * * *", job_id=job_id,
                             source="tick", action=action)
    scheduler._disarm(jid)
    clock = FakeClock(scheduler, scheduler._jobs[jid].created_at)
    clock.set(next_minute() + timedelta(seconds=jump_seconds - 60))
    return agent, jid, clock


async def test_t54_default_message_executor(tmp_path):
    """T54：渲染 <cron-fire ... coalescedCount="N"> XML，入队
    kind=EVENT, source=job.source, priority=STEER。"""
    runtime, scheduler = make_cron_harness(tmp_path)
    agent, jid, clock = await _schedule_and_fire(
        runtime, scheduler, CronAction(kind="message", prompt="请执行每日沉淀"))
    try:
        await scheduler._fire(jid)
        events = _events()
        assert len(events) == 1
        msg = events[0]
        assert msg.source == "tick"
        assert msg.priority is MessagePriority.STEER
        text = msg.content[0].text
        assert text.startswith('<cron-fire jobId="j" cron="* * * * *"')
        assert 'recurring="true"' in text and 'coalescedCount="1"' in text
        assert "请执行每日沉淀" in text and text.rstrip().endswith("</cron-fire>")
    finally:
        scheduler._stop()
        await agent.destroy()


async def test_t55_tool_executor_on_time(tmp_path):
    """T55：coalesced_count==1 真执行（ToolCall id 为 cron-<uuid4>），
    多块 EVENT（标注块 + 塑形块）；工具异常 → EVENT 推回，不逃逸。"""
    runtime, scheduler = make_cron_harness(tmp_path)
    runtime.register_tool(EchoTool())
    EchoTool.calls = []
    agent, jid, clock = await _schedule_and_fire(
        runtime, scheduler,
        CronAction(kind="tool_call", tool="echo", args={"text": "hi"}))
    tc_ids = []
    agent.hooks.before_tool_call(
        lambda a, tc: (tc_ids.append(tc.id), tc)[1], by="test")
    agent.add_tool("echo")
    try:
        await scheduler._fire(jid)
        assert EchoTool.calls == [{"text": "hi"}]          # 真执行
        assert tc_ids and tc_ids[0].startswith("cron-")    # cron-<uuid4>
        events = _events()
        assert len(events) == 1
        blocks = events[0].content
        assert len(blocks) >= 2                            # 标注块 + 结果块
        assert blocks[0].text.startswith('<cron-fire jobId="j"')
        assert '<cron-tool-call tool="echo"' in blocks[0].text
        assert any(isinstance(b, TextBlock) and "echo:hi" in b.text
                   for b in blocks[1:])
    finally:
        scheduler._stop()
        await agent.destroy()


async def test_t55_tool_executor_error_paths(tmp_path):
    """T55 续：工具 execute 抛异常 → error ToolResult → 结果块含错误文本
    （EVENT 推回，不逃逸进事件循环）。"""
    runtime, scheduler = make_cron_harness(tmp_path)
    runtime.register_tool(BoomTool())
    agent, jid, clock = await _schedule_and_fire(
        runtime, scheduler, CronAction(kind="tool_call", tool="boom"))
    agent.add_tool("boom")
    try:
        await scheduler._fire(jid)
        events = _events()
        assert len(events) == 1
        assert any("工具内部炸了" in getattr(b, "text", "") for b in events[0].content)
    finally:
        scheduler._stop()
        await agent.destroy()


async def test_t55_tool_executor_unknown_tool(tmp_path):
    """T55 续：未知工具别名（agent.tool_call 上抛）→ <cron-tool-error>
    EVENT，不逃逸。"""
    runtime, scheduler = make_cron_harness(tmp_path)
    agent, jid, clock = await _schedule_and_fire(
        runtime, scheduler, CronAction(kind="tool_call", tool="ghost-tool"))
    try:
        await scheduler._fire(jid)
        events = _events()
        assert len(events) == 1
        assert "<cron-tool-error>" in events[0].content[0].text
    finally:
        scheduler._stop()
        await agent.destroy()


async def test_t56_tool_executor_coalesced_notice(tmp_path):
    """T56：coalesced_count>1 不真执行工具，渲染「休眠期错过 N 次」通知。"""
    runtime, scheduler = make_cron_harness(tmp_path)
    runtime.register_tool(EchoTool())
    EchoTool.calls = []
    agent, jid, clock = await _schedule_and_fire(
        runtime, scheduler,
        CronAction(kind="tool_call", tool="echo", args={"folder": "INBOX"}))
    agent.add_tool("echo")
    clock.set(next_minute() + timedelta(minutes=4, seconds=1))   # 错过 5 个理想点
    try:
        await scheduler._fire(jid)
        assert EchoTool.calls == []            # 不真执行
        events = _events()
        assert len(events) == 1
        text = events[0].content[0].text
        assert 'coalescedCount="5"' in text
        assert "休眠期错过了 5 次对工具 echo 的定时调用" in text
        assert "INBOX" in text               # 参数摘要
    finally:
        scheduler._stop()
        await agent.destroy()
