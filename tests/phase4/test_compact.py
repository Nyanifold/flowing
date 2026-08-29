"""阶段 4 composables/use_compact 测试（W39）：测试清单 T113–T121。

全部 FakeProvider 脚本回放驱动：turn 1 以携带大额 ``usage`` 的响应
落锚点（``total_tokens=85000`` / ``context_window=100000`` ≈ 85%），
turn 2 的主回合 ``after_provider_gen`` 触发压缩序列（检测口径：本轮
响应尚未挂树，估计按上一锚点）。
"""

from __future__ import annotations

import json

import pytest

from flowing.composables import use_compact
from flowing.composables.compact import DEFAULT_COMPACT_PROMPT
from flowing.context import ContextUsageEstimate
from flowing.errors import ServerError, UnknownHookPointError
from flowing.errors import Intercepted
from flowing.message import MessageKind
from flowing.model import ModelConfig
from flowing.parsable import Parsable
from flowing.providers import Usage

from composables_support import (
    CompactAgent,
    SimpleAgent,
    make_composables_harness,
    script_provider,
    text_response,
)

BIG_USAGE = Usage(input=85000, fresh_input=85000, output=0, cache_read=0,
                  cache_write=0, reasoning=0, total_tokens=85000)


def _compact_roots(agent):
    """树中 source="compact" 的新根消息。"""
    return [m for m in agent._messages.values()
            if m.source == "compact" and m.kind is MessageKind.SYSTEM]


async def _two_turn_setup(tmp_path, *, agent_cls=CompactAgent, script_tail=()):
    """公共 arrange：turn 1 落 85% 锚点；返回 (runtime, provider, agent)。"""
    runtime, provider = make_composables_harness(tmp_path, agent_cls=agent_cls)
    agent = await runtime.create_agent("test-agent")
    script_provider(provider, text_response("r1", usage=BIG_USAGE), *script_tail)
    r1 = await agent.query("第一轮")
    assert r1.status == "completed"
    return runtime, provider, agent


async def test_t113_no_compact_zero_overhead(tmp_path):
    """T113：未调 use_compact → 上下文占用 95% → 无钩子触发、无新根、
    head 不因此改变（逐字节等价）。"""
    runtime, provider, agent = await _two_turn_setup(
        tmp_path, agent_cls=SimpleAgent,
        script_tail=(text_response("r2"),))
    try:
        with pytest.raises(UnknownHookPointError):
            agent.hooks.on_compact               # 未声明钩子点
        result = await agent.query("第二轮")
        assert result.status == "completed"
        assert _compact_roots(agent) == []       # 无新根
        assert not hasattr(agent, "compact_prompt")
        assert len(provider.received) == 2       # 无 side_query
    finally:
        await agent.destroy()


async def test_t114_compact_full_sequence(tmp_path):
    """T114：启用（默认 0.8）占用 85% → 主回合成功 provider_gen() 后：
    on_compact 收到 ContextUsageEstimate 一次；side_query 被调一次
    （指令为 resolve 后 compact_prompt）；新增 parent_id is None、
    kind=SYSTEM、source="compact" 根消息；current_head_id 链路切换；
    旧链物理完整；本回合未 abort；后续 provider_gen 只见摘要。"""
    runtime, provider, agent = await _two_turn_setup(
        tmp_path,
        script_tail=(text_response("r2"), text_response("摘要内容"),
                     text_response("r3")))
    estimates = []
    try:
        def _spy(a, estimate):
            estimates.append(estimate)
            return estimate

        agent.hooks.on_compact(_spy, by="test")
        old_ids = set(agent._messages)           # 旧链 id 集
        r2 = await agent.query("第二轮")
        assert r2.status == "completed"
        assert r2.turn.aborted is False          # 本回合未 abort

        assert len(estimates) == 1               # on_compact 恰好一次
        assert isinstance(estimates[0], ContextUsageEstimate)

        roots = _compact_roots(agent)
        assert len(roots) == 1
        root = roots[0]
        assert root.parent_id is None
        assert root.content[0].text == "摘要内容"
        assert set(agent._messages) >= old_ids   # 旧链物理完整

        # side_query 恰好一次，指令为 resolve 后的 compact_prompt
        assert len(provider.received) == 3
        side_context = provider.received[2]
        assert side_context.messages[-1].content[0].text == DEFAULT_COMPACT_PROMPT

        # head 链路：本回合 PROVIDER 响应嫁接在新根上（seek 语义）
        head = agent._messages[agent.current_head_id]
        assert head.content[0].text == "r2"
        assert head.parent_id == root.id

        # 后续 provider_gen 上下文从新根上溯：只见摘要，不见旧链内容
        r3 = await agent.query("第三轮")
        assert r3.status == "completed"
        ctx3_texts = [b.text for m in provider.received[3].messages
                      for b in m.content if hasattr(b, "text")]
        assert "摘要内容" in ctx3_texts
        assert "r1" not in ctx3_texts and "第一轮" not in ctx3_texts
    finally:
        await agent.destroy()


async def test_t115_side_line_filtered_by_pattern(tmp_path):
    """T115：压缩过程中 side_query 响应的 by 为 "_side"，且该副线的
    after_provider_gen 因 pattern 不匹配不分发到本 handler（dispatch
    层过滤，无递归）。"""
    runtime, provider, agent = await _two_turn_setup(
        tmp_path, script_tail=(text_response("r2"), text_response("摘要")))
    bys = []
    estimates = []
    try:
        def _by_spy(a, response):
            bys.append(response.by)
            return response

        def _compact_spy(a, estimate):
            estimates.append(estimate)
            return estimate

        agent.hooks.after_provider_gen(_by_spy, by="test")   # 无 pattern：全量观测
        agent.hooks.on_compact(_compact_spy, by="test")
        r2 = await agent.query("第二轮")
        assert r2.status == "completed"
        # 嵌套时序：compact handler 注册在前，主响应的 dispatch 先进入它，
        # side_query 的副线 dispatch（"_side"）在其内部完成，外层 dispatch
        # 随后才到观测位——故观测序为 ["_side", "_turn"]
        assert bys == ["_side", "_turn"]         # 副线来源标记
        assert len(estimates) == 1               # 副线未再触发本 handler
        assert len(_compact_roots(agent)) == 1   # 无递归压缩
    finally:
        await agent.destroy()


async def test_t116_on_compact_intercepted(tmp_path):
    """T116：on_compact handler raise Intercepted → 不调 side_query、
    不建根、不换链，回合照常继续。"""
    runtime, provider, agent = await _two_turn_setup(
        tmp_path, script_tail=(text_response("r2"),))
    try:
        def _veto(a, estimate):
            raise Intercepted("本次不压")

        agent.hooks.on_compact(_veto, by="test")
        r2 = await agent.query("第二轮")
        assert r2.status == "completed"          # 回合照常继续
        assert len(provider.received) == 2       # 未调 side_query
        assert _compact_roots(agent) == []       # 不建根
        head = agent._messages[agent.current_head_id]
        assert head.content[0].text == "r2"      # 不换链
    finally:
        await agent.destroy()


async def test_t117_unknown_window_never_triggers(tmp_path):
    """T117：模型未声明 context_window（usage_ratio is None）→ 任何
    占用下均不触发。"""
    runtime, provider, agent = await _two_turn_setup(
        tmp_path, script_tail=(text_response("r2"), text_response("r3")))
    try:
        agent.model = ModelConfig(model="fake-model", provider="fake",
                                  context_window=None, max_output_tokens=4096)
        r2 = await agent.query("第二轮")
        r3 = await agent.query("第三轮")
        assert r2.status == r3.status == "completed"
        assert _compact_roots(agent) == []
        assert len(provider.received) == 3       # 无 side_query
    finally:
        await agent.destroy()


async def test_t118_side_query_failure_no_swap_no_raise(tmp_path):
    """T118：side_query 返回空文本 / 抛 Provider 异常 → 不换链、异常
    不外抛、本回合正常收尾；下一次超阈值 provider_gen 后再次尝试
    （无热循环）。"""
    # 子例 1：side_query 抛 Provider 异常；下一轮再次尝试并成功
    runtime, provider, agent = await _two_turn_setup(
        tmp_path,
        script_tail=(text_response("r2"), ServerError("side 失败"),
                     text_response("r3"), text_response("摘要2")))
    try:
        r2 = await agent.query("第二轮")          # side_query 抛异常
        assert r2.status == "completed"          # 异常不外抛、正常收尾
        assert _compact_roots(agent) == []       # 不换链
        assert len(provider.received) == 3       # side_query 已尝试一次

        r3 = await agent.query("第三轮")          # 下一次超阈值自然重试
        assert r3.status == "completed"
        assert len(provider.received) == 5       # 无热循环：仅随主回合再试
        roots = _compact_roots(agent)
        assert len(roots) == 1 and roots[0].content[0].text == "摘要2"
    finally:
        await agent.destroy()

    # 子例 2：side_query 返回空文本（abort / 截断口径）→ 不换链
    runtime2, provider2, agent2 = await _two_turn_setup(
        tmp_path / "b", script_tail=(text_response("r2"), text_response("")))
    try:
        r2 = await agent2.query("第二轮")
        assert r2.status == "completed"
        assert _compact_roots(agent2) == []
        assert len(provider2.received) == 3
    finally:
        await agent2.destroy()


async def test_t119_threshold_validation(tmp_path):
    """T119：threshold=0 或 1.5 → 抛 ValueError 无任何注册副作用。"""
    runtime, _ = make_composables_harness(tmp_path, agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        for bad in (0, 1.5):
            with pytest.raises(ValueError):
                use_compact(agent, threshold=bad)
        with pytest.raises(UnknownHookPointError):
            agent.hooks.on_compact               # 未声明
        assert list(agent.hooks.after_provider_gen) == []
        assert not hasattr(agent, "compact_prompt")
    finally:
        await agent.destroy()


async def test_t120_developer_compact_prompt_wins(tmp_path):
    """T120：子类已定义 compact_prompt = Parsable("自定义") → use_compact
    后实例仍是自定义值，side_query 承载自定义指令（开发者优先）。"""

    class CustomCompactAgent(CompactAgent):
        compact_prompt = Parsable("自定义压缩指令")

    runtime, provider, agent = await _two_turn_setup(
        tmp_path, agent_cls=CustomCompactAgent,
        script_tail=(text_response("r2"), text_response("摘要")))
    try:
        r2 = await agent.query("第二轮")
        assert r2.status == "completed"
        assert agent.compact_prompt.resolve(agent) == "自定义压缩指令"
        assert len(provider.received) == 3
        side_context = provider.received[2]
        assert side_context.messages[-1].content[0].text == "自定义压缩指令"
    finally:
        await agent.destroy()


async def test_t121_append_only_and_fork_hooks(tmp_path):
    """T121：压缩完成后旧链消息全部仍在 _messages / tree.jsonl
    （append-only 无 tombstone）；fork 经 before_fork / after_fork
    正常钩子路径；before_fork 拦截 → 新根已落盘而 head 未切
    （白压缩一次，安全）。"""
    # 子例 1：正常路径——append-only + fork 钩子路径
    runtime, provider, agent = await _two_turn_setup(
        tmp_path, script_tail=(text_response("r2", usage=BIG_USAGE),
                               text_response("摘要")))
    fork_calls = []
    try:
        def _before(a, target):
            fork_calls.append(("before", target))
            return target

        def _after(a, target):
            fork_calls.append(("after", target))
            return target

        agent.hooks.before_fork(_before, by="test")
        agent.hooks.after_fork(_after, by="test")
        old_ids = set(agent._messages)
        r2 = await agent.query("第二轮")
        assert r2.status == "completed"
        roots = _compact_roots(agent)
        assert len(roots) == 1
        assert [c[0] for c in fork_calls] == ["before", "after"]
        assert set(agent._messages) >= old_ids   # 旧链仍在内存树

        # tree.jsonl：append-only——旧链消息行俱在，无 tombstone
        tree_lines = (agent._session_dir / "tree.jsonl").read_text(
            encoding="utf-8").splitlines()
        assert tree_lines
        rows = [json.loads(line) for line in tree_lines]
        assert all(row.get("op") != "remove" for row in rows)
        persisted = json.dumps(rows)
        for mid in old_ids:
            assert mid in persisted

        # 子例 2：before_fork 拦截 → 新根已落盘而 head 未切
        # （r2 带大 usage：第三轮 anchored 估计仍超阈值，二次触发压缩）
        def _veto(a, target):
            raise Intercepted("不许切")

        agent.hooks.before_fork(_veto, by="test-veto")   # 排在观测 handler 之后
        script_provider(provider, text_response("r3"), text_response("摘要3"))
        head_before = agent.current_head_id
        r3 = await agent.query("第三轮")
        assert r3.status == "completed"
        roots2 = _compact_roots(agent)
        assert len(roots2) == 2                  # 新根已建（落盘）
        assert agent.current_head_id != roots2[1].id   # 但 head 未切
        # head 仍在新根之外的链上（本轮响应嫁接旧 head 所在链）
        assert agent.current_head_id != head_before   # head 随挂树前移（r3）
        assert agent._messages[agent.current_head_id].parent_id != roots2[1].id
    finally:
        await agent.destroy()
