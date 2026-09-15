"""``flowing.tool`` —— 工具子系统：三层能力描述的 Tool 形态。

.. rubric:: 功能介绍

本子包汇出工具子系统的全部公开类型：可执行对象（:class:`Tool` 基类、
:class:`ScriptTool` 作者基类，以及三种按声明驱动的具体实现
:class:`McpTool` / :class:`CliTool` / :class:`RequestTool`）、LLM 可见
声明（:class:`ToolDefinition`）、Agent 级绑定（:class:`ToolEntry`）、
调用与结果（:class:`ToolCall` / :class:`ToolResult`，状态四值见
:data:`ToolStatus`）、全局注册表（:class:`ToolRegistry`）、工具类型
判别值（:data:`ToolType`）、打标装饰器（:func:`flowing_tool`），以及
结果归一与媒体族（:func:`normalize_output` / :func:`output_to_blocks`、
媒体载体 :class:`Image` / :class:`File` / :class:`Audio` /
:class:`Video`、转换器 :class:`MediaConverter` /
:func:`register_media_converter`——定义在 :mod:`flowing.media`，经本包
re-export）。

框架自带的出厂内置工具（核心 ``SubagentInvokeTool`` 与标准件
``FinishTool`` 等）定义在 :mod:`flowing.builtins`，不在本模块。

三层能力描述（Tool / 子 Agent / Skill 共享同一模式，三层各自独立
演化、互不侵入）：

.. list-table::
   :header-rows: 1

   * - 维度
     - Tool
     - 子 Agent
     - Skill
   * - 可执行对象
     - :class:`Tool` （``execute()``）
     - Agent 类
     - Skill 内容
   * - LLM 可见声明
     - :class:`ToolDefinition`
     - catalog XML 条目
     - catalog XML 条目
   * - Agent 级绑定
     - :class:`ToolEntry`
     - ``SubagentEntry``
     - ``SkillEntry`` （扩展）

绑定层独立存在的理由：同一个工具在不同 Agent 上可以呈现不同的 LLM
视图（如 ``FinishTool`` 在 ReviewerAgent 上暴露 ``score`` / ``pass`` /
``issues``，在 PaymentAgent 上暴露 ``transaction_id`` / ``status`` /
``charged_amount``）——通过 `ToolEntry` 覆写实现，不改全局注册表、
不改工具本身。

本模块全部公开签名属跨版本稳定契约；``_`` 前缀符号为内部 API，不属
稳定契约。

.. rubric:: 全局约定（跨符号、影响使用的约定）

工具调用时序：LLM 在一次逻辑 Turn 中发出工具调用后，框架按固定顺序
处理。``Agent.tool_call`` 先 dispatch ``before_tool_call`` 钩子
（handler 可改写 `ToolCall` 的参数；把 `ToolResult` 放进 ``shortcut``
字段可跳过工具执行；``raise Intercepted`` 硬阻断，工具不执行、结果
为 ``blocked``），随后仅按别名查找 Agent 的工具绑定表（找不到抛
``UnknownToolError``）、聚合参数，经 ``Tool.__call__`` 执行，然后
dispatch ``on_tool_yields`` （工具本体产出的每一份非 blocked 结果：
同步一次；后台工具的收据、每个分段、终值与终止通知各一次——value
是携带 ``name`` / ``tool_call_id`` / ``production`` 元信息的
`ToolResult`，可改写 ``output`` 原料值），再 dispatch
``after_tool_call`` （可改写结果）并在返回前对结果做一次幂等归一
（已归一的值重复归一结果不变）。完整时序见 :mod:`flowing.agent`，钩子语义见 :mod:`flowing.hooks`。

``builtin::`` 命名空间：出厂内置工具注册在 ``builtin::`` 命名空间下。
裸名查找先查 ``default::`` 再查 ``builtin::``——插件 / 应用可以在
``default::`` 注册同名工具覆盖内置行为，被覆盖的条目仍可用
``builtin::xxx`` 全限定名显式引用。注册不等于可见：Agent 的工具目录
只包含它在 ``.fya`` 或 ``add_tool`` 中显式声明的条目；可写文件、
执行命令等危险工具必须由使用者显式声明，框架不会因为注册就把它们
暴露给 LLM。

参数优先级：指定值（``specified``，含注入表达式）最高，其次 LLM
传入参数，最后 schema 默认值。指定值对 LLM 不可见、不可被 LLM 覆盖
——固定值或宿主上下文注入的参数（``user_id`` / ``trace_id`` 等）用
指定值声明，这是防篡改的安全边界。注入表达式
``{{ self.inject('key') }}`` 在调用时以调用方 Agent 为上下文求值，
沿 provide-inject 链向根查找；链上找不到提供者抛
``MissingProvideError``。

工具业务错误是正常产物：``execute`` 抛普通异常 →
``ToolResult(status="error")``，LLM 可见、不触发任何错误钩子；``execute`` 内
``raise Intercepted`` → ``ToolResult.blocked``。参数 / 返回值中的
编程错误（参数缺类型标注、返回值含违禁块、返回不可转换对象等）
走框架错误通道直接上抛（``ValueError`` / ``FormatError`` 等），不包
成 LLM 可见结果。

审批等策略在 ``before_tool_call`` handler 内实现：``requires_approval``
之类的字段只是 ``.fya`` 的非保留字段，原样成为工具对象的普通属性，
框架不解析、不据此做任何自动行为。handler 内 ``await`` 审批——通过
返回原值、改参数返回改写后的 `ToolCall`、拒绝 ``raise Intercepted``。

路径约定：工具定义文件（``.fya`` / ``.py``）的定位、``@/`` 项目根
锚定与命名空间派生规则见 :meth:`ToolRegistry.get`；``@/`` 上下文按
asyncio Task 隔离（见 :mod:`flowing.runtime`）。

.. rubric:: 使用示例

.. code-block:: python

    from flowing import ScriptTool
    from pydantic import BaseModel

    class PayArgs(BaseModel):
        order_id: str
        amount: float

    class MakePayment(ScriptTool):
        \"\"\"对指定订单发起支付。仅在用户明确确认支付意图后调用。\"\"\"

        name = "make-payment"      # 必填：不再由类名推断
        args_model = PayArgs

        async def execute(self, *, order_id: str, amount: float) -> dict:
            return {"tx": "fake", "order_id": order_id, "amount": amount}

    tool = MakePayment()
    result = await tool({"order_id": "o1", "amount": 9.9})
    assert result.status == "completed"

.. seealso::

    - :mod:`flowing.agent` —— ``Agent.tool_call`` 的完整工具调用时序。
    - :mod:`flowing.hooks` —— ``before_tool_call`` / ``after_tool_call``
      钩子语义。
    - :mod:`flowing.builtins` —— 出厂内置工具。
    - :mod:`flowing.params` —— 参数声明与 schema 桥接。
    - :mod:`flowing.message` —— 工具调用与结果的落树形态。
"""

from flowing.media import (
    Audio,
    File,
    Image,
    MediaConverter,
    Video,
    normalize_output,
    output_to_blocks,
    register_media_converter,
    _MEDIA_CONVERTERS,   # 私有符号的有意 re-export（测试与扩展经 flowing.tool 取用）
)
from flowing.tool.cli import CliTool
from flowing.tool.core import (
    TOOL_NAMING,   # 有意 re-export
    Tool,
    ToolCall,
    ToolDefinition,
    ToolEntry,
    ToolResult,
    ToolStatus,
    ToolType,
    _apply_param_aliases,   # 有意 re-export
    _infer_from_execute,   # 有意 re-export
)
from flowing.tool.mcp import McpTool
from flowing.tool.registry import ToolRegistry
from flowing.tool.request import RequestTool
from flowing.tool.script import (
    ScriptTool,
    flowing_tool,
    _auto_generate_tool,   # 有意 re-export
    _FLOWING_TOOL_MARKS,   # 有意 re-export
)

__all__ = [
    "Tool",
    "ScriptTool",
    "McpTool",
    "CliTool",
    "RequestTool",
    "ToolDefinition",
    "ToolEntry",
    "ToolCall",
    "ToolResult",
    "ToolRegistry",
    "ToolStatus",
    "ToolType",
    "Image",
    "File",
    "Audio",
    "Video",
    "MediaConverter",
    "register_media_converter",
    "normalize_output",
    "output_to_blocks",
    "flowing_tool",
]
