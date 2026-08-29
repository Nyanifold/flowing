"""阶段 4 workflow 工具面测试（W34–W35）：测试清单 T88–T90。

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


def _capture_plugin_messages(agent, done: asyncio.Event,
                             *, source: str | None = None) -> list:
    """经 after_enqueue 钩子收集 PLUGIN 消息（按 source 过滤），到时置位。"""
    collected = []

    def _collect(agent, msg):
        if msg.kind is MessageKind.PLUGIN and (source is None or msg.source == source):
            collected.append(msg)
            done.set()
        return msg

    agent.hooks.after_enqueue(_collect, by="test")
    return collected


async def test_t88_receipt_before_run_completes(project, tmp_path):
    """T88：LLM 调 run-workflow(path="@/verify_fix.py", max_rounds=2) →
    execute 在 run() 完成之前返回收据 {"status":"started",...}；实例的
    caller 是该 Agent；path 不透传给 run()。"""
    runtime = make_runtime(tmp_path)
    runtime.use(WorkflowPlugin())
    provider = add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent")
    try:
        agent.add_tool("run-workflow")
        gate = asyncio.Event()
        agent.wf_gate = gate   # verify_fix.py 的 run 先等闸门（证明收据先于完成）
        tool_results = _capture_tool_results(agent)
        plugin_done = asyncio.Event()
        plugin_msgs = _capture_plugin_messages(
            agent, plugin_done, source="workflow:verify-fix")

        step1, _ = tool_call_response(
            ("run-workflow", {"path": "@/verify_fix.py", "max_rounds": 2}))
        script_provider(provider, step1, text_response("已启动"))
        result = await agent.query("启动验证-修复工作流")
        assert result.status == "completed"

        # 收据：LLM 可见的 completed ToolResult 承载 started 收据
        (receipt,) = [r for r in tool_results
                      if isinstance(r.output, dict)
                      and r.output.get("status") == "started"]
        assert receipt.status == "completed"
        assert receipt.output == {"status": "started",
                                  "workflow": "@/verify_fix.py"}
        assert plugin_msgs == []   # gate 未置位：run 未完成 → 收据严格在先

        gate.set()   # 放行 run：PLUGIN 消息交付 = run 完成信号
        await asyncio.wait_for(plugin_done.wait(), 2.0)
        echo = json.loads(plugin_msgs[0].content[0].text)
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
    runtime.use(WorkflowPlugin())
    provider = add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent")
    try:
        agent.add_tool("run-workflow")
        plugin_done = asyncio.Event()
        texts = []
        done = asyncio.Event()

        def _collect(a, msg):
            if msg.kind is MessageKind.PLUGIN and msg.source == "workflow:caller-query":
                texts.append(msg.content[0].text)
                done.set()
            return msg

        agent.hooks.after_enqueue(_collect, by="test")
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
    的 LLM 可见结果；后台任务自身异常不向工具传播（收据仍 started，
    异常记日志）。"""
    runtime = make_runtime(tmp_path, register_default_type=False)
    runtime.register_agent_type("caller-agent", SimpleAgent)
    # 刻意不注册 "test-agent"：quick.py 的 run 在后台任务内失败
    runtime.use(WorkflowPlugin())
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

        # 后半：run() 后台失败 → 不向工具传播，收据仍为 started + 记日志
        tool_results.clear()
        step2, _ = tool_call_response(("run-workflow", {"path": "@/quick.py"}))
        script_provider(provider, step2, text_response("收尾"))
        with caplog.at_level(logging.ERROR,
                             logger="flowing.plugins.workflow.plugin"):
            await agent.query("启动 quick")
            assert tool_results[0].status == "completed"
            assert tool_results[0].output == {"status": "started",
                                              "workflow": "@/quick.py"}
            await asyncio.sleep(0.2)   # 等后台任务失败落定
        assert any("workflow 后台运行失败" in r.message
                   for r in caplog.records)
    finally:
        await runtime.shutdown()
