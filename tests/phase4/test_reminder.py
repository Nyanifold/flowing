"""阶段 4 composables/use_system_reminder 测试（W40）：测试清单
T122–T126（spec 无编号测试案例，从行为规约转化）。

全部 FakeProvider 脚本回放驱动；注入观测走 ``TurnResult.turn.message_ids``
（挂树批次）与消息树本体；clean/recover 语义用真 Runtime 重放验证。
"""

from __future__ import annotations

from flowing.errors import Intercepted
from flowing.message import MessageKind

from composables_support import (
    ReminderAgent,
    add_fake_provider,
    make_composables_harness,
    make_runtime,
    script_provider,
    text_response,
)


def _reminders_in_turn(agent, turn):
    """本回合挂树批次中 source="system-reminder" 的消息。"""
    return [agent._messages[mid] for mid in turn.message_ids
            if agent._messages[mid].source == "system-reminder"]


def _reminders_in_tree(agent):
    return [m for m in agent._messages.values()
            if m.source == "system-reminder"]


async def test_t122_single_compressed_message_per_turn(tmp_path):
    """T122：默认参数 → 每个逻辑 Turn 的 before_turn 把 contents 压缩为
    一条 Message(kind=EVENT, source="system-reminder",
    tags=["system-reminder"]) 附加到 pending_messages 末尾（排在触发
    消息之后），随批次挂树持久化。"""
    ReminderAgent.reminder_args = [["静态提醒", lambda a: "动态提醒"]]
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ReminderAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, text_response("r1"), text_response("r2"))
        for expected_user in ("第一轮", "第二轮"):
            result = await agent.query(expected_user)
            assert result.status == "completed"
            reminders = _reminders_in_turn(agent, result.turn)
            assert len(reminders) == 1               # 压缩为一条
            reminder = reminders[0]
            assert reminder.kind is MessageKind.EVENT
            assert reminder.tags == ["system-reminder"]
            assert [b.text for b in reminder.content] == ["静态提醒", "动态提醒"]
            # 附加在触发消息之后：批次序为 user → reminder → provider
            kinds = [agent._messages[mid].kind for mid in result.turn.message_ids]
            assert kinds == [MessageKind.USER, MessageKind.EVENT,
                             MessageKind.PROVIDER]
        assert len(_reminders_in_tree(agent)) == 2   # 随批次挂树持久化
    finally:
        ReminderAgent.reminder_args = []
        await agent.destroy()


async def test_t123_clean_semantics(tmp_path):
    """T123：clean=True → after_turn 按 tags 擦除本回合注入的提醒
    （head 回退由 Agent 层处理）；clean=False → 提醒留树（崩溃可
    恢复，真 Runtime 重放验证）。"""
    # clean=True：注入后擦除
    ReminderAgent.reminder_args = [["易逝提醒"]]
    ReminderAgent.reminder_kwargs = {"clean": True}
    runtime, provider = make_composables_harness(tmp_path / "clean",
                                                 agent_cls=ReminderAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, text_response("r1"), text_response("r2"))
        r1 = await agent.query("第一轮")
        # 回合内确实注入过（before_turn 附加），after_turn 已按 tags 擦除
        assert _reminders_in_tree(agent) == []
        head = agent._messages[agent.current_head_id]
        assert head.kind is MessageKind.PROVIDER   # head 未被擦除波及
        r2 = await agent.query("第二轮")           # 下一回合照常注入再擦除
        assert r2.status == "completed"
        assert _reminders_in_tree(agent) == []
    finally:
        ReminderAgent.reminder_args = []
        ReminderAgent.reminder_kwargs = {}
        await agent.destroy()

    # clean=False：留树 + 崩溃重放可恢复（真 Runtime + 真 recover 管线）
    ReminderAgent.reminder_args = [["持久提醒"]]
    rt1 = make_runtime(tmp_path / "persist")
    rt1.register_agent_type("test-agent", ReminderAgent)
    provider1 = add_fake_provider(rt1)
    script_provider(provider1, text_response("r1"))
    agent1 = await rt1.create_agent("test-agent")
    await agent1.query("第一轮")
    assert len(_reminders_in_tree(agent1)) == 1
    agent_id = agent1.node_id
    await rt1.shutdown()

    rt2 = make_runtime(tmp_path / "persist")
    rt2.register_agent_type("test-agent", ReminderAgent)
    add_fake_provider(rt2)
    try:
        recovered = await rt2.recover_agent(agent_id)
        reminders = _reminders_in_tree(recovered)
        assert len(reminders) == 1               # 崩溃可恢复
        assert reminders[0].content[0].text == "持久提醒"
    finally:
        ReminderAgent.reminder_args = []
        await rt2.shutdown()


async def test_t124_interval_gating(tmp_path):
    """T124：message_interval=4 → 距上次注入新增消息 <4 的回合跳过；
    time_interval 与关系（任一不满足即跳过）；两者均 0 → 每回合注入。"""
    # 子例 1：message_interval=4（注入回合 +3 条消息，普通回合 +2 条）
    ReminderAgent.reminder_args = [["r"]]
    ReminderAgent.reminder_kwargs = {"message_interval": 4}
    runtime, provider = make_composables_harness(tmp_path / "msg",
                                                 agent_cls=ReminderAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, *(text_response(f"r{i}") for i in range(4)))
        injected = []
        for i in range(4):
            result = await agent.query(f"第{i}轮")
            injected.append(bool(_reminders_in_turn(agent, result.turn)))
        # t1 首次注入；t2 +3<4 跳过；t3 累计 +5≥4 注入；t4 +3<4 跳过
        assert injected == [True, False, True, False]
    finally:
        ReminderAgent.reminder_args = []
        ReminderAgent.reminder_kwargs = {}
        await agent.destroy()

    # 子例 2：time_interval=3600（与关系：时间轴不满足即跳过）
    ReminderAgent.reminder_args = [["r"]]
    ReminderAgent.reminder_kwargs = {"time_interval": 3600}
    runtime2, provider2 = make_composables_harness(tmp_path / "time",
                                                   agent_cls=ReminderAgent)
    agent2 = await runtime2.create_agent("test-agent")
    try:
        script_provider(provider2, text_response("r1"), text_response("r2"))
        r1 = await agent2.query("一")
        r2 = await agent2.query("二")
        assert bool(_reminders_in_turn(agent2, r1.turn)) is True
        assert bool(_reminders_in_turn(agent2, r2.turn)) is False   # 距上次 < 3600s
    finally:
        ReminderAgent.reminder_args = []
        ReminderAgent.reminder_kwargs = {}
        await agent2.destroy()

    # 子例 3：两者均 0 → 每回合注入
    ReminderAgent.reminder_args = [["r"]]
    runtime3, provider3 = make_composables_harness(tmp_path / "zero",
                                                   agent_cls=ReminderAgent)
    agent3 = await runtime3.create_agent("test-agent")
    try:
        script_provider(provider3, text_response("r1"), text_response("r2"))
        for q in ("一", "二"):
            result = await agent3.query(q)
            assert len(_reminders_in_turn(agent3, result.turn)) == 1
    finally:
        ReminderAgent.reminder_args = []
        await agent3.destroy()


async def test_t125_empty_contents_and_intercepted_batch(tmp_path):
    """T125：contents 为空清单或全部回调返回空串 → 本回合不注入；
    before_turn 被 Intercepted 阻断 → 批次整体丢弃、提醒随之不注入。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ReminderAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        # contents 缺省（None → 空清单）：不注入
        script_provider(provider, text_response("r1"))
        r1 = await agent.query("一")
        assert r1.status == "completed"
        assert _reminders_in_tree(agent) == []

        # 全部回调返回空串：不注入（运行期改contents无效——新实例验证）
    finally:
        await agent.destroy()

    ReminderAgent.reminder_args = [[lambda a: "", ""]]
    runtime2, provider2 = make_composables_harness(tmp_path / "empty",
                                                   agent_cls=ReminderAgent)
    agent2 = await runtime2.create_agent("test-agent")
    try:
        script_provider(provider2, text_response("r1"), text_response("r2"))
        r1 = await agent2.query("一")
        assert r1.status == "completed"
        assert _reminders_in_tree(agent2) == []

        # before_turn 被 Intercepted 阻断：批次整体丢弃（含已附加的提醒）
        ReminderAgent.reminder_args = [["会被丢弃的提醒"]]

        def _block(a, turn):
            raise Intercepted("安全拦截")

        agent2.hooks.before_turn(_block, by="test")
        size_before = len(agent2._messages)
        r2 = await agent2.query("二")
        assert r2.status == "blocked"
        assert len(agent2._messages) == size_before   # 触发消息与提醒均未挂树
        assert _reminders_in_tree(agent2) == []
    finally:
        ReminderAgent.reminder_args = []
        await agent2.destroy()


async def test_t126_remove_by_owner_uninstall(tmp_path):
    """T126：remove_by_owner("system-reminder") → before_turn/after_turn
    两个 handler 整组移除，后续回合无注入。"""
    ReminderAgent.reminder_args = [["r"]]
    ReminderAgent.reminder_kwargs = {"clean": True}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ReminderAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, text_response("r1"), text_response("r2"))
        r1 = await agent.query("一")
        assert r1.status == "completed"
        removed = agent.hooks.before_turn.remove_by_owner("system-reminder")
        removed += agent.hooks.after_turn.remove_by_owner("system-reminder")
        assert removed == 2                        # 注入 + 清理两个 handler
        r2 = await agent.query("二")
        assert r2.status == "completed"
        assert _reminders_in_turn(agent, r2.turn) == []   # 不再注入
        assert _reminders_in_tree(agent) == []
    finally:
        ReminderAgent.reminder_args = []
        ReminderAgent.reminder_kwargs = {}
        await agent.destroy()


async def test_error_turn_with_cleaned_reminder_no_crash(tmp_path):
    """回归：clean=True 的提醒在 after_turn 被擦除后，error 回合（PROVIDER
    消息从未挂树）的 build_turn_result 逆序遍历 turn.message_ids 时跳过
    已 tombstone 的 id，不抛 KeyError——回合以 status="error" 正常交付。"""
    from flowing.errors import ServerError

    ReminderAgent.reminder_args = [["易逝提醒"]]
    ReminderAgent.reminder_kwargs = {"clean": True}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ReminderAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, ServerError("boom"))
        r = await agent.query("会失败的一轮")
        assert r.status == "error"          # 不楔死、不变 turn crashed
        assert r.final_text == ""           # 无 PROVIDER 消息
        assert _reminders_in_tree(agent) == []
    finally:
        ReminderAgent.reminder_args = []
        ReminderAgent.reminder_kwargs = {}
        await agent.destroy()
