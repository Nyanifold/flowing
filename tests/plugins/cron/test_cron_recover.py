"""cron 恢复测试。

真 Runtime + 真 recover 管线（``recover_agent`` → setup 重跑 →
``after_recover`` → 补发过期 + 重建定时器）；测试时钟钉死
``flowing.plugins.cron.jobs._now``，消除跨分钟漂移。
"""

from __future__ import annotations

import json
from datetime import timedelta

from flowing.message import MessageKind
from flowing.plugins.cron import CronPlugin, jobs, schedule

from cron_support import (
    FIXTURES_PERSISTENCE,
    CronAgent,
    JOBS,
    add_fake_provider,
    make_runtime,
    local_at,
    script_provider,
    text_response,
)


def _events():
    return [m for m in CronAgent.captured if m.kind is MessageKind.EVENT]


async def test_restart_recover_sweep(tmp_path):
    """schedule 后进程重启（新 Runtime 同目录）→ recover → after_recover
    补发过期（合并一次、模板如实计数）并重建定时器，游标推进。"""
    CronAgent.captured = []
    base = local_at(2026, 9, 3, 7, 0, 0)
    JOBS._now = lambda: base
    rt1 = make_runtime(tmp_path)
    rt1.install(CronPlugin())
    rt1.register_agent_type(CronAgent, name="cron-agent")
    add_fake_provider(rt1)
    agent = await rt1.create_agent("cron-agent")
    aid = agent.node_id
    jid = schedule(agent, "* * * * *", "x", job_id="j", source="tick")
    # 模拟停机区间：游标回拨 5 分钟（写透落盘，随「进程重启」存续）
    agent.state.cron_jobs = [
        {**rec, "last_fired_at": (base - timedelta(minutes=5)).isoformat()}
        for rec in agent.state.cron_jobs
    ]
    await rt1.shutdown()

    fixed = local_at(2026, 9, 3, 7, 0, 30)   # 测试时钟钉死（重启后时刻）
    JOBS._now = lambda: fixed
    rt2 = make_runtime(tmp_path)
    rt2.install(CronPlugin())
    rt2.register_agent_type(CronAgent, name="cron-agent")
    script_provider(add_fake_provider(rt2), text_response("ok"))
    try:
        agent2 = await rt2.recover_agent(aid)
        # after_recover：补发 + 重建
        assert [j.id for j in jobs(agent2)] == [jid]
        assert jid in agent2._cron._timers      # 定时器已重新武装
        events = _events()
        assert len(events) == 1                 # 合并补发一次
        assert "missed 5 trigger(s)" in events[0].content[0].text
        assert agent2.state.cron_jobs[0]["last_fired_at"] == \
            "2026-09-03T07:00:30"               # 游标推进到恢复时刻
    finally:
        await rt2.shutdown()


async def test_recover_coalesced_from_fixture(tmp_path):
    """预写语料：last_fired_at 为一小时前的每分钟任务 → recover 后队列立即
    出现「错过了 60 次」的合并补发，游标推进。

    语料：``tests/fixtures/persistence/state-cron-jobs.jsonl``（新记录
    形态：无 node_id/action，含 content/source）。
    """
    CronAgent.captured = []
    rt1 = make_runtime(tmp_path)
    rt1.install(CronPlugin())
    rt1.register_agent_type(CronAgent, name="cron-agent")
    add_fake_provider(rt1)
    agent = await rt1.create_agent("cron-agent")
    aid, session_dir = agent.node_id, agent._session_dir
    await rt1.shutdown()

    lines = (FIXTURES_PERSISTENCE / "state-cron-jobs.jsonl").read_text(
        encoding="utf-8").splitlines()
    row = json.loads(lines[1])   # {"op":"set","key":"cron_jobs","value":[...]}
    with open(session_dir / "state.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    fixed = local_at(2026, 8, 30, 1, 0, 30)    # 语料 last_fired=00:00:00，一小时后
    JOBS._now = lambda: fixed
    rt2 = make_runtime(tmp_path)
    rt2.install(CronPlugin())
    rt2.register_agent_type(CronAgent, name="cron-agent")
    script_provider(add_fake_provider(rt2), text_response("ok"))
    try:
        agent2 = await rt2.recover_agent(aid)
        events = _events()
        assert len(events) == 1
        assert "missed 60 trigger(s)" in events[0].content[0].text
        assert agent2.state.cron_jobs[0]["id"] == "fixture-minutely"
        assert agent2.state.cron_jobs[0]["last_fired_at"] == \
            "2026-08-30T01:00:30"
        assert "fixture-minutely" in agent2._cron._timers
    finally:
        await rt2.shutdown()
