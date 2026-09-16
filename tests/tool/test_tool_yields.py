"""on_tool_yields 产物级钩子：同步 / 异步统一产物链的钩子契约测试。

覆盖（设计文档 ``dev-docs/2026-09-16_03-44_on_tool_yields产物级钩子-
修改与测试设计.md`` §7）：同步触发与元信息 / pattern 过滤 / 改写 /
Intercepted 两态 / 不触发三分支 / 收据 / 分段 / 终值 / 后台拦截通知 /
终止通知 / 无 caller / 嵌套形态 / 投递失败兜底 / 取消通知 / 存量缺口
补测（嵌套、分段投递失败、取消断言、caller=None、_deliver 兜底）。
"""

from __future__ import annotations

import asyncio
import gc
import logging
import warnings
from pathlib import Path
from uuid import uuid4

import pytest

from flowing.agent import _start_background_drive
from flowing.errors import Intercepted
from flowing.hooks import HookRegistry
from flowing.message import MessageKind, StructBlock, TextBlock
from flowing.tool import ScriptTool, Tool, ToolCall, ToolDefinition, ToolResult

from harness import (
    add_fake_provider,
    make_runtime,
)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


class _FakeCaller:
    """最小调用方替身：收集 EVENT 投递 + 空钩子注册表 + 后台驱动接缝。"""

    def __init__(self) -> None:
        self.messages: list = []
        self._background_tasks: dict[str, asyncio.Task] = {}
        self.hooks = HookRegistry()
        self.fail_enqueue = False   # 投递失败模拟开关

    async def enqueue_message(self, msg) -> str:
        if self.fail_enqueue:
            raise RuntimeError("模拟投递失败")
        self.messages.append(msg)
        return msg.id

    def _drive_background(self, tool, source, form, tool_call=None) -> str:
        return _start_background_drive(self, tool, source, form, tool_call)

    def track_background_task(self, task: asyncio.Task) -> str:
        task_id = uuid4().hex
        self._background_tasks[task_id] = task
        task.add_done_callback(
            lambda t: self._background_tasks.pop(task_id, None))
        return task_id


async def _drain_until(predicate, *, attempts: int = 200) -> bool:
    for _ in range(attempts):
        await asyncio.sleep(0.01)
        if predicate():
            return True
    return False


def _make_tool(execute_fn, *, background: bool = False) -> ScriptTool:
    attrs = {
        "execute": staticmethod(execute_fn),
        "name": "bg-tool",
        "description": "后台测试工具",
    }
    if background:
        attrs["background"] = True
    return type("BgTool", (ScriptTool,), attrs)()


def _texts(msg) -> list[str]:
    return [b.text for b in msg.content if isinstance(b, TextBlock)]


class _EchoTool(ScriptTool):
    """同步回显工具（text 必填）。"""

    name = "echo"

    async def execute(self, *, text: str) -> str:
        return text


class _BoomTool(ScriptTool):
    """同步抛普通异常 → error 产物。"""

    name = "boom"

    async def execute(self, *, text: str) -> str:
        raise RuntimeError("炸了")


class _RefuseTool(ScriptTool):
    """execute 内抛 Intercepted → blocked 产物。"""

    name = "refuse"

    async def execute(self, *, text: str) -> str:
        raise Intercepted("工具内部拒绝")


@pytest.fixture
async def agent(tmp_path):
    """真 Runtime + 真 Agent（工作循环运行中）；测试末销毁。"""
    rt = make_runtime(tmp_path)
    add_fake_provider(rt)
    ag = await rt.create_agent("test-agent")
    yield ag
    await ag.destroy()


def _bind(agent, tool) -> None:
    agent.runtime.tool_registry.register(tool)
    agent.add_tool(tool.definition.name)


# ---------------------------------------------------------------------------
# 同步路径
# ---------------------------------------------------------------------------


async def test_sync_fires_once_with_metadata(agent):
    """同步 completed：触发一次；name=别名 / tool_call_id / production='sync'。"""
    seen = []
    agent.hooks.on_tool_yields(lambda a, r: seen.append(r) or r, by="test")
    _bind(agent, _EchoTool())

    result = await agent.tool_call(ToolCall(id="c1", name="echo", args={"text": "hi"}))

    assert result.status == "completed" and result.output == "hi"
    assert len(seen) == 1
    fired = seen[0]
    assert fired.name == "echo" and fired.tool_call_id == "c1"
    assert fired.production == "sync" and fired.status == "completed"


async def test_pattern_filter_by_alias(agent):
    """match_on='name'：pattern 命中别名才触发。"""
    hit, miss = [], []
    agent.hooks.on_tool_yields["ec*"](lambda a, r: hit.append(r) or r, by="test")
    agent.hooks.on_tool_yields["nope-*"](lambda a, r: miss.append(r) or r, by="test")
    _bind(agent, _EchoTool())

    await agent.tool_call(ToolCall(id="c1", name="echo", args={"text": "hi"}))

    assert len(hit) == 1 and miss == []


async def test_rewrite_raw_output(agent):
    """handler 改写 output 原料值：tool_call 返回值与挂树塑形均反映改写。"""
    def _rewrite(a, result):
        result.output = {"rewritten": True}
        return result

    agent.hooks.on_tool_yields(_rewrite, by="test")
    _bind(agent, _EchoTool())

    result = await agent.tool_call(ToolCall(id="c1", name="echo", args={"text": "hi"}))

    assert result.output == {"rewritten": True}
    msg = result.as_message("c1")
    assert msg.content == [StructBlock(data={"rewritten": True})]


async def test_sync_intercepted_becomes_blocked(agent):
    """on_tool_yields 拦截 → blocked（工具已执行、结果被丢弃）；dispatch 一次。"""
    calls = []

    def _veto(a, result):
        calls.append(result)
        raise Intercepted("审批拒绝")

    agent.hooks.on_tool_yields(_veto, by="test")
    _bind(agent, _EchoTool())

    result = await agent.tool_call(ToolCall(id="c1", name="echo", args={"text": "hi"}))

    assert result.status == "blocked" and result.output == "审批拒绝"
    assert len(calls) == 1   # 单次 dispatch，无重入


async def test_error_status_fires(agent):
    """execute 抛普通异常 → error 产物照常触发（production='sync'）。"""
    seen = []
    agent.hooks.on_tool_yields(lambda a, r: seen.append(r) or r, by="test")
    _bind(agent, _BoomTool())

    result = await agent.tool_call(ToolCall(id="c1", name="boom", args={"text": "x"}))

    assert result.status == "error"
    assert len(seen) == 1 and seen[0].status == "error"
    assert seen[0].production == "sync"


async def test_non_yield_paths_do_not_fire(agent):
    """不触发三分支：before 拦截 blocked / shortcut / LLM 校验失败 error。"""
    yields, afters = [], []
    agent.hooks.on_tool_yields(lambda a, r: yields.append(r) or r, by="test")
    agent.hooks.after_tool_call(lambda a, r: afters.append(r) or r, by="test")
    _bind(agent, _EchoTool())

    # ① before_tool_call 拦截：工具未执行，blocked 非 tool yield
    agent.hooks.before_tool_call(
        lambda a, tc: (_ for _ in ()).throw(Intercepted("闸口拒绝")), by="test")
    r1 = await agent.tool_call(ToolCall(id="c1", name="echo", args={"text": "hi"}))
    assert r1.status == "blocked"
    assert yields == [] and afters == []   # after_tool_call 亦不触发（现行规则）

    # ② shortcut：产物出自 handler
    agent.hooks.before_tool_call[0].enabled = False
    def _shortcut(a, tc):
        tc.shortcut = ToolResult(status="completed", output="sc")
        return tc
    agent.hooks.before_tool_call(_shortcut, by="test")
    r2 = await agent.tool_call(ToolCall(id="c2", name="echo", args={"text": "hi"}))
    assert r2.status == "completed" and r2.output == "sc"
    assert yields == [] and len(afters) == 1   # after_tool_call 照常

    # ③ LLM 视角校验失败：工具未执行的 error（after_tool_call 照常）
    agent.hooks.before_tool_call[1].enabled = False   # 关闭 shortcut handler
    r3 = await agent.tool_call(
        ToolCall(id="c3", name="echo", args={"bogus": 1}))
    assert r3.status == "error"
    assert yields == [] and len(afters) == 2


async def test_execute_intercepted_blocked_skipped(agent):
    """execute 内 Intercepted → blocked 到达统一点被守卫跳过（不触发）。"""
    yields, afters = [], []
    agent.hooks.on_tool_yields(lambda a, r: yields.append(r) or r, by="test")
    agent.hooks.after_tool_call(lambda a, r: afters.append(r) or r, by="test")
    _bind(agent, _RefuseTool())

    result = await agent.tool_call(ToolCall(id="c1", name="refuse", args={"text": "x"}))

    assert result.status == "blocked"
    assert yields == [] and len(afters) == 1


async def test_no_handler_behavior_unchanged(agent):
    """未注册 handler：结果与塑形与纯管线产出逐点一致。"""
    _bind(agent, _EchoTool())

    result = await agent.tool_call(ToolCall(id="c1", name="echo", args={"text": "hi"}))

    assert result.status == "completed" and result.output == "hi"
    assert result.name == "echo" and result.tool_call_id == "c1"
    assert result.production == "sync"
    msg = result.as_message("c1")
    assert msg.content == [TextBlock(text="hi")]
    assert msg.tool_call_id == "c1" and msg.tool_status == "completed"


# ---------------------------------------------------------------------------
# 收据与后台段
# ---------------------------------------------------------------------------


async def test_receipt_fires_with_pending(agent):
    """async gen 工具：收据 production='receipt' + status='pending'；两钩子各一次。"""
    yields, afters = [], []
    agent.hooks.on_tool_yields(lambda a, r: yields.append(r) or r, by="test")
    agent.hooks.after_tool_call(lambda a, r: afters.append(r) or r, by="test")

    async def gen(*, text: str):
        yield {"status": "started"}

    tool = _make_tool(gen)
    agent.runtime.tool_registry.register(tool)
    agent.add_tool("bg-tool")

    result = await agent.tool_call(ToolCall(id="c1", name="bg-tool", args={"text": "x"}))

    assert result.status == "pending" and result.background_task_id is not None
    assert len(yields) == 1 and len(afters) == 1
    assert yields[0].production == "receipt" and yields[0].status == "pending"
    assert yields[0].name == "bg-tool" and yields[0].tool_call_id == "c1"


async def test_segments_fire_and_rewrite_delivered(agent):
    """分段：每 yield 触发一次 production='segment'；改写生效于投递内容。"""
    caller = _FakeCaller()
    seen = []

    def _rewrite(a, result):
        seen.append(result.production)
        if result.production == "segment":
            result.output = {"seg": "rewritten"}
        return result

    caller.hooks.on_tool_yields(_rewrite, by="test")

    async def gen(*, text: str):
        yield {"status": "started"}
        yield {"n": 1}
        yield {"n": 2}

    tool = _make_tool(gen)
    r = await tool({"text": "x"}, caller=caller)
    assert r.status == "pending"
    assert await _drain_until(lambda: len(caller.messages) >= 2)

    assert seen == ["segment", "segment"]
    for msg in caller.messages:
        assert msg.kind is MessageKind.EVENT and msg.source == "tool_result"
        assert StructBlock(data={"seg": "rewritten"}) in msg.content


async def test_task_final_fires(agent):
    """Task 形态：终值 production='final'、status='completed'。"""
    caller = _FakeCaller()
    seen = []
    caller.hooks.on_tool_yields(lambda a, r: seen.append(r) or r, by="test")
    gate = asyncio.Event()

    async def execute(*, text: str):
        async def _bg():
            await gate.wait()
            return text.upper()
        return asyncio.ensure_future(_bg())

    tool = _make_tool(execute)
    r = await tool({"text": "hi"}, caller=caller)
    assert r.status == "pending"
    gate.set()
    assert await _drain_until(lambda: bool(seen))

    assert seen[0].production == "final" and seen[0].status == "completed"
    assert await _drain_until(lambda: bool(caller.messages))
    assert any("HI" in t for t in _texts(caller.messages[0]))


async def test_asyncgen_normal_exhaustion_has_no_final(agent):
    """正常耗尽：无 production='final' 产物、无补发通知（行为不变）。"""
    caller = _FakeCaller()
    seen = []
    caller.hooks.on_tool_yields(lambda a, r: seen.append(r.production) or r, by="test")

    async def gen(*, text: str):
        yield {"status": "started"}
        yield {"status": "done"}

    tool = _make_tool(gen)
    r = await tool({"text": "x"}, caller=caller)
    assert await _drain_until(lambda: bool(caller.messages))
    assert await _drain_until(
        lambda: r.background_task_id not in caller._background_tasks)
    await asyncio.sleep(0.05)   # 耗尽后无追发

    assert seen == ["segment"]   # 唯一后续分段；无 final
    assert len(caller.messages) == 1


async def test_background_intercepted_drop_and_notice(agent):
    """后台拦截：该分段不入队；通知 EVENT 含工具名与 reason；后续分段继续。"""
    caller = _FakeCaller()

    def _veto_segments(a, result):
        if result.production == "segment" and result.output == {"n": 1}:
            raise Intercepted("内容违规")
        return result

    caller.hooks.on_tool_yields(_veto_segments, by="test")

    async def gen(*, text: str):
        yield {"status": "started"}
        yield {"n": 1}
        yield {"n": 2}

    tool = _make_tool(gen)
    await tool({"text": "x"}, caller=caller)
    assert await _drain_until(lambda: len(caller.messages) >= 2)

    notice, delivered = caller.messages[0], caller.messages[1]
    assert any("yield intercepted, reason: 内容违规" in t for t in _texts(notice))
    assert any("bg-tool" in t for t in _texts(notice))
    assert StructBlock(data={"n": 2}) in delivered.content
    assert all(StructBlock(data={"n": 1}) not in m.content
               for m in caller.messages)


async def test_terminal_notifications_pass_hook(agent):
    """终止通知（中途异常 / 取消）：包装 error + production='final' 过钩子。"""
    caller = _FakeCaller()
    seen = []
    caller.hooks.on_tool_yields(lambda a, r: seen.append(r) or r, by="test")

    async def gen(*, text: str):
        yield {"status": "started"}
        raise RuntimeError("后台炸了")

    tool = _make_tool(gen)
    await tool({"text": "x"}, caller=caller)
    assert await _drain_until(lambda: bool(seen))

    assert seen[0].production == "final" and seen[0].status == "error"
    assert "后台炸了" in (seen[0].error or "")
    assert await _drain_until(lambda: bool(caller.messages))
    assert any("后台炸了" in t for t in _texts(caller.messages[0]))


async def test_cancel_notice_delivered(agent):
    """取消驱动任务：「async task cancelled」通知实际入队（production='final'）。"""
    caller = _FakeCaller()
    seen = []
    caller.hooks.on_tool_yields(lambda a, r: seen.append(r) or r, by="test")

    started = asyncio.Event()

    async def gen(*, text: str):
        yield {"status": "started"}
        started.set()
        await asyncio.Event().wait()   # 永不放行
        yield {"status": "done"}       # pragma: no cover

    tool = _make_tool(gen)
    r = await tool({"text": "x"}, caller=caller)
    await asyncio.wait_for(started.wait(), 1)   # 等驱动任务真正进入等待再取消
    task = caller._background_tasks[r.background_task_id]
    task.cancel()
    assert await _drain_until(lambda: bool(caller.messages))

    assert any("async task cancelled" in t for t in _texts(caller.messages[0]))
    assert seen and seen[0].production == "final" and seen[0].status == "error"
    assert task.cancelled()


async def test_no_caller_no_drive_no_dispatch():
    """caller=None 编程路径：pending 照返、不注册不驱动、生成器被关闭。"""
    closed = []

    async def gen(*, text: str):
        try:
            yield {"status": "started"}
            yield {"status": "done"}   # pragma: no cover
        finally:
            closed.append(True)

    tool = _make_tool(gen)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        r = await tool({"text": "x"}, caller=None)
        gc.collect()

    assert r.status == "pending" and r.background_task_id is None
    assert closed == [True]   # aclose 生效
    assert not any("GeneratorExit" in str(w.message) for w in caught)


async def test_segment_delivery_failure_warns_and_continues(caplog):
    """分段投递失败：记 warning、后续分段继续驱动。"""
    caller = _FakeCaller()

    async def gen(*, text: str):
        yield {"status": "started"}
        yield {"n": 1}
        yield {"n": 2}

    tool = _make_tool(gen)
    caller.fail_enqueue = True
    with caplog.at_level(logging.WARNING, logger="flowing.agent"):
        r = await tool({"text": "x"}, caller=caller)
        assert await _drain_until(
            lambda: r.background_task_id not in caller._background_tasks)
    assert any("report delivery failed" in rec.message for rec in caplog.records)

    # 仅首段失败、后续恢复：第二段正常投递
    caller.messages.clear()
    caller.fail_enqueue = False
    delivered = []

    async def gen2(*, text: str):
        yield {"status": "started"}
        yield {"n": 1}
        delivered.append(True)
        yield {"n": 2}

    tool2 = _make_tool(gen2)
    r2 = await tool2({"text": "x"}, caller=caller)
    assert await _drain_until(lambda: len(caller.messages) >= 2)
    assert delivered == [True]   # 单段失败不中断驱动


async def test_task_final_delivery_failure_is_logged(caplog):
    """Task 终值投递链失败：兜底捕获 + 日志，不成无人 retrieve 的异常。"""
    caller = _FakeCaller()
    caller.fail_enqueue = True

    async def execute(*, text: str):
        async def _bg():
            return text
        return asyncio.ensure_future(_bg())

    tool = _make_tool(execute)
    with caplog.at_level(logging.ERROR, logger="flowing.agent"):
        r = await tool({"text": "x"}, caller=caller)
        assert await _drain_until(
            lambda: r.background_task_id not in caller._background_tasks)
    assert any("final result delivery failed" in rec.message
               for rec in caplog.records)


async def test_nested_asyncgen_form():
    """嵌套形态（async def execute 返回 async gen）：收据 + 逐段 + 首 yield 前分派。"""
    caller = _FakeCaller()

    async def execute(*, text: str):
        async def _inner():
            yield {"status": "started"}
            yield {"status": "done"}
        return _inner()

    tool = _make_tool(execute)
    r = await tool({"text": "x"}, caller=caller)
    assert r.status == "pending" and r.output == {"status": "started"}
    assert await _drain_until(lambda: bool(caller.messages))
    assert StructBlock(data={"status": "done"}) in caller.messages[0].content

    # 首 yield 前 Intercepted → blocked；普通异常 → error
    async def refuse(*, text: str):
        async def _inner():
            raise Intercepted("审批拒绝")
            yield 1   # pragma: no cover
        return _inner()

    r2 = await _make_tool(refuse)({"text": "x"}, caller=caller)
    assert r2.status == "blocked" and r2.output == "审批拒绝"

    async def boom(*, text: str):
        async def _inner():
            raise RuntimeError("炸了")
            yield 1   # pragma: no cover
        return _inner()

    r3 = await _make_tool(boom)({"text": "x"}, caller=caller)
    assert r3.status == "error" and "炸了" in (r3.error or "")
