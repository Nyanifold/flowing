"""配对封闭（树内永远成对）测试：abort 执行期 cancelled 封闭、
恢复期 synthetic 落盘封闭、装配配对断言。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from flowing.errors import UnpairedToolCallError
from flowing.message import Message, MessageKind, TextBlock, ToolCallBlock
from flowing.providers import ProviderResponse
from flowing.tool import Tool, ToolDefinition

from harness import script_provider, text_response, tool_call_response


class Echo(Tool):
    definition = ToolDefinition(name="echo", description="回显",
                                params_schema={"text": {"type": "string"}})

    async def execute(self, *, text: str) -> str:
        return text


def _calls(agent) -> dict[str, ToolCallBlock]:
    return {b.id: b for m in agent._messages.values()
            if m.kind is MessageKind.PROVIDER
            for b in m.content if isinstance(b, ToolCallBlock)}


def _results(agent) -> dict[str, Message]:
    return {m.tool_call_id: m for m in agent._messages.values()
            if m.kind is MessageKind.TOOL}


def _abort_on_tool_call(host, msg):
    if any(isinstance(b, ToolCallBlock) for b in msg.content):
        host.abort_turn()
    return msg


async def test_batch_skip_closes_with_cancelled(runtime, provider):
    """批次前 abort → 整批未执行 → 每个调用以 cancelled 封闭挂树、
    下一轮请求合法（配对断言不炸）。"""
    runtime.register_tool(Echo())
    agent = await runtime.create_agent("test-agent")
    agent.add_tool("echo")
    step1, _ = tool_call_response(("echo", {"text": "a"}), ("echo", {"text": "b"}))
    script_provider(provider, step1, text_response("第二轮"))
    agent.hooks.on_turn_append(_abort_on_tool_call, by="test")
    r1 = await agent.query("go")
    assert r1.status == "cancelled"
    calls, results = _calls(agent), _results(agent)
    assert len(calls) == 2
    assert set(calls) <= set(results), "每个调用都必须有配对结果（树内封闭）"
    for cid in calls:
        assert results[cid].tool_status == "cancelled"
        assert results[cid].content == []
    r2 = await agent.query("go2")
    assert r2.status == "completed", "封闭后下一轮请求必须合法"


async def test_in_flight_abort_pairs_with_partial(runtime, provider):
    """批次执行中 abort：在途工具被竞速中断，结果以 tool_status=
    "cancelled" 真实挂树（真实配对，不产生封闭占位）。"""
    class Slow(Tool):
        definition = ToolDefinition(name="slow", description="慢工具",
                                    params_schema={})

        async def execute(self, *, caller=None) -> str:
            await asyncio.sleep(30)
            return "done"

    runtime.register_tool(Slow())
    agent = await runtime.create_agent("test-agent")
    agent.add_tool("slow")

    async def _abort_later():
        await asyncio.sleep(0.1)
        agent.abort_turn()

    task = asyncio.ensure_future(_abort_later())
    step1, _ = tool_call_response(("slow", {}))
    script_provider(provider, step1, text_response("ok"))
    r1 = await agent.query("go")
    await task
    assert r1.status == "cancelled"
    calls, results = _calls(agent), _results(agent)
    assert set(calls) <= set(results), "在途工具被取消后结果仍真实配对挂树"
    for cid, m in results.items():
        assert m.tool_status == "cancelled", "被竞速中断的工具结果是 cancelled（不是封闭占位）"


async def test_recovery_closure_persisted(runtime, tmp_path):
    """恢复封闭落盘：恢复后树内成对且 tree.jsonl 含占位行 + move 调整行；
    二次恢复不重复合成（幂等）。"""
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    (session_dir / "core.jsonl").write_text(
        '{"type": "meta", "format_version": 1}\n'
        '{"op": "set", "key": "current_head_id", "value": "2"}\n')
    def _rec(mid, kind, parent, content):
        return json.dumps({
            "type": "message", "id": mid, "kind": kind, "parent_id": parent,
            "content": content, "tool_call_id": None, "tool_status": None,
            "turn_end": False, "partial": False, "synthetic": False,
            "source": "", "tags": [], "priority": 3,
            "timestamp": "2026-09-21T00:00:00", "usage": None}, ensure_ascii=False)

    rows = [
        '{"type": "meta", "format_version": 1}',
        _rec("1", "user", None, [{"type": "text", "text": "读文件"}]),
        _rec("2", "provider", "1", [
            {"type": "tool_call", "id": "call-9", "name": "read",
             "args": {"path": "/tmp/x"}}]),
        _rec("3", "user", "2", [{"type": "text", "text": "继续"}]),
    ]
    (session_dir / "tree.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    from harness import SimpleAgent
    agent = await runtime.create_agent(SimpleAgent, session_dir=session_dir)
    await agent._restore()
    results = _results(agent)
    assert "call-9" in results and results["call-9"].synthetic
    assert agent.current_head_id == results["call-9"].id, "调用是链尾时 head 上移到占位"
    await agent._tree_store.drain()   # write-behind 排空后再断言落盘行
    lines = (session_dir / "tree.jsonl").read_text(encoding="utf-8").splitlines()
    assert any('"synthetic-call-9"' in ln for ln in lines), "占位行必须落盘"
    assert any('"type": "move"' in ln for ln in lines), "邻接调整行必须落盘"

    # 二次恢复：answered 命中已落盘占位，不重复合成
    agent2 = await runtime.create_agent(SimpleAgent, session_dir=session_dir)
    await agent2._restore()
    n = sum(1 for m in agent2._messages.values() if m.synthetic)
    assert n == 1, "同一占位只存在一份（幂等）"


async def test_assembly_asserts_unpaired(runtime):
    """手工造未配对树 → 装配配对断言响亮报错（UnpairedToolCallError）。"""
    agent = await runtime.create_agent("test-agent")
    a = agent.chain.branch(None, Message(kind=MessageKind.USER,
                                         content=[TextBlock(text="读")]))
    await agent.fork(a)
    b = agent.chain.branch(a, Message(kind=MessageKind.PROVIDER, content=[
        ToolCallBlock(id="call-z", name="read", args={})]))
    await agent.fork(b)
    with pytest.raises(UnpairedToolCallError):
        agent._assemble_context()


async def test_closure_lands_on_call_branch(runtime):
    """分支情形：封闭插在调用所在分支（provider 直接后继）。"""
    agent = await runtime.create_agent("test-agent")
    a = agent.chain.branch(None, Message(kind=MessageKind.USER,
                                         content=[TextBlock(text="主链")]))
    await agent.fork(a)
    b = agent.chain.branch(a, Message(kind=MessageKind.PROVIDER, content=[
        ToolCallBlock(id="call-b", name="read", args={})]))
    # 在分支 b 上模拟收尾封闭（与 _close_orphan_tool_calls 同渠道）
    closure_id = agent.chain.insert(
        b, Message(kind=MessageKind.TOOL, tool_call_id="call-b",
                   tool_status="cancelled", content=[]))
    assert agent._messages[closure_id].parent_id == b, "封闭挂在调用直接后继"
    # 切 head 到主链另一分支：该分支视图无调用也无封闭，各自自洽
    await agent.fork(a)
    walk_ids = [m.id for m in agent.chain.walk(a)]
    assert b not in walk_ids and closure_id not in walk_ids


async def test_results_append_in_completion_order(runtime, provider):
    """结果即完成即挂：快工具的结果先于慢工具挂树（实时完成序，
    不再等整批按块序）。"""
    class Timed(Tool):
        def __init__(self, name: str, delay: float) -> None:
            self._delay = delay
            self.definition = ToolDefinition(name=name, description=name,
                                             params_schema={})

        async def execute(self) -> str:
            await asyncio.sleep(self._delay)
            return self.definition.name

    runtime.register_tool(Timed("slow-t", 0.3))
    runtime.register_tool(Timed("fast-t", 0.01))
    agent = await runtime.create_agent("test-agent")
    agent.add_tool("slow-t")
    agent.add_tool("fast-t")
    # 块序：slow 在前、fast 在后；完成序应反过来
    step1, _ = tool_call_response(("slow-t", {}), ("fast-t", {}))
    script_provider(provider, step1, text_response("ok"))
    r = await agent.query("go")
    assert r.status == "completed"
    tool_msgs = [m for m in agent.chain.walk(agent.current_head_id)
                 if m.kind is MessageKind.TOOL]
    names = [m.content[0].text for m in reversed(tool_msgs) if m.content]
    assert names == ["fast-t", "slow-t"], "结果按完成序挂树（fast 先、slow 后）"
