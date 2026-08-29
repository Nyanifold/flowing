"""阶段 4 cron 触发链路测试（W23–W24/W26）：测试清单 T46–T47。

T46 走真实 ``loop.call_later`` 武装路径（FakeClock 把「距下一理想点」压到
~0.1s 真实延迟，时钟随真实流逝自动推进）；T47 用 FakeClock 手动跳变 +
直接 ``_fire`` 驱动（shortcut / 合并计数语义，确定性无真实等待）。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from flowing.message import MessageKind, MessagePriority
from flowing.plugins.cron import CronAction

from cron_support import CronAgent, FakeClock, make_cron_harness, next_minute


def _events():
    return [m for m in CronAgent.captured if m.kind is MessageKind.EVENT]


async def test_t46_timer_trigger_chain(tmp_path):
    """T46：after_create 声明式注册 → 推进时钟到下一分钟 → on_cron_trigger
    handler 被调用 → 队列出现 kind=EVENT, source="tick" 的 STEER 消息。"""
    runtime, scheduler = make_cron_harness(tmp_path)
    CronAgent.captured = []
    CronAgent.declarative_jobs = [{
        "cron": "* * * * *", "job_id": "t", "source": "tick",
        "action": CronAction(kind="message", prompt="x"),
    }]
    try:
        agent = await runtime.create_agent("cron-agent", start_loop=False)
        triggers = []
        agent.hooks.on_cron_trigger["tick"](
            lambda a, t: (triggers.append(t), t)[1], by="test")
        # 测试时钟钉在下一理想点前 0.1s（真实武装延迟 0.1s，随真实流逝推进）
        FakeClock(scheduler, next_minute() - timedelta(seconds=0.1))
        # HarnessRuntime 迷你管线不 dispatch after_create（真管线第 9 步
        # 会 dispatch）——显式触发，声明式注册在此发生
        await agent.hooks.after_create.dispatch(agent)
        assert [j.id for j in scheduler.jobs(agent.node_id)] == ["t"]

        await asyncio.sleep(0.6)   # 真实定时器到点 → _on_timer → _fire

        assert [t.job.id for t in triggers] == ["t"]   # handler 被调用
        assert triggers[0].fire.coalesced_count == 1   # 准点触发
        events = _events()
        assert len(events) == 1
        assert events[0].source == "tick"
        assert events[0].priority is MessagePriority.STEER
        assert 'coalescedCount="1"' in events[0].content[0].text
        assert "t" in scheduler._timers   # 回调后按下一理想点重新武装
    finally:
        CronAgent.declarative_jobs = None
        scheduler._stop()
        await agent.destroy()


async def test_t47_shortcut_then_coalesced_delivery(tmp_path):
    """T47：handler 置 shortcut=True → 无 EVENT、游标不推进；
    下一分钟 coalesced_count=2 合并交付。"""
    runtime, scheduler = make_cron_harness(tmp_path)
    CronAgent.captured = []
    agent = await runtime.create_agent("cron-agent", start_loop=False)
    try:
        jid = scheduler.schedule(
            agent.node_id, "* * * * *", job_id="t", source="tick",
            action=CronAction(kind="message", prompt="x"))
        scheduler._disarm(jid)   # 本测试手动驱动 _fire，不走真实定时器
        job = scheduler._jobs[jid]
        triggers = []

        @agent.hooks.on_cron_trigger["tick"]
        def _(a, trigger):
            triggers.append(trigger)
            if trigger.fire.coalesced_count == 1:
                trigger.shortcut = True   # 准点触发跳过（如有活动）
            return trigger

        clock = FakeClock(scheduler, job.created_at)
        point1 = next_minute(job.created_at)

        clock.set(point1 + timedelta(seconds=1))
        await scheduler._fire(jid)
        assert _events() == []               # 无 EVENT 入队
        assert job.last_fired_at is None     # 游标不推进

        clock.set(point1 + timedelta(minutes=1, seconds=1))
        await scheduler._fire(jid)           # 下一分钟：合并交付
        events = _events()
        assert len(events) == 1
        assert 'coalescedCount="2"' in events[0].content[0].text
        assert triggers[-1].fire.coalesced_count == 2
        assert job.last_fired_at is not None   # 成功交付，游标推进
    finally:
        scheduler._stop()
        await agent.destroy()
