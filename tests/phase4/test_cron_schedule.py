"""阶段 4 cron 注册/取消/查询与委托总表测试（v2）。

覆盖：use_cron 后 ``cron_jobs`` 键恒存在（首启冻结空表落盘）；模块 API
（schedule/unschedule/jobs）的注册边界校验；二次赋值写透；定时器武装/
取消与总表同步。
"""

import json

import pytest

from flowing.plugins.cron import CronJob, jobs, schedule, unschedule

from cron_support import (
    CronAgent,
    FakeClock,
    SimpleAgent,
    make_cron_harness,
    local_at,
)


async def _fresh_agent(runtime):
    CronAgent.captured = []
    return await runtime.create_agent("cron-agent", start_loop=False)


async def test_key_exists_and_frozen_after_use_cron(tmp_path):
    """use_cron 即登记键：属性读取可用、值为 []，且首启冻结空表落盘。"""
    runtime = make_cron_harness(tmp_path, install_plugin=False)
    agent = await _fresh_agent(runtime)
    try:
        assert agent.state.cron_jobs == []      # 键恒存在（直接属性访问）
        assert hasattr(agent, "_cron")
    finally:
        await agent.destroy()                   # destroy 触发 flush/压缩
    lines = (agent._session_dir / "state.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(x) for x in lines.splitlines() if '"cron_jobs"' in x]
    assert rows and rows[-1]["value"] == []     # 空表已冻结落盘


async def test_schedule_registers_and_persists(tmp_path):
    runtime = make_cron_harness(tmp_path, install_plugin=False)
    agent = await _fresh_agent(runtime)
    clock = FakeClock(local_at(2026, 9, 3, 7, 0, 0))
    try:
        jid = schedule(agent, "3 2 * * *", "每日沉淀 {{current_time}}",
                       job_id="daily", source="consolidation")
        assert jid == "daily"
        assert len(agent.state.cron_jobs) == 1
        rec = agent.state.cron_jobs[0]
        assert rec["id"] == "daily" and rec["cron"] == "3 2 * * *"
        assert rec["source"] == "consolidation"
        assert rec["recurring"] is True and rec["last_fired_at"] is None
        assert "daily" in agent._cron._timers        # 已武装
    finally:
        await agent.destroy()


async def test_schedule_validation_boundary(tmp_path):
    """注册边界校验：未启用 / 空 content / 坏 cron / 占位符非法 / id 冲突。"""
    runtime = make_cron_harness(tmp_path, install_plugin=False)
    agent = await _fresh_agent(runtime)
    try:
        # 未 use_cron
        rt2 = make_cron_harness(tmp_path / "b", install_plugin=False)
        rt2.register_agent_type("test-agent", SimpleAgent)
        plain = await rt2.create_agent("test-agent", start_loop=False)
        try:
            with pytest.raises(ValueError):
                schedule(plain, "* * * * *", "x")
        finally:
            await plain.destroy()
        # 空 content
        with pytest.raises(ValueError, match="content"):
            schedule(agent, "* * * * *", "")
        # cron 字段数 / 非法
        with pytest.raises(ValueError):
            schedule(agent, "0 3 * *", "x")
        with pytest.raises(ValueError):
            schedule(agent, "not a cron", "x")
        # 占位符格式非法（注册期探针）
        with pytest.raises(ValueError, match="current_time"):
            schedule(agent, "* * * * *", "now {{current_time:%Q}}")
        # job_id 冲突
        schedule(agent, "* * * * *", "x", job_id="dup")
        with pytest.raises(ValueError, match="job_id"):
            schedule(agent, "* * * * *", "y", job_id="dup")
        assert len(agent.state.cron_jobs) == 1        # 校验失败全部未注册
    finally:
        await agent.destroy()


async def test_unschedule_and_jobs_snapshot(tmp_path):
    runtime = make_cron_harness(tmp_path, install_plugin=False)
    agent = await _fresh_agent(runtime)
    try:
        a = schedule(agent, "* * * * *", "a")
        b = schedule(agent, "0 3 * * *", "b")
        assert unschedule(agent, a) is True
        assert unschedule(agent, a) is False          # 幂等
        assert a not in agent._cron._timers
        assert [j.id for j in jobs(agent)] == [b]
        # jobs() 只读快照：改动返回值不影响总表
        snap = jobs(agent)
        assert isinstance(snap[0], CronJob)
        snap.clear()
        assert len(jobs(agent)) == 1
        assert unschedule(agent, b) is True
        assert agent.state.cron_jobs == []
    finally:
        await agent.destroy()
