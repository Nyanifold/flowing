"""阶段 4 composables/use_prompt_until 测试：turn 收尾断言续跑循环。

全部 FakeProvider 脚本回放驱动；导向续跑的后续回合是 fire-and-forget
（无 query 等待者），观测走消息树本体 + 轮询等待树生长到位。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from flowing.agent import TurnContext
from flowing.message import MessageKind, MessagePriority

from composables_support import (
    PromptUntilAgent,
    SimpleAgent,
    make_composables_harness,
    script_provider,
    text_response,
)


async def _wait_until(cond, timeout: float = 2.0, step: float = 0.02) -> bool:
    """轮询等待条件成立（续跑回合无等待者，只能观测树生长）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def _steers_in_tree(agent):
    return [m for m in agent._messages.values()
            if m.source == "prompt-until"]


async def test_predicate_true_is_noop(tmp_path):
    """断言恒成立 → 什么都不做：无导向消息入队，树只含常规回合消息。"""
    calls: list = []
    PromptUntilAgent.prompt_until_args = [
        lambda a, t: calls.append(t) or True, "不应出现的导向"]
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=PromptUntilAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, text_response("r1"), text_response("r2"))
        r1 = await agent.query("第一轮")
        r2 = await agent.query("第二轮")
        assert r1.status == "completed"
        assert r2.status == "completed"
        await asyncio.sleep(0.1)
        assert _steers_in_tree(agent) == []       # 无导向消息
        assert len(agent._messages) == 4          # 2 × (user + provider)
        assert len(calls) == 2                    # 每回合收尾各检查一次
        assert all(isinstance(t, TurnContext) for t in calls)
    finally:
        PromptUntilAgent.prompt_until_args = []
        await agent.destroy()


async def test_steer_until_predicate_passes(tmp_path):
    """断言不成立 → steer EVENT 消息入队 → 下一回合消费 → 断言成立后停。

    覆盖：消息形态（kind/priority/source/tags）、树链位置（排在 r1 之
    后、r2 挂其下）、断言回调收 TurnContext、导向不进触发回合。
    """
    seen_turns: list = []

    def predicate(agent, turn) -> bool:
        seen_turns.append(turn)
        return len(seen_turns) >= 2   # 第 1 回合不成立 → 导向；第 2 回合通过

    PromptUntilAgent.prompt_until_args = [predicate, "继续"]
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=PromptUntilAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, text_response("r1"), text_response("r2"))
        result = await agent.query("开工")
        assert result.status == "completed"
        # 导向消息在回合 1 收尾时入队（先于 query 返回），由续跑回合消费
        assert await _wait_until(lambda: len(seen_turns) >= 2)
        steers = _steers_in_tree(agent)
        assert len(steers) == 1
        steer = steers[0]
        assert steer.kind is MessageKind.EVENT
        assert steer.priority == MessagePriority.STEER
        assert steer.tags == ["prompt-until"]
        assert [b.text for b in steer.content] == ["继续"]

        tree = agent._messages
        r1_id = result.turn.message_ids[-1]
        # 导向不进触发回合：回合 1 只有 user + r1
        assert steer.id not in result.turn.message_ids
        # 树链：r1 → steer → r2（续跑回合消费导向，其响应挂导向之下）
        assert steer.parent_id == r1_id
        r2 = next(m for m in tree.values()
                  if m.kind is MessageKind.PROVIDER and m.id != r1_id)
        assert r2.parent_id == steer.id

        # 断言回调两回合各一次，收到的都是 after_turn 的 TurnContext
        assert len(seen_turns) == 2
        assert all(isinstance(t, TurnContext) for t in seen_turns)
        assert seen_turns[0].aborted is False
    finally:
        PromptUntilAgent.prompt_until_args = []
        await agent.destroy()


async def test_str_template_reevaluated_per_enqueue(tmp_path):
    """字符串导向注册时归一为 Parsable，模板随每次导向现场求值——两次
    导向求值结果随实例属性变化。"""
    from flowing.composables import use_prompt_until

    calls: list = []

    def predicate(agent, turn) -> bool:
        calls.append(1)
        if len(calls) == 2:
            agent.mode = "乙"   # 第 2 次导向（本回合收尾）应求值出新值
        return len(calls) >= 3   # 前两回合导向，第 3 回合通过

    runtime, provider = make_composables_harness(tmp_path, agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    agent.mode = "甲"
    use_prompt_until(agent, predicate, "继续 {{ mode }}")
    try:
        script_provider(provider, *(text_response(f"r{i}") for i in range(3)))
        await agent.query("开工")
        assert await _wait_until(lambda: len(calls) >= 3)
        texts = [[b.text for b in m.content] for m in _steers_in_tree(agent)]
        assert texts == [["继续 甲"], ["继续 乙"]]
    finally:
        await agent.destroy()


async def test_callable_message_agent_arg_and_none_gives_up(tmp_path):
    """callable 导向收 (agent)；返回 None / 空串 → 放弃导向，循环终止。"""
    from flowing.composables import use_prompt_until

    calls: list = []
    runtime, provider = make_composables_harness(tmp_path, agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_prompt_until(agent, lambda a, t: calls.append(1) or False,
                     lambda a: None if len(calls) >= 2 else "催一次")
    try:
        script_provider(provider, text_response("r1"), text_response("r2"))
        result = await agent.query("开工")
        assert result.status == "completed"
        # 回合 1：导向一次；回合 2 收尾：callable 返回 None → 不再导向
        assert await _wait_until(lambda: len(calls) >= 2)
        assert len(calls) == 2
        assert len(_steers_in_tree(agent)) == 1
        await asyncio.sleep(0.1)
        assert len(agent._messages) == 4   # user, r1, steer, r2（无第 3 回合）
    finally:
        await agent.destroy()


async def test_aborted_turn_skipped(tmp_path):
    """turn.aborted 的回合（取消 / 打断）不检查、不入队——取消语义优先，
    断言回调收不到该回合。"""
    calls: list = []
    PromptUntilAgent.prompt_until_args = [
        lambda a, t: calls.append(t) or True, "不应出现的导向"]
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=PromptUntilAgent)
    agent = await runtime.create_agent("test-agent")

    def _aborter(a, context):
        a.abort_turn()   # 回合内打断：本回合以 aborted 收尾
        return context

    agent.hooks.before_provider_gen(_aborter, by="test-abort")
    try:
        script_provider(provider, text_response("r1"))
        result = await agent.query("会被打断的一轮")
        assert result.status == "cancelled"
        assert result.turn.aborted is True
        await asyncio.sleep(0.1)
        assert calls == []                        # 断言回调未收到该回合
        assert _steers_in_tree(agent) == []       # 未导向
        assert len(agent._messages) == 2          # user + r1，无续跑回合
    finally:
        PromptUntilAgent.prompt_until_args = []
        await agent.destroy()


async def test_remove_by_owner_uninstall(tmp_path):
    """remove_by_owner("prompt-until") 整组移除 handler → 后续回合不再
    检查、不再导向，进行中的续跑循环就此终止。"""
    calls: list = []
    PromptUntilAgent.prompt_until_args = [
        lambda a, t: calls.append(t) or len(calls) >= 2, "继续"]
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=PromptUntilAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, text_response("r1"), text_response("r2"),
                        text_response("r3"))
        await agent.query("第一问")
        # 回合 1 导向 → 回合 2 断言通过 → 循环自然终止
        assert await _wait_until(lambda: len(calls) >= 2)
        assert len(_steers_in_tree(agent)) == 1

        removed = agent.hooks.after_turn.remove_by_owner("prompt-until")
        assert removed == 1

        r2 = await agent.query("第二问")
        assert r2.status == "completed"
        await asyncio.sleep(0.1)
        assert len(calls) == 2                    # 不再检查
        assert len(_steers_in_tree(agent)) == 1   # 不再导向
    finally:
        PromptUntilAgent.prompt_until_args = []
        await agent.destroy()


async def test_fourth_type_message_fails_fast(tmp_path):
    """第四类导向内容（非 callable / Parsable / str）→ 调用时 TypeError。"""
    from flowing.composables import use_prompt_until

    runtime, provider = make_composables_harness(tmp_path, agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    with pytest.raises(TypeError):
        use_prompt_until(agent, lambda a, t: True, 123)
