"""阶段 4 cron 调度器测试（W22–W24）：测试清单 T40–T45、T49、T51–T53。

除 T46（真实 call_later 武装路径）外，触发链路一律以 FakeClock 手动跳变 +
直接 ``await scheduler._fire(job_id)`` 驱动，不依赖真实等待。
"""

from __future__ import annotations

import pytest

from flowing.message import MessageKind
from flowing.plugins.cron import CronAction

from cron_support import (
    CronAgent,
    FakeClock,
    make_cron_harness,
    next_minute,
    utcnow,
)
from datetime import timedelta


@pytest.fixture
async def harness(tmp_path):
    runtime, scheduler = make_cron_harness(tmp_path)
    yield runtime, scheduler
    scheduler._stop()   # 收尾：停全部定时器，防跨测试泄漏
    for node_id, node in list(runtime._nodes.items()):
        if node is runtime:
            continue
        try:
            await node.destroy()
        except Exception:
            pass


async def _new_agent(runtime):
    CronAgent.captured = []
    return await runtime.create_agent("cron-agent", start_loop=False)


def _events():
    return [m for m in CronAgent.captured if m.kind is MessageKind.EVENT]


async def test_t40_bad_cron(harness):
    runtime, scheduler = harness
    with pytest.raises(ValueError):
        scheduler.schedule("agent-1", "bad expr",
                           action=CronAction(kind="message", prompt="x"))
    assert scheduler.jobs() == []   # 任务表仍空


async def test_t41_duplicate_job_id(harness):
    runtime, scheduler = harness
    agent = await _new_agent(runtime)
    scheduler.schedule(agent.node_id, "* * * * *", job_id="a",
                       action=CronAction(kind="message", prompt="x"))
    with pytest.raises(ValueError):
        scheduler.schedule(agent.node_id, "0 3 * * *", job_id="a",
                           action=CronAction(kind="message", prompt="y"))
    job = scheduler.jobs()[0]
    assert job.cron == "* * * * *" and job.action.prompt == "x"   # 原任务不变


async def test_t42_kind_without_executor(harness):
    runtime, scheduler = harness
    agent = await _new_agent(runtime)
    with pytest.raises(ValueError):
        scheduler.schedule(agent.node_id, "* * * * *",
                           action=CronAction(kind="workflow"))   # 无该 kind 执行器
    assert scheduler.jobs() == []


async def test_t43_dormant_target_key_error(harness):
    _, scheduler = harness
    with pytest.raises(KeyError):
        scheduler.schedule("ghost-agent", "* * * * *",
                           action=CronAction(kind="message", prompt="x"))
    assert scheduler.jobs() == []   # S-38：不做半持久化


async def test_t44_unschedule(harness, tmp_path):
    runtime, scheduler = harness
    agent = await _new_agent(runtime)
    assert scheduler.unschedule("ghost") is False   # 不存在 → 幂等 False
    jid = scheduler.schedule(agent.node_id, "* * * * *", job_id="j",
                             action=CronAction(kind="message", prompt="x"))
    assert jid in scheduler._timers   # 已武装
    assert scheduler.unschedule(jid) is True
    assert scheduler.jobs() == [] and jid not in scheduler._timers
    assert agent.state.cron_jobs == []   # 任务表与 state.jsonl 写透同步
    assert scheduler.unschedule(jid) is False   # 再次移除 → False


async def test_t45_jobs_snapshot_and_next_fire(harness):
    runtime, scheduler = harness
    agent = await _new_agent(runtime)
    jid = scheduler.schedule(agent.node_id, "* * * * *", job_id="j",
                             action=CronAction(kind="message", prompt="x"))
    snapshot = scheduler.jobs()
    snapshot.clear()   # 拷贝列表：改返回值不影响调度器内部
    assert len(scheduler.jobs()) == 1
    # next_fire 为 ISO 8601 字符串；不存在的 job_id → KeyError
    from datetime import datetime
    datetime.fromisoformat(scheduler.next_fire(jid))
    with pytest.raises(KeyError):
        scheduler.next_fire("ghost")


async def test_t49_destroy_keeps_jobs(harness):
    runtime, scheduler = harness
    agent = await _new_agent(runtime)
    jid = scheduler.schedule(agent.node_id, "* * * * *", job_id="j",
                             action=CronAction(kind="message", prompt="x"))
    before = scheduler.jobs(agent.node_id)
    await agent.destroy()
    after = scheduler.jobs(agent.node_id)   # destroy ≠ 删除：任务保留
    assert [j.id for j in after] == [j.id for j in before] == [jid]
    assert agent.node_id not in runtime._nodes


async def test_t51_dormant_gate(harness):
    """T51：_fire 第 2 步休眠门控——不交付、不推进游标、任务保留。"""
    runtime, scheduler = harness
    agent = await _new_agent(runtime)
    jid = scheduler.schedule(agent.node_id, "* * * * *", job_id="j",
                             source="tick",
                             action=CronAction(kind="message", prompt="x"))
    runtime._nodes.pop(agent.node_id)   # 模拟休眠（实体出活体表）
    clock = FakeClock(scheduler, utcnow())
    clock.set(next_minute() + timedelta(seconds=1))
    await scheduler._fire(jid)
    assert _events() == []                       # 不交付
    assert scheduler._jobs[jid].last_fired_at is None   # 不推进游标
    assert scheduler.jobs(agent.node_id) != []   # 任务保留


async def test_t52_kind_without_executor_at_fire(harness):
    """T52：恢复出的旧任务 kind 无执行器 → warn 并跳过本次，不逃逸。"""
    runtime, scheduler = harness
    agent = await _new_agent(runtime)
    created = utcnow()
    # 模拟恢复：自定义 kind 旧任务直接重建进表（绕过 schedule 的 kind 校验）
    scheduler._load_jobs(agent.node_id, [{
        "id": "legacy", "node_id": agent.node_id, "cron": "* * * * *",
        "action": {"kind": "legacy-kind", "prompt": "", "tool": "", "args": {}},
        "source": "legacy", "created_at": created.isoformat(),
    }])
    scheduler._disarm("legacy")   # 本测试不走真实定时器
    clock = FakeClock(scheduler, created)
    clock.set(next_minute(created) + timedelta(seconds=1))
    with pytest.warns(UserWarning, match="无注册执行器"):
        await scheduler._fire("legacy")
    assert _events() == []
    assert scheduler._jobs["legacy"].last_fired_at is None   # 未交付，游标不动


async def test_t53_one_shot_self_delete(harness):
    """T53：recurring=False 第一次成功交付后自删；到点未交付不自删。"""
    runtime, scheduler = harness
    agent = await _new_agent(runtime)
    jid = scheduler.schedule(
        agent.node_id, "* * * * *", job_id="once", source="tick",
        recurring=False, action=CronAction(kind="message", prompt="x"))
    scheduler._disarm(jid)   # 手动驱动，不走真实定时器
    job = scheduler._jobs[jid]
    clock = FakeClock(scheduler, job.created_at)

    # 先到点但被 shortcut 跳过 → 不算成功交付，不自删
    @agent.hooks.on_cron_trigger["tick"]
    def _(a, trigger):
        trigger.shortcut = True
        return trigger

    clock.set(next_minute(job.created_at) + timedelta(seconds=1))
    await scheduler._fire(jid)
    assert jid in scheduler._jobs and _events() == []

    # 解除 shortcut，下一理想点成功交付 → 自删（内存表与落盘同步）
    agent.hooks.on_cron_trigger[0].enabled = False
    clock.set(next_minute(job.created_at) + timedelta(minutes=1, seconds=1))
    await scheduler._fire(jid)
    assert jid not in scheduler._jobs
    assert agent.state.cron_jobs == []
    assert len(_events()) == 1
