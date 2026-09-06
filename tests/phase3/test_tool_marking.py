"""阶段 3 tool 打标与提升（W18）单元测试。

覆盖 ``flowing_tool``（两种用法打标、只打标不注册、返回原函数）与
``_auto_generate_tool``（打标函数 → ScriptTool 子类实例；name 文件名推断 +
装饰器参数一致性断言；description 函数 docstring 首段；execute 真实可调）。
注册表消费侧（``ToolRegistry.get`` 的 .py 提升路径）见
``test_tool_registry.py``（清单 35–37）。
"""

from __future__ import annotations

import pytest

from flowing.errors import NameMismatchError
from flowing.tool import (
    ScriptTool,
    _FLOWING_TOOL_MARKS,
    _auto_generate_tool,
    flowing_tool,
)

# 本文件内定义的打标函数，其 co_filename 即本文件——推断名 = 本文件名去
# .py 后 snake→kebab（"test-tool-marking"）
INFERRED_FROM_THIS_FILE = "test-tool-marking"


class TestFlowingToolMarking:
    def test_bare_decorator_marks_and_returns_same_fn(self):
        """@flowing_tool 无参用法：返回原函数、打 None 标、登记打标表。"""

        @flowing_tool
        async def some_tool(x: int) -> int:
            return x

        assert some_tool.__flowing_tool_name__ is None   # 纯推断
        assert some_tool in _FLOWING_TOOL_MARKS
        assert some_tool.__name__ == "some_tool"   # 原函数未被包装

    def test_positional_and_keyword_name_forms(self):
        """@flowing_tool("name") 位置形态与 name= 关键字形态等价。"""

        @flowing_tool("some-name")
        def fn_positional(x: int) -> int:
            return x

        @flowing_tool(name="some-name")
        def fn_keyword(x: int) -> int:
            return x

        assert fn_positional.__flowing_tool_name__ == "some-name"
        assert fn_keyword.__flowing_tool_name__ == "some-name"


class TestAutoGenerateTool:
    def test_promotes_to_script_tool_instance(self):
        """打标函数 → ScriptTool 子类实例：name 文件名推断、description
        取函数 docstring 整体（0904）、args_model 从签名构建。"""

        @flowing_tool
        async def promoted(order_id: str, amount: float = 0.01) -> dict:
            """对指定订单发起支付。

            第二段同样进入描述（整体回退）。
            """
            return {"tx": "fake", "order_id": order_id}

        tool = _auto_generate_tool(promoted)
        assert isinstance(tool, ScriptTool)
        assert type(tool).__name__ == "TestToolMarking"   # kebab → Pascal
        assert tool.definition.name == INFERRED_FROM_THIS_FILE
        assert tool.definition.description == "对指定订单发起支付。\n\n第二段同样进入描述（整体回退）。"
        assert set(tool.definition.params_schema) == {"order_id", "amount"}
        # 无 default → 必填；有 default → 可选（schema 不带 default 键）
        assert "default" not in tool.definition.params_schema["order_id"]
        assert tool.definition.params_schema["amount"]["default"] == 0.01

    async def test_promoted_tool_is_callable(self):
        """提升产物经 Tool.__call__ 真实执行（非 mock）。"""

        @flowing_tool
        async def runnable(x: int) -> int:
            """可运行的打标函数。"""
            return x * 2

        tool = _auto_generate_tool(runnable)
        result = await tool({"x": 21})
        assert result.status == "completed" and result.output == 42

    def test_decorator_name_assertion(self):
        """装饰器参数与文件名推断名不符 → NameMismatchError（清单 36 的
        函数级形态；注册表级形态见 test_tool_registry.py）。"""

        @flowing_tool("totally-other-name")
        def mismatched(x: int) -> int:
            return x

        with pytest.raises(NameMismatchError) as exc_info:
            _auto_generate_tool(mismatched)
        assert exc_info.value.declared == "totally-other-name"
        assert exc_info.value.inferred == INFERRED_FROM_THIS_FILE

    def test_no_docstring_falls_back_to_empty(self):
        @flowing_tool
        def nodoc(x: int) -> int:
            return x

        assert _auto_generate_tool(nodoc).definition.description == ""
