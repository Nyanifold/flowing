"""阶段 2：消息族、控制族与 Turn 引擎测试（T44–T84）。

覆盖：query/消息入队与撤回（T44–T48）、provider_gen 双模式与 delta（T49–T54）、
side_query 边界（T55–T57）、fork（T58–T60）、pause/resume/cancel 族
（T61–T66）、②.5 urgent 吸收（T67–T69）、工具循环（T70–T72、T74–T79）、
空 turn 边缘（T73）、上下文估算（T80–T82）、树手术 head 语义（T83）、
钩子异常不楔死（T84）、provider_gen 在途取消竞速（T141–T143）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from flowing.errors import Intercepted, InvalidRequestError
from flowing.agent import Execution, ForkContext
from flowing.message import (
    Message,
    MessageKind,
    MessagePriority,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
)
from flowing.providers import ProviderDelta, Usage
from flowing.tool import Tool, ToolCall, ToolDefinition, ToolResult

from conftest import (
    SimpleAgent,
    script_provider,
    text_response,
    tool_call_response,
)


# ---------------------------------------------------------------------------
# 测试工具（占位 Tool 子类，R-02 占位 __call__ 驱动）
# ---------------------------------------------------------------------------


class EchoTool(Tool):
    definition = ToolDefinition(
        name="echo", description="回显参数",
        params_schema={"text": {"type": "string"}})

    async def execute(self, *, text: str) -> str:
        return text


class OpenTool(Tool):
    """strict=False 工具：LLM 多传的未声明参数放行透传到 execute
    （「工具层不限制参数」契约的回归载体）。"""

    definition = ToolDefinition(
        name="open", description="开放参数工具",
        params_schema={"text": {"type": "string"}}, strict=False)

    async def execute(self, *, text: str = "", **extra) -> dict:
        return {"text": text, "extra": extra}


class WaitTool(Tool):
    """门控工具：等到共享 Event 置位才返回；记录调用次数。"""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate
        self.calls = 0

    definition = ToolDefinition(
        name="wait", description="等待门控", params_schema={})

    async def execute(self) -> str:
        self.calls += 1
        await self.gate.wait()
        return f"done-{self.calls}"


class AsyncResultTool(Tool):
    """异步工具：execute 返回 asyncio.Task（结果经完成回调 EVENT 入队）。"""

    definition = ToolDefinition(
        name="async-echo", description="异步回显",
        params_schema={"text": {"type": "string"}})

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate   # 外部控制完成时点（测试时序闸门）
        self.task: asyncio.Task | None = None

    async def execute(self, *, text: str) -> asyncio.Task:
        async def _bg() -> str:
            await self.gate.wait()
            return text.upper()

        self.task = asyncio.create_task(_bg())
        return self.task


class AsyncFailTool(Tool):
    definition = ToolDefinition(
        name="async-fail", description="异步失败", params_schema={})

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate
        self.task: asyncio.Task | None = None

    async def execute(self) -> asyncio.Task:
        async def _bg() -> str:
            await self.gate.wait()
            raise RuntimeError("后台炸了")

        self.task = asyncio.create_task(_bg())
        return self.task


async def _yield(n: int = 3) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def _wait_until(pred, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("等待条件超时")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# T44 / T45：query 的等待语义
# ---------------------------------------------------------------------------


async def test_t44_pending_turns_cleaned(agent, provider):
    script_provider(provider, text_response("ok"))
    result = await agent.query("hi")
    assert result.status == "completed"
    assert agent._pending_turns == {}   # finally 清理


async def test_t45_concurrent_queries_separate_turns(agent, provider):
    first_started = asyncio.Event()
    release = asyncio.Event()

    async def _gated(context, model):
        if not first_started.is_set():
            first_started.set()
            await release.wait()
            return text_response("第一回合")
        return text_response("第二回合")

    provider.generate_fn = _gated
    t1 = asyncio.create_task(agent.query("一"))
    await first_started.wait()
    t2 = asyncio.create_task(agent.query("二"))   # 活跃回合中排队
    await _yield()
    release.set()
    r1 = await asyncio.wait_for(t1, 2)
    r2 = await asyncio.wait_for(t2, 2)
    assert r1 is not r2   # 各自 resolve 到各自回合
    assert r1.final_text == "第一回合"
    assert r2.final_text == "第二回合"


# ---------------------------------------------------------------------------
# T46–T48：入队钩子与队列管理
# ---------------------------------------------------------------------------


async def test_t46_before_enqueue_intercepted(agent):
    after_calls: list = []

    async def _guard(a, msg):
        if "违规" in "".join(getattr(b, "text", "") for b in msg.content):
            raise Intercepted("拒绝")
        return msg

    agent.hooks.before_enqueue(_guard)
    agent.hooks.after_enqueue(lambda a, m: after_calls.append(m.id) or m)
    with pytest.raises(Intercepted):
        await agent.enqueue_message(Message(
            kind=MessageKind.USER, content=[TextBlock(text="违规内容")]))
    assert len(agent._message_queue) == 0   # 队列长度不变
    assert after_calls == []   # after_enqueue 不触发


async def test_t47_cancel_queued(agent, provider):
    first_started = asyncio.Event()
    release = asyncio.Event()

    async def _gated(context, model):
        if not first_started.is_set():
            first_started.set()
            await release.wait()
        return text_response("ok")

    provider.generate_fn = _gated
    t1 = asyncio.create_task(agent.query("一"))
    await first_started.wait()

    t2 = asyncio.create_task(agent.query("二"))   # 活跃回合中排队 + 有等待者
    await _yield()
    queued_id = next(iter(agent._pending_turns))   # t1 已出队弹出，只剩 t2
    assert agent.cancel_queued(queued_id) is True
    r2 = await asyncio.wait_for(t2, 2)
    assert r2.status == "cancelled"   # 等待者联动 resolve cancelled
    assert len(agent._message_queue) == 0

    # 已出队的消息 -> False
    in_flight_id = agent.current_turn.message_ids[0]
    assert agent.cancel_queued(in_flight_id) is False
    release.set()
    r1 = await asyncio.wait_for(t1, 2)
    assert r1.status == "completed"   # 等待者由回合正常 resolve


async def test_t48_set_queued_priority(agent, provider):
    seen_texts: list[str] = []

    async def _rec(context, model):
        # 记录每回合触发消息文本（context 根消息即触发者）
        seen_texts.append(context.messages[-1].content[0].text)
        return text_response("ok")

    provider.generate_fn = _rec
    agent.pause()   # 挂起工作循环，先排队
    m1 = await agent.message("普通-先入")
    m2 = await agent.message("普通-后入")
    assert agent.set_queued_priority(m2, MessagePriority.HIGH) is True
    agent.resume()
    await _wait_until(lambda: len(seen_texts) >= 2)   # 等两回合跑完
    assert seen_texts[:2] == ["普通-后入", "普通-先入"]   # 提升后先消费
    assert agent.set_queued_priority(m1, MessagePriority.LOW) is False   # 已出队


# ---------------------------------------------------------------------------
# T49–T54：provider_gen 双模式与流式 delta
# ---------------------------------------------------------------------------


async def test_t49_stream_default_without_subscribers(agent, provider):
    async def _stream(context, model):
        yield ProviderDelta(kind="text", text="流式", content_index=0)
        yield ProviderDelta(kind="text", text="输出", content_index=1,
                            usage=Usage(input=1, fresh_input=1, output=2,
                                        cache_read=0, cache_write=0,
                                        reasoning=0, total_tokens=3))

    provider.stream_fn = _stream
    result = await agent.query("hi")   # 无 on_provider_delta 订阅者，dispatch 空转
    assert result.status == "completed"
    assert result.final_text == "流式输出"
    assert len(provider.received) == 1


async def test_t50_nonstream_single_full_delta(agent, provider):
    deltas: list[ProviderDelta] = []
    agent.hooks.on_provider_delta(lambda a, d: deltas.append(d) or d)
    script_provider(provider, text_response("整段文本", usage=Usage(
        input=1, fresh_input=1, output=1, cache_read=0, cache_write=0,
        reasoning=0, total_tokens=2)))
    context = agent._assemble_context()
    response = await agent.provider_gen(context, stream=False, by="probe")
    assert len(provider.received) == 1   # 非流式请求（generate 被调）
    assert len(deltas) == 1   # 恰好一条全量 delta
    assert deltas[0].text == "整段文本"
    assert deltas[0].kind == "text" and deltas[0].by == "probe"   # 格式与流式一致
    assert response.by == "probe"   # by 盖写响应


async def test_t51_stream_two_deltas_concat(agent, provider):
    deltas: list[str] = []
    agent.hooks.on_provider_delta(lambda a, d: deltas.append(d.text) or d)

    async def _stream(context, model):
        yield ProviderDelta(kind="text", text="hello ", content_index=0)
        yield ProviderDelta(kind="text", text="world", content_index=0)

    provider.stream_fn = _stream
    result = await agent.query("hi")
    assert deltas == ["hello ", "world"]   # 钩子收两条
    msg = agent._messages[result.turn.message_ids[-1]]
    assert "".join(b.text for b in msg.content) == "hello world"   # 完整拼接


async def test_t52_abort_preflight(agent, provider):
    agent.abort_turn()   # _turn_abort 已置位
    context = agent._assemble_context()
    response = await agent.provider_gen(context)
    assert response.message is None
    assert response.finish is False and response.cancelled is True
    assert len(provider.received) == 0   # 未发起 Provider 调用


async def test_t53_side_pattern_filter(agent, provider):
    side_deltas: list[str] = []
    agent.hooks.on_provider_delta["_side"](lambda a, d: side_deltas.append(d.text) or d)
    script_provider(provider, text_response("主线"), text_response("副线"))
    await agent.query("主线问题")
    assert side_deltas == []   # 主 Turn delta 不触发 _side pattern handler
    text = await agent.side_query("副线问题")
    assert text == "副线"
    assert len(side_deltas) == 1   # side_query 时触发


async def test_t54_stream_abort_partial_kept(agent, provider):
    dispatched: list[str] = []

    async def _watch(a, delta):
        dispatched.append(delta.text)
        a.abort_turn()   # 首条 delta 后中断流式
        return delta

    agent.hooks.on_provider_delta(_watch)

    async def _stream(context, model):
        yield ProviderDelta(kind="text", text="已累积", content_index=0)
        yield ProviderDelta(kind="text", text="不应到达", content_index=0)

    provider.stream_fn = _stream
    result = await agent.query("hi")
    assert result.status == "cancelled"
    assert dispatched == ["已累积"]   # 取消点起不再 dispatch
    msg = agent._messages[result.turn.message_ids[-1]]
    assert msg.kind is MessageKind.PROVIDER and msg.partial is True
    assert "".join(b.text for b in msg.content) == "已累积"   # 已累积内容定型保留
    assert msg.turn_end is True   # 取消关闭（S-14）
    await agent._tree_store.drain()
    assert '"partial": true' in (agent._session_dir / "tree.jsonl").read_text()   # 落盘保留


# ---------------------------------------------------------------------------
# T141–T143：provider_gen 在途取消竞速（cancel 信号取消进行中的 Provider 调用）
# ---------------------------------------------------------------------------


async def test_t141_nonstream_inflight_cancel(agent, provider):
    """T141：非流式 generate 在途（永不返回）→ cancel() 竞速取消：provider_gen
    及时返回 cancelled 响应（不等 HTTP 完成），在途调用被 CancelledError 注入。"""
    started = asyncio.Event()
    cancelled: list[bool] = []

    async def _hang(context, model):
        started.set()
        try:
            await asyncio.Event().wait()   # 永不置位：模拟停滞的在途请求
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    provider.generate_fn = _hang
    context = agent._assemble_context()
    gen = asyncio.create_task(agent.provider_gen(context, stream=False))
    await started.wait()
    await agent.cancel()
    response = await asyncio.wait_for(gen, 2)
    assert response.cancelled is True
    assert response.message is None and response.finish is False
    assert cancelled == [True]   # 在途调用被 cancel（CancelledError 注入 await 点）
    assert agent._executions == {}   # Execution 注册 finally 清理不变量
    agent._turn_abort.clear()   # 复原，避免影响 fixture 收尾


async def test_t142_stream_stalled_inflight_cancel(agent, provider):
    """T142：流式在途停滞（delta 后永不产出）→ cancel() 竞速取消：及时返回
    partial=True + cancelled=True 响应（已累积内容保留），底层流被 aclose。"""
    started = asyncio.Event()
    closed: list[bool] = []

    async def _stream(context, model):
        try:
            yield ProviderDelta(kind="text", text="已累积", content_index=0)
            started.set()
            await asyncio.Event().wait()   # 停滞：不再有 delta 到达
        finally:
            closed.append(True)   # anext 被 cancel / aclose 触发底层清理

    provider.stream_fn = _stream
    context = agent._assemble_context()
    gen = asyncio.create_task(agent.provider_gen(context))   # 默认流式
    await started.wait()
    await agent.cancel()
    response = await asyncio.wait_for(gen, 2)
    assert response.cancelled is True
    assert response.message is not None and response.message.partial is True
    assert "".join(b.text for b in response.message.content) == "已累积"
    assert closed == [True]   # 底层流被关闭（连接不滞留）
    agent._turn_abort.clear()


async def test_t143_side_query_inflight_cancel(agent, provider):
    """T143：side_query 在途取消 → 及时返回空文本（非流式竞速覆盖副线）。"""
    started = asyncio.Event()

    async def _hang(context, model):
        started.set()
        await asyncio.Event().wait()   # 永不置位

    provider.generate_fn = _hang
    side = asyncio.create_task(agent.side_query("副线问题"))
    await started.wait()
    await agent.cancel()
    assert await asyncio.wait_for(side, 2) == ""   # abort 时返回空文本，不抛异常
    agent._turn_abort.clear()


# ---------------------------------------------------------------------------
# T55–T57：side_query 边界
# ---------------------------------------------------------------------------


async def test_t55_side_query_no_commit(agent, provider):
    script_provider(provider, text_response("历史答复"))
    await agent.query("先有历史")
    await agent._tree_store.drain()
    before = (set(agent._messages), agent.current_head_id, agent.last_result,
              (agent._session_dir / "tree.jsonl").read_text())

    script_provider(provider, text_response("副线答复"))
    text = await agent.side_query("总结以上对话")
    assert text == "副线答复"
    await agent._tree_store.drain()
    assert set(agent._messages) == before[0]   # 不挂树
    assert agent.current_head_id == before[1]
    assert agent.last_result == before[2]   # 不写 last_result
    assert (agent._session_dir / "tree.jsonl").read_text() == before[3]   # 不落盘


async def test_t56_side_query_model_tag(runtime, provider, fixtures_dir):
    # 标签解析链：fixtures 的 fast -> deepseek-v4（provider deepseek-personal）
    runtime._model_tags_path = fixtures_dir / "providers" / "model-tags.yaml"
    runtime._models_path = fixtures_dir / "providers" / "models.yaml"
    runtime.add_provider("deepseek-personal", provider)   # fast 解析目标的 provider
    agent = await runtime.create_agent(SimpleAgent)
    seen_models: list[str] = []

    async def _gen(context, model):
        seen_models.append(model.model)
        return text_response("ok")

    provider.generate_fn = _gen
    original_model = agent.model
    text = await agent.side_query("副线", model_tag="fast")
    assert text == "ok"
    assert seen_models == ["deepseek-chat"]   # 本次用 fast 解析的模型
    assert agent.model is original_model   # self.model 不被改写


async def test_t57_side_query_response_consumption(agent, provider):
    from flowing.message import ThinkingBlock

    # [ThinkingBlock, TextBlock("ok")] -> "ok"（thinking 不参与返回值）
    script_provider(provider, text_response("ok"))
    assert await agent.side_query("q1") == "ok"

    # 响应含 ToolCallBlock -> 不执行不续轮
    resp, _ = tool_call_response(("echo", {"text": "x"}), text="带工具")
    script_provider(provider, resp)
    assert await agent.side_query("q2") == "带工具"   # 只拼 TextBlock

    # 仅 ThinkingBlock -> "" 不抛
    thinking_only = text_response("").__class__(
        message=Message(kind=MessageKind.PROVIDER,
                        content=[ThinkingBlock(thinking="想")]),
        finish=True)
    script_provider(provider, thinking_only)
    assert await agent.side_query("q3") == ""


# ---------------------------------------------------------------------------
# T58–T60：fork
# ---------------------------------------------------------------------------


async def test_t58_fork_switches_branch(agent, provider):
    m6 = Message(id="m6", kind=MessageKind.USER, content=[TextBlock(text="根")])
    agent.push(m6)
    m7 = Message(id="m7", kind=MessageKind.PROVIDER, content=[TextBlock(text="支7")])
    m8 = Message(id="m8", kind=MessageKind.PROVIDER, content=[TextBlock(text="支8")])
    agent.chain.branch("m6", m7)
    agent.chain.branch("m6", m8)   # m6 有子消息 m7/m8 两分支

    await agent.fork("m8")
    assert agent.current_head_id == "m8"
    path_ids = [m.id for m in agent._assemble_context().messages]
    assert "m8" in path_ids and "m7" not in path_ids   # 组装路径含 m8 不含 m7

    # 错误路径：目标不在树中 -> ValueError；on_fork 拦截 -> Intercepted 上抛
    with pytest.raises(ValueError):
        await agent.fork("ghost")
    with pytest.raises(ValueError):
        await agent.fork("m7-deleted-never-existed")
    await agent.fork("m7")   # 兄弟分支可切
    assert agent.current_head_id == "m7"

    async def _veto(a, ctx):
        raise Intercepted("不许 fork")

    agent.hooks.on_fork(_veto)
    with pytest.raises(Intercepted):
        await agent.fork("m6")
    assert agent.current_head_id == "m7"   # 被拦截，游标不动


async def test_t59_in_turn_fork_seek(runtime, provider):
    # 先完成第一回合：u1 -> p1
    script_provider(provider, text_response("第一轮"))
    agent = await runtime.create_agent(SimpleAgent)
    r1 = await agent.query("一")
    u1 = r1.turn.message_ids[0]

    # 第二回合：before_provider_gen 钩子里 fork 到 u1（回合内 seek）
    async def _seek(a, context):
        if a.current_turn is not None and len(a.current_turn.message_ids) == 1:
            await a.fork(u1)   # 后续 append 挂 u1 链
        return context

    script_provider(provider, text_response("第二轮"))
    agent.hooks.before_provider_gen(_seek)
    r2 = await agent.query("二")
    msgs = [agent._messages[mid] for mid in r2.turn.message_ids]
    trigger, reply = msgs[0], msgs[-1]
    assert trigger.parent_id == r1.turn.message_ids[-1]   # 批次挂树在旧链
    assert reply.parent_id == u1   # fork 后 append 挂在新基址（跨链）
    assert agent.current_head_id == reply.id
    # 下一轮 context 从 u1 上溯：含 u1/reply，不含第一轮的 p1
    path_ids = [m.id for m in agent._assemble_context().messages]
    assert u1 in path_ids and r1.turn.message_ids[-1] not in path_ids


async def test_t60_fork_dangling_tool_call(agent, provider):
    # 构造「tool_call 已挂树、结果未 append」的 fork 落点
    user = Message(id="u", kind=MessageKind.USER, content=[TextBlock(text="问")])
    agent.push(user)
    call_msg = Message(id="p-call", kind=MessageKind.PROVIDER,
                       content=[ToolCallBlock(id="c-x", name="echo",
                                              args={"text": "x"})])
    agent.push(call_msg)   # head 停在工具执行相位（结果未 append）

    async def _strict(context, model):
        # 模拟 provider 的配对校验：context 中有 tool_call 但无配对结果 -> 显式报错
        answered = {m.tool_call_id for m in context.messages
                    if m.kind is MessageKind.TOOL}
        for m in context.messages:
            for b in m.content:
                if isinstance(b, ToolCallBlock) and b.id not in answered:
                    raise InvalidRequestError("配对断裂：有 tool_call 无结果")
        return text_response("ok")

    provider.generate_fn = _strict
    result = await agent.query("继续")
    assert result.status == "error"   # provider 显式报错走 on_provider_error（无 handler -> error）
    # 旧链数据完好（append-only）
    assert agent._messages["p-call"].content[0].id == "c-x"
    assert agent._messages["u"].content[0].text == "问"


async def test_t144_fork_none_suspends_head(agent, provider):
    """T144：fork(None) = 游标悬置——head 置 None、上下文组装为空路径，
    此后第一条挂树消息以 parent_id=None 开新根（旧树完整保留为森林）；
    on_fork 可双向改写换后 id ↔ None；非 None 目标仍须在树。"""
    script_provider(provider, text_response("ok"))
    await agent.query("hi")
    old_head = agent.current_head_id
    assert old_head is not None

    await agent.fork(None)
    assert agent.current_head_id is None
    assert list(agent.chain.walk(agent.current_head_id)) == []   # 空路径
    assert agent._assemble_context().messages == []
    assert old_head in agent._messages   # 悬置不删除：旧树完整保留

    # 悬置后第一条挂树消息开新根（push 既有行为），与旧链并存为森林
    new_id = agent.push(Message(kind=MessageKind.USER,
                                content=[TextBlock(text="重新开始")]))
    assert agent._messages[new_id].parent_id is None
    assert agent.current_head_id == new_id
    assert [m.id for m in agent._assemble_context().messages] == [new_id]   # 新链不回溯旧链

    # 非 None 目标仍须在树（含已被移除的 id）
    with pytest.raises(ValueError):
        await agent.fork("ghost")

    # on_fork 双向改写换后 id：id → None、None → id（注册序链式生效）
    agent.hooks.on_fork(
        lambda a, c: ForkContext(c.previous_head_id, None), by="to-none")
    await agent.fork(old_head)   # 换后被改写为 None → 悬置
    assert agent.current_head_id is None
    agent.hooks.on_fork(
        lambda a, c: (ForkContext(c.previous_head_id, new_id)
                      if c.target_message_id is None else c), by="to-id")
    await agent.fork(None)   # to-none 保持 None，to-id 改写为 new_id
    assert agent.current_head_id == new_id


# ---------------------------------------------------------------------------
# T61 / T62：pause / resume
# ---------------------------------------------------------------------------


async def test_t61_pause_in_tool_loop(runtime, provider):
    agent = await runtime.create_agent(SimpleAgent)
    agent.runtime.register_tool(EchoTool())
    agent.add_tool("echo")

    step1, _ = tool_call_response(("echo", {"text": "一"}))
    step2, _ = tool_call_response(("echo", {"text": "二"}))
    script_provider(provider, step1, step2, text_response("完成"))

    paused_once = False

    async def _pause_after_first(a, result):
        nonlocal paused_once
        if not paused_once:   # 只在首个工具结果后挂起一次（否则 resume 后再触发）
            paused_once = True
            a.pause()   # 工具段执行后挂起：下一个 provider_gen / tool_call 前检查点生效
        return result

    agent.hooks.after_tool_call(_pause_after_first)
    task = asyncio.create_task(agent.query("开始"))
    await _wait_until(lambda: agent.paused)
    assert len(provider.received) == 1   # 挂在检查点 ②：provider 调用计数冻结
    agent.resume()
    result = await asyncio.wait_for(task, 2)
    assert result.status == "completed"
    tool_msgs = [agent._messages[mid] for mid in result.turn.message_ids
                 if agent._messages[mid].kind is MessageKind.TOOL]
    assert len(tool_msgs) == 2   # 恢复后从该 tool_call 继续，已执行结果不丢
    assert result.final_text == "完成"


async def test_t62_pause_resume_recursive(runtime, provider):
    parent = await runtime.create_agent(SimpleAgent)
    child = await runtime.create_agent(SimpleAgent, parent_id=parent.node_id)
    parent._children[child.node_id] = child
    e = Execution(id="ex", kind="tool", tags=[], started_at=__import__("datetime").datetime.now(),
                  cancel=asyncio.Event(), pause=asyncio.Event())
    child._executions["ex"] = e

    parent.pause_recursive()
    assert parent.paused and child.paused
    assert not e.pause.is_set()   # 不动任何 Execution
    parent.resume_recursive()
    assert not parent.paused and not child.paused


# ---------------------------------------------------------------------------
# T63–T66：cancel 族
# ---------------------------------------------------------------------------


async def test_t63_abort_inside_on_provider_error(agent, provider):
    from flowing.errors import RateLimitedError

    script_provider(provider, RateLimitedError("限流"))
    abort_calls: list = []

    async def _give_up(a, ctx):
        a.abort_turn()   # handler 内决定放弃本回合
        ctx.can_continue = True
        return ctx

    agent.hooks.on_provider_error(_give_up)
    agent.hooks.before_turn_abort(lambda a, t: abort_calls.append(1) or t)
    result = await agent.query("hi")
    # continue 后下一次 provider_gen 开头检测信号返回 cancelled，abort 收口
    assert result.status == "cancelled"
    assert abort_calls == [1]   # before_turn_abort 恰好一次


async def test_t64_cancel_sets_all_signals(agent):
    e_tool = Execution(id="t", kind="tool", tags=[], started_at=__import__("datetime").datetime.now(),
                       cancel=asyncio.Event(), pause=asyncio.Event())
    e_agent = Execution(id="a", kind="agent", tags=[], started_at=__import__("datetime").datetime.now(),
                        cancel=asyncio.Event(), pause=asyncio.Event())
    agent._executions.update({"t": e_tool, "a": e_agent})
    await agent.cancel()
    assert e_tool.cancel.is_set() and e_agent.cancel.is_set()
    assert agent._turn_abort.is_set()


async def test_t65_before_cancel_intercepted(agent):
    async def _veto(a, ctx):
        raise Intercepted("关键事务进行中")

    agent.hooks.before_cancel(_veto)
    e = Execution(id="t", kind="tool", tags=[], started_at=__import__("datetime").datetime.now(),
                  cancel=asyncio.Event(), pause=asyncio.Event())
    agent._executions["t"] = e
    with pytest.raises(Intercepted):
        await agent.cancel()
    assert not e.cancel.is_set()
    assert not agent._turn_abort.is_set()   # 所有信号未置位


async def test_t66_cancel_idle_fires_after_cancel(agent):
    calls: list = []
    agent.hooks.after_cancel(lambda a, ctx: calls.append(1) or ctx)
    await agent.cancel()   # 空闲 Agent：信号置位是事实，after_cancel 照常
    assert calls == [1]
    assert agent._turn_abort.is_set()
    agent._turn_abort.clear()   # 复原，避免影响 fixture 收尾


# ---------------------------------------------------------------------------
# T67–T69：检查点 ②.5 urgent 吸收
# ---------------------------------------------------------------------------


async def test_t67_steer_absorbed_same_turn(agent, provider):
    gate = asyncio.Event()
    wait_tool = WaitTool(gate)
    agent.runtime.register_tool(wait_tool)
    agent.add_tool("wait")
    step1, _ = tool_call_response(("wait", {}))

    async def _check_context(context, model):
        return text_response("完成")

    script_provider(provider, step1, _check_context)
    task = asyncio.create_task(agent.query("开始"))
    await _yield(6)   # 第一轮回合进入工具执行（挂在 gate 上）
    await agent.steer("预算上限改为 500")   # 回合进行中注入
    await _yield(2)
    gate.set()
    result = await asyncio.wait_for(task, 2)
    assert result.status == "completed"
    assert not result.turn.aborted   # STEER 吸收不 abort
    # 当轮 context 可见：第二轮 provider_gen 收到的消息含该 steer
    second_ctx = provider.received[1]
    assert any("预算上限" in getattr(b, "text", "")
               for m in second_ctx.messages for b in m.content)
    # steer 消息已挂树
    assert any("预算上限" in getattr(b, "text", "")
               for m in agent._messages.values() for b in m.content)


async def test_t68_interrupt_absorbed_and_aborted(agent, provider):
    gate = asyncio.Event()
    wait_tool = WaitTool(gate)
    agent.runtime.register_tool(wait_tool)
    agent.add_tool("wait")
    step1, _ = tool_call_response(("wait", {}))
    script_provider(provider, step1, text_response("不应到达"))
    abort_calls: list = []
    agent.hooks.before_turn_abort(lambda a, t: abort_calls.append(1) or t)

    t1 = asyncio.create_task(agent.query("开始"))
    await _yield(6)   # 回合进入工具执行
    t2 = asyncio.create_task(agent.query("打断", priority=MessagePriority.INTERRUPT))
    await _yield(2)
    gate.set()
    r1, r2 = await asyncio.wait_for(asyncio.gather(t1, t2), 2)
    assert r1 is r2   # 吸收消息的等待者并入本回合，共享同一 TurnResult
    assert r1.status == "cancelled"   # abort 收口
    assert abort_calls == [1]   # before_turn_abort 恰好一次
    # INTERRUPT 消息已挂树
    kinds = [agent._messages[mid].kind for mid in r1.turn.message_ids]
    assert kinds.count(MessageKind.USER) == 2


async def test_t69_gates_division(agent, provider):
    before_turn_batches: list[int] = []

    async def _record(a, turn):
        before_turn_batches.append(len(turn.pending_messages))
        return turn

    async def _reject(a, msg):
        if "违规" in getattr(msg.content[0], "text", ""):
            raise Intercepted("入队拦截")
        return msg

    agent.hooks.before_turn(_record)
    agent.hooks.before_enqueue(_reject)

    # ②.5 吸收的消息不经过 before_turn；before_enqueue 在入队时已拦截
    with pytest.raises(Intercepted):
        await agent.steer("违规 steer")   # 入队闸门拦截，永远到不了 ②.5
    assert len(agent._message_queue) == 0

    gate = asyncio.Event()
    wait_tool = WaitTool(gate)
    agent.runtime.register_tool(wait_tool)
    agent.add_tool("wait")
    step1, _ = tool_call_response(("wait", {}))
    script_provider(provider, step1, text_response("完成"))
    task = asyncio.create_task(agent.query("开始"))
    await _yield(6)
    await agent.steer("合法 steer")
    gate.set()
    result = await asyncio.wait_for(task, 2)
    assert result.status == "completed"
    # before_turn 只见出队批次（触发消息一条）；steer 经 ②.5 吸收不进 before_turn
    assert before_turn_batches == [1]
    # 挂树：触发 user + provider(工具调用) + tool 结果 + steer + provider(收尾)
    assert len(result.turn.message_ids) == 5


# ---------------------------------------------------------------------------
# T70–T72：并行工具批 / finish 置位转移 / turn_end 写入
# ---------------------------------------------------------------------------


async def test_t70_parallel_tool_batch(runtime, provider):
    events: list[str] = []

    class SlowTool(Tool):
        definition = ToolDefinition(
            name="slow", description="慢工具",
            params_schema={"n": {"type": "integer"}})

        async def execute(self, *, n: int) -> str:
            events.append(f"start-{n}")
            await asyncio.sleep(0.05)
            events.append(f"end-{n}")
            return f"r{n}"

    runtime.register_tool(SlowTool())
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("slow")
    step1, calls = tool_call_response(("slow", {"n": 1}), ("slow", {"n": 2}))
    script_provider(provider, step1, text_response("完成"))
    result = await agent.query("开始")
    assert result.status == "completed"
    # asyncio.gather 并行：两个 start 都在任一 end 之前
    assert events[:2] == ["start-1", "start-2"]
    # 结果按响应原始顺序挂树
    tool_msgs = [agent._messages[mid] for mid in result.turn.message_ids
                 if agent._messages[mid].kind is MessageKind.TOOL]
    assert [m.tool_call_id for m in tool_msgs] == [c.id for c in calls]


async def test_t71_finish_output_shift(runtime, provider):
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("finish")   # builtin::finish（register_builtins 占位注册）
    step1, calls = tool_call_response(("finish", {"summary": "做完了"}))
    script_provider(provider, step1)   # 工具置位后视同 finish，不再调 provider
    result = await agent.query("交卷")
    assert result.status == "completed"
    assert agent.last_result == {"summary": "做完了"}   # finish_output dict
    # 首个触发置位的 TOOL 消息标 turn_end=True
    tool_msg = next(agent._messages[mid] for mid in result.turn.message_ids
                    if agent._messages[mid].kind is MessageKind.TOOL)
    assert tool_msg.tool_call_id == calls[0].id
    assert tool_msg.turn_end is True
    provider_msg = agent._messages[result.turn.message_ids[1]]
    assert provider_msg.turn_end is False   # PROVIDER 消息不追溯改写
    assert len(provider.received) == 1   # 工具段执行完即收尾，无第二轮 provider_gen


async def test_t72_turn_end_written_by_agent_layer(runtime, provider):
    runtime.register_tool(EchoTool())
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("echo")
    step1, _ = tool_call_response(("echo", {"text": "x"}))
    script_provider(provider, step1, text_response("完成"))
    result = await agent.query("开始")
    kinds = [agent._messages[mid] for mid in result.turn.message_ids]
    provider_msgs = [m for m in kinds if m.kind is MessageKind.PROVIDER]
    # turn_end == (finish or cancelled)：工具调用响应 False，收尾响应 True
    assert provider_msgs[0].turn_end is False
    assert provider_msgs[1].turn_end is True


# ---------------------------------------------------------------------------
# T73：空 turn 边缘（abort 于首次 provider_gen 前）
# ---------------------------------------------------------------------------


async def test_t73_empty_turn(runtime, provider):
    agent = await runtime.create_agent(SimpleAgent)

    async def _clear_and_abort(a, turn):
        turn.pending_messages.clear()   # 清空 = 空 turn（不显式挂树）
        a.abort_turn()
        return turn

    agent.hooks.before_turn(_clear_and_abort)
    result = await agent.query("hi")
    assert result.status == "cancelled"
    assert result.final_text == "" and result.token_usage is None
    assert len(agent._messages) == 0   # 不产生新树节点
    assert agent.current_head_id is None   # head 不变
    assert len(provider.received) == 0


# ---------------------------------------------------------------------------
# T74–T77：tool_call 钩子链与 _normalize
# ---------------------------------------------------------------------------


async def test_t74_before_tool_call_rewrite(runtime, provider):
    runtime.register_tool(EchoTool())
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("echo")

    async def _rewrite(a, tc):
        tc.args["text"] = "改写后"
        return tc

    agent.hooks.before_tool_call(_rewrite)
    result = await agent.tool_call(ToolCall(id="c1", name="echo",
                                            args={"text": "原始"}))
    assert result.status == "completed"
    assert result.output == "改写后"   # execute 收到改写后参数


async def test_t74b_schema_default_fill(runtime, provider):
    """_normalize 第 3 步：schema 默认值填充（前两步未给的参数按 default 补齐）。"""

    class DefaultTool(Tool):
        definition = ToolDefinition(
            name="greeter", description="",
            params_schema={"name": {"type": "string"},
                           "lang": {"type": "string", "default": "zh"}})

        async def execute(self, *, name: str, lang: str) -> str:
            return f"{lang}:{name}"

    runtime.register_tool(DefaultTool())
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("greeter")
    result = await agent.tool_call(ToolCall(id="c1", name="greeter",
                                            args={"name": "甲"}))
    assert result.status == "completed"
    assert result.output == "zh:甲"   # lang 未传 -> default 补齐


async def test_t75_before_tool_call_intercepted(runtime, provider):
    executed: list = []

    class SpyTool(Tool):
        definition = ToolDefinition(name="spy", description="", params_schema={})

        async def execute(self):
            executed.append(1)
            return "x"

    runtime.register_tool(SpyTool())
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("spy")
    after_calls: list = []
    agent.hooks.after_tool_call(lambda a, r: after_calls.append(1) or r)

    async def _veto(a, tc):
        raise Intercepted("审批拒绝")

    agent.hooks.before_tool_call(_veto)
    result = await agent.tool_call(ToolCall(id="c1", name="spy", args={}))
    assert result.status == "blocked"
    assert executed == []   # 工具本体不执行
    assert after_calls == []   # after_tool_call 不触发
    # as_message 塑形为 [TextBlock(reason)]
    msg = result.as_message("c1")
    assert msg.kind is MessageKind.TOOL and msg.tool_status == "blocked"
    assert isinstance(msg.content[0], TextBlock)
    assert "审批拒绝" in msg.content[0].text


async def test_t76_shortcut_and_after_intercepted(runtime, provider):
    runtime.register_tool(EchoTool())
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("echo")
    executed_after: list = []
    agent.hooks.after_tool_call(lambda a, r: executed_after.append(r.status) or r)

    async def _cache(a, tc):
        tc.shortcut = ToolResult(status="completed", output="缓存命中")
        return tc

    agent.hooks.before_tool_call(_cache)
    result = await agent.tool_call(ToolCall(id="c1", name="echo",
                                            args={"text": "不执行"}))
    assert result.status == "completed" and result.output == "缓存命中"
    assert executed_after == ["completed"]   # after_tool_call 照常触发

    # after_tool_call 内 Intercepted -> 伪造 blocked 返回不传播
    async def _veto_after(a, r):
        raise Intercepted("结果违规")

    agent2 = await runtime.create_agent(SimpleAgent)
    agent2.add_tool("echo")
    agent2.hooks.after_tool_call(_veto_after)
    result2 = await agent2.tool_call(ToolCall(id="c2", name="echo",
                                              args={"text": "x"}))
    assert result2.status == "blocked"
    assert "结果违规" in (result2.output or "")


async def test_t77_hallucinated_param(runtime, provider):
    runtime.register_tool(EchoTool())
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("echo")
    after_seen: list = []
    agent.hooks.after_tool_call(lambda a, r: after_seen.append(r.status) or r)

    step1, _ = tool_call_response(("echo", {"text": "x", "ghost": 1}))
    script_provider(provider, step1, text_response("知错就改"))
    result = await agent.query("开始")
    tool_msg = next(agent._messages[mid] for mid in result.turn.message_ids
                    if agent._messages[mid].kind is MessageKind.TOOL)
    assert tool_msg.tool_status == "error"   # LLM 视角校验失败是正常产物、进消息树
    err_text = "".join(getattr(b, "text", "") for b in tool_msg.content)
    assert "ghost" in err_text   # 错误文本以 LLM 命名空间（幻觉参数名）
    assert after_seen == ["error"]   # after_tool_call 照常触发
    assert result.status == "completed"   # 回合不因此异常终止


async def test_t77b_strict_false_tool_receives_undeclared_params(runtime, provider):
    """T77 补充回归：strict=False 工具的 LLM 多传参数放行透传到 execute
    （ToolDefinition「False 时工具层不限制参数」契约；strict=True 的拒绝
    语义由 T77 覆盖，不回归）。"""
    runtime.register_tool(OpenTool())
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("open")
    result = await agent.tool_call(ToolCall(
        id="c1", name="open", args={"text": "x", "max_rounds": 2}))
    assert result.status == "completed"
    assert result.output == {"text": "x", "extra": {"max_rounds": 2}}


# ---------------------------------------------------------------------------
# T78 / T79：异步工具透明化与 abort 于工具循环
# ---------------------------------------------------------------------------


async def test_t78_async_tool_pending_and_event(runtime, provider):
    gate = asyncio.Event()
    async_tool = AsyncResultTool(gate)
    fail_tool = AsyncFailTool(gate)
    runtime.register_tool(async_tool)
    runtime.register_tool(fail_tool)
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("async-echo")
    agent.add_tool("async-fail")

    step1, calls = tool_call_response(("async-echo", {"text": "ab"}),
                                      ("async-fail", {}))
    # 两条 EVENT 各触发一个后续回合（queue 消费），兜底响应防脚本耗尽
    script_provider(provider, step1, text_response("收尾"),
                    text_response("事件回合一"), text_response("事件回合二"))
    result = await agent.query("开始")
    assert result.status == "completed"
    tool_msgs = [agent._messages[mid] for mid in result.turn.message_ids
                 if agent._messages[mid].kind is MessageKind.TOOL]
    assert [m.tool_status for m in tool_msgs] == ["pending", "pending"]   # 收据配对封闭
    # B10：pending 收据附加「后台任务 ID」块（Task 路径 output 仍 None——
    # 内容只有附加块，无结果块）
    assert [len(m.content) for m in tool_msgs] == [1, 1]
    assert all(isinstance(b, TextBlock) and b.text.startswith("background task ID: ")
               for m in tool_msgs for b in m.content)

    # Task 完成后 EVENT 消息入队（标注块 + 结果块 / 标注块 + 错误文本块），
    # 由工作循环消费挂树——轮询等待两条 EVENT 出现（消费通道即时发生）
    gate.set()
    await asyncio.wait_for(asyncio.shield(async_tool.task), 2)
    try:
        await asyncio.wait_for(asyncio.shield(fail_tool.task), 2)
    except RuntimeError:
        pass   # 后台任务按设计失败
    deadline = asyncio.get_running_loop().time() + 2
    while True:
        events = sorted(
            (m for m in agent._messages.values() if m.kind is MessageKind.EVENT),
            key=lambda m: m.timestamp)
        if len(events) >= 2:
            break
        assert asyncio.get_running_loop().time() < deadline, "EVENT 消息未按时挂树"
        await asyncio.sleep(0.01)
    ok_evt, fail_evt = events
    assert ok_evt.source == "tool_result"
    assert ok_evt.priority == MessagePriority.STEER
    assert isinstance(ok_evt.content[0], TextBlock)   # 标注块
    assert len(ok_evt.content) == 2   # 标注块 + 结果块
    assert "AB" in ok_evt.content[1].text   # 结果块（"ab" 大写）
    assert "后台炸了" in fail_evt.content[1].text   # 错误文本块（与同步 error 同语义）


async def test_t79_abort_skips_remaining_tools(runtime, provider):
    executed: list[str] = []

    class AbortingTool(Tool):
        definition = ToolDefinition(name="aborter", description="", params_schema={})

        async def execute(self, *, caller=None) -> str:
            executed.append("aborter")
            caller.abort_turn()   # 执行中置位退出信号
            return "部分结果"   # 协作式：返回部分结果

    class NeverTool(Tool):
        definition = ToolDefinition(name="never", description="", params_schema={})

        async def execute(self) -> str:
            executed.append("never")
            return "x"

    runtime.register_tool(AbortingTool())
    runtime.register_tool(NeverTool())
    agent = await runtime.create_agent(SimpleAgent)
    agent.add_tool("aborter")
    agent.add_tool("never")
    step1, _ = tool_call_response(("aborter", {}))
    # 下一轮的 never 工具调用永远不会发生：abort 在 ② 检查点收口
    step2, _ = tool_call_response(("never", {}))
    script_provider(provider, step1, step2, text_response("不应到达"))
    result = await agent.query("开始")
    assert result.status == "cancelled"
    assert executed == ["aborter"]   # 剩余工具被跳过不 execute
    tool_msg = next(agent._messages[mid] for mid in result.turn.message_ids
                    if agent._messages[mid].kind is MessageKind.TOOL)
    assert tool_msg.content[0].text == "部分结果"   # 部分结果正常挂树


# ---------------------------------------------------------------------------
# T80–T82：estimate_context_tokens
# ---------------------------------------------------------------------------


def _mk_usage(total: int) -> Usage:
    return Usage(input=total, fresh_input=total, output=0, cache_read=0,
                 cache_write=0, reasoning=0, total_tokens=total)


async def test_t80_anchor_measured_plus_tail(agent):
    from flowing.message import estimate_message_tokens

    agent.push(Message(kind=MessageKind.USER, content=[TextBlock(text="问")]))
    anchor = Message(kind=MessageKind.PROVIDER,
                     content=[TextBlock(text="答")], usage=_mk_usage(5000))
    agent.push(anchor)
    t1 = Message(kind=MessageKind.TOOL, tool_call_id="c1", tool_status="completed",
                 content=[TextBlock(text="结果一")])
    t2 = Message(kind=MessageKind.TOOL, tool_call_id="c2", tool_status="completed",
                 content=[TextBlock(text="结果二")])
    agent.push(t1)
    agent.push(t2)
    est = agent.estimate_context_tokens()
    assert est.measured == 5000
    assert est.anchor_message_id == anchor.id
    assert est.tokens == 5000 + estimate_message_tokens(t1) + estimate_message_tokens(t2)
    assert est.estimated == estimate_message_tokens(t1) + estimate_message_tokens(t2)
    assert est.context_window == 100000   # harness 模型声明


async def test_t81_fresh_session_all_estimated(runtime, provider):
    agent = await runtime.create_agent(SimpleAgent)
    est = agent.estimate_context_tokens()
    assert est.measured is None and est.anchor_message_id is None
    plain = est.estimated
    assert plain > 0   # 含 system prompt（prompt_blocks[0] 惰性引用块）

    agent.add_tool("finish")
    with_tool = agent.estimate_context_tokens()
    assert with_tool.estimated > plain   # 新增启用工具的 schema 计入估算


async def test_t82_anchor_migration_on_remove(agent):
    agent.push(Message(kind=MessageKind.USER, content=[TextBlock(text="问")]))
    p1 = Message(kind=MessageKind.PROVIDER, content=[TextBlock(text="答1")],
                 usage=_mk_usage(5000))
    agent.push(p1)
    p2 = Message(kind=MessageKind.PROVIDER, content=[TextBlock(text="答2")],
                 usage=_mk_usage(3000))
    agent.push(p2)
    p3 = Message(kind=MessageKind.PROVIDER, content=[TextBlock(text="答3")],
                 usage=_mk_usage(0))   # total_tokens==0 不算有效锚点
    agent.push(p3)
    assert agent.estimate_context_tokens().anchor_message_id == p2.id

    agent.remove(p3.id)   # 删 head：回退到 p2
    assert agent.estimate_context_tokens().anchor_message_id == p2.id
    agent.remove(p2.id)   # 锚点被 chain.remove -> 落到次近有效 PROVIDER
    est = agent.estimate_context_tokens()
    assert est.anchor_message_id == p1.id
    assert est.measured == 5000   # 无陈旧值


# ---------------------------------------------------------------------------
# T83：树手术的 head 语义
# ---------------------------------------------------------------------------


async def test_t83_head_maintenance(runtime, provider):
    agent = await runtime.create_agent(SimpleAgent)
    m1 = Message(id="m1", kind=MessageKind.USER, content=[TextBlock(text="1")])
    m2 = Message(id="m2", kind=MessageKind.USER, content=[TextBlock(text="2")])
    m3 = Message(id="m3", kind=MessageKind.USER, content=[TextBlock(text="3")],
                 tags=["tmp"])
    agent.push(m1)
    agent.push(m2)
    agent.push(m3)

    agent.remove("m2")   # 删非 head：m3 是 m2 直接子 → 自动重挂到 m1，head 不动
    assert agent.current_head_id == "m3"
    assert agent._messages["m3"].parent_id == "m1"   # 0904：链连续、无孤儿
    agent.remove("m3")   # 删 head：head 回退到其 parent（m3.parent 已是 m1）
    assert agent.current_head_id == "m1"

    # 连续 pop() 删到根后 head 为 None（干净链上逐条删）
    agent2 = await runtime.create_agent(SimpleAgent)
    for i in ("a", "b", "c"):
        agent2.push(Message(id=i, kind=MessageKind.USER,
                            content=[TextBlock(text=i)]))
    while agent2.current_head_id is not None:
        agent2.pop()
    assert agent2.current_head_id is None
    assert len(agent2._messages) == 0

    # remove_by_tags 删到 head：head 回退其删除前亲节点
    a = Message(id="a", kind=MessageKind.USER, content=[TextBlock(text="a")])
    b = Message(id="b", kind=MessageKind.USER, content=[TextBlock(text="b")],
                tags=["reminder"])
    agent2.push(a)
    agent2.push(b)
    removed = agent2.remove_by_tags({"reminder"})
    assert removed == 1
    assert agent2.current_head_id == "a"


# ---------------------------------------------------------------------------
# T84：钩子抛异常不楔死 agent
# ---------------------------------------------------------------------------


async def test_t84_hook_exception_not_wedged(agent, provider, caplog):
    async def _bad_after_turn(a, turn):
        raise ValueError("收尾钩子炸了")

    agent.hooks.after_turn(_bad_after_turn, by="bad")
    script_provider(provider, text_response("一"), text_response("二"))
    await agent.message("第一条")   # fire-and-forget：after_turn 抛错由工作循环兜底记日志
    await _yield(8)
    assert agent.current_turn is None   # 释放回合身份牌先于一切钩子
    agent.hooks.after_turn.remove_by_owner("bad")   # 移除坏钩子（异常处理属应用层）
    result = await agent.query("第二条")   # 队列后续消息照常消费
    assert result.status == "completed"
    assert result.final_text == "二"


# ---------------------------------------------------------------------------
# 补充：_dequeue 重试循环（R-13 落地语义）与 enqueue_messages 批量入队
# ---------------------------------------------------------------------------


async def test_dequeue_hook_discard_retries(agent, provider):
    """before_dequeue 窗口内扔掉消息 -> dequeue_nowait 得 None -> 重等重发；
    每条真正出队的消息之前恰好一次 before 派发。"""
    before_calls: list[int] = []
    dropped = {"done": False}

    async def _drop_first(a, _value=None):   # 无 value 钩子点同样收 (agent, value)
        before_calls.append(len(agent._message_queue))
        if not dropped["done"]:
            dropped["done"] = True
            victim = agent._message_queue.peek()
            agent.cancel_queued(victim.id)   # 钩子在窗口内扔掉消息（合法出口）
        return None

    agent.hooks.before_dequeue(_drop_first)
    script_provider(provider, text_response("ok"))
    mid1 = await agent.message("会被扔掉")
    await _yield(4)
    assert dropped["done"]
    result = await agent.query("正常消费")
    assert result.status == "completed"
    # before 恰好对「真正出队的消息」各派发一次（被扔消息那次不计入消费）
    assert before_calls[0] >= 1


async def test_enqueue_messages_batch(agent, provider):
    m1 = Message(kind=MessageKind.USER, content=[TextBlock(text="一")])
    m2 = Message(kind=MessageKind.USER, content=[TextBlock(text="二")])
    ids = await agent.enqueue_messages([m1, m2])
    assert ids == [m1.id, m2.id]   # 返回顺序与输入一致
    assert len(agent._message_queue) == 2

    # 单条形态与 Intercepted 不回滚
    async def _veto(a, msg):
        if "拒" in getattr(msg.content[0], "text", ""):
            raise Intercepted("拒")
        return msg

    agent.hooks.before_enqueue(_veto)
    with pytest.raises(Intercepted):
        await agent.enqueue_messages([
            Message(kind=MessageKind.USER, content=[TextBlock(text="三")]),
            Message(kind=MessageKind.USER, content=[TextBlock(text="拒")]),
        ])
    assert len(agent._message_queue) == 3   # 已入队的不回滚


# ---------------------------------------------------------------------------
# T07（审查 Finding 2 补落点）：流式基类回退与非流式等价
# ---------------------------------------------------------------------------


async def test_t07_stream_fallback_matches_nonstream(agent, provider):
    """未覆写 generate_stream 的 adapter（FakeProvider 只注入 generate_fn）
    走流式 provider_gen：on_provider_delta 恰好收到一条完整文本 delta，
    最终响应与非流式等价。"""
    usage = Usage(input=3, fresh_input=3, output=2, cache_read=0,
                  cache_write=0, reasoning=0, total_tokens=5)
    deltas: list[ProviderDelta] = []
    agent.hooks.on_provider_delta(lambda a, d: deltas.append(d) or d)

    script_provider(provider, text_response("等价文本", usage=usage))
    stream_resp = await agent.provider_gen(agent._assemble_context())   # 默认 stream=True，基类回退
    assert len(deltas) == 1   # 恰好一条全量 delta
    assert deltas[0].kind == "text" and deltas[0].text == "等价文本"

    deltas.clear()
    script_provider(provider, text_response("等价文本", usage=usage))
    nonstream_resp = await agent.provider_gen(agent._assemble_context(),
                                              stream=False)
    assert len(deltas) == 1 and deltas[0].text == "等价文本"

    # 最终响应与非流式等价：文本 / finish / usage 一致
    s_text = "".join(b.text for b in stream_resp.message.content
                     if isinstance(b, TextBlock))
    n_text = "".join(b.text for b in nonstream_resp.message.content
                     if isinstance(b, TextBlock))
    assert s_text == n_text == "等价文本"
    assert stream_resp.finish is True and nonstream_resp.finish is True
    assert (stream_resp.message.usage.total_tokens
            == nonstream_resp.message.usage.total_tokens == 5)


# ---------------------------------------------------------------------------
# 默认出队批次：队首 INTERRUPT/STEER 连续段 + 首条非紧急消息
# ---------------------------------------------------------------------------


async def test_dequeue_batch_urgent_prefix_merges(agent, provider):
    """[steer, steer, user1, user2] → 第一回合消费前三条（紧急段 + 首条
    非紧急），第二回合消费 user2（非紧急不继续合并）。"""
    batches: list[list[str]] = []

    async def _record(a, turn):
        batches.append([getattr(m.content[0], "text", "")
                        for m in turn.pending_messages])
        return turn

    agent.hooks.before_turn(_record)
    # 出队闸门：before_dequeue handler 挂住工作循环的出队动作，直到四条
    # 消息全部入队（pause 不能拦住已 parked 在 wait_not_empty 的出队，
    # 批次形成的确定性用闸门保证）
    gate = asyncio.Event()

    async def _hold(a, _value=None):   # 无 value 钩子点同样收 (agent, value)
        await gate.wait()

    agent.hooks.before_dequeue(_hold)
    script_provider(provider, text_response("r1"), text_response("r2"))
    await agent.steer("导向一")
    await agent.steer("导向二")
    q1 = asyncio.create_task(agent.query("问题一"))
    q2 = asyncio.create_task(agent.query("问题二"))
    for _ in range(100):   # 等两个 query 完成打包入队
        if len(agent._message_queue) == 4:
            break
        await asyncio.sleep(0)
    assert len(agent._message_queue) == 4
    gate.set()
    r1, r2 = await asyncio.gather(q1, q2)
    assert batches[0] == ["导向一", "导向二", "问题一"]   # 紧急段 + 首条非紧急
    assert batches[1] == ["问题二"]                        # 非紧急不继续合并
    assert r1.status == r2.status == "completed"
    # steer 与触发消息同批挂树：首个回合 context 已可见
    first_ctx = provider.received[0]
    assert any("导向一" in getattr(b, "text", "")
               for m in first_ctx.messages for b in m.content)


async def test_dequeue_batch_urgent_only(agent, provider):
    """队中全是 STEER：整段为一个批次——单回合、provider 只调一次、
    两条 steer 均挂树。"""
    batches: list[int] = []

    async def _record(a, turn):
        batches.append(len(turn.pending_messages))
        return turn

    agent.hooks.before_turn(_record)
    gate = asyncio.Event()   # 出队闸门（同 test_dequeue_batch_urgent_prefix_merges）

    async def _hold(a, _value=None):   # 无 value 钩子点同样收 (agent, value)
        await gate.wait()

    agent.hooks.before_dequeue(_hold)
    script_provider(provider, text_response("ok"))
    await agent.steer("导向一")
    await agent.steer("导向二")
    for _ in range(100):
        if len(agent._message_queue) == 2:
            break
        await asyncio.sleep(0)
    assert len(agent._message_queue) == 2
    gate.set()
    await _yield(10)  # 等回合跑完
    assert batches == [2]                 # 两条 STEER 并入同一回合批次
    assert len(provider.received) == 1    # 只开一次 LLM 调用
    steer_texts = [getattr(b, "text", "")
                   for m in agent._messages.values() for b in m.content]
    assert "导向一" in steer_texts and "导向二" in steer_texts


async def test_dequeue_batch_single_when_no_urgent(agent, provider):
    """无紧急前缀时批次为单条：两条普通 USER 排队 → 两个独立回合。"""
    batches: list[list[str]] = []

    async def _record(a, turn):
        batches.append([getattr(m.content[0], "text", "")
                        for m in turn.pending_messages])
        return turn

    agent.hooks.before_turn(_record)
    script_provider(provider, text_response("r1"), text_response("r2"))
    agent.pause()
    q1 = asyncio.create_task(agent.query("问题一"))
    q2 = asyncio.create_task(agent.query("问题二"))
    for _ in range(100):   # 等两个 query 完成打包入队（工作循环仍暂停）
        if len(agent._message_queue) == 2:
            break
        await asyncio.sleep(0)
    assert len(agent._message_queue) == 2
    agent.resume()
    r1, r2 = await asyncio.gather(q1, q2)
    assert batches == [["问题一"], ["问题二"]]
    assert r1.status == r2.status == "completed"
