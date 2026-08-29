"""阶段 4 cron 插件面测试（W26）：测试清单 T37–T39 之外的部分——
T37（install 注册面）、T38（执行器覆盖语义）、T50（双层启用零开销）、
T60（shutdown 插件收尾）。（T39 恢复回顾在 test_cron_recover.py。）
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from flowing.errors import MissingProvideError
from flowing.message import MessageKind
from flowing.plugins.cron import (
    CronAction,
    CronPlugin,
    CronScheduler,
    cron_scheduler_key,
    use_cron,
)

from cron_support import (
    CronAgent,
    FakeClock,
    make_cron_harness,
    make_runtime,
    add_fake_provider,
    next_minute,
)


async def test_t37_install_surface(tmp_path):
    """T37：新 Runtime use(CronPlugin()) → inject 得调度器；四件工具就位。"""
    runtime = make_runtime(tmp_path)
    runtime.use(CronPlugin())
    try:
        scheduler = runtime.inject(cron_scheduler_key)
        assert isinstance(scheduler, CronScheduler)
        for name in ("schedule-cron", "schedule-cron-message",
                     "schedule-cron-tool-call", "manage-cron"):
            assert runtime.tool_registry.get(name) is not None
    finally:
        await runtime.shutdown()


async def test_t38_executor_override(tmp_path):
    """T38：CronPlugin(executors={"message": spy}) → spy 以
    (agent, job, action, ctx) 被调用，默认入队不发生（覆盖语义）。"""
    seen = []

    async def spy(agent, job, action, ctx):
        seen.append((agent, job, action, ctx))

    runtime, scheduler = make_cron_harness(tmp_path, executors={"message": spy})
    CronAgent.captured = []
    agent = await runtime.create_agent("cron-agent", start_loop=False)
    try:
        jid = scheduler.schedule(
            agent.node_id, "* * * * *", job_id="j", source="tick",
            action=CronAction(kind="message", prompt="x"))
        scheduler._disarm(jid)
        clock = FakeClock(scheduler, scheduler._jobs[jid].created_at)
        clock.set(next_minute() + timedelta(seconds=1))
        await scheduler._fire(jid)
        assert len(seen) == 1
        s_agent, s_job, s_action, s_ctx = seen[0]
        assert s_agent is agent and s_job.id == jid
        assert s_action.kind == "message" and s_action.prompt == "x"
        assert s_ctx.coalesced_count == 1
        # 覆盖语义：默认入队不发生
        assert [m for m in CronAgent.captured if m.kind is MessageKind.EVENT] == []
    finally:
        scheduler._stop()
        await agent.destroy()


async def test_t50_use_cron_without_plugin(tmp_path):
    """T50：未装 CronPlugin → use_cron(self) 抛 MissingProvideError。"""
    from cron_support import HarnessRuntime, SimpleAgent

    runtime = HarnessRuntime(tmp_path)
    runtime.register_agent_type("test-agent", SimpleAgent)
    agent = await runtime.create_agent("test-agent", start_loop=False)
    try:
        with pytest.raises(MissingProvideError):
            use_cron(agent)
        assert "on_cron_trigger" not in agent.hooks._hook_points   # 零开销：无钩子点
    finally:
        await agent.destroy()


async def test_t60_shutdown_stops_timers(tmp_path):
    """T60：runtime.shutdown() 插件收尾 → _stop 停全部定时器，
    任务定义不删（重放可恢复）。"""
    runtime = make_runtime(tmp_path)
    runtime.use(CronPlugin())
    runtime.register_agent_type("cron-agent", CronAgent)
    add_fake_provider(runtime)
    agent = await runtime.create_agent("cron-agent")
    scheduler = runtime.inject(cron_scheduler_key)
    jid = scheduler.schedule(
        agent.node_id, "* * * * *", job_id="j", source="tick",
        action=CronAction(kind="message", prompt="x"))
    session_dir = agent._session_dir
    assert jid in scheduler._timers   # 已武装

    await runtime.shutdown()

    assert scheduler._timers == {}          # 全部定时器已停
    assert jid in scheduler._jobs           # 任务定义不删
    lines = (session_dir / "state.jsonl").read_text(encoding="utf-8")
    jobs_rows = [json.loads(x) for x in lines.splitlines()
                 if '"cron_jobs"' in x]
    assert jobs_rows and any(
        j["id"] == jid for row in jobs_rows for j in row["value"])   # 落盘存续
