"""阶段 3 tool 执行族（W15–W16）测试：测试清单 32、33、34。

覆盖 ``_infer_from_execute``（签名 → 模型；缺标注报错；caller/*args/**kwargs
跳过）、``Tool.__call__`` 调度层真身（Task 包装 pending 收据 + 固定 watcher
EVENT 入队；内部校验在 try 之外上抛）。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from flowing.errors import MissingSchemaError
from flowing.message import MessageKind, TextBlock
from flowing.tool import (
    ScriptTool,
    Tool,
    ToolDefinition,
    _infer_from_execute,
)


class _FakeCaller:
    """最小调用方替身：收集 enqueue_message 投递的消息。"""

    def __init__(self) -> None:
        self.messages: list = []

    async def enqueue_message(self, msg) -> str:
        self.messages.append(msg)
        return msg.id


async def _drain_until(predicate, *, attempts: int = 100) -> bool:
    """让事件循环走到 predicate 成立（watcher 的 ensure_future 链需若干 tick）。"""
    for _ in range(attempts):
        await asyncio.sleep(0.01)
        if predicate():
            return True
    return False


# ---------------------------------------------------------------------------
# _infer_from_execute（清单 32）
# ---------------------------------------------------------------------------


class TestInferFromExecute:
    def test_t32_missing_annotation_raises(self):
        """清单 32：execute 参数缺类型标注（且无 args_model）→ MissingSchemaError。"""

        class BadTool(ScriptTool):
            description = "d"

            async def execute(self, *, order_id):
                return order_id

        with pytest.raises(MissingSchemaError):
            BadTool()

    def test_t32_caller_and_variadics_skipped(self):
        """清单 32：caller / *args / **kwargs 不进 schema。"""

        class GoodTool(ScriptTool):
            description = "d"

            async def execute(self, *args, a: int, caller, **kwargs):
                return a

        tool = GoodTool()
        assert set(tool.definition.params_schema) == {"a"}

    def test_infer_required_and_optional(self):
        """签名 → 模型：无默认必填、有默认可选。"""

        async def fn(x: int, y: str = "d") -> None:
            ...

        model = _infer_from_execute(fn)
        with pytest.raises(ValidationError):
            model.model_validate({})
        assert model.model_validate({"x": 1}).y == "d"


# ---------------------------------------------------------------------------
# ScriptTool.__init__（W17，清单 31 + 名字推断 / 描述回退链）
# ---------------------------------------------------------------------------


class TestScriptToolInit:
    def test_t31_definition_mutex_warns_and_wins(self, caplog):
        """清单 31：同时声明 args_model 与 definition → 告警日志 +
        以显式 definition 为准。"""
        from pydantic import BaseModel

        class SomeArgs(BaseModel):
            x: int

        class BothTool(ScriptTool):
            definition = ToolDefinition(
                name="both", description="显式声明", params_schema={})
            args_model = SomeArgs

            async def execute(self) -> str:
                return "x"

        tool = BothTool()
        assert tool.definition is BothTool.definition
        assert "互斥" in caplog.text

    def test_name_inferred_from_class_name(self):
        """name 缺省由类名 kebab 化推断；显式声明仅作一致性断言。"""

        class MakePayment(ScriptTool):
            description = "d"

            async def execute(self, *, amount: float) -> dict:
                return {}

        assert MakePayment().definition.name == "make-payment"

        class MakePaymentAssert(ScriptTool):
            name = "make-payment-assert"   # 与类名推断一致：合法
            description = "d"

            async def execute(self) -> str:
                return "x"

        assert MakePaymentAssert().definition.name == "make-payment-assert"

    def test_name_mismatch_raises(self):
        from flowing.errors import NameMismatchError

        class MakePaymentMismatch(ScriptTool):
            name = "other-name"
            description = "d"

            async def execute(self) -> str:
                return "x"

        with pytest.raises(NameMismatchError):
            MakePaymentMismatch()

    def test_description_fallback_chain(self):
        """description 三级回退链：显式 > 类 docstring 首段 > execute docstring。"""

        class DocstringTool(ScriptTool):
            """对指定订单发起支付。

            第二段不应进入描述。
            """

            async def execute(self) -> str:
                return "x"

        assert DocstringTool().definition.description == "对指定订单发起支付。"

        class ExecuteDocstringTool(ScriptTool):
            async def execute(self) -> str:
                """执行体的说明文字。"""
                return "x"

        tool = ExecuteDocstringTool()
        assert tool.definition.description == "执行体的说明文字。"

        class NoDocstringTool(ScriptTool):
            async def execute(self) -> str:
                return "x"

        assert NoDocstringTool().definition.description == ""


# ---------------------------------------------------------------------------
# Tool.__call__：Task 包装 + pending 收据 + 固定 watcher（清单 33）
# ---------------------------------------------------------------------------


class TestAsyncTaskDispatch:
    async def test_t33_pending_receipt_and_watcher_event(self):
        """清单 33：execute 返回 Task → pending 收据；Task 完成时固定 watcher
        产标注块 + 结果块的 EVENT 入队（source="tool_result"）。"""
        gate = asyncio.Event()

        class AsyncEchoTool(Tool):
            definition = ToolDefinition(
                name="async-echo", description="异步回显",
                params_schema={"text": {"type": "string"}})

            async def execute(self, *, text: str) -> asyncio.Task:
                async def _bg() -> str:
                    await gate.wait()
                    return text.upper()

                self.task = asyncio.create_task(_bg())
                return self.task

        tool = AsyncEchoTool()
        caller = _FakeCaller()
        result = await tool({"text": "hi"}, caller=caller)
        assert result.status == "pending"
        assert result.output is None and result.error is None
        assert caller.messages == []   # 收据路径不入队

        gate.set()
        await tool.task
        assert await _drain_until(lambda: bool(caller.messages))
        msg = caller.messages[0]
        assert msg.kind is MessageKind.EVENT
        assert msg.source == "tool_result"
        # 多块形态：标注块 + 结果块
        assert isinstance(msg.content[0], TextBlock)
        assert "async-echo" in msg.content[0].text
        assert any(isinstance(b, TextBlock) and "HI" in b.text
                   for b in msg.content[1:])

    async def test_t33b_async_failure_delivers_error_text(self):
        """清单 33 附：Task 异常 → 标注块 + 错误文本块（与同步 error 同语义）。"""

        class AsyncFailTool(Tool):
            definition = ToolDefinition(
                name="async-fail", description="异步失败", params_schema={})

            async def execute(self) -> asyncio.Task:
                async def _bg() -> str:
                    raise RuntimeError("后台炸了")

                return asyncio.create_task(_bg())

        tool = AsyncFailTool()
        caller = _FakeCaller()
        result = await tool({}, caller=caller)
        assert result.status == "pending"
        assert await _drain_until(lambda: bool(caller.messages))
        msg = caller.messages[0]
        assert msg.kind is MessageKind.EVENT and msg.source == "tool_result"
        assert any(isinstance(b, TextBlock) and "后台炸了" in b.text
                   for b in msg.content)


# ---------------------------------------------------------------------------
# Tool.__call__：内部校验（S-33，清单 34）
# ---------------------------------------------------------------------------


class TestInternalValidation:
    async def test_t34_validation_error_escapes_outside_try(self, caplog):
        """清单 34：聚合终值不满足 _args_model → ValidationError 上抛
        （不变成 ToolResult(error)，不进 LLM 可见文本），并记日志。"""

        class PayTool(ScriptTool):
            description = "支付"

            async def execute(self, *, amount: float) -> dict:
                return {"amount": amount}

        tool = PayTool()
        with pytest.raises(ValidationError):
            await tool({"amount": "not-a-float"})
        assert "内部校验失败" in caplog.text

    async def test_t34b_direct_subclass_lazy_model(self):
        """直接子类化 Tool 的逃生舱写法（无 __init__）：_args_model 就地
        按 execute 签名构建并缓存，校验语义不变。"""

        class DirectTool(Tool):
            definition = ToolDefinition(
                name="direct", description="d",
                params_schema={"n": {"type": "integer"}})

            async def execute(self, *, n: int) -> int:
                return n

        tool = DirectTool()
        with pytest.raises(ValidationError):
            await tool({"n": "x"})
        ok = await tool({"n": 3})
        assert ok.status == "completed" and ok.output == 3
