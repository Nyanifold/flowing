"""阶段 4 cron 钩子（on_cron_trigger）测试（v2）。

钩子逐智能体声明（``by="cron"``，``match_on="source"``）：交付例程在
组装文本前 dispatch ``CronFireContext``；handler 可改写 content / 置
shortcut / ``raise Intercepted``；pattern 注册按 value 顶层 source 过滤。
"""

from flowing.errors import Intercepted
from flowing.message import MessageKind
from flowing.plugins.cron import CronFireContext, schedule

from cron_support import CronAgent, JOBS, make_cron_harness, local_at


def _pin(t):
    JOBS._now = lambda: t


async def _setup(tmp_path):
    runtime = make_cron_harness(tmp_path, install_plugin=False)
    CronAgent.captured = []
    agent = await runtime.create_agent("cron-agent", start_loop=False)
    return runtime, agent


def _events():
    return [m for m in CronAgent.captured if m.kind is MessageKind.EVENT]


async def test_hook_rewrites_content(tmp_path):
    """plain handler 可改写 ctx.content（仅本次生效）；改写文本同样走
    {{current_time}} 替换。"""
    runtime, agent = await _setup(tmp_path)
    try:
        seen: list[CronFireContext] = []

        def rewrite(a, fire: CronFireContext) -> CronFireContext:
            seen.append(fire)
            fire.content = "改写后 {{current_time:%H:%M}}"
            return fire

        agent.hooks.on_cron_trigger(rewrite, by="test")
        _pin(local_at(2026, 9, 3, 7, 0, 0))
        jid = schedule(agent, "* * * * *", "原文")
        _pin(local_at(2026, 9, 3, 7, 1, 0))
        await agent._cron._fire(jid)
        assert len(seen) == 1
        fire = seen[0]
        assert fire.id == jid and fire.source == "" and fire.cron == "* * * * *"
        assert fire.coalesced_count == 1
        assert _events()[0].content[0].text == "改写后 07:01"
        assert agent.state.cron_jobs[0]["content"] == "原文"   # 改写不落盘
    finally:
        await agent.destroy()


async def test_shortcut_skips_and_accumulates(tmp_path):
    """shortcut 跳过本次（游标不推进、miss 累积）；pattern 按 source 过滤。"""
    runtime, agent = await _setup(tmp_path)
    try:

        def skip_when_single(a, fire: CronFireContext) -> CronFireContext:
            if fire.coalesced_count == 1:
                fire.shortcut = True      # 首次（count==1）跳过
            return fire

        agent.hooks.on_cron_trigger["skip-*"](skip_when_single)
        _pin(local_at(2026, 9, 3, 7, 0, 0))
        jid = schedule(agent, "* * * * *", "沉淀", source="skip-daily")
        _pin(local_at(2026, 9, 3, 7, 1, 0))
        await agent._cron._fire(jid)                       # count==1 → 跳过
        assert _events() == []
        assert agent.state.cron_jobs[0]["last_fired_at"] is None   # 游标不动
        _pin(local_at(2026, 9, 3, 7, 2, 0))
        await agent._cron._fire(jid)                       # count==2 → 放行
        events = _events()
        assert len(events) == 1
        assert "错过了 2 次触发" in events[0].content[0].text
        assert agent.state.cron_jobs[0]["last_fired_at"] == "2026-09-03T07:02:00"
    finally:
        await agent.destroy()


async def test_intercepted_skips(tmp_path):
    runtime, agent = await _setup(tmp_path)
    try:

        def block(a, fire: CronFireContext) -> CronFireContext:
            raise Intercepted("本轮不提醒")

        agent.hooks.on_cron_trigger(block, by="test")
        _pin(local_at(2026, 9, 3, 7, 0, 0))
        jid = schedule(agent, "* * * * *", "x", source="block")
        _pin(local_at(2026, 9, 3, 7, 1, 0))
        await agent._cron._fire(jid)
        assert _events() == []
        assert agent.state.cron_jobs[0]["last_fired_at"] is None
    finally:
        await agent.destroy()


async def test_pattern_groups_by_source(tmp_path):
    """pattern 注册按 source fnmatch 分组：不同 source 只命中对应 handler。"""
    runtime, agent = await _setup(tmp_path)
    try:
        daily_seen: list[str] = []
        all_seen: list[str] = []

        def on_daily(a, fire: CronFireContext) -> CronFireContext:
            daily_seen.append(fire.id)
            return fire

        def on_all(a, fire: CronFireContext) -> CronFireContext:
            all_seen.append(fire.id)
            return fire

        agent.hooks.on_cron_trigger["daily-*"](on_daily)
        agent.hooks.on_cron_trigger(on_all, by="test")
        _pin(local_at(2026, 9, 3, 7, 0, 0))
        da = schedule(agent, "* * * * *", "da", source="daily-a")
        db = schedule(agent, "* * * * *", "db", source="daily-b")
        wc = schedule(agent, "* * * * *", "wc", source="week-c")
        for minute, jid in ((1, da), (2, db), (3, wc)):
            _pin(local_at(2026, 9, 3, 7, minute, 0))
            await agent._cron._fire(jid)
        assert daily_seen == [da, db]                      # 只命中 daily-*
        assert all_seen == [da, db, wc]                    # plain 全收
    finally:
        await agent.destroy()
