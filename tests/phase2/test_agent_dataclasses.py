"""阶段 2：agent 数据类与回合产物测试（T21–T33）。

覆盖：TurnContext 载体（T21/T22）、TurnResult 共享与四结局（T23/T24/T30–T33）、
Execution 注册清理与分组取消（T25/T26）、build_turn_result 聚合口径（T32，
含 R-09 finish_reason 断言）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from flowing.agent import Execution, TurnContext, build_turn_result
from flowing.errors import Intercepted, RateLimitedError
from flowing.message import MessageKind
from flowing.providers import Usage

from conftest import SimpleAgent, script_provider, text_response, tool_call_response


def _usage(total: int = 100) -> Usage:
    return Usage(input=total - 10, fresh_input=total - 10, output=10,
                 cache_read=0, cache_write=0, reasoning=0, total_tokens=total)


# ---------------------------------------------------------------------------
# T21：空闲 Agent 的单回合基线
# ---------------------------------------------------------------------------


async def test_t21_basic_turn(agent, provider):
    script_provider(provider, text_response("你好！"))
    result = await agent.query("你好")
    assert isinstance(result.turn, TurnContext)
    assert result.turn.aborted is False
    # message_ids 依序含 USER 与 PROVIDER 消息 id
    msgs = [agent._messages[mid] for mid in result.turn.message_ids]
    assert [m.kind for m in msgs] == [MessageKind.USER, MessageKind.PROVIDER]
    assert agent.current_head_id == msgs[-1].id
    assert msgs[-1].turn_end is True   # agent 层写入（S-14）


# ---------------------------------------------------------------------------
# T22：turn 进行中 abort_turn
# ---------------------------------------------------------------------------


async def test_t22_abort_turn_mid_turn(runtime, provider):
    gate = asyncio.Event()
    abort_marks: list[bool] = []
    after_reads: list[bool] = []

    async def _gated(context, model):
        gate.set()   # 回合进行中信号
        await asyncio.sleep(0.05)
        return text_response("晚了")

    provider.generate_fn = _gated
    agent = await runtime.create_agent(SimpleAgent)
    agent.hooks.before_turn_abort(lambda a, t: abort_marks.append(t.aborted) or t)
    agent.hooks.after_turn(lambda a, t: after_reads.append(t.aborted) or t)

    task = asyncio.create_task(agent.query("hi"))
    await gate.wait()
    agent.abort_turn()   # 下一检查点退出
    result = await asyncio.wait_for(task, 2)
    assert result.turn.aborted is True
    assert result.status == "cancelled"
    assert abort_marks == [True]   # before_turn_abort 恰好一次
    assert after_reads == [True]   # after_turn 读到 aborted


# ---------------------------------------------------------------------------
# T23：drain 合并两条消息为一回合，等待者共享同一 TurnResult
# ---------------------------------------------------------------------------


async def test_t23_drain_shared_result(runtime, provider):
    script_provider(provider, text_response("合并回复"))

    agent = await runtime.create_agent(SimpleAgent, start_loop=False)

    async def _drain():
        await agent._message_queue.wait_not_empty()   # 先等再有批（drain_all 不阻塞，空调用会空转）
        return await agent._message_queue.drain_all()

    agent._dequeue = _drain   # 覆写扩展点：drain 合并
    agent._loop_task = asyncio.create_task(agent._work_loop())

    t1 = asyncio.create_task(agent.query("一"))
    t2 = asyncio.create_task(agent.query("二"))
    r1, r2 = await asyncio.wait_for(asyncio.gather(t1, t2), 2)
    assert r1 is r2   # 同一 TurnResult 实例
    assert result_kinds(r1, agent) == [
        MessageKind.USER, MessageKind.USER, MessageKind.PROVIDER]


def result_kinds(result, agent):
    return [agent._messages[mid].kind for mid in result.turn.message_ids]


# ---------------------------------------------------------------------------
# T24：before_turn Intercepted → blocked
# ---------------------------------------------------------------------------


async def test_t24_before_turn_intercepted(runtime, provider):
    agent = await runtime.create_agent(SimpleAgent)

    async def _guard(a, turn):
        raise Intercepted("内容违规")

    agent.hooks.before_turn(_guard)
    result = await agent.query("违规内容")
    assert result.status == "blocked"   # 不抛 Intercepted
    assert result.final_text == ""   # 批次未挂树
    assert len(agent._messages) == 0
    assert agent.current_head_id is None
    assert len(provider.received) == 0   # provider 未被调用


# ---------------------------------------------------------------------------
# T25 / T26：Execution 注册清理与分组取消
# ---------------------------------------------------------------------------


async def test_t25_execution_cleanup_on_error(runtime, provider):
    from flowing.tool import Tool, ToolCall, ToolDefinition

    class BoomTool(Tool):
        definition = ToolDefinition(name="boom", description="总是炸",
                                    params_schema={})

        async def execute(self):
            raise RuntimeError("boom")

    runtime.register_tool(BoomTool())
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("boom")

    result = await agent.tool_call(ToolCall(id="c1", name="boom", args={}))
    assert result.status == "error"   # 工具异常是正常产物
    assert "boom" in (result.error or "")
    assert agent._executions == {}   # finally 清理，无幽灵条目


async def test_t26_cancel_by_tag(agent):
    e1 = Execution(id="e1", kind="tool", tags=["bash"],
                   started_at=datetime.now(),
                   cancel=asyncio.Event(), pause=asyncio.Event())
    e2 = Execution(id="e2", kind="agent", tags=["subagent"],
                   started_at=datetime.now(),
                   cancel=asyncio.Event(), pause=asyncio.Event())
    agent._executions["e1"] = e1
    agent._executions["e2"] = e2
    agent.cancel_by_tag("bash")
    assert e1.cancel.is_set()
    assert not e2.cancel.is_set()
    assert not agent._turn_abort.is_set()   # 不动 turn 侧


# ---------------------------------------------------------------------------
# T30 / T31：ProviderErrorContext 决策
# ---------------------------------------------------------------------------


async def test_t30_provider_error_breaks_turn(agent, provider):
    script_provider(provider, RateLimitedError("限流", provider="fake"),
                    text_response("后续消息照常"))
    result = await agent.query("hi")
    assert result.status == "error"   # 回合中断
    assert len(provider.received) == 1

    result2 = await agent.query("再来")   # Agent 存活可消费后续消息
    assert result2.status == "completed"
    assert result2.final_text == "后续消息照常"


async def test_t31_can_continue_retries_same_turn(agent, provider):
    script_provider(provider, RateLimitedError("限流"), text_response("重试成功"))
    seen: list[str] = []

    async def _retry(a, ctx):
        seen.append(type(ctx.error).__name__)
        ctx.can_continue = True
        return ctx

    agent.hooks.on_provider_error(_retry)
    result = await agent.query("hi")
    assert seen == ["RateLimitedError"]
    assert result.status == "completed"   # 同一 TurnContext 内重新发起 provider_gen
    assert result.final_text == "重试成功"
    assert len(provider.received) == 2


# ---------------------------------------------------------------------------
# T32 / T33：build_turn_result 聚合
# ---------------------------------------------------------------------------


async def test_t32_completed_aggregation(agent, provider):
    script_provider(provider, text_response("完成", usage=_usage(100)))
    result = await agent.query("hi")
    assert result.status == "completed"
    assert result.final_text == "完成"
    assert result.token_usage is not None
    assert result.token_usage.total_tokens == 100
    assert result.token_usage.raw == {}   # raw 不聚合
    assert result.finish_reason == ""   # 流式路径无 stop_reason 载体（R-09 落实：
    # provider_data 不经 delta 透传，组装响应 provider_data={}）

    # R-09 补断言：completed 结局填末次响应的 stop_reason（非流式路径经
    # provider_data 传入，由 _run_turn 显式传参）——直接驱动 build_turn_result
    done_turn = TurnContext(started_at=datetime.now())
    assert build_turn_result(done_turn, agent,
                             finish_reason="end_turn").finish_reason == "end_turn"

    # 七字段求和口径：直接驱动 build_turn_result 的双 usage 聚合
    u1, u2 = _usage(100), _usage(50)
    turn = TurnContext(started_at=datetime.now())
    turn.usages.extend([u1, u2])
    merged = build_turn_result(turn, agent)
    assert merged.token_usage.total_tokens == 150
    assert merged.token_usage.output == 20
    assert merged.token_usage.raw == {}

    # usages 空 -> None（区分「未上报」与「真零」）
    empty = TurnContext(started_at=datetime.now())
    assert build_turn_result(empty, agent).token_usage is None


async def test_t33_uncaught_inner_exception(agent, provider):
    async def _bad_hook(a, msg):
        raise ValueError("钩子炸了")

    agent.hooks.before_turn_append(_bad_hook)
    result = await agent.query("hi")
    assert result.status == "error"
    assert result.final_text == ""
    assert result.finish_reason == "error"
