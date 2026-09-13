"""阶段 4 cron 插件面测试（v2）。

覆盖：CronPlugin 只注册两件工具（无 provide 键）；use_cron 独立于插件
安装；零开销不变量（未 use_cron 的 Agent 无钩子点/无运行时）；destroy
取消定时器句柄。
"""

from __future__ import annotations

import pytest

from flowing.plugins.cron import CronPlugin, schedule, use_cron

from cron_support import (
    CronAgent,
    HarnessRuntime,
    SimpleAgent,
    make_cron_harness,
)


async def test_install_registers_two_tools(tmp_path):
    """CronPlugin.install 只注册 schedule-cron / manage-cron 两件工具。"""
    runtime = HarnessRuntime(tmp_path)
    CronPlugin().install(runtime)
    for name in ("schedule-cron", "manage-cron"):
        tool = runtime.tool_registry.get(name)
        assert tool is not None and tool.definition.name == name


async def test_use_cron_independent_of_plugin(tmp_path):
    """未装 CronPlugin 也能 use_cron + schedule（插件只管 LLM 工具入口）。"""
    runtime = make_cron_harness(tmp_path, install_plugin=False)
    agent = await runtime.create_agent("cron-agent", start_loop=False)
    try:
        jid = schedule(agent, "* * * * *", "x")
        assert jid and agent.state.cron_jobs[0]["content"] == "x"
    finally:
        await agent.destroy()


async def test_zero_overhead_without_use_cron(tmp_path):
    """未 use_cron：无钩子点、无运行时槽；调模块 API 抛 ValueError。"""
    runtime = HarnessRuntime(tmp_path)
    runtime.register_agent_type(SimpleAgent, name="test-agent")
    agent = await runtime.create_agent("test-agent", start_loop=False)
    try:
        assert "on_cron_trigger" not in agent.hooks._hook_points
        assert not hasattr(agent, "_cron")
        with pytest.raises(ValueError):
            schedule(agent, "* * * * *", "x")
    finally:
        await agent.destroy()


async def test_destroy_cancels_timers_keeps_records(tmp_path):
    """destroy：before_destroy 取消全部定时器句柄；记录留在总表随
    session 存续（不删除）。"""
    runtime = make_cron_harness(tmp_path, install_plugin=False)
    agent = await runtime.create_agent("cron-agent", start_loop=False)
    jid = schedule(agent, "* * * * *", "x")
    assert jid in agent._cron._timers
    session_dir = agent._session_dir
    await agent.destroy()
    assert agent._cron._timers == {}            # 定时器已取消
    # 记录仍在（随 session 存续；destroy 关闭 store 时已 flush）
    lines = (session_dir / "state.jsonl").read_text(encoding="utf-8")
    assert any('"cron_jobs"' in line for line in lines.splitlines())
