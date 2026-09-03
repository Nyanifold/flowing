"""阶段 4 cron 交付例程测试（v2）。

到点触发与恢复补发共用统一交付例程；测试钉死 ``jobs._now`` 为精确时刻
并直接驱动 ``agent._cron._fire(job_id)``（确定性路径，不做真实等待）。
"""

import asyncio

from flowing.message import MessageKind, MessagePriority
from flowing.plugins.cron import schedule

from cron_support import CronAgent, JOBS, make_cron_harness, local_at


def _pin(t):
    JOBS._now = lambda: t          # 精确钉时：isoformat 无微秒噪声


async def _setup(tmp_path):
    runtime = make_cron_harness(tmp_path, install_plugin=False)
    CronAgent.captured = []
    agent = await runtime.create_agent("cron-agent", start_loop=False)
    return runtime, agent


def _events():
    return [m for m in CronAgent.captured if m.kind is MessageKind.EVENT]


async def test_on_time_bare_push(tmp_path):
    """count==1 到点：裸 content（含 {{current_time}} 替换），EVENT/STEER，
    source=job.source，游标推进写透。"""
    runtime, agent = await _setup(tmp_path)
    try:
        _pin(local_at(2026, 9, 3, 7, 0, 0))
        jid = schedule(agent, "* * * * *", "现在是 {{current_time:%H:%M}}",
                       source="tick")
        _pin(local_at(2026, 9, 3, 7, 1, 0))
        await agent._cron._fire(jid)
        events = _events()
        assert len(events) == 1
        msg = events[0]
        assert msg.source == "tick"
        assert msg.priority == MessagePriority.STEER
        assert msg.content[0].text == "现在是 07:01"        # 裸文本，无模板
        assert agent.state.cron_jobs[0]["last_fired_at"] == "2026-09-03T07:01:00"
    finally:
        await agent.destroy()


async def test_empty_source_falls_back_to_cron(tmp_path):
    runtime, agent = await _setup(tmp_path)
    try:
        _pin(local_at(2026, 9, 3, 7, 0, 0))
        jid = schedule(agent, "* * * * *", "x")             # source 缺省 ""
        _pin(local_at(2026, 9, 3, 7, 1, 0))
        await agent._cron._fire(jid)
        assert _events()[0].source == "cron"
    finally:
        await agent.destroy()


async def test_missed_multiple_uses_notice_template(tmp_path):
    """count>=2 错过多次：唯一补发模板自含内容，含 cron/count/last_fired_at；
    content 先替换后拼入。"""
    runtime, agent = await _setup(tmp_path)
    try:
        _pin(local_at(2026, 9, 3, 7, 0, 0))
        jid = schedule(agent, "* * * * *",
                       "请执行每日沉淀 {{current_time:%H:%M}}")
        _pin(local_at(2026, 9, 3, 7, 3, 0))                 # 错过 3 个点
        await agent._cron._fire(jid)
        events = _events()
        assert len(events) == 1
        text = events[0].content[0].text
        assert text.startswith("Scheduled job * * * * * missed 3 trigger(s)")
        assert "last successful delivery: never delivered" in text   # 从未交付的游标
        assert text.endswith("请执行每日沉淀 07:03")          # content 先替换后拼入
        assert agent.state.cron_jobs[0]["last_fired_at"] == "2026-09-03T07:03:00"
    finally:
        await agent.destroy()


async def test_notice_with_previous_delivery(tmp_path):
    """模板 {last_fired_at} 取交付前游标（上次成功交付时刻）。"""
    runtime, agent = await _setup(tmp_path)
    try:
        _pin(local_at(2026, 9, 3, 7, 0, 0))
        jid = schedule(agent, "* * * * *", "沉淀")
        _pin(local_at(2026, 9, 3, 7, 1, 0))
        await agent._cron._fire(jid)                        # 首次成功交付
        _pin(local_at(2026, 9, 3, 7, 4, 0))                 # 再错过 07:02/07:03/07:04
        await agent._cron._fire(jid)
        events = _events()
        assert len(events) == 2
        assert "missed 3 trigger(s) (last successful delivery: 2026-09-03 07:01)" in \
            events[1].content[0].text
    finally:
        await agent.destroy()


async def test_recurring_false_one_shot_self_removes(tmp_path):
    """一次性任务：成功交付一次后自移除（总表与定时器同步）。"""
    runtime, agent = await _setup(tmp_path)
    try:
        _pin(local_at(2026, 9, 3, 7, 0, 0))
        jid = schedule(agent, "* * * * *", "只提醒一次", recurring=False)
        assert jid in agent._cron._timers
        _pin(local_at(2026, 9, 3, 7, 1, 0))
        await agent._cron._fire(jid)
        assert len(_events()) == 1
        assert agent.state.cron_jobs == []                  # 自移除
        assert jid not in agent._cron._timers               # 句柄已取消
    finally:
        await agent.destroy()


async def test_concurrent_fires_do_not_lose_cursors(tmp_path):
    """同分钟两个任务并发交付：写回前重读总表、只动本记录，互不覆盖。"""
    runtime, agent = await _setup(tmp_path)
    try:
        _pin(local_at(2026, 9, 3, 7, 0, 0))
        j1 = schedule(agent, "* * * * *", "A")
        j2 = schedule(agent, "* * * * *", "B")
        _pin(local_at(2026, 9, 3, 7, 2, 0))
        await asyncio.gather(agent._cron._fire(j1),
                             agent._cron._fire(j2))
        recs = {r["id"]: r for r in agent.state.cron_jobs}
        assert recs[j1]["last_fired_at"] == "2026-09-03T07:02:00"
        assert recs[j2]["last_fired_at"] == "2026-09-03T07:02:00"
        assert len(_events()) == 2
    finally:
        await agent.destroy()
