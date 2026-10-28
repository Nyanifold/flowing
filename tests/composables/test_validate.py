"""composables/validate 测试：JSON Schema 校验（回合最终回复 + 入站消息）。

全部 FakeProvider 脚本回放驱动；出方向（``use_validate_turn_result``）的
steer 修复发生在同一逻辑回合的内层循环中（``query`` 一次等待覆盖全部
修复轮次），观测走 ``TurnResult`` + 消息树本体；入方向
（``use_validate_enqueued_message``）的处置在入队调用方的 ``await``
栈上直接观测（``Intercepted`` / ``InputValidationError`` / 英文告警）。
provider 脚本步数即调用次数断言——脚本是迭代器，多调一次即
``StopIteration``（回合 error 结局）暴露。
"""

from __future__ import annotations

import pytest

from flowing.composables.validate import (
    use_validate_enqueued_message,
    use_validate_turn_result,
)
from flowing.errors import (
    FlowingError,
    InputValidationError,
    Intercepted,
    OutputValidationError,
)
from flowing.message import (
    ImageBlock,
    Message,
    MessageKind,
    MessagePriority,
    StructBlock,
    TextBlock,
)
from flowing.providers import ProviderResponse

from composables_support import (
    SimpleAgent,
    ValidateAgent,
    make_composables_harness,
    script_provider,
    text_response,
    tool_call_response,
)

SCHEMA = {"answer": {"type": "string"}}
IN_SCHEMA = {"question": {"type": "string"}}


def _steers_in_tree(agent):
    return [m for m in agent._messages.values()
            if m.source == "validate-turn-result"]


def _turn_end_messages(agent):
    return [m for m in agent._messages.values() if m.turn_end]


# ---------------------------------------------------------------------------
# use_validate_turn_result（出方向：回合最终回复校验）
# ---------------------------------------------------------------------------


async def test_valid_json_turn_ends_normally(tmp_path):
    """合规 JSON：provider 一次调用、回合正常结束、不 steer。"""
    ValidateAgent.validate_kwargs = {"schema": SCHEMA}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, text_response('{"answer": "hi"}'))
        result = await agent.query("提问")
        assert result.status == "completed"
        assert result.final_text == '{"answer": "hi"}'
        assert len(agent._messages) == 2            # user + provider
        assert len(_turn_end_messages(agent)) == 1  # 唯一 turn_end
        assert _steers_in_tree(agent) == []
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


async def test_invalid_json_steer_same_turn(tmp_path):
    """不合法 JSON → 同回合 steer 修复：provider 两次调用、只有一个逻辑
    回合（一条 turn_end=True 消息）、修复消息已挂树且链式位置正确。"""
    ValidateAgent.validate_kwargs = {"schema": SCHEMA}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider,
                        text_response("this is not json"),
                        text_response('{"answer": "ok"}'))
        result = await agent.query("提问")
        assert result.status == "completed"
        assert result.final_text == '{"answer": "ok"}'

        # 一个逻辑回合：触发消息 + 坏响应 + 修复消息 + 修复响应
        assert len(result.turn.message_ids) == 4
        assert len(_turn_end_messages(agent)) == 1
        assert len(agent._messages) == 4

        steers = _steers_in_tree(agent)
        assert len(steers) == 1
        steer = steers[0]
        assert steer.kind is MessageKind.EVENT
        assert steer.priority == MessagePriority.STEER
        assert steer.tags == ["validate-turn-result"]
        text = "".join(b.text for b in steer.content)
        assert text == (
            "The final response does not conform to the required output schema.\n"
            "Validation error:\n"
            "Invalid JSON at line 1, column 1 (char 0): Expecting value\n"
            "Please re-emit the final response as a single JSON object "
            "matching the schema."
        )

        # 树链：user → 坏响应 → 修复消息 → 修复响应
        tree = agent._messages
        bad_id = result.turn.message_ids[1]
        fix_id = result.turn.message_ids[3]
        assert tree[steer.id].parent_id == bad_id
        assert tree[fix_id].parent_id == steer.id
        assert tree[bad_id].turn_end is False     # 坏响应不关闭回合
        assert tree[fix_id].turn_end is True      # 修复响应关闭回合
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


async def test_json_schema_mismatch_also_steers(tmp_path):
    """合法 JSON 但不符合 schema → steer 携带逐字段格式化诊断。"""
    ValidateAgent.validate_kwargs = {"schema": SCHEMA}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider,
                        text_response('{"answer": 123}'),
                        text_response('{"answer": "fixed"}'))
        result = await agent.query("提问")
        assert result.status == "completed"
        steers = _steers_in_tree(agent)
        assert len(steers) == 1
        text = "".join(b.text for b in steers[0].content)
        assert text == (
            "The final response does not conform to the required output schema.\n"
            "Validation error:\n"
            "- answer: Input should be a valid string; received 123\n"
            "Please re-emit the final response as a single JSON object "
            "matching the schema."
        )
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


async def test_json_schema_multiple_errors_steer_together(tmp_path):
    """同一响应的多个字段错误按 Pydantic 顺序进入同一次修复消息。"""
    ValidateAgent.validate_kwargs = {
        "schema": {"answer": {"type": "string"},
                   "detail": {"type": "string"}}
    }
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider,
                        text_response('{"answer": 123, "detail": 456}'),
                        text_response('{"answer": "fixed", "detail": "ok"}'))
        result = await agent.query("提问")
        assert result.status == "completed"
        steers = _steers_in_tree(agent)
        assert len(steers) == 1
        text = "".join(b.text for b in steers[0].content)
        assert "Validation error:\n" in text
        assert ("- answer: Input should be a valid string; received 123\n"
                "- detail: Input should be a valid string; received 456\n") in text
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


async def test_exhaustion_gives_up_and_resets_next_turn(tmp_path):
    """超限放行 + 按回合计数复位：max_retries=1 时第 2 次失败放行；下一
    回合再失败从第 1 次重新 steer（两次 steer 渲染 attempt 均为 1）。"""
    ValidateAgent.validate_kwargs = {
        "schema": SCHEMA,
        "max_retries": 1,
        "template": "Attempt {{ attempt }}/{{ max_retries }}: {{ error }}",
    }
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider,
                        text_response("bad one"),
                        text_response("bad two"),
                        text_response("bad three"),
                        text_response('{"answer": "ok"}'))
        r1 = await agent.query("第一问")
        assert r1.status == "completed"
        assert r1.final_text == "bad two"   # 超限放行：坏响应自然关闭回合

        r2 = await agent.query("第二问")
        assert r2.status == "completed"
        assert r2.final_text == '{"answer": "ok"}'

        steers = _steers_in_tree(agent)
        assert len(steers) == 2
        texts = ["".join(b.text for b in m.content) for m in steers]
        # 两回合各 steer 一次，attempt 都从 1 起（超限放行后计数已复位）
        expected = (
            "Attempt 1/1: Invalid JSON at line 1, column 1 (char 0): "
            "Expecting value"
        )
        assert texts == [expected, expected]
        assert len(_turn_end_messages(agent)) == 2   # 两个逻辑回合各一条
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


async def test_max_retries_zero_passes_through_silently(tmp_path):
    """max_retries=0：首次失败即放行，无 steer 消息。"""
    ValidateAgent.validate_kwargs = {"schema": SCHEMA, "max_retries": 0}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, text_response("bad"))
        result = await agent.query("提问")
        assert result.status == "completed"
        assert _steers_in_tree(agent) == []
        assert len(_turn_end_messages(agent)) == 1
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


async def test_on_fail_warn(tmp_path):
    """on_fail="warn"：英文告警后经放行，回合自然结束，无 steer。"""
    ValidateAgent.validate_kwargs = {"schema": SCHEMA, "on_fail": "warn"}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, text_response("bad"))
        with pytest.warns(UserWarning, match="schema validation"):
            result = await agent.query("提问")
        assert result.status == "completed"
        assert _steers_in_tree(agent) == []
        assert len(agent._messages) == 2
        assert len(_turn_end_messages(agent)) == 1
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


async def test_on_fail_raise_turn_ends_error(tmp_path):
    """on_fail="raise"：handler 抛 OutputValidationError，经回合异常通道
    上抛 → 回合以 error 结局（query 等待者拿 status="error"），异常本体
    是 FlowingError 子类、携带校验错误文本。"""
    ValidateAgent.validate_kwargs = {"schema": SCHEMA, "on_fail": "raise"}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider, text_response("bad"))
        result = await agent.query("提问")
        assert result.status == "error"

        # 低层直验：dispatch 同一 handler，异常类型与字段精确可断
        resp = ProviderResponse(
            message=Message(kind=MessageKind.PROVIDER,
                            content=[TextBlock(text="bad")]),
            finish=True)
        resp.by = "_turn"
        with pytest.raises(OutputValidationError) as exc_info:
            await agent.hooks.after_provider_gen.dispatch(agent, resp)
        assert isinstance(exc_info.value, FlowingError)
        assert exc_info.value.error == (
            "Invalid JSON at line 1, column 1 (char 0): Expecting value")
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


async def test_template_jinja_context_rendering(tmp_path):
    """template 参数按 Jinja2 现场渲染：error / attempt / max_retries
    变量随本次校验结果求值。"""
    ValidateAgent.validate_kwargs = {
        "schema": {"answer": {"type": "integer"}},
        "template": "A{{ attempt }}/{{ max_retries }}:{{ error }}",
    }
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider,
                        text_response("{oops"),
                        text_response('{"answer": 42}'))
        result = await agent.query("提问")
        assert result.status == "completed"
        steers = _steers_in_tree(agent)
        assert len(steers) == 1
        text = "".join(b.text for b in steers[0].content)
        assert text == (
            "A1/3:Invalid JSON at line 1, column 2 (char 1): "
            "Expecting property name enclosed in double quotes"
        )
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


async def test_template_from_agent_validate_output_error(tmp_path):
    """template 缺省读 Agent 的 ``validate_output_error`` 属性（参数缺省时
    的回落链）。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    agent.validate_output_error = "custom fix {{ attempt }}"
    use_validate_turn_result(agent, schema=SCHEMA)
    try:
        script_provider(provider,
                        text_response("bad"),
                        text_response('{"answer": "ok"}'))
        result = await agent.query("提问")
        assert result.status == "completed"
        steers = _steers_in_tree(agent)
        assert len(steers) == 1
        assert [b.text for b in steers[0].content] == ["custom fix 1"]
    finally:
        await agent.destroy()


async def test_schema_from_agent_output(tmp_path):
    """schema 缺省读 Agent 的 ``output`` 属性（对象级属性，回落链末位）。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    agent.output = SCHEMA
    use_validate_turn_result(agent)
    try:
        script_provider(provider, text_response('{"answer": "ok"}'))
        result = await agent.query("提问")
        assert result.status == "completed"
        assert _steers_in_tree(agent) == []
    finally:
        await agent.destroy()


async def test_missing_schema_fails_fast_at_mount(tmp_path):
    """参数与 agent.output 皆无 schema → 挂载时内置 ValueError，无副作用。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        with pytest.raises(ValueError, match="schema"):
            use_validate_turn_result(agent)
        # 挂载失败未注册 handler：坏文本照常通过
        script_provider(provider, text_response("bad"))
        result = await agent.query("提问")
        assert result.status == "completed"
        assert _steers_in_tree(agent) == []
    finally:
        await agent.destroy()


async def test_invalid_args_fail_fast_at_mount(tmp_path):
    """on_fail 非法 / max_retries 为负 → 挂载时 ValueError。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        with pytest.raises(ValueError, match="on_fail"):
            use_validate_turn_result(agent, schema=SCHEMA, on_fail="bogus")
        with pytest.raises(ValueError, match="max_retries"):
            use_validate_turn_result(agent, schema=SCHEMA, max_retries=-1)
    finally:
        await agent.destroy()


async def test_finish_path_skipped(tmp_path):
    """finish 结构化交卷路径自动跳过：带 tool_call 块的响应不校验，
    finish 置位 finish_output 后回合结束，无 steer、provider 只调一次。"""
    ValidateAgent.validate_kwargs = {"schema": SCHEMA}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    agent.add_tool("finish")
    try:
        resp, _blocks = tool_call_response(("finish", {"summary": "done"}))
        script_provider(provider, resp)
        result = await agent.query("任务")
        assert result.status == "completed"
        assert agent.last_result == {"summary": "done"}
        assert _steers_in_tree(agent) == []
        providers = [m for m in agent._messages.values()
                     if m.kind is MessageKind.PROVIDER]
        assert len(providers) == 1
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


async def test_custom_by_owner(tmp_path):
    """by 参数：修复消息 source / tags 取自定义 owner，
    remove_by_owner(by) 整组移除。"""
    ValidateAgent.validate_kwargs = {"schema": SCHEMA, "by": "my-validate"}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        script_provider(provider,
                        text_response("bad"),
                        text_response('{"answer": "ok"}'))
        result = await agent.query("提问")
        assert result.status == "completed"
        steers = [m for m in agent._messages.values()
                  if m.source == "my-validate"]
        assert len(steers) == 1
        assert steers[0].tags == ["my-validate"]

        removed = agent.hooks.after_provider_gen.remove_by_owner("my-validate")
        assert removed == 1
        script_provider(provider, text_response("bad"))
        result2 = await agent.query("再问")
        assert result2.status == "completed"
        assert len([m for m in agent._messages.values()
                    if m.source == "my-validate"]) == 1
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


# ---------------------------------------------------------------------------
# use_validate_enqueued_message（入方向：入站消息校验）
# ---------------------------------------------------------------------------


async def test_enqueued_valid_text_passes(tmp_path):
    """合规文本：入队放行、回合正常执行。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)
    try:
        script_provider(provider, text_response("answer"))
        result = await agent.query('{"question": "hi"}')
        assert result.status == "completed"
        assert len(agent._messages) == 2            # user + provider
        assert len(_turn_end_messages(agent)) == 1
    finally:
        await agent.destroy()


async def test_enqueued_valid_struct_block_passes(tmp_path):
    """合规 StructBlock：data 直校通过、入队放行。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)
    try:
        script_provider(provider,
                        text_response("answer"),
                        text_response("done"))
        msg_id = await agent.message([StructBlock(data={"question": "hi"})])
        assert isinstance(msg_id, str)
        result = await agent.query('{"question": "follow"}')
        assert result.status == "completed"
        struct_msgs = [m for m in agent._messages.values()
                       if any(isinstance(b, StructBlock) for b in m.content)]
        assert len(struct_msgs) == 1
    finally:
        await agent.destroy()


async def test_enqueued_invalid_json_reject_via_query(tmp_path):
    """JSON 非法 + reject（默认）：query() 调用方拿 Intercepted，
    ``_pending_turns`` 无泄漏、消息不入树。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)
    try:
        with pytest.raises(Intercepted, match="schema validation"):
            await agent.query("this is not json")
        assert agent._pending_turns == {}       # finally 清理：等待句柄不泄漏
        assert len(agent._messages) == 0        # 被拒消息不入树
    finally:
        await agent.destroy()


async def test_enqueued_schema_mismatch_reject(tmp_path):
    """合法 JSON 但不符合 schema（pydantic ValidationError）→ 同样拒绝。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)
    try:
        with pytest.raises(Intercepted, match="schema validation"):
            await agent.message('{"question": 123}')
        assert len(agent._messages) == 0
    finally:
        await agent.destroy()


async def test_enqueued_reject_via_message_caller(tmp_path):
    """reject 在入队调用方 await 栈上原样上抛：message() 调用方拿
    Intercepted，队列不变。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)
    try:
        with pytest.raises(Intercepted):
            await agent.message("bad")
        assert len(agent._messages) == 0
    finally:
        await agent.destroy()


async def test_enqueued_batch_reject_keeps_enqueued(tmp_path):
    """enqueue_messages 批量中途被拒：已入队的不回滚，被拒的不进树。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)
    try:
        script_provider(provider,
                        text_response("one"),
                        text_response("two"),
                        text_response("three"))
        good = Message(kind=MessageKind.USER,
                       source="peer_a",
                       content=[TextBlock(text='{"question": "ok"}')])
        bad = Message(kind=MessageKind.USER,
                      source="peer_b",
                      content=[TextBlock(text="not json")])
        with pytest.raises(Intercepted):
            await agent.enqueue_messages([good, bad])
        # 等待 follow-up 回合完成（其前 good 必已被消费——FIFO）
        result = await agent.query('{"question": "follow-up"}')
        assert result.status == "completed"
        user_sources = sorted(m.source for m in agent._messages.values()
                              if m.kind is MessageKind.USER)
        assert user_sources == ["", "peer_a"]     # bad 从未入队
    finally:
        await agent.destroy()


async def test_enqueued_on_fail_warn_passes_with_warning(tmp_path):
    """on_fail="warn"：英文告警后放行，消息照常入队消费。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA, on_fail="warn")
    try:
        script_provider(provider,
                        text_response("one"),
                        text_response("two"))
        with pytest.warns(UserWarning, match="schema validation"):
            await agent.message("bad")
        result = await agent.query('{"question": "ok"}')
        assert result.status == "completed"
        assert len(agent._messages) == 4          # 两条 user + 两条 provider
    finally:
        await agent.destroy()


async def test_enqueued_on_fail_raise_propagates(tmp_path):
    """on_fail="raise"：InputValidationError 原样上抛至入队调用方
    （无回合异常通道兜底），``error`` 字段携带校验错误文本。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA, on_fail="raise")
    try:
        with pytest.raises(InputValidationError) as exc_info:
            await agent.query("bad")
        assert isinstance(exc_info.value, FlowingError)
        assert exc_info.value.error == (
            "Invalid JSON at line 1, column 1 (char 0): Expecting value")
        assert str(exc_info.value) == (
            "enqueued message failed schema validation: "
            "Invalid JSON at line 1, column 1 (char 0): Expecting value"
        )
        assert agent._pending_turns == {}
        assert len(agent._messages) == 0

        # schema 不符同理：格式化诊断正文进 error 字段
        with pytest.raises(InputValidationError) as exc_info2:
            await agent.message('{"question": 123}')
        assert exc_info2.value.error == (
            "- question: Input should be a valid string; received 123")
    finally:
        await agent.destroy()


async def test_enqueued_no_kind_filter_gates_all_kinds_by_default(tmp_path):
    """kinds 缺省不过滤：EVENT / SUBAGENT / PEER 的非法 JSON 同样被拒
    （默认不对其他 kind 做隐式排除）。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)
    try:
        for kind in (MessageKind.EVENT, MessageKind.SUBAGENT,
                     MessageKind.PEER):
            with pytest.raises(Intercepted, match="schema validation"):
                await agent.message("not json", kind=kind)
        assert len(agent._messages) == 0        # 全部被拒，无一入树
    finally:
        await agent.destroy()


async def test_enqueued_kinds_tuple_limits_gated_kinds(tmp_path):
    """kinds=(USER,)：EVENT 非法 JSON 放行、USER 仍被拒（显式过滤）。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA,
                                  kinds=(MessageKind.USER,))
    try:
        script_provider(provider,
                        text_response("e"),
                        text_response("u"))
        await agent.message("not json", kind=MessageKind.EVENT)   # 放行
        with pytest.raises(Intercepted, match="schema validation"):
            await agent.message("not json")                        # USER 被拒
        result = await agent.query('{"question": "ok"}')
        assert result.status == "completed"
        kinds_in_tree = {m.kind for m in agent._messages.values()}
        assert MessageKind.EVENT in kinds_in_tree
    finally:
        await agent.destroy()


async def test_enqueued_source_str_hit_and_miss(tmp_path):
    """source 为 str：命中模式才闸，未命中放行。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA,
                                  source="peer_a")
    try:
        script_provider(provider,
                        text_response("one"),
                        text_response("two"))
        with pytest.raises(Intercepted):
            await agent.message("bad", source="peer_a")     # 命中 → 拒
        result = await agent.query('{"question": "ok"}',
                                   source="peer_b")          # 未命中 → 放行
        assert result.status == "completed"
        user_sources = sorted(m.source for m in agent._messages.values()
                              if m.kind is MessageKind.USER)
        assert user_sources == ["peer_b"]     # 被拒的 peer_a 从未入树
    finally:
        await agent.destroy()


async def test_enqueued_source_list_and_glob(tmp_path):
    """source 为 list：任一模式命中即闸；glob（peer_*）按 fnmatch 匹配。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA,
                                  source=["peer_a", "peer_b"])
    try:
        with pytest.raises(Intercepted):
            await agent.message("bad", source="peer_b")
    finally:
        await agent.destroy()

    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA,
                                  source="peer_*")
    try:
        with pytest.raises(Intercepted):
            await agent.message("bad", source="peer_x")     # glob 命中
        script_provider(provider, text_response("ok"))
        result = await agent.query('{"question": "ok"}',
                                   source="other")          # glob 未命中
        assert result.status == "completed"
    finally:
        await agent.destroy()


async def test_enqueued_source_empty_string_only_gates_unsourced(tmp_path):
    """source="" 只闸未填 source 的消息（外部投递默认空串）；
    显式 source 的消息放行。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA, source="")
    try:
        with pytest.raises(Intercepted):
            await agent.message("bad")                      # source 默认 ""
        script_provider(provider, text_response("ok"))
        result = await agent.query('{"question": "ok"}',
                                   source="peer_a")
        assert result.status == "completed"
    finally:
        await agent.destroy()


async def test_enqueued_none_source_and_kinds_and(tmp_path):
    """source=None 不过滤本维度；source 与 kinds AND 合取——kind 不在
    元组内或 source 未命中都放行。（kinds=None 缺省维度由默认测试覆盖。）"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA,
                                  kinds=(MessageKind.USER,),
                                  source="peer_*")
    try:
        script_provider(provider,
                        text_response("one"),
                        text_response("two"))
        # PEER + peer_* 命中：kind 不在元组内 → 放行（AND 之一不满足）
        await agent.message("not json", kind=MessageKind.PEER,
                            source="peer_a")
        # USER + source 未命中 → 放行
        result = await agent.query('{"question": "ok"}',
                                   source="external")
        assert result.status == "completed"
        kinds_in_tree = {m.kind for m in agent._messages.values()}
        assert MessageKind.PEER in kinds_in_tree
    finally:
        await agent.destroy()


async def test_enqueued_match_predicate(tmp_path):
    """match 谓词：match(msg) 为真才进入校验，与 kinds/source AND 合取。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(
        agent, schema=IN_SCHEMA,
        match=lambda msg: "raw" in msg.tags)
    try:
        script_provider(provider,
                        text_response("one"),
                        text_response("two"))
        await agent.message("bad")                       # 无 tag → 谓词假 → 放行
        with pytest.raises(Intercepted):
            await agent.message("bad", tags=["raw"])     # 谓词真 → 校验 → 拒
        result = await agent.query('{"question": "ok"}')
        assert result.status == "completed"
        user_msgs = [m for m in agent._messages.values()
                     if m.kind is MessageKind.USER]
        assert len(user_msgs) == 2                       # 被拒者不入树
    finally:
        await agent.destroy()


async def test_enqueued_mixed_blocks_any_failure_rejects(tmp_path):
    """混合块消息：TextBlock 合规但 StructBlock 不合规 → 任一不合格即拒。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)
    try:
        msg = Message(kind=MessageKind.USER, content=[
            TextBlock(text='{"question": "ok"}'),
            StructBlock(data={"wrong": "shape"}),
        ])
        with pytest.raises(Intercepted) as exc_info:
            await agent.enqueue_message(msg)
        assert str(exc_info.value) == (
            "enqueued message failed schema validation: StructBlock 0:\n"
            "- question: Field required"
        )
        assert len(agent._messages) == 0
    finally:
        await agent.destroy()


async def test_enqueued_pure_media_passes(tmp_path):
    """纯媒体块消息（无 TextBlock / StructBlock 负载）放行。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)
    try:
        script_provider(provider,
                        text_response("one"),
                        text_response("two"))
        msg_id = await agent.message(
            [ImageBlock(data="QUJD", name="demo.png")])
        assert isinstance(msg_id, str)
        result = await agent.query('{"question": "ok"}')
        assert result.status == "completed"
        media_msgs = [m for m in agent._messages.values()
                      if any(isinstance(b, ImageBlock) for b in m.content)]
        assert len(media_msgs) == 1
    finally:
        await agent.destroy()


async def test_enqueued_schema_from_agent_input_attr(tmp_path):
    """schema 缺省读 Agent 的 ``input`` 属性（回落链末位）。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    agent.input = IN_SCHEMA
    use_validate_enqueued_message(agent)
    try:
        with pytest.raises(Intercepted):
            await agent.message("bad")
        assert len(agent._messages) == 0
    finally:
        await agent.destroy()


async def test_enqueued_missing_schema_fails_fast_at_mount(tmp_path):
    """参数与 agent.input 皆无 schema → 挂载时内置 ValueError，无副作用。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        with pytest.raises(ValueError, match="schema"):
            use_validate_enqueued_message(agent)
        # 挂载失败未注册 handler：坏文本照常入队
        script_provider(provider, text_response("ok"))
        result = await agent.query("bad")
        assert result.status == "completed"
    finally:
        await agent.destroy()


async def test_enqueued_invalid_args_fail_fast_at_mount(tmp_path):
    """on_fail 非法 / kinds 为空 → 挂载时 ValueError，不产生注册副作用。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    try:
        with pytest.raises(ValueError, match="on_fail"):
            use_validate_enqueued_message(agent, schema=IN_SCHEMA,
                                          on_fail="bogus")
        with pytest.raises(ValueError, match="kinds"):
            use_validate_enqueued_message(agent, schema=IN_SCHEMA,
                                          kinds=())
    finally:
        await agent.destroy()


async def test_enqueued_custom_by_owner(tmp_path):
    """by 参数：remove_by_owner(by) 整组移除后策略失效。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA, by="my-gate")
    try:
        with pytest.raises(Intercepted):
            await agent.message("bad")
        removed = agent.hooks.on_enqueue.remove_by_owner("my-gate")
        assert removed == 1
        script_provider(provider, text_response("ok"))
        result = await agent.query("bad")     # 移除后不再校验
        assert result.status == "completed"
    finally:
        await agent.destroy()


async def test_enqueued_default_gates_steer_repair_messages(tmp_path):
    """默认不做隐式排除：同挂 turn-result 时，其 steer 修复消息（EVENT，
    非 JSON 模板）同样过闸并被拒——steer 入队抛 Intercepted，经回合异常
    通道收尾，回合以 error 结局，steer 消息从未入树。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_turn_result(agent, schema=SCHEMA)          # steer 默认
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)  # reject 默认
    try:
        script_provider(provider, text_response("not json"))   # 坏回复 → steer
        result = await agent.query('{"question": "ask"}')
        assert result.status == "error"
        assert _steers_in_tree(agent) == []     # 修复消息被入站闸门拦截
    finally:
        await agent.destroy()


async def test_enqueued_explicit_filter_lets_steer_through(tmp_path):
    """共存：挂载时显式豁免 STEER 优先级消息后，turn-result 的修复
    全链路正常，入站闸门仍闸外部消息——兼容是应用层的显式选择。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_turn_result(agent, schema=SCHEMA)          # steer 默认
    use_validate_enqueued_message(
        agent, schema=IN_SCHEMA,
        match=lambda m: m.priority != MessagePriority.STEER)
    try:
        script_provider(provider,
                        text_response("not json"),              # 坏回复 → steer
                        text_response('{"answer": "fixed"}'))   # 修复回复
        result = await agent.query('{"question": "ask"}')
        assert result.status == "completed"
        assert result.final_text == '{"answer": "fixed"}'
        steers = _steers_in_tree(agent)
        assert len(steers) == 1                 # steer 修复消息经豁免入队
        assert len(_turn_end_messages(agent)) == 1

        # 入站闸门仍在工作
        with pytest.raises(Intercepted):
            await agent.message("bad inbound")
    finally:
        await agent.destroy()


@pytest.mark.parametrize("on_fail", ["steer", "warn", "raise"])
async def test_output_policies_share_validation_body(tmp_path, on_fail):
    """output 三策略复用同一正文，并保留各自处置结果。"""
    ValidateAgent.validate_kwargs = {"schema": SCHEMA, "on_fail": on_fail}
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=ValidateAgent)
    agent = await runtime.create_agent("test-agent")
    body = "- answer: Input should be a valid string; received 123"
    try:
        if on_fail == "steer":
            script_provider(provider,
                            text_response('{"answer": 123}'),
                            text_response('{"answer": "fixed"}'))
            result = await agent.query("提问")
            assert result.status == "completed"
            steers = _steers_in_tree(agent)
            assert len(steers) == 1
            assert "".join(b.text for b in steers[0].content) == (
                "The final response does not conform to the required output "
                "schema.\nValidation error:\n" + body + "\n"
                "Please re-emit the final response as a single JSON object "
                "matching the schema."
            )
        elif on_fail == "warn":
            script_provider(provider, text_response('{"answer": 123}'))
            with pytest.warns(UserWarning) as caught:
                result = await agent.query("提问")
            assert result.status == "completed"
            assert str(caught[0].message) == (
                "turn output failed schema validation: " + body)
            assert _steers_in_tree(agent) == []
        else:
            script_provider(provider, text_response('{"answer": 123}'))
            result = await agent.query("提问")
            assert result.status == "error"
            response = ProviderResponse(
                message=Message(
                    kind=MessageKind.PROVIDER,
                    content=[TextBlock(text='{"answer": 123}')]),
                finish=True,
                by="_turn",
            )
            with pytest.raises(OutputValidationError) as exc_info:
                await agent.hooks.after_provider_gen.dispatch(agent, response)
            assert exc_info.value.error == body
            assert str(exc_info.value) == (
                "turn output failed schema validation: " + body)
    finally:
        ValidateAgent.validate_kwargs = {}
        await agent.destroy()


@pytest.mark.parametrize("on_fail", ["reject", "warn", "raise"])
async def test_input_policies_share_validation_body(tmp_path, on_fail):
    """input 三策略复用同一正文，并保留放行、拒绝与异常结果。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA, on_fail=on_fail)
    body = "- question: Input should be a valid string; received 123"
    try:
        if on_fail == "reject":
            with pytest.raises(Intercepted) as exc_info:
                await agent.message('{"question": 123}')
            assert str(exc_info.value) == (
                "enqueued message failed schema validation: " + body)
            assert len(agent._messages) == 0
        elif on_fail == "warn":
            script_provider(provider,
                            text_response("ok"), text_response("done"))
            with pytest.warns(UserWarning) as caught:
                msg_id = await agent.message('{"question": 123}')
            assert str(caught[0].message) == (
                "enqueued message failed schema validation: " + body)
            result = await agent.query('{"question": "follow"}')
            assert result.status == "completed"
            assert msg_id in agent._messages
        else:
            with pytest.raises(InputValidationError) as exc_info:
                await agent.message('{"question": 123}')
            assert exc_info.value.error == body
            assert str(exc_info.value) == (
                "enqueued message failed schema validation: " + body)
            assert len(agent._messages) == 0
    finally:
        await agent.destroy()


@pytest.mark.parametrize("on_fail", ["reject", "warn", "raise"])
async def test_struct_block_policies_include_ordinal(tmp_path, on_fail):
    """混合内容中第二个 StructBlock 失败时序号只按 StructBlock 计数。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA, on_fail=on_fail)
    body = (
        "StructBlock 1:\n"
        "- question: Input should be a valid string; received 123"
    )
    msg = Message(kind=MessageKind.USER, content=[
        StructBlock(data={"question": "first"}),
        TextBlock(text='{"question": "text"}'),
        StructBlock(data={"question": 123}),
    ])
    try:
        if on_fail == "reject":
            with pytest.raises(Intercepted) as exc_info:
                await agent.enqueue_message(msg)
            assert str(exc_info.value) == (
                "enqueued message failed schema validation: " + body)
            assert len(agent._messages) == 0
        elif on_fail == "warn":
            script_provider(provider,
                            text_response("ok"), text_response("done"))
            with pytest.warns(UserWarning) as caught:
                await agent.enqueue_message(msg)
            assert str(caught[0].message) == (
                "enqueued message failed schema validation: " + body)
            result = await agent.query('{"question": "follow"}')
            assert result.status == "completed"
            assert msg.id in agent._messages
        else:
            with pytest.raises(InputValidationError) as exc_info:
                await agent.enqueue_message(msg)
            assert exc_info.value.error == body
            assert str(exc_info.value) == (
                "enqueued message failed schema validation: " + body)
            assert len(agent._messages) == 0
    finally:
        await agent.destroy()


async def test_text_failure_stops_before_struct_blocks(tmp_path):
    """文本负载先失败时不继续校验或追加后续 StructBlock 错误。"""
    runtime, provider = make_composables_harness(tmp_path,
                                                 agent_cls=SimpleAgent)
    agent = await runtime.create_agent("test-agent")
    use_validate_enqueued_message(agent, schema=IN_SCHEMA)
    msg = Message(kind=MessageKind.USER, content=[
        TextBlock(text="bad"),
        StructBlock(data={"question": 123}),
    ])
    try:
        with pytest.raises(Intercepted) as exc_info:
            await agent.enqueue_message(msg)
        assert str(exc_info.value) == (
            "enqueued message failed schema validation: "
            "Invalid JSON at line 1, column 1 (char 0): Expecting value"
        )
        assert "StructBlock" not in str(exc_info.value)
        assert len(agent._messages) == 0
    finally:
        await agent.destroy()
