"""composables/use_auto_compact 测试：请求前头尾保留式自动压缩。

驱动方式同 test_compact.py（FakeProvider 脚本回放），但触发用小
``context_window`` + 真实文本长度（``estimate_message_tokens`` 字符启发式，
CJK 约 1 字符/token）使 ``usage_ratio`` 超阈值；脚本顺序为「side_query
摘要响应 → 主请求响应」。
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from flowing.composables import use_auto_compact
from flowing.errors import Intercepted, ServerError
from flowing.message import Message, MessageKind, TextBlock, ToolCallBlock
from flowing.model import ModelConfig

from composables_support import (
    SimpleAgent,
    make_composables_harness,
    script_provider,
    text_response,
)

WINDOW = 1000
"""测试用上下文窗口：阈值 0.8 → 估计超 800 token 触发压缩。"""


class AutoCompactAgent(SimpleAgent):
    """setup 中 ``use_auto_compact``（类属性 ``auto_kwargs`` 透传参数）。"""

    auto_kwargs: ClassVar[dict[str, Any]] = {}

    async def setup(self, **kwargs: Any) -> None:
        await super().setup(**kwargs)
        use_auto_compact(self, **type(self).auto_kwargs)


def _push_user(agent, text: str) -> str:
    return agent.push(Message(kind=MessageKind.USER,
                              content=[TextBlock(text=text)]))


def _push_provider(agent, text: str) -> str:
    return agent.push(Message(kind=MessageKind.PROVIDER,
                              content=[TextBlock(text=text)]))


def _push_tool_pair(agent, call_id: str, result: str = "ok") -> tuple[str, str]:
    pid = agent.push(Message(kind=MessageKind.PROVIDER,
                             content=[ToolCallBlock(id=call_id, name="echo", args={})]))
    tid = agent.push(Message(kind=MessageKind.TOOL, tool_call_id=call_id,
                             tool_status="completed",
                             content=[TextBlock(text=result)]))
    return pid, tid


def _compacted(agent) -> list[Message]:
    """树中 source="auto_compact" 的压缩消息。"""
    return [m for m in agent._messages.values() if m.source == "auto_compact"]


def _head_chain(agent) -> list[Message]:
    return list(agent.chain.walk(agent.current_head_id))[::-1]


async def _make(tmp_path, *, auto_kwargs: dict | None = None,
                window: int = WINDOW):
    cls = type("AC", (AutoCompactAgent,), {"auto_kwargs": auto_kwargs or {}})
    runtime, provider = make_composables_harness(tmp_path, agent_cls=cls)
    agent = await runtime.create_agent(
        "test-agent",
        model=ModelConfig(model="fake-model", provider="fake",
                          context_window=window, max_output_tokens=100))
    return runtime, provider, agent


async def test_trigger_before_request_full_sequence(tmp_path):
    """超阈值 → before_provider_gen 触发压缩：side_query 一次取摘要、开分支
    换链、本次主请求直接携压缩后上下文发出、旧链物理完整、本轮指令保留在
    后缀（链尾连续段始终保留规则）。"""
    runtime, provider, agent = await _make(
        tmp_path, auto_kwargs={"head_tokens": 350, "tail_tokens": 400})
    try:
        _push_user(agent, "原始指令" * 75)          # ~300 token，前缀
        _push_provider(agent, "旧回复一" * 75)      # ~300 token，中段
        _push_user(agent, "中段指令" * 75)          # ~300 token，中段（进汇编）
        _push_provider(agent, "旧回复二" * 75)      # ~300 token，后缀
        script_provider(provider, text_response("SUMMARY"), text_response("final"))

        r = await agent.query("继续做")
        assert r.status == "completed"

        # side_query（摘要）+ 主请求，恰好两次 provider 调用
        assert len(provider.received) == 2
        # 压缩消息：SYSTEM + source=auto_compact + 三段式内容
        [compacted] = _compacted(agent)
        assert compacted.kind is MessageKind.SYSTEM
        text = compacted.content[0].text
        assert text.startswith("following are compacted:")
        assert "## User instructions\n" + "中段指令" * 75 in text
        assert "## Compaction summary\nSUMMARY" in text
        # 主请求携压缩后上下文：含压缩消息与本轮指令（后缀深拷贝副本）
        main_ctx = provider.received[1]
        main_ids = [m.id for m in main_ctx.messages]
        assert compacted.id in main_ids
        assert any(m.content and m.content[0].text == "继续做"
                   for m in main_ctx.messages)
        # 旧链物理完整：四条原始消息 id 与内容不变
        assert any(m.content[0].text == "旧回复一" * 75
                   for m in agent._messages.values())
        # 新链（head 路径）= 前缀 + 压缩消息 + 后缀 + 本轮响应
        chain_ids = [m.id for m in _head_chain(agent)]
        assert compacted.id in chain_ids
    finally:
        await agent.destroy()


async def test_custom_compact_template(tmp_path):
    """compact_template 开发者定义优先（类属性 Parsable）：压缩消息按自定义
    模板拼装；模板经 self 读实例状态。"""
    from flowing.parsable import Parsable

    class CustomTmplAgent(AutoCompactAgent):
        compact_template = Parsable(
            "COMPACTED\n## Compaction summary\n{{ summary }}"
            "{% if self.todos %}\n\n## Task list\n{{ self.todos }}{% endif %}")
        auto_kwargs: ClassVar[dict[str, Any]] = {
            "head_tokens": 350, "tail_tokens": 400}

        async def setup(self, **kwargs: Any) -> None:
            self.todos = "[ ] 改代码"
            await super().setup(**kwargs)

    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=CustomTmplAgent)
    agent = await runtime.create_agent(
        "test-agent",
        model=ModelConfig(model="fake-model", provider="fake",
                          context_window=WINDOW, max_output_tokens=100))
    try:
        _push_user(agent, "原始指令" * 75)
        _push_provider(agent, "旧回复一" * 75)
        _push_user(agent, "中段指令" * 75)
        _push_provider(agent, "旧回复二" * 75)
        script_provider(provider, text_response("SUMMARY"),
                        text_response("final"))
        r = await agent.query("继续做")
        assert r.status == "completed"
        [compacted] = _compacted(agent)
        text = compacted.content[0].text
        assert text.startswith("COMPACTED")         # 自定义模板，非缺省格式
        assert "## Compaction summary\nSUMMARY" in text
        assert "\n\n## Task list\n[ ] 改代码" in text   # 模板经 self 读状态
        assert "following are compacted:" not in text
    finally:
        await agent.destroy()


async def test_prefix_suffix_cut_at_provider_boundary(tmp_path):
    """前缀/后缀切割点都在 PROVIDER 上边界：前缀共享原节点、新链无孤儿 TOOL。"""
    runtime, provider, agent = await _make(
        tmp_path, window=300,
        auto_kwargs={"head_tokens": 60, "tail_tokens": 60})
    try:
        u1 = _push_user(agent, "头部指令" * 15)                # ~60 token，前缀
        _push_provider(agent, "头部回复" * 15)                 # 超头预算 → 中段
        _push_tool_pair(agent, "call-mid", result="结果" * 20)  # 中段 tool 对
        _push_user(agent, "中段指令" * 15)
        _push_provider(agent, "尾部回复" * 15)                 # 后缀
        script_provider(provider, text_response("S"), text_response("final"))

        r = await agent.query("尾部指令")
        assert r.status == "completed"

        chain = _head_chain(agent)
        # 前缀共享原节点（不拷贝）
        assert chain[0].id == u1
        [compacted] = _compacted(agent)
        assert compacted in chain
        # 配对完整：新链上每个 TOOL 的配对锚都能在同链找到
        call_ids = {b.id for m in chain for b in m.content
                    if isinstance(b, ToolCallBlock)}
        for m in chain:
            if m.kind is MessageKind.TOOL:
                assert m.tool_call_id in call_ids
        # 中段 tool 对被压掉（不在新链），尾部回复保留
        assert "call-mid" not in call_ids
    finally:
        await agent.destroy()


async def test_user_instruction_budget(tmp_path):
    """中段 USER 汇编受 user_instruction_tokens 限制：超出部分不进压缩消息。"""
    runtime, provider, agent = await _make(
        tmp_path, window=200,
        auto_kwargs={"head_tokens": 50, "tail_tokens": 60,
                     "user_instruction_tokens": 30})
    try:
        _push_user(agent, "头部指令" * 10)        # ~40 token，前缀
        _push_provider(agent, "头部回复" * 10)    # 中段
        _push_user(agent, "短指令")               # 中段，进汇编（~3 token）
        _push_provider(agent, "中段回复")         # 中段
        _push_user(agent, "长指令" * 15)          # 中段，超 30 预算 → 不进
        _push_provider(agent, "尾部回复" * 10)    # 后缀
        script_provider(provider, text_response("S"), text_response("final"))

        r = await agent.query("尾部指令")
        assert r.status == "completed"

        [compacted] = _compacted(agent)
        text = compacted.content[0].text
        assert "短指令" in text
        assert "长指令" not in text   # 超预算的长指令不进汇编
    finally:
        await agent.destroy()


async def test_suffix_deepcopy_new_ids_old_chain_intact(tmp_path):
    """后缀深拷贝：新链后缀是新铸 id 的副本（TOOL 段连同配对 PROVIDER 一起
    保留）；旧链消息 id 与内容不变。"""
    runtime, provider, agent = await _make(
        tmp_path, window=200,
        auto_kwargs={"head_tokens": 50, "tail_tokens": 100})
    try:
        _push_user(agent, "头部指令" * 12)                 # ~48 token，前缀
        _push_provider(agent, "头部回复" * 12)             # 中段
        _push_provider(agent, "尾部回复" * 12)             # 后缀
        _push_tool_pair(agent, "call-tail", result="尾部结果" * 8)   # 后缀 tool 对
        old_ids = set(agent._messages)
        old_texts = {m.content[0].text for m in agent._messages.values()
                     if m.content and isinstance(m.content[0], TextBlock)}
        script_provider(provider, text_response("S"), text_response("final"))

        r = await agent.query("尾部指令")
        assert r.status == "completed"

        # 用主请求时刻的 Context 断言后缀（此刻本轮响应尚未挂树）
        main_msgs = provider.received[1].messages
        [compacted] = _compacted(agent)
        suffix = main_msgs[[m.id for m in main_msgs].index(compacted.id) + 1:]
        assert suffix
        # 后缀副本：id 全新、文本在旧链中有同源；TOOL 与配对 PROVIDER 同在
        call_ids = {b.id for m in suffix for b in m.content
                    if isinstance(b, ToolCallBlock)}
        assert "call-tail" in call_ids
        for m in suffix:
            assert m.id not in old_ids
            if m.kind is MessageKind.TOOL:
                assert m.tool_call_id in call_ids
            if m.content and isinstance(m.content[0], TextBlock):
                # 副本必在旧链有同源；唯一的例外是本轮新指令的副本（旧链同文本
                # 消息存在但不在 old_texts 快照内——快照先于 query）
                assert (m.content[0].text in old_texts
                        or m.content[0].text == "尾部指令")
        # 旧链完整
        assert old_ids <= set(agent._messages)
    finally:
        await agent.destroy()


async def test_side_query_no_recursion(tmp_path):
    """副线请求的散装 Context（末条消息不在树内）不触发压缩。"""
    runtime, provider, agent = await _make(tmp_path, window=200)
    try:
        _push_provider(agent, "长回复" * 100)   # ~400 token，比率 >1
        script_provider(provider, text_response("副线答案"))
        out = await agent.side_query("一个副线问题")
        assert out == "副线答案"
        assert len(provider.received) == 1   # 未递归触发压缩的 side_query
        assert _compacted(agent) == []
    finally:
        await agent.destroy()


async def test_failure_semantics(tmp_path):
    """side_query 异常 / 空摘要 / on_compact 拦截：均不换链、不外抛。"""

    # side_query 抛异常
    runtime, provider, agent = await _make(tmp_path / "err", window=200)
    try:
        _push_provider(agent, "长回复" * 100)
        script_provider(provider, ServerError("boom"), text_response("final"))
        r = await agent.query("指令")
        assert r.status == "completed"   # 异常不外抛，主请求照常
        assert _compacted(agent) == []
    finally:
        await agent.destroy()

    # 空摘要
    runtime, provider, agent = await _make(tmp_path / "empty", window=200)
    try:
        _push_provider(agent, "长回复" * 100)
        script_provider(provider, text_response(""), text_response("final"))
        r = await agent.query("指令")
        assert r.status == "completed"
        assert _compacted(agent) == []
    finally:
        await agent.destroy()

    # on_compact 拦截
    runtime, provider, agent = await _make(tmp_path / "veto", window=200)
    try:
        _push_provider(agent, "长回复" * 100)

        async def _veto(agent, estimate):
            raise Intercepted("veto")

        agent.hooks.on_compact(_veto, by="test")
        script_provider(provider, text_response("final"))
        r = await agent.query("指令")
        assert r.status == "completed"
        assert _compacted(agent) == []
        assert len(provider.received) == 1   # side_query 未被调用
    finally:
        await agent.destroy()


async def test_below_threshold_no_action(tmp_path):
    """未超阈值（短链）→ 零动作。"""
    runtime, provider, agent = await _make(tmp_path)
    try:
        script_provider(provider, text_response("r"))
        r = await agent.query("短")
        assert r.status == "completed"
        assert _compacted(agent) == []
        assert len(provider.received) == 1   # 无 side_query
    finally:
        await agent.destroy()


def test_param_validation(tmp_path):
    """参数越界抛 ValueError，且无任何注册副作用。"""
    import anyio

    async def _run():
        runtime, provider = make_composables_harness(tmp_path,
                                                     agent_cls=SimpleAgent)
        agent = await runtime.create_agent("test-agent")
        with pytest.raises(ValueError):
            use_auto_compact(agent, threshold=0)
        with pytest.raises(ValueError):
            use_auto_compact(agent, threshold=1.1)
        with pytest.raises(ValueError):
            use_auto_compact(agent, tail_tokens=-1)
        with pytest.raises(ValueError):
            use_auto_compact(agent, head_tokens=-1)
        with pytest.raises(ValueError):
            use_auto_compact(agent, user_instruction_tokens=-1)
        assert list(agent.hooks.before_provider_gen) == []   # 零注册副作用
        await agent.destroy()

    anyio.run(_run)
