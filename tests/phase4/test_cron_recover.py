"""阶段 4 cron 恢复回顾测试（W24/W26）：测试清单 T39、T48。

真 Runtime + 真 recover 管线（``recover_agent`` → setup 重跑 →
``after_recover`` → ``_load_jobs`` + ``_sweep``）；测试时钟钉死
``scheduler._now``，消除跨分钟漂移。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from flowing.message import MessageKind
from flowing.plugins.cron import CronAction, CronPlugin, cron_scheduler_key

from cron_support import (
    FIXTURES_PERSISTENCE,
    CronAgent,
    add_fake_provider,
    make_runtime,
    script_provider,
    text_response,
    utcnow,
)


def _events():
    return [m for m in CronAgent.captured if m.kind is MessageKind.EVENT]


async def test_t39_restart_recover_sweep(tmp_path):
    """T39：schedule 后进程重启（新 Runtime 同目录）→ 按 agent_id 恢复 →
    jobs(node_id) 重含该任务，且 after_recover 触发的 sweep 立即合并交付一次。"""
    CronAgent.captured = []
    rt1 = make_runtime(tmp_path)
    rt1.use(CronPlugin())
    rt1.register_agent_type("cron-agent", CronAgent)
    add_fake_provider(rt1)
    agent = await rt1.create_agent("cron-agent")
    scheduler1 = rt1.inject(cron_scheduler_key)
    jid = scheduler1.schedule(
        agent.node_id, "* * * * *", job_id="j", source="tick",
        action=CronAction(kind="message", prompt="x"))
    # 模拟停机区间：游标回拨 5 分钟（写透落盘，随「进程重启」存续）
    base = utcnow().replace(second=0, microsecond=0)
    agent.state.cron_jobs = [
        {**j, "last_fired_at": (base - timedelta(minutes=5)).isoformat()}
        for j in agent.state.cron_jobs
    ]
    await rt1.shutdown()   # 插件收尾停全部定时器；session 记录保留

    rt2 = make_runtime(tmp_path)   # 新 Runtime 同目录 = 进程重启
    rt2.use(CronPlugin())
    rt2.register_agent_type("cron-agent", CronAgent)
    script_provider(add_fake_provider(rt2), text_response("ok"))
    scheduler2 = rt2.inject(cron_scheduler_key)
    fixed = base + timedelta(seconds=30)   # 测试时钟钉死
    scheduler2._now = lambda: fixed
    try:
        await rt2.recover_agent(agent.node_id)
        # after_recover：_load_jobs 重建任务表与定时器 + _sweep 立即合并交付
        assert [j.id for j in scheduler2.jobs(agent.node_id)] == [jid]
        assert jid in scheduler2._timers   # 定时器已重新武装
        events = _events()
        assert len(events) == 1            # sweep 立即合并交付一次
        assert 'coalescedCount="5"' in events[0].content[0].text  # 覆盖停机区间
        assert scheduler2._jobs[jid].last_fired_at == fixed      # 游标推进
    finally:
        await rt2.shutdown()


async def test_t48_recover_coalesced_60(tmp_path):
    """T48：last_fired_at 为一小时前的每分钟任务 → recover 后队列立即出现
    coalescedCount=60 的 EVENT，last_fired_at 推进到现在（测试时钟）。

    语料：``tests/fixtures/persistence/state-cron-jobs.jsonl``（预写
    cron_jobs 键），node_id 改写为本测试 agent 后追加进 session 的
    state.jsonl（模拟停机前已有的任务表）。
    """
    CronAgent.captured = []
    rt1 = make_runtime(tmp_path)
    rt1.use(CronPlugin())
    rt1.register_agent_type("cron-agent", CronAgent)
    add_fake_provider(rt1)
    agent = await rt1.create_agent("cron-agent")
    agent_id, session_dir = agent.node_id, agent._session_dir
    await rt1.shutdown()

    lines = (FIXTURES_PERSISTENCE / "state-cron-jobs.jsonl").read_text(
        encoding="utf-8").splitlines()
    row = json.loads(lines[1])   # {"op":"set","key":"cron_jobs","value":[...]}
    row["value"][0]["node_id"] = agent_id
    with open(session_dir / "state.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    rt2 = make_runtime(tmp_path)
    rt2.use(CronPlugin())
    rt2.register_agent_type("cron-agent", CronAgent)
    script_provider(add_fake_provider(rt2), text_response("ok"))
    scheduler2 = rt2.inject(cron_scheduler_key)
    # fixture 的 last_fired_at=2026-08-30T00:00:00；钉住 now 为一小时后
    fixed = datetime(2026, 8, 30, 1, 0, 30)
    scheduler2._now = lambda: fixed
    try:
        await rt2.recover_agent(agent_id)
        events = _events()
        assert len(events) == 1
        assert 'coalescedCount="60"' in events[0].content[0].text
        assert scheduler2._jobs["fixture-minutely"].last_fired_at == fixed
    finally:
        await rt2.shutdown()
