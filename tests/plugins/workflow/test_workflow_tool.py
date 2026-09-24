"""workflow 工具面测试：测试清单 T88–T90。

LLM 入口由 FakeProvider 脚本回放驱动；``run-workflow`` 必异步（后台任务 +
立即返回收据），T89 是 caller.query 防死锁规则的核心回归。
"""

from __future__ import annotations

import asyncio
import json
import logging

from flowing.message import MessageKind
from flowing.plugins.workflow import WorkflowPlugin

from workflow_support import (
    SimpleAgent,
    add_fake_provider,
    make_runtime,
    project,   # noqa: F401（fixture 注册）
    script_provider,
    text_response,
    tool_call_response,
)


def _capture_tool_results(agent) -> list:
    """经 after_tool_call 钩子收集 ToolResult。"""
    collected = []

    def _collect(agent, result):
        collected.append(result)
        return result

    agent.hooks.after_tool_call(_collect, by="test")
    return collected


def _capture_event_messages(agent, done: asyncio.Event,
                             *, source: str | None = None) -> list:
    """经 on_enqueue 钩子收集 EVENT 消息（按 source 过滤），到时置位。"""
    collected = []

    def _collect(agent, msg):
        if msg.kind is MessageKind.EVENT and (source is None or msg.source == source):
            collected.append(msg)
            done.set()
        return msg

    agent.hooks.on_enqueue(_collect, by="test")
    return collected


async def test_t88_receipt_before_run_completes(project, tmp_path):
    """T88：LLM 调 run-workflow(path="@/verify_fix.py", max_rounds=2) →
    execute 在 run() 完成之前返回收据 {"status":"started",...}；实例的
    caller 是该 Agent；path 不透传给 run()。"""
    runtime = make_runtime(tmp_path)
    runtime.install(WorkflowPlugin())
    provider = add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent")
    try:
        agent.add_tool("run-workflow")
        gate = asyncio.Event()
        agent.wf_gate = gate   # verify_fix.py 的 run 先等闸门（证明收据先于完成）
        tool_results = _capture_tool_results(agent)
        event_done = asyncio.Event()
        event_msgs = _capture_event_messages(
            agent, event_done, source="workflow:verify-fix")

        step1, _ = tool_call_response(
            ("run-workflow", {"path": "@/verify_fix.py", "max_rounds": 2}))
        script_provider(provider, step1, text_response("已启动"))
        result = await agent.query("启动验证-修复工作流")
        assert result.status == "completed"

        # 收据：pending ToolResult 承载 started 收据（B12——async gen 首 yield，
        # 带后台任务注册键）
        (receipt,) = [r for r in tool_results
                      if isinstance(r.output, dict)
                      and r.output.get("status") == "started"]
        assert receipt.status == "pending"
        assert receipt.background_task_id is not None   # B10：注册键随收据交付
        assert receipt.output == {"status": "started",
                                  "workflow": "@/verify_fix.py"}
        assert event_msgs == []   # gate 未置位：run 未完成 → 收据严格在先

        gate.set()   # 放行 run：EVENT 消息交付 = run 完成信号
        await asyncio.wait_for(event_done.wait(), 2.0)
        echo = json.loads(event_msgs[0].content[0].text)
        assert echo["caller"] == agent.node_id   # 实例的 caller 是该 Agent
        assert echo["max_rounds"] == 2           # 其余参数透传给 run()
        assert "path" not in echo                # path 不透传
    finally:
        await runtime.shutdown()


async def test_t89_caller_query_no_deadlock(project, tmp_path):
    """T89：workflow 的 run() 中 await self.caller.query("?") → caller
    工作循环在当前逻辑 Turn 结束后消费该消息，无死锁（异步防死锁规则
    回归）。"""
    runtime = make_runtime(tmp_path)
    runtime.install(WorkflowPlugin())
    provider = add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent")
    try:
        agent.add_tool("run-workflow")
        texts = []
        done = asyncio.Event()

        def _collect(a, msg):
            if msg.kind is MessageKind.EVENT and msg.source == "workflow:caller-query":
                texts.append(msg.content[0].text)
                done.set()
            return msg

        agent.hooks.on_enqueue(_collect, by="test")
        step1, _ = tool_call_response(
            ("run-workflow", {"path": "@/caller_query.py", "prompt": "第一问"}))
        # 三步脚本：turn1 工具调用 → turn1 收尾文本 → turn2（workflow 的
        # caller.query 消息）应答文本
        script_provider(provider, step1, text_response("工作流已启动"),
                        text_response("回答一"))
        result = await asyncio.wait_for(agent.query("启动"), 5.0)
        assert result.status == "completed"
        await asyncio.wait_for(done.wait(), 5.0)   # 死锁则此处超时失败
        assert texts == ["got:回答一"]
    finally:
        await runtime.shutdown()


async def test_t90_resolve_failure_wrapped_run_failure_contained(
        project, tmp_path, caplog):
    """T90：resolve_workflow 解析失败 → 经工具边界包装为 status="error"
    的 LLM 可见结果（首 yield 前异常，B2）；后台运行段失败 → 框架投递
    EVENT 错误块（LLM 可见）+ flowing.tool 日志（B3，双通道）。"""
    runtime = make_runtime(tmp_path, register_default_type=False)
    runtime.register_agent_type(SimpleAgent, name="caller-agent")
    # 刻意不注册 "test-agent"：quick.py 的 run 在后台任务内失败
    runtime.install(WorkflowPlugin())
    provider = add_fake_provider(runtime)
    agent = await runtime.create_agent("caller-agent")
    try:
        agent.add_tool("run-workflow")
        tool_results = _capture_tool_results(agent)

        # 前半：解析失败 → error 结果（LLM 可见，不触发错误钩子）
        step1, _ = tool_call_response(("run-workflow", {"path": "@/ghost.py"}))
        script_provider(provider, step1, text_response("收尾"))
        await agent.query("启动不存在的工作流")
        assert tool_results[0].status == "error"
        assert "ghost.py" in tool_results[0].error

        # 后半：run() 后台失败 → 框架投递 EVENT 错误块（LLM 可见）+ 日志
        # （logger 变为 flowing.tool，B3/S3 迁移）
        tool_results.clear()
        step2, _ = tool_call_response(("run-workflow", {"path": "@/quick.py"}))
        script_provider(provider, step2, text_response("收尾"))
        with caplog.at_level(logging.ERROR, logger="flowing.tool"):
            await agent.query("启动 quick")
            assert tool_results[0].status == "pending"
            assert tool_results[0].output == {"status": "started",
                                              "workflow": "@/quick.py"}
            await asyncio.sleep(0.2)   # 等后台任务失败落定
        assert any("async tool run-workflow failed in the background" in r.message
                   for r in caplog.records)
    finally:
        await runtime.shutdown()


async def test_workflow_plugin_launch_root(project):
    """launch：注册后按定义文件创建 Workflow 根。

    断言根形态：``caller is None``、``runtime`` 正确绑定、``node_id`` 为
    ``workflow-*`` 前缀、``_parent_id`` 指向 Runtime（provide 链终点）。
    """
    import pytest
    runtime = make_runtime(project)
    try:
        plugin = WorkflowPlugin()
        runtime.install(plugin)
        wf = plugin.launch("@/verify_fix.py")
        assert wf.caller is None
        assert wf.runtime is runtime
        assert wf.node_id.startswith("workflow-")
        assert wf._parent_id == runtime.node_id
    finally:
        await runtime.shutdown()


def test_workflow_plugin_launch_unregistered():
    """launch：未注册（未 ``runtime.install(WorkflowPlugin())``）→ ValueError。"""
    import pytest
    plugin = WorkflowPlugin()
    with pytest.raises(ValueError):
        plugin.launch("@/verify_fix.py")
