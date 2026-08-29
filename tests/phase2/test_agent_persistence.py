"""阶段 2：持久化接线与恢复测试（T85–T87、T89、T92，及 T91 实质覆盖）。

说明：T88 / T90 / T91 形式上属 recover_agent 管线（runtime 批次推迟），但
_restore 本体的行为（写闸门解锁、压缩时点①、撕裂末行/版本迁移重放由
persistence 层承担）在本文件用「直接构造 Agent + 预写 fixtures / tmp 语料」
驱动，尽量覆盖实质语义。

另含 W27 子 Agent 唤起（invoke_subagent 经 harness 迷你 create 管线端到端）
与 S-34 写闸门护栏的覆盖。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from flowing.message import Message, MessageKind, TextBlock, ToolCallBlock

from conftest import SimpleAgent, script_provider, text_response


def _push_text(agent, mid: str, text: str) -> None:
    agent.push(Message(id=mid, kind=MessageKind.USER,
                       content=[TextBlock(text=text)]))


async def _restored(runtime, session_dir: Path, *, unlock_gate: bool = False):
    """在同一 session 目录上新建实例并直接驱动 _restore（recover 管线替身）。"""
    agent = await runtime.create_agent(SimpleAgent, session_dir=session_dir,
                                       unlock_gate=unlock_gate)
    await agent._restore()
    return agent


# ---------------------------------------------------------------------------
# T85 / T86：变更记录行落盘与重放一致
# ---------------------------------------------------------------------------


async def test_t85_tombstone_line_and_replay(runtime, provider, tmp_path):
    agent = await runtime.create_agent(SimpleAgent)
    _push_text(agent, "m1", "一")
    _push_text(agent, "m2", "二")
    _push_text(agent, "m3", "三")
    agent.chain.remove("m2")
    await agent._tree_store.drain()
    text = (agent._session_dir / "tree.jsonl").read_text()
    assert json.loads(text.strip().splitlines()[-1]) == {"type": "tombstone", "id": "m2"}

    session_dir = agent._session_dir
    await agent.destroy()
    restored = await _restored(runtime, session_dir)
    assert "m2" not in restored._messages   # 重放后权威链不含 m2
    assert set(restored._messages) == {"m1", "m3"}
    assert restored.current_head_id == "m3"


async def test_t86_move_line_and_replay(runtime, provider):
    agent = await runtime.create_agent(SimpleAgent)
    _push_text(agent, "m1", "一")
    _push_text(agent, "m2", "二")
    agent.chain.branch("m2", Message(id="m4", kind=MessageKind.USER,
                                     content=[TextBlock(text="支")]))
    agent.chain.reparent("m4", to="m1")
    await agent._tree_store.drain()
    text = (agent._session_dir / "tree.jsonl").read_text()
    assert json.loads(text.strip().splitlines()[-1]) == {
        "type": "move", "id": "m4", "parent_id": "m1"}

    session_dir = agent._session_dir
    await agent.destroy()
    restored = await _restored(runtime, session_dir)
    assert restored._messages["m4"].parent_id == "m1"


# ---------------------------------------------------------------------------
# T87：poison 传染——同步重抛首次落盘异常
# ---------------------------------------------------------------------------


async def test_t87_poison_reraise(runtime, provider):
    agent = await runtime.create_agent(SimpleAgent)
    boom = OSError("磁盘写失败")
    agent._tree_store._write_record = lambda record: (_ for _ in ()).throw(boom)
    _push_text(agent, "m1", "一")   # submit 入队（尚未落盘）
    await agent._tree_store.drain()   # drain 中写盘异常 -> poison（barrier 被唤醒放行）
    with pytest.raises(OSError) as exc_drain:
        await agent._tree_store.drain()   # 任务已终结且 poison：drain 同步重抛
    assert exc_drain.value is boom
    # poison 态：变更行与消息行提交同步重抛同一异常
    with pytest.raises(OSError) as exc_info:
        agent.chain.update("m1", [TextBlock(text="改")])
    assert exc_info.value is boom
    with pytest.raises(OSError) as exc_info2:
        agent.push(Message(id="m2", kind=MessageKind.USER,
                           content=[TextBlock(text="二")]))
    assert exc_info2.value is boom   # _persist_message 同律


# ---------------------------------------------------------------------------
# T89：孤立 tool_call 的占位合成（M-25）
# ---------------------------------------------------------------------------


async def test_t89_orphan_tool_call_placeholder(runtime, provider, tmp_path, copy_fixture):
    # tree-ok.jsonl 改造变体：把 m2 替换为含孤立 ToolCallBlock 的 PROVIDER 消息
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    orphan_call = ToolCallBlock(id="call-orphan", name="echo", args={"text": "x"})
    lines = [
        json.dumps({"type": "meta", "format_version": 1}),
        json.dumps({"type": "message", "id": "m1", "parent_id": None,
                    "kind": "user",
                    "content": [{"type": "text", "text": "查订单"}],
                    "tool_call_id": None, "tool_status": None,
                    "turn_end": False, "partial": False, "synthetic": False,
                    "source": "", "tags": [], "priority": 3,
                    "timestamp": "2026-08-29T00:00:00", "usage": None}),
        json.dumps({"type": "message", "id": "m2", "parent_id": "m1",
                    "kind": "provider",
                    "content": [{"type": "tool_call", "id": "call-orphan",
                                 "name": "echo", "args": {"text": "x"}}],
                    "tool_call_id": None, "tool_status": None,
                    "turn_end": False, "partial": False, "synthetic": False,
                    "source": "", "tags": [], "priority": 3,
                    "timestamp": "2026-08-29T00:00:00", "usage": None}),
    ]
    (session_dir / "tree.jsonl").write_text("\n".join(lines) + "\n")

    agent = await _restored(runtime, session_dir)
    placeholders = [m for m in agent._messages.values() if m.synthetic]
    assert len(placeholders) == 1
    ph = placeholders[0]
    assert ph.kind is MessageKind.TOOL
    assert ph.tool_call_id == "call-orphan"
    assert ph.tool_status == "error"
    assert isinstance(ph.content[0], TextBlock) and ph.content[0].text   # 占位说明
    assert ph.parent_id == "m2"
    assert agent.current_head_id == ph.id   # provider 消息本是尾：占位成新尾
    # 组装上下文配对封闭：m2 之后紧跟占位 TOOL
    path = agent._assemble_context().messages
    ids = [m.id for m in path]
    assert ids == ["m1", "m2", ph.id]


# ---------------------------------------------------------------------------
# T91 实质：写闸门与压缩时点①（_restore 前后）
# ---------------------------------------------------------------------------


async def test_t91_gate_and_compaction_on_restore(runtime, provider, tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "state.jsonl").write_text(
        '{"type": "meta", "format_version": 1}\n'
        '{"op": "set", "key": "n", "value": 5}\n')

    agent = await runtime.create_agent(SimpleAgent, session_dir=session_dir,
                                       unlock_gate=False)   # 管线未解锁
    with pytest.raises(RuntimeError):
        agent.state.n = 1   # _restore 前写抛错
    assert agent._state_bag._write_gate_open is False

    await agent._restore()
    assert agent._state_bag._write_gate_open is True   # 闸门解开
    assert agent.state.n == 5   # 重放装袋
    agent.state.n = 6   # 写透恢复可用
    await agent._state_bag._store.drain()
    rows = [json.loads(line) for line in
            (session_dir / "state.jsonl").read_text().splitlines() if line.strip()]
    data_rows = [r for r in rows if r.get("type") != "meta"]
    # 压缩时点①：恢复重放后全量压缩已执行（sync 原子重写）——文件只余终态
    # 一行 set（重放的 n=5 与随后 n=6 经压缩/末行合并归一），随后置 7 追加一行
    agent.state.n = 7
    await agent._state_bag._store.drain()
    rows2 = [json.loads(line) for line in
             (session_dir / "state.jsonl").read_text().splitlines() if line.strip()]
    data_rows2 = [r for r in rows2 if r.get("type") != "meta"]
    assert data_rows2[-1] == {"op": "set", "key": "n", "value": 7}
    assert len(data_rows2) <= 2   # 压缩生效，历史行不膨胀


# ---------------------------------------------------------------------------
# T92：destroy 的排空屏障（契约②钉死点）
# ---------------------------------------------------------------------------


async def test_t92_destroy_flush_barrier(runtime, provider):
    script_provider(provider, text_response("最后一条"))
    agent = await runtime.create_agent(SimpleAgent)
    await agent.query("hi")   # 消息行已提交（write-behind，未必落盘）
    session_dir = agent._session_dir
    await agent.destroy()   # 关闭两后端：drain 排空屏障生效
    text = (session_dir / "tree.jsonl").read_text()
    assert '"最后一条"' in text   # destroy 前最后提交的消息行在文件中，不静默丢尾


# ---------------------------------------------------------------------------
# W27：子 Agent 唤起（经 harness 迷你 create 管线端到端）
# ---------------------------------------------------------------------------


async def test_create_subagent_gate(runtime, provider):
    """S-34 追加裁决：写闸门未开时不支持创建子智能体（fail fast）。"""
    agent = await runtime.create_agent(SimpleAgent, unlock_gate=False)
    with pytest.raises(RuntimeError):
        await agent.create_subagent("simple-agent")


async def test_invoke_subagent_end_to_end(runtime, provider):
    parent = await runtime.create_agent(SimpleAgent)
    parent.add_agent("test-agent", alias="kid")

    async def _child_reply(context, model):
        # 子 Agent 收到的提示经 context 传入
        last = context.messages[-1].content[0].text
        return text_response(f"子代回复：{last}")

    provider.generate_fn = _child_reply
    result = await parent.invoke_subagent("kid", prompt="审查 auth 模块",
                                          name="reviewer")
    assert result.subagent_status == "completed"
    assert result.result == "子代回复：审查 auth 模块"   # last_result 文本回传
    assert result.name_alias == "reviewer"
    child_id = result.subagent_id
    assert parent._child_ids["reviewer"] == child_id   # 语义名登记
    assert child_id in parent._children   # 生命周期子树

    # resume 续接同一实例
    result2 = await parent.invoke_subagent("kid", resume="reviewer",
                                           prompt="继续")
    assert result2.subagent_id == child_id   # 带记忆续接
    assert result2.subagent_status == "completed"

    # before_subagent_invoke 拦截：未创建任何实例
    from flowing.errors import Intercepted

    async def _veto(a, invocation):
        raise Intercepted("不许唤起")

    parent.hooks.before_subagent_invoke(_veto)
    with pytest.raises(Intercepted):
        await parent.invoke_subagent("kid", prompt="再来")


# ---------------------------------------------------------------------------
# 审查 Finding 1 回归：占位消息确定性 id，二次恢复历史不丢
# ---------------------------------------------------------------------------


async def test_orphan_placeholder_stable_across_recovers(tmp_path):
    """「恢复 → 继续对话 → destroy → 再恢复」：孤立 tool_call 的占位消息以
    确定性 id（``synthetic-<call_id>``）重新合成，parent 链自愈，
    崩溃前历史与新回合消息完整保留。"""
    from conftest import add_fake_provider, make_runtime

    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent", agent_id="agent-orphan")

    # 构造「tool_call 已挂树、结果未 append」的崩溃现场（半截 turn）
    agent.push(Message(id="m-user", kind=MessageKind.USER,
                       content=[TextBlock(text="崩溃前历史")]))
    agent.push(Message(id="m-call", kind=MessageKind.PROVIDER,
                       content=[ToolCallBlock(id="call-x", name="echo",
                                              args={"text": "x"})]))
    await agent.destroy()

    # 第一次恢复：合成确定性 id 占位，head 上移到占位消息
    rec1 = await runtime.recover_agent("agent-orphan")
    assert rec1.current_head_id == "synthetic-call-x"
    placeholder1 = rec1._messages["synthetic-call-x"]
    assert placeholder1.synthetic is True
    assert placeholder1.tool_call_id == "call-x"

    # 恢复后继续对话：新消息以 parent_id=占位.id 落盘
    script_provider(provider, text_response("继续的回复"))
    r = await rec1.query("继续")
    assert r.status == "completed"
    head_after = r.turn.message_ids[-1]
    await rec1.destroy()

    # 第二次恢复：占位落回同一 id，parent 链自愈，历史完整
    rec2 = await runtime.recover_agent("agent-orphan")
    assert rec2.current_head_id == head_after   # 续上最后持久化消息
    placeholder2 = rec2._messages["synthetic-call-x"]
    assert placeholder2.synthetic and placeholder2.tool_call_id == "call-x"
    path_texts = [getattr(b, "text", "")
                  for m in rec2._assemble_context().messages for b in m.content]
    assert "崩溃前历史" in path_texts   # 崩溃前历史不丢
    assert "继续" in path_texts and "继续的回复" in path_texts   # 新回合也在
    await rec2.destroy()
