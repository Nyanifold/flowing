"""阶段 3 后台任务机制测试（B1–B13）：新增测试组 1–10（规格 §5）。

覆盖：async gen 三形态、首 yield 契约（except 顺序）、后台阶段失败双通道、
yield 归一化与违禁块容错、收据注册键、注册表生命周期、取消 API 与 destroy
统一取消、background 标记（类属性 / .fya 声明通道 / B13 收口）、返回 Task
路径回归（注册表强引用）、查询 API 与 ``Agent.cancel()``/``cancel_by_tag``
边界（S12）。真 Agent 侧由 phase2 conftest 的 ``make_runtime`` 驱动。
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path
from uuid import uuid4

import pytest

from flowing.agent import Agent
from flowing.errors import FormatError, Intercepted
from flowing.message import MessageKind, StructBlock, TextBlock, ToolCallBlock
from flowing.tool import (
    CliTool,
    ScriptTool,
    Tool,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)

_PHASE2_CONFTEST = Path(__file__).parent.parent / "phase2" / "conftest.py"
_spec = importlib.util.spec_from_file_location("phase2_conftest", _PHASE2_CONFTEST)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
make_runtime = _mod.make_runtime
add_fake_provider = _mod.add_fake_provider
script_provider = _mod.script_provider
text_response = _mod.text_response


class _FakeCaller:
    """最小调用方替身：收集 enqueue_message 投递的消息（B9 注册表 stub）。"""

    def __init__(self) -> None:
        self.messages: list = []
        self._background_tasks: dict[str, asyncio.Task] = {}

    async def enqueue_message(self, msg) -> str:
        self.messages.append(msg)
        return msg.id

    def track_background_task(self, task: asyncio.Task) -> str:
        """与 Agent.track_background_task 同构的测试替身实现。"""
        task_id = uuid4().hex
        self._background_tasks[task_id] = task
        task.add_done_callback(
            lambda t: self._background_tasks.pop(task_id, None))
        return task_id


async def _drain_until(predicate, *, attempts: int = 200) -> bool:
    """让事件循环走到 predicate 成立（后台驱动/回调需若干 tick）。"""
    for _ in range(attempts):
        await asyncio.sleep(0.01)
        if predicate():
            return True
    return False


def _make_tool(execute_fn, *, background: bool = False) -> ScriptTool:
    """按 execute 函数构造 ScriptTool 子类实例（background 类属性可选，B6）。

    ``execute`` 经 ``staticmethod`` 包装——与框架对裸函数的提升路径
    （``_auto_generate_tool`` / ``_script_tool_from_fya``）同款；直接放
    类属性会变成绑定方法，``inspect.signature`` 对首参 keyword-only 的
    纯函数做绑定转换会抛 ``ValueError``。
    """
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


# ---------------------------------------------------------------------------
# 组 1：async gen 三形态
# ---------------------------------------------------------------------------


async def test_g1_asyncgen_three_shapes():
    """收据+结果（2 yield）/ 仅收据（1 yield）/ 纯副作用（条件性 0 yield）。"""
    caller = _FakeCaller()

    async def gen2(*, text: str):
        yield {"status": "started", "text": text}
        await asyncio.sleep(0.01)
        yield {"status": "done", "text": text}

    async def gen1(*, text: str):
        yield {"pid": 42}

    async def gen0(*, text: str):
        if False:
            yield 1

    tool2, tool1, tool0 = (_make_tool(f) for f in (gen2, gen1, gen0))

    # 收据+结果：pending 收据带首 yield 内容；后台 EVENT 送达
    r2 = await tool2({"text": "hi"}, caller=caller)
    assert r2.status == "pending"
    assert r2.output == {"status": "started", "text": "hi"}
    assert r2.background_task_id is not None
    assert r2.background_task_id in caller._background_tasks
    assert await _drain_until(lambda: bool(caller.messages))
    msg = caller.messages[0]
    assert msg.kind is MessageKind.EVENT and msg.source == "tool_result"
    assert any(isinstance(b, StructBlock) and b.data.get("status") == "done"
               for b in msg.content)

    # 仅收据：无后续 yield → 无 EVENT；驱动任务完成即弃
    caller.messages.clear()
    r1 = await tool1({"text": "x"}, caller=caller)
    assert r1.status == "pending" and r1.output == {"pid": 42}
    assert await _drain_until(
        lambda: r1.background_task_id not in caller._background_tasks)
    assert caller.messages == []

    # 纯副作用：条件性 0 yield → 空 pending 收据（StopAsyncIteration → None）
    r0 = await tool0({"text": "x"}, caller=caller)
    assert r0.status == "pending" and r0.output is None


# ---------------------------------------------------------------------------
# 组 2：首 yield 契约（except 顺序分派）
# ---------------------------------------------------------------------------


async def test_g2_first_yield_contract():
    """首 yield 前异常按 except 顺序分派：普通异常 → error；Intercepted → blocked。"""
    caller = _FakeCaller()

    async def gen_fail(*, text: str):
        raise RuntimeError("准备阶段炸了")
        yield 1   # pragma: no cover（不可达；yield 使函数成为 async gen）

    async def gen_intercepted(*, text: str):
        raise Intercepted("审批拒绝")
        yield 1   # pragma: no cover

    r = await _make_tool(gen_fail)({"text": "x"}, caller=caller)
    assert r.status == "error" and "准备阶段炸了" in r.error
    assert r.background_task_id is None

    r2 = await _make_tool(gen_intercepted)({"text": "x"}, caller=caller)
    assert r2.status == "blocked" and r2.output == "审批拒绝"
    assert caller._background_tasks == {}   # 未注册（不挂 pending）


# ---------------------------------------------------------------------------
# 组 3：后台阶段失败（双通道）
# ---------------------------------------------------------------------------


async def test_g3_background_failure_dual_channel(caplog):
    """首 yield 后异常 → EVENT 错误块（LLM 可见）+ flowing.tool 日志。"""
    caller = _FakeCaller()

    async def gen(*, text: str):
        yield {"status": "started"}
        raise RuntimeError("后台炸了")

    with caplog.at_level(logging.ERROR, logger="flowing.tool"):
        r = await _make_tool(gen)({"text": "x"}, caller=caller)
        assert r.status == "pending"
        assert await _drain_until(lambda: bool(caller.messages))
    msg = caller.messages[0]
    assert msg.kind is MessageKind.EVENT and msg.source == "tool_result"
    assert any("后台炸了" in t for t in _texts(msg))
    assert any("异步工具 bg-tool 后台运行失败" in rec.message
               for rec in caplog.records)


# ---------------------------------------------------------------------------
# 组 4：yield 归一化与违禁块容错
# ---------------------------------------------------------------------------


async def test_g4_yield_normalization_and_forbidden_block():
    """yield 值归一化塑形；违禁块（ToolCallBlock）→ 转文本（不抛、不泄漏）。"""
    caller = _FakeCaller()

    async def gen(*, text: str):
        yield {"status": "started"}                    # ① 收据（不进消息）
        yield {"epoch": 1, "val_loss": 0.5}            # ② dict → StructBlock
        yield "进度文本"                                # ③ str → TextBlock
        yield ToolCallBlock(id="t1", name="x", args={})  # ④ 违禁块 → 文本说明
        yield {"status": "done"}                       # ⑤ 最终呈现

    r = await _make_tool(gen)({"text": "x"}, caller=caller)
    assert r.status == "pending" and r.output == {"status": "started"}
    assert await _drain_until(lambda: len(caller.messages) >= 4)
    # 报告 1：dict 归一为 StructBlock
    m1 = caller.messages[0]
    assert any(isinstance(b, StructBlock) and b.data.get("epoch") == 1
               for b in m1.content)
    # 报告 2：str 归一为 TextBlock
    m2 = caller.messages[1]
    assert "进度文本" in _texts(m2)
    # 报告 3：违禁块容错转文本（不抛异常、不泄漏协议块）
    m3 = caller.messages[2]
    assert all(isinstance(b, TextBlock) for b in m3.content)
    assert any("违禁块" in t for t in _texts(m3))
    assert not any(isinstance(b, ToolCallBlock)
                   for m in caller.messages for b in m.content)
    # 报告 4：后续 yield 正常继续
    assert any(isinstance(b, StructBlock) and b.data.get("status") == "done"
               for b in caller.messages[3].content)


# ---------------------------------------------------------------------------
# 组 5：收据注册键
# ---------------------------------------------------------------------------


async def test_g5_receipt_registration_key():
    """pending 收据 background_task_id 与注册表一致；as_message 附加任务 ID 块。"""
    caller = _FakeCaller()

    async def gen(*, text: str):
        yield {"status": "started", "text": text}

    tool = _make_tool(gen)
    r = await tool({"text": "hi"}, caller=caller)
    assert r.status == "pending"
    assert r.background_task_id in caller._background_tasks
    # as_message 塑形：content 在作者数据块之后附加「后台任务 ID」块（B10）
    msg = r.as_message("tc-1")
    assert msg.kind is MessageKind.TOOL and msg.tool_status == "pending"
    assert any(b.data.get("text") == "hi"
               for b in msg.content if isinstance(b, StructBlock))
    task_blocks = [t for t in _texts(msg) if t.startswith("后台任务 ID：")]
    assert task_blocks == [f"后台任务 ID：{r.background_task_id}"]
    # 非 pending 状态不携带注册键块
    done = ToolResult(status="completed", output={"ok": 1})
    done_msg = done.as_message("tc-2")
    assert not any(t.startswith("后台任务 ID：") for t in _texts(done_msg))


# ---------------------------------------------------------------------------
# 组 6：注册表生命周期
# ---------------------------------------------------------------------------


async def test_g6_registry_lifecycle():
    """完成/异常/取消后从注册表移除（无残留）；运行中在册。"""
    caller = _FakeCaller()
    gate_a = asyncio.Event()
    gate_b = asyncio.Event()

    async def gen(*, text: str):
        yield {"status": "started"}
        await gate_a.wait()

    async def gen_b(*, text: str):
        yield {"status": "started"}
        await gate_b.wait()

    async def gen_fail(*, text: str):
        yield {"status": "started"}
        raise RuntimeError("后台炸了")

    tool = _make_tool(gen)
    tool_b = _make_tool(gen_b)
    fail_tool = _make_tool(gen_fail)

    # 运行中在册
    r = await tool({"text": "x"}, caller=caller)
    assert r.background_task_id in caller._background_tasks
    # 完成即弃
    gate_a.set()
    assert await _drain_until(
        lambda: r.background_task_id not in caller._background_tasks)
    # 异常即弃
    r2 = await fail_tool({"text": "x"}, caller=caller)
    assert await _drain_until(
        lambda: r2.background_task_id not in caller._background_tasks)
    # 取消即弃
    r3 = await tool_b({"text": "x"}, caller=caller)
    caller._background_tasks[r3.background_task_id].cancel()
    assert await _drain_until(
        lambda: r3.background_task_id not in caller._background_tasks)


# ---------------------------------------------------------------------------
# 组 7：取消 API 与 destroy 统一取消（真 Agent）
# ---------------------------------------------------------------------------


async def test_g7_cancel_api_and_destroy(tmp_path):
    """cancel 命中/未命中/已完成；cancel_all；destroy 后注册表立即为空（B11）。"""
    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    script_provider(provider, text_response("ok"))
    agent = await runtime.create_agent("test-agent")
    captured: list = []
    agent.hooks.after_enqueue(
        lambda a, m: captured.append(m) or m, by="test")
    gate = asyncio.Event()
    gen_started = asyncio.Event()

    async def gen(*, text: str):
        yield {"status": "started"}
        gen_started.set()   # 驱动任务越过首步的信号（取消投递的确定性前提）
        await gate.wait()

    tool = _make_tool(gen)
    try:
        r = await tool({"text": "x"}, caller=agent)
        tid = r.background_task_id
        # 未命中 → False 幂等
        assert agent.cancel_background_task("nope") is False
        # 等驱动任务越过首步再取消——取消早于任务首步时（ensure_future 只是
        # 排程）CancelledError 在任务体运行前抛出，无「已取消」投递（B4 边界）
        assert await _drain_until(gen_started.is_set)
        # 命中 → 任务取消 + 「已取消」EVENT 投递 + 注册表移除
        assert agent.cancel_background_task(tid) is True
        assert await _drain_until(
            lambda: tid not in agent._background_tasks)
        assert any("异步任务被取消" in t
                   for m in captured for t in _texts(m))
        # cancel_all：全部取消（含已完成的——命中语义与 task.cancel() 返回值无关）
        r2 = await tool({"text": "x"}, caller=agent)
        assert agent.cancel_all_background_tasks() is None
        assert await _drain_until(
            lambda: r2.background_task_id not in agent._background_tasks)
        # destroy：统一取消 + 显式 clear——注册表立即为空（不依赖回调时序）
        r3 = await tool({"text": "x"}, caller=agent)
        assert agent._background_tasks
        await agent.destroy()
        assert agent._background_tasks == {}
        assert agent.get_background_task(r3.background_task_id) is None
    finally:
        await runtime.shutdown()


# ---------------------------------------------------------------------------
# 组 8：background 标记（B6/B13）——类属性 / .fya 通道 / 负例
# ---------------------------------------------------------------------------


async def test_g8_background_flag_and_fya(fixtures_dir, tmp_path):
    """background 标记：类属性后台化；.fya 声明通道（两 fixture）；负例 FormatError。"""
    caller = _FakeCaller()

    async def plain(*, text: str) -> dict:
        await asyncio.sleep(0.01)
        return {"status": "done", "text": text}

    # 未标记 → 行为不变（await 到底 completed）
    r = await _make_tool(plain)({"text": "x"}, caller=caller)
    assert r.status == "completed"
    assert r.output == {"status": "done", "text": "x"}

    # background=True → pending 收据（output=None，S7）+ 完成 EVENT
    caller.messages.clear()
    r2 = await _make_tool(plain, background=True)({"text": "x"}, caller=caller)
    assert r2.status == "pending" and r2.output is None
    assert r2.background_task_id in caller._background_tasks
    assert await _drain_until(lambda: bool(caller.messages))
    assert any(isinstance(b, StructBlock) and b.data.get("status") == "done"
               for m in caller.messages for b in m.content)

    # .fya 通道一：async gen 无标记也后台（B5，train-net fixture）
    registry = ToolRegistry()
    train = registry.get("train-net",
                         source_dir=fixtures_dir / "fya" / "tools")
    assert getattr(train, "background", False) is False   # 未写字段
    caller.messages.clear()
    r3 = await train({"dataset": "mnist"}, caller=caller)
    assert r3.status == "pending"
    assert r3.output == {"status": "started",
                         "dataset": "mnist", "epochs": 4}
    assert await _drain_until(lambda: any(
        any(isinstance(b, StructBlock) and b.data.get("status") == "done"
            for b in m.content) for m in caller.messages))

    # .fya 通道二：background: true + 普通 async def（B6，long-task fixture）
    long_tool = registry.get("long-task",
                             source_dir=fixtures_dir / "fya" / "tools")
    assert getattr(long_tool, "background", False) is True
    caller.messages.clear()
    r4 = await long_tool({"seconds": 0.01}, caller=caller)
    assert r4.status == "pending" and r4.output is None
    assert await _drain_until(lambda: bool(caller.messages))

    # 负例：非 bool → FormatError；cli/request/mcp 声明 → FormatError（B13）
    cases = {
        "bad-bool": ("type: script\nbackground: \"yes\"\n"
                     "callable: ./impl.py::fn\n"),
        "bad-cli": ("type: cli\ncommand: echo hi\n"
                    "args:\n  word:\n    type: string\n"
                    "background: true\n"),
        "bad-req": ("type: request\nurl: http://127.0.0.1:1\n"
                    "args:\n  q:\n    type: string\n"
                    "background: true\n"),
        "bad-mcp": ("type: mcp\ncommand: nope\nbackground: true\n"),
    }
    for name, fya_text in cases.items():
        d = tmp_path / name
        d.mkdir()
        (d / "TOOL.fya").write_text(fya_text, encoding="utf-8")
        if name == "bad-bool":
            (d / "impl.py").write_text(
                "async def fn(x: str) -> dict:\n"
                "    return {'ok': x}\n", encoding="utf-8")
        with pytest.raises(FormatError):
            ToolRegistry().get(name, source_dir=tmp_path)

    # 手写 CliTool 子类声明 background → 被忽略（不后台化、不报错，B13）
    class IgnoredBgCli(CliTool):
        background = True

    cli = IgnoredBgCli(
        definition=ToolDefinition(
            name="cli-x", description="",
            params_schema={"word": {"type": "string"}}),
        command="echo hi")
    r5 = await cli({"word": "hi"}, caller=caller)
    assert r5.status == "completed"   # 同步执行到底，未后台化


# ---------------------------------------------------------------------------
# 组 9：返回 Task 路径回归（注册表强引用）
# ---------------------------------------------------------------------------


async def test_g9_task_return_path_regression():
    """execute 返回 Task → pending 收据 + 三结局投递 + 新注册表强引用。"""
    caller = _FakeCaller()
    gate = asyncio.Event()

    class TaskTool(Tool):
        definition = ToolDefinition(
            name="task-tool", description="返回 Task",
            params_schema={"text": {"type": "string"}})

        async def execute(self, *, text: str) -> asyncio.Task:
            async def _bg() -> str:
                await gate.wait()
                return text.upper()

            self.task = asyncio.create_task(_bg())
            return self.task

    tool = TaskTool()
    r = await tool({"text": "hi"}, caller=caller)
    assert r.status == "pending" and r.output is None
    assert r.background_task_id in caller._background_tasks   # 注册表强引用（补既有缺口）
    gate.set()
    assert await _drain_until(lambda: bool(caller.messages))
    msg = caller.messages[0]
    assert msg.kind is MessageKind.EVENT and msg.source == "tool_result"
    assert any("HI" in t for t in _texts(msg))
    assert await _drain_until(
        lambda: r.background_task_id not in caller._background_tasks)

    # 异常结局：标注块 + 错误文本块（与同步 error 同语义）
    class FailTaskTool(Tool):
        definition = ToolDefinition(
            name="fail-task", description="失败", params_schema={})

        async def execute(self) -> asyncio.Task:
            async def _bg() -> str:
                raise RuntimeError("后台炸了")
            return asyncio.create_task(_bg())

    r2 = await FailTaskTool()({}, caller=caller)
    assert r2.status == "pending"
    assert await _drain_until(lambda: bool(caller.messages))
    assert any("后台炸了" in t for t in _texts(caller.messages[-1]))


# ---------------------------------------------------------------------------
# 组 10：查询 API 与 cancel 边界（S12）
# ---------------------------------------------------------------------------


async def test_g10_query_api_and_cancel_boundary(tmp_path):
    """查询 API 快照；Agent.cancel()/cancel_by_tag 不取消后台 Task（S12）。"""
    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    script_provider(provider, text_response("ok"))
    agent = await runtime.create_agent("test-agent")
    gate = asyncio.Event()

    async def gen(*, text: str):
        yield {"status": "started"}
        await gate.wait()

    tool = _make_tool(gen)
    try:
        r = await tool({"text": "x"}, caller=agent)
        tid = r.background_task_id
        # 快照：复制 dict，不返回内部引用
        snap = agent.get_background_tasks()
        assert snap == agent._background_tasks
        snap.clear()
        assert agent.get_background_tasks()   # 清空快照不影响注册表
        assert agent.get_background_task(tid) is not None
        assert agent.get_background_task("nope") is None
        # S12：cancel()/cancel_by_tag 只置位 _executions 协作信号——不取消后台 Task
        await agent.cancel()
        agent.cancel_by_tag("whatever")
        assert agent.get_background_task(tid) is not None   # 仍在册
        # 显式取消收尾
        assert agent.cancel_background_task(tid) is True
        assert await _drain_until(
            lambda: tid not in agent._background_tasks)
    finally:
        await runtime.shutdown()
