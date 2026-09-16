"""cron LLM 工具测试。

工具经 ``tool({...}, caller=agent)``（``Tool.__call__`` 调度层）直接驱动
——``caller`` 由框架按签名注入的通道与 LLM 入口一致；校验违约经
``Tool.__call__`` 包装为 ``status="error"`` 的 LLM 可见结果。
"""

from __future__ import annotations

from datetime import datetime

from flowing.plugins.cron import jobs, schedule, unschedule

from cron_support import CronAgent, make_cron_harness


async def _setup(tmp_path):
    runtime = make_cron_harness(tmp_path, install_plugin=True)
    CronAgent.captured = []
    agent = await runtime.create_agent("cron-agent", start_loop=False)
    get = runtime.tool_registry.get
    return runtime, agent, get


async def test_schedule_cron_validation(tmp_path):
    """content 空 / cron 非法 / 占位符格式非法 → status="error" 的
    ToolResult（LLM 可见），且全部未注册。"""
    runtime, agent, get = await _setup(tmp_path)
    other = await runtime.create_agent("cron-agent", start_loop=False)
    try:
        tool = get("schedule-cron")
        empty = await tool({"cron": "* * * * *", "content": ""}, caller=agent)
        bad_cron = await tool({"cron": "bad expr", "content": "x"}, caller=agent)
        bad_fmt = await tool({"cron": "* * * * *",
                              "content": "{{current_time:%Q}}"}, caller=agent)
        for result in (empty, bad_cron, bad_fmt):
            assert result.status == "error", result
            assert result.error                     # LLM 可见错误文本
        assert jobs(agent) == [] and jobs(other) == []
    finally:
        await agent.destroy()
        await other.destroy()


async def test_schedule_cron_receipt_and_caller_lock(tmp_path):
    """收据 {job_id,cron,source,recurring,next_fire}；目标锁定 caller。"""
    runtime, agent, get = await _setup(tmp_path)
    other = await runtime.create_agent("cron-agent", start_loop=False)
    try:
        r = await get("schedule-cron")(
            {"cron": "* * * * *", "content": "x", "source": "s1"},
            caller=agent)
        assert r.status == "completed"
        out = r.output
        assert set(out) == {"job_id", "cron", "source", "recurring",
                            "next_fire"}
        assert out["cron"] == "* * * * *" and out["source"] == "s1"
        assert out["recurring"] is True
        datetime.fromisoformat(out["next_fire"])    # ISO 8601
        # 目标锁定 caller：任务全部归 caller 名下
        assert [j.id for j in jobs(agent)] == [out["job_id"]]
        assert jobs(other) == []
    finally:
        await agent.destroy()
        await other.destroy()


async def test_manage_cron(tmp_path):
    """list 只含 caller 的任务；cancel 缺 job_id → error；存在 →
    {"cancelled": True}，不存在 → False。"""
    runtime, agent, get = await _setup(tmp_path)
    other = await runtime.create_agent("cron-agent", start_loop=False)
    try:
        own = await get("schedule-cron")(
            {"cron": "* * * * *", "content": "mine"}, caller=agent)
        await get("schedule-cron")(
            {"cron": "* * * * *", "content": "others"}, caller=other)
        manage = get("manage-cron")

        listed = await manage({"action": "list"}, caller=agent)
        assert listed.status == "completed"
        jobs_list = listed.output["jobs"]
        assert [j["id"] for j in jobs_list] == [own.output["job_id"]]
        assert jobs_list[0]["content"] == "mine"    # CronJob dict 形态

        missing = await manage({"action": "cancel"}, caller=agent)
        assert missing.status == "error"            # 缺 job_id

        ghost = await manage({"action": "cancel", "job_id": "ghost"},
                             caller=agent)
        assert ghost.status == "completed"
        assert ghost.output == {"cancelled": False}  # 不存在 → False

        gone = await manage({"action": "cancel",
                             "job_id": own.output["job_id"]}, caller=agent)
        assert gone.output == {"cancelled": True}
        assert jobs(agent) == []
        assert len(jobs(other)) == 1                 # 不影响其他 Agent
    finally:
        await agent.destroy()
        await other.destroy()
