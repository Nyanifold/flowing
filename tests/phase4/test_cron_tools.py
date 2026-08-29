"""阶段 4 cron LLM 工具测试（W25）：测试清单 T57–T59。

工具经 ``tool({...}, caller=agent)``（``Tool.__call__`` 调度层）直接驱动
——``caller`` 由框架按签名注入的通道与 LLM 入口一致；校验违约经
``Tool.__call__`` 包装为 ``status="error"`` 的 LLM 可见结果。
"""

from __future__ import annotations

from datetime import datetime

from flowing.plugins.cron import cron_scheduler_key

from cron_support import CronAgent, make_cron_harness


async def _setup(tmp_path):
    runtime, scheduler = make_cron_harness(tmp_path)
    CronAgent.captured = []
    agent = await runtime.create_agent("cron-agent", start_loop=False)
    get = runtime.tool_registry.get
    return runtime, scheduler, agent, get


async def test_t57_schedule_cron_validation(tmp_path):
    """T57：prompt/tool 同时给出或同时缺失、tool_args 在缺 tool 时给出、
    cron 非法 → 均为 status="error" 的 ToolResult。"""
    runtime, scheduler, agent, get = await _setup(tmp_path)
    try:
        tool = get("schedule-cron")
        both = await tool({"cron": "* * * * *", "prompt": "x", "tool": "t"},
                          caller=agent)
        neither = await tool({"cron": "* * * * *"}, caller=agent)
        orphan_args = await tool({"cron": "* * * * *", "prompt": "x",
                                  "tool_args": {"a": 1}}, caller=agent)
        bad_cron = await tool({"cron": "bad expr", "prompt": "x"}, caller=agent)
        for result in (both, neither, orphan_args, bad_cron):
            assert result.status == "error", result
            assert result.error                       # LLM 可见错误文本
        assert scheduler.jobs() == []                 # 全部未注册
    finally:
        scheduler._stop()
        await agent.destroy()


async def test_t58_caller_locking_and_receipt(tmp_path):
    """T58：三个 schedule 工具目标均锁定 caller.node_id；收据含
    {job_id, cron, next_fire}（ISO 8601）。"""
    runtime, scheduler, agent, get = await _setup(tmp_path)
    other = await runtime.create_agent("cron-agent", start_loop=False)
    try:
        r1 = await get("schedule-cron")(
            {"cron": "* * * * *", "prompt": "x", "source": "s1"}, caller=agent)
        r2 = await get("schedule-cron-message")(
            {"cron": "0 3 * * *", "prompt": "y"}, caller=agent)
        r3 = await get("schedule-cron-tool-call")(
            {"cron": "*/5 * * * *", "tool": "check"}, caller=agent)
        for receipt, cron in ((r1, "* * * * *"), (r2, "0 3 * * *"),
                              (r3, "*/5 * * * *")):
            assert receipt.status == "completed"
            out = receipt.output
            assert set(out) == {"job_id", "cron", "next_fire"}
            assert out["cron"] == cron
            datetime.fromisoformat(out["next_fire"])   # ISO 8601
        # 目标锁定 caller：LLM 参数面无 node_id，任务全部归 caller 名下
        assert len(scheduler.jobs(agent.node_id)) == 3
        assert scheduler.jobs(other.node_id) == []
        kinds = {j.action.kind for j in scheduler.jobs(agent.node_id)}
        assert kinds == {"message", "tool_call"}
    finally:
        scheduler._stop()
        await agent.destroy()
        await other.destroy()


async def test_t59_manage_cron(tmp_path):
    """T59：list 只含 caller 的任务；cancel 缺 job_id → error；
    存在 → {"cancelled": True}，不存在 → False。"""
    runtime, scheduler, agent, get = await _setup(tmp_path)
    other = await runtime.create_agent("cron-agent", start_loop=False)
    try:
        own = await get("schedule-cron-message")(
            {"cron": "* * * * *", "prompt": "mine"}, caller=agent)
        await get("schedule-cron-message")(
            {"cron": "* * * * *", "prompt": "others"}, caller=other)
        manage = get("manage-cron")

        listed = await manage({"action": "list"}, caller=agent)
        assert listed.status == "completed"
        jobs = listed.output["jobs"]
        assert [j["id"] for j in jobs] == [own.output["job_id"]]   # 只含 caller 的
        assert jobs[0]["action"]["prompt"] == "mine"   # CronJob dict 形态

        missing = await manage({"action": "cancel"}, caller=agent)
        assert missing.status == "error"               # 缺 job_id

        ghost = await manage({"action": "cancel", "job_id": "ghost"},
                             caller=agent)
        assert ghost.output == {"cancelled": False}    # 不存在 → False

        gone = await manage({"action": "cancel",
                             "job_id": own.output["job_id"]}, caller=agent)
        assert gone.output == {"cancelled": True}
        assert scheduler.jobs(agent.node_id) == []
        assert len(scheduler.jobs(other.node_id)) == 1   # 不影响其他 Agent
    finally:
        scheduler._stop()
        await agent.destroy()
        await other.destroy()
