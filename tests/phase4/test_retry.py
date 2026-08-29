"""阶段 4 composables/use_retry 测试（W38）：测试清单 T100–T112。

全部 FakeProvider 脚本回放驱动；退避延迟经 ``sleep_spy`` 记录断言，
不做真实等待。T112 按 R6 澄清口径验证（Composable 不做幂等去重记号：
重复调用按注册语义叠加、各自独立、不抛错——与原测试清单「同参数
幂等去重、异参报错」的冲突以澄清为准）。
"""

from __future__ import annotations

import asyncio

import pytest

from flowing.composables import use_retry
from flowing.errors import (
    AuthenticationError,
    ContextLengthError,
    RateLimitedError,
    ServerError,
    UnknownHookPointError,
)
from flowing.tool import Tool, ToolDefinition

from composables_support import (
    RetryAgent,
    SimpleAgent,
    make_composables_harness,
    script_provider,
    sleep_spy,
    text_response,
    tool_call_response,
)


class EchoTool(Tool):
    definition = ToolDefinition(
        name="echo", description="回显参数",
        params_schema={"text": {"type": "string"}})

    async def execute(self, *, text: str) -> str:
        return text


async def test_t100_no_retry_zero_overhead(tmp_path):
    """T100：未调 use_retry → Provider 抛 ServerError → on_provider_error
    空链分发、can_continue=False、逻辑 Turn 终止、Agent 回空闲。"""
    runtime, provider = make_composables_harness(tmp_path, agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        assert list(agent.hooks.on_provider_error) == []   # 零开销：空链
        assert list(agent.hooks.before_turn) == []
        script_provider(provider, ServerError("boom"))
        result = await agent.query("你好")
        assert result.status == "error"
        assert len(provider.received) == 1               # 无重试
        # Agent 存活回空闲：换脚本后可正常消费下一条消息
        script_provider(provider, text_response("ok"))
        result2 = await agent.query("再来")
        assert result2.status == "completed"
        assert result2.final_text == "ok"
    finally:
        await agent.destroy()


async def test_t101_exponential_backoff_delays(tmp_path):
    """T101：默认参数 → 连续 2 次 RateLimitedError 后成功 → 两次各
    sleep 1.0s / 2.0s（min(base*2**(n-1), 60)），第三次成功，Turn
    正常结束。"""
    runtime, provider = make_composables_harness(tmp_path)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, RateLimitedError("429"),
                        RateLimitedError("429"), text_response("ok"))
        with sleep_spy() as spy:
            result = await agent.query("hi")
        assert result.status == "completed"
        assert spy.recorded == [1.0, 2.0]
        assert len(provider.received) == 3
    finally:
        await agent.destroy()


async def test_t102_max_retries_give_up(tmp_path):
    """T102：max_retries=2 → Provider 持续 ServerError → attempt=1、2 各
    sleep base_delay 重试，attempt=3 放弃；LLM 共被调用 3 次。"""
    RetryAgent.retry_kwargs = {"max_retries": 2, "base_delay": 0.5}
    runtime, provider = make_composables_harness(tmp_path)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, *(ServerError("5xx") for _ in range(5)))
        with sleep_spy() as spy:
            result = await agent.query("hi")
        assert result.status == "error"
        assert spy.recorded == [0.5, 0.5]      # 基础设施错误恒 base_delay
        assert len(provider.received) == 3     # 初始 1 + 重试 2
    finally:
        RetryAgent.retry_kwargs = {}
        await agent.destroy()


async def test_t103_non_retryable_immediate_return(tmp_path):
    """T103：AuthenticationError → handler 立即返回、不 sleep、
    can_continue=False，Turn 终止。"""
    runtime, provider = make_composables_harness(tmp_path)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, AuthenticationError("401"))
        with sleep_spy() as spy:
            result = await agent.query("hi")
        assert result.status == "error"
        assert spy.recorded == []
        assert len(provider.received) == 1
    finally:
        await agent.destroy()


async def test_t104_context_length_bypasses_hook(tmp_path):
    """T104：ContextLengthError 不经 on_provider_error 直接上抛（回归
    验证不被拦截）。"""
    runtime, provider = make_composables_harness(tmp_path)
    agent = await runtime.create_agent("test-agent")
    seen = []
    try:
        def _spy(a, ctx):
            seen.append(ctx)
            return ctx

        agent.hooks.on_provider_error(_spy, by="test")
        script_provider(provider, ContextLengthError("overflow"))
        result = await agent.query("hi")
        assert result.status == "error"
        assert seen == []                      # 不经 on_provider_error
        assert len(provider.received) == 1
    finally:
        await agent.destroy()


async def test_t105_fixed_backoff(tmp_path):
    """T105：backoff="fixed", base_delay=0.5 → 连续 RateLimitedError
    每次延迟恒 0.5s。"""
    RetryAgent.retry_kwargs = {"backoff": "fixed", "base_delay": 0.5}
    runtime, provider = make_composables_harness(tmp_path)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, RateLimitedError("429"),
                        RateLimitedError("429"), RateLimitedError("429"),
                        text_response("ok"))
        with sleep_spy() as spy:
            result = await agent.query("hi")
        assert result.status == "completed"
        assert spy.recorded == [0.5, 0.5, 0.5]
    finally:
        RetryAgent.retry_kwargs = {}
        await agent.destroy()


async def test_t106_remove_by_owner_whole_group(tmp_path):
    """T106：remove_by_owner("retry") 后抛 ServerError → 默认策略整组
    移除（on_provider_error 决策 handler 与 before_turn 归零 handler
    共用 by="retry"，分别经各自 HookList 移除），Turn 直接终止。"""
    runtime, provider = make_composables_harness(tmp_path)
    agent = await runtime.create_agent("test-agent")
    try:
        removed = agent.hooks.on_provider_error.remove_by_owner("retry")
        removed += agent.hooks.before_turn.remove_by_owner("retry")
        assert removed == 2                    # 决策 + 归零两个 handler
        script_provider(provider, ServerError("5xx"))
        with sleep_spy() as spy:
            result = await agent.query("hi")
        assert result.status == "error"
        assert spy.recorded == []
        assert len(provider.received) == 1
    finally:
        await agent.destroy()


async def test_t107_shared_turn_failure_budget(tmp_path):
    """T107：同一逻辑 Turn 内第一次 provider_gen() 用掉 2 次重试后成功，
    第二次 provider_gen() 抛 ServerError（max_retries=3）→ 计数器不
    清零，本次 attempt 从 3 起计（共享 Turn 失败预算）。"""
    RetryAgent.retry_kwargs = {"max_retries": 3, "base_delay": 0.01}
    runtime, provider = make_composables_harness(tmp_path)
    runtime.register_tool(EchoTool())
    agent = await runtime.create_agent("test-agent")
    agent.add_tool("echo")
    try:
        tool_resp, _ = tool_call_response(("echo", {"text": "x"}))
        script_provider(
            provider,
            RateLimitedError("429"),             # attempt 1 → sleep 0.01
            RateLimitedError("429"),             # attempt 2 → sleep 0.02
            tool_resp,                           # 第一次 provider_gen 成功
            ServerError("5xx"),                  # attempt 3 → sleep 0.01
            ServerError("5xx"),                  # attempt 4 > 3 → 放弃
        )
        with sleep_spy() as spy:
            result = await agent.query("hi")
        assert result.status == "error"
        assert spy.recorded == [0.01, 0.02, 0.01]   # 计数器跨 provider_gen 共享
        assert len(provider.received) == 5
    finally:
        RetryAgent.retry_kwargs = {}
        await agent.destroy()


async def test_t108_invalid_params_no_side_effects(tmp_path):
    """T108：use_retry(agent, max_retries=-1) → ValueError，两个钩子链
    均无新增条目；base_delay<0 / backoff 非法同理（先校验后注册）。"""
    runtime, _ = make_composables_harness(tmp_path, agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        for kwargs in ({"max_retries": -1}, {"base_delay": -0.1},
                       {"backoff": "bogus"}):
            with pytest.raises(ValueError):
                use_retry(agent, **kwargs)
        assert list(agent.hooks.on_provider_error) == []
        assert list(agent.hooks.before_turn) == []
        with pytest.raises(UnknownHookPointError):
            agent.hooks.on_retry               # on_retry 未声明
    finally:
        await agent.destroy()


async def test_t109_counter_resets_across_turns(tmp_path):
    """T109：Turn 内重试 1 次后 Turn 正常结束 → 下一条消息开新 Turn 抛
    RateLimitedError → before_turn 归零 handler 已将计数器重置，
    attempt 从 1 起计（延迟回到 2**0 档）。"""
    RetryAgent.retry_kwargs = {"base_delay": 0.01}
    runtime, provider = make_composables_harness(tmp_path)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider,
                        RateLimitedError("429"), text_response("ok1"),
                        RateLimitedError("429"), text_response("ok2"))
        with sleep_spy() as spy:
            r1 = await agent.query("第一轮")
            r2 = await agent.query("第二轮")
        assert r1.status == r2.status == "completed"
        assert spy.recorded == [0.01, 0.01]    # 第二轮 attempt 从 1 起计
    finally:
        RetryAgent.retry_kwargs = {}
        await agent.destroy()


async def test_t110_unknown_error_not_retried(tmp_path):
    """T110：自定义未知异常 MyBizarreError → 不重试，can_continue=False。"""

    class MyBizarreError(Exception):
        pass

    runtime, provider = make_composables_harness(tmp_path)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, MyBizarreError("weird"))
        with sleep_spy() as spy:
            result = await agent.query("hi")
        assert result.status == "error"
        assert spy.recorded == []
        assert len(provider.received) == 1
    finally:
        await agent.destroy()


async def test_t111_on_retry_observation_signal(tmp_path):
    """T111：订阅者挂在 on_retry → 抛 RateLimitedError → 订阅者每重试
    一次收到一条 {"attempt","max_retries","delay","error"} 快照；放弃
    路径不发信号。"""
    runtime, provider = make_composables_harness(tmp_path)
    agent = await runtime.create_agent("test-agent")
    signals = []
    try:
        def _spy(a, value):
            signals.append(value)
            return value

        agent.hooks.on_retry(_spy, by="test")
        # 默认 max_retries=3：attempt 1/2/3 发信号，attempt 4 放弃不发
        script_provider(provider, *(RateLimitedError("429") for _ in range(4)))
        with sleep_spy():
            result = await agent.query("hi")
        assert result.status == "error"
        await asyncio.sleep(0)                 # fire-and-forget 信号落位
        await asyncio.sleep(0)
        assert [s["attempt"] for s in signals] == [1, 2, 3]
        for s in signals:
            assert s["max_retries"] == 3
            assert isinstance(s["error"], RateLimitedError)
        assert [s["delay"] for s in signals] == [1.0, 2.0, 4.0]
    finally:
        await agent.destroy()


async def test_t112_repeated_calls_stack_independently(tmp_path):
    """T112（R6 澄清口径）：Composable 不做幂等去重记号——同参数或不同
    参数的重复调用均按注册语义叠加、各自独立、不抛错；max_retries=0 →
    立即放弃（等价不重试，多一次空分发）。

    原测试清单为「同参数幂等去重、异参报错」，与 R6 澄清（允许不同
    参数多次 use 同一 Composable）冲突，以澄清为准。
    """
    runtime, provider = make_composables_harness(tmp_path, agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        use_retry(agent)
        use_retry(agent)                       # 同参数：叠加，不抛错
        use_retry(agent, max_retries=5)        # 异参：同样叠加，不报错
        assert len([e for e in agent.hooks.on_provider_error
                    if e.by == "retry"]) == 3
        assert len([e for e in agent.hooks.before_turn
                    if e.by == "retry"]) == 3  # 每次调用各叠加一组 handler
        assert agent.hooks.on_retry is not None  # declare 幂等，钩子点仅一个
        # 行为面：三组 handler 各自持有独立计数闭包——一次失败各睡一次
        script_provider(provider, ServerError("5xx"), text_response("ok"))
        with sleep_spy() as spy:
            result = await agent.query("hi")
        assert result.status == "completed"
        assert spy.recorded == [1.0, 1.0, 1.0]
        assert len(provider.received) == 2

        # max_retries=0：计数器加 1 后 1 > 0 立即放弃，等价不重试
        agent2 = await runtime.create_agent("test-agent")
        try:
            use_retry(agent2, max_retries=0)
            script_provider(provider, ServerError("5xx"))
            with sleep_spy() as spy2:
                result2 = await agent2.query("hi")
            assert result2.status == "error"
            assert spy2.recorded == []
            assert len(provider.received) == 3   # agent2 只调用 1 次
        finally:
            await agent2.destroy()
    finally:
        await agent.destroy()
