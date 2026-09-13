"""``flowing.tool.script`` —— ``ScriptTool`` 作者基类与 ``@flowing_tool`` 打标通道。

script 工具的两种定义路径：手写 :class:`ScriptTool` 子类，或
:func:`flowing_tool` 打标裸函数（经 ``_auto_generate_tool`` 提升为
ScriptTool 子类）。公开符号经 ``flowing.tool`` re-export。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import inspect
import logging

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from flowing.errors import MissingFieldError, NameMismatchError
from flowing.params import bridge_properties
from flowing.paths import infer_name, kebab_to_pascal

from flowing.tool.core import (
    TOOL_NAMING,
    Tool,
    ToolDefinition,
    _infer_from_execute,
    _whole_doc,
)

_logger = logging.getLogger("flowing.tool")

class ScriptTool(Tool):
    """script 工具的作者基类——类属性声明 + 框架自动生成 `ToolDefinition`。

    .. rubric:: 功能介绍

    工具作者不直接构造 `ToolDefinition`，而是在子类上声明
    ``name`` / ``description`` / ``args_model`` 类属性；``__init__`` 时
    框架自动生成 ``self.definition``。``name`` **必填**（不由类名 kebab
    推断——ScriptTool 禁止没有 name 字段）；手写子类须类体
    显式 ``name``（或显式 ``definition``），``.fya`` / ``.py`` 文件通道的
    规范名 = 文件身份（文件名/目录名，加载层注入，类体显式 ``name`` 与
    文件身份不符抛 :class:`flowing.errors.NameMismatchError`）。
    ``args_model`` 可省略——从 ``execute()`` 签名的类型标注与默认值构建
    模型。

    script 工具共有三条等价的定义通道：手写本类子类（主路径）；
    ``@flowing_tool`` 打标函数（框架自动提升为等价子类）；``.fya`` 的
    ``callable:`` 指针指向裸函数。规范名来源：手写通道 = 类体显式
    ``name``（必填）；文件通道（``.fya`` / ``.py`` / 打标函数）= 文件名 /
    目录名身份（声明面权威，加载层注入）。``description`` 按显式声明 >
    类 docstring 整体 > ``execute()`` docstring 整体的三级回退链取。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing import ScriptTool
        from pydantic import BaseModel, Field

        class PayArgs(BaseModel):
            # 声明即模型：LLM schema 与执行校验都从这里派生
            order_id: str = Field(description="订单 ID")
            amount: float = Field(ge=0.01)   # 约束直接进 schema 与校验

        class MakePayment(ScriptTool):
            \"\"\"对指定订单发起支付。仅在用户明确确认支付意图后调用。\"\"\"

            name = "make-payment"         # 必填：不再由类名推断
            description = "发起支付"
            args_model = PayArgs          # 参数声明（BaseModel 子类）

            async def execute(self, *, order_id: str, amount: float,
                              caller: Agent) -> dict:
                ...

    后台工具形态（async generator）：``execute`` 写成 async generator——
    第一个 ``yield`` 是「准备完成」标记（可空值），`Tool.__call__` 等待它
    作为 ``pending`` 收据（``tool_status="pending"`` 的 TOOL 消息带内容）；
    后续每个 ``yield`` 由框架后台驱动并逐段投递 EVENT 消息（LLM 可见）；
    首 yield 前只允许轻量准备，长任务必须放在首 yield 之后（违反的后果
    是收据延迟——作者责任）。「只要后台、不要中间报告」的普通 async
    ``execute`` 可声明类属性 ``background = True`` （仅 script 型生效）走
    同一 ``pending`` 通道：

    .. code-block:: python

        class RunWorkflowTool(ScriptTool):
            async def execute(self, *, path: str, caller: Agent, **args):
                wf = resolve_workflow(path)                     # 轻量准备
                yield {"status": "started", "workflow": path}   # ① 收据
                await wf.run(**args)                            # 长任务（后台）
                yield {"status": "done", "workflow": path}      # ② EVENT

    async generator 禁止带值 ``return`` （PEP 525 只允许裸 ``return``）
    ——最终呈现 = 最后一条 ``yield``；没有 ``yield`` 的函数不是 async
    generator（走普通执行路径）；``background = True`` 只对普通 async
    ``execute`` （单次返回）生效，async gen 无需标记。

    .. rubric:: 行为要点

    - 互斥规则：类属性声明（``name`` / ``description`` / ``args_model``）
      与手写 ``definition`` 类属性互斥；同时存在时框架发警告，以显式
      ``definition`` 为准。
    - 声明通道互斥：``callable:`` 指向的函数不允许带 ``@flowing_tool``
      装饰（打标 = 自动提升通道，``callable:`` = 显式指针通道，二选一）；
      违反抛 :class:`flowing.errors.FormatError`。
    - script 工具注册为全局单例（Runtime 一份，所有 Agent 共享）——同一
      份用户代码不应实例化多次。
    - 每个 ``.py`` 文件至多一个打标函数；与 Tool 子类同文件并存或含多个
      打标函数 → :class:`flowing.errors.AmbiguousToolError` （定向查找阶段
      判别）。
    - 类属性在 ``__init__`` 前已就绪，无时序问题。

    :raises flowing.errors.MissingSchemaError: 既无 ``args_model`` 声明、
      ``execute()`` 参数又缺类型标注时（签名构建不出字段类型）。
    :raises flowing.errors.NameMismatchError: 文件通道（``.fya`` / ``.py``
      加载）类体显式 ``name`` 与文件身份（文件名/目录名）不符时（防错位）；
      手写直接构造通道无此错误（``name`` 缺失抛
      :class:`flowing.errors.MissingFieldError`）。

    .. seealso::

        - :func:`flowing.tool.flowing_tool` —— 打标函数通道。
        - :func:`flowing.tool._infer_from_execute` —— 签名推断的内部实现。
        - :mod:`flowing.params` —— 声明层规则与桥接子集。
    """

    name: str
    """规范工具名（kebab-case）。**必填**（不由类名 kebab
    化推断）：手写子类须类体显式声明；``.fya`` / ``.py`` 文件通道由加载层
    以文件身份（文件名/目录名）注入，类体显式 ``name`` 与文件身份不符抛
    :class:`flowing.errors.NameMismatchError`；与显式 ``definition`` 互斥。
    """
    description: str
    """工具描述。类属性声明；缺省时按三级回退链取：显式 ``description``
    > 类 docstring **整体** > ``execute()`` docstring **整体**。
    """
    args_model: type[BaseModel] | None
    """参数声明：Pydantic ``BaseModel`` 子类，声明即模型——LLM schema 由
    ``model_json_schema()`` 派生、执行校验即模型本身；``None`` 时由
    ``_infer_from_execute(self.execute)`` 从签名构建模型。
    """


    def __init__(self) -> None:
        """自动生成 ``self.definition`` （同步构造，不触网、不注册）。

        .. rubric:: 行为要点

        - 顺序：显式 ``definition`` 类属性存在 → 直接使用（同时声明
          ``name`` / ``description`` / ``args_model`` 时发告警日志，仍以
          显式 ``definition`` 为准——互斥规则）；否则要求类体显式
          ``name``（必填、不由类名推断；缺失抛
          :class:`flowing.errors.MissingFieldError`），``description`` 按
          三级回退链取（显式声明 > 类 docstring **整体** >
          ``execute()`` docstring **整体**，皆无 → 空串），``args_model`` 未声明时按
          ``self.args_model or _infer_from_execute(self.execute)`` 取值，
          最后生成 ``ToolDefinition`` （``params_schema`` 取
          ``args_model.model_json_schema()["properties"]``）。
        - 后置条件：``self.definition`` 非 ``None``；``self._has_caller``
          已按 ``execute`` 签名检测完毕。
        """
        cls = type(self)
        if "definition" in cls.__dict__:
            if any(key in cls.__dict__ for key in ("name", "description", "args_model")):
                # 互斥规则：与 name/description/args_model 同时声明 -> 告警日志，
                # 以显式 definition 为准
                _logger.warning(
                    "ScriptTool subclass %s declares both definition and "
                    "name/description/args_model — mutually exclusive rule: the explicit definition wins",
                    cls.__name__)
            self.definition = cls.__dict__["definition"]
        else:
            # name 必填（不由类名 kebab 推断——ScriptTool 禁止
            # 没有 name 字段）。类体显式 name（或经 .fya/.py 文件身份注入的
            # name）即规范名；只看本类 __dict__——继承来的 name 不参与
            # （与 agent 侧 _load_agent_from_py 的口径一致）
            explicit_name = cls.__dict__.get("name")
            if explicit_name is None:
                raise MissingFieldError(
                    "name", cls.__name__)   # FormatError 族：声明缺必填字段
            args_model = getattr(self, "args_model", None)
            if args_model is None:
                args_model = _infer_from_execute(self.execute)  # 从 execute 签名构建模型
            # description 三级回退链：显式声明 > 类 docstring 整体 >
            # execute() docstring 整体；皆无 -> 空串。注意用 cls.__doc__
            # 而非 inspect.getdoc(cls)——后者会继承基类 docstring
            description = getattr(cls, "description", None)
            if description is None:
                # 无显式 description 时回退 docstring **整体**（cleandoc 全文）
                description = (_whole_doc(cls.__doc__)
                               or _whole_doc(self.execute.__doc__) or "")
            self.definition = ToolDefinition(
                name=explicit_name, description=description,
                # 声明即模型：schema 从模型派生，但先收敛到桥接子集——
                # pydantic 的 model_json_schema() 会给 property 附 title 等
                # 展示键，直接进 definition 会在 agent 侧 schema_to_model
                # 桥接时被超子集校验拒绝（F1：工具声明三通道皆可用）。
                params_schema=bridge_properties(
                    args_model.model_json_schema()["properties"]))
        self._has_caller = "caller" in inspect.signature(self.execute).parameters
        self._execution = None
        # 创建时定内部校验模型——声明即模型，无需再编译；
        # 显式 definition 路径下若未带模型，就地从 execute 签名构建兜底
        self._args_model = getattr(self, "args_model", None) or _infer_from_execute(self.execute)


# ──────────────────────────────────────────────────────────────────
# @flowing_tool 打标通道
# ──────────────────────────────────────────────────────────────────



_FLOWING_TOOL_MARKS: set[Callable[..., Any]] = set()
"""进程级打标表（``@flowing_tool`` 登记处）。

发现机制是函数对象上的 ``__flowing_tool_name__`` 标记属性（``.py`` 命中后
模块扫描逐对象检查）；本表仅作打标行为的登记——import 期不需要 Runtime
存在，多 Runtime 安全。内部 API，不属稳定契约。
"""


def flowing_tool(fn: Callable[..., Any] | None = None, *,
                 name: str | None = None) -> Any:
    """``@flowing_tool`` 打标装饰器——把裸函数标记为 script 工具。

    .. rubric:: 功能介绍

    script 工具的两条定义通道之一（另一条是手写 `ScriptTool` 子类）。
    装饰器只打标不注册：在函数上记录标记（进程级打标表），实例化与注册
    推迟到工具被引用时由 :meth:`flowing.tool.ToolRegistry.get` 完成——
    import 期不需要 Runtime 存在，多 Runtime 安全。

    两种用法：``@flowing_tool`` （名字由文件名 / 目录名推断）或
    ``@flowing_tool("make-payment")`` （参数仅作一致性断言——必须与推断名
    一致，不符抛 :class:`flowing.errors.NameMismatchError`）。定义处的
    显式打标让「这个函数是工具」成为作者意图而非框架猜测。

    .. rubric:: 行为要点

    - 每个 ``.py`` 文件至多一个打标函数；与 Tool 子类同文件并存或含多个
      打标函数 → :class:`flowing.errors.AmbiguousToolError`。
    - 通道互斥：被 ``TOOL.fya`` 的 ``callable: {路径}::{函数名}`` 指向的
      函数不允许打标（显式指针通道与自动提升通道二选一），违反抛
      :class:`flowing.errors.FormatError`。
    - 名字推断：文件名去 ``.py`` 并 snake → kebab 规范化；``TOOL.py`` /
      ``tool.py`` 通用名时取目录名。
    - 不实例化 Tool、不触碰任何 Runtime / 注册表；返回原函数。

    :param fn: 被装饰函数（无参用法由 Python 装饰器协议传入）。
    :param name: 一致性断言参数；``None`` 时纯推断。

    .. seealso::

        - :class:`flowing.tool.ScriptTool` —— 另一条定义通道。
        - :meth:`flowing.tool.ToolRegistry.get` —— 消费点。
        - :class:`flowing.errors.NameMismatchError` —— 断言失败。
    """
    # 打标：fn.__flowing_tool_name__ = name（None = 纯推断）；登记进程级
    # 打标表；返回原函数；实例化/注册推迟到 get（import 期无 Runtime 依赖）
    if isinstance(fn, str):
        # @flowing_tool("make-payment") 位置形态（spec 使用示例）：字符串
        # 实参即一致性断言名（关键字形态 name=... 等价）
        name, fn = fn, None

    def _mark(f: Callable[..., Any]) -> Callable[..., Any]:
        f.__flowing_tool_name__ = name  # type: ignore[attr-defined]
        _FLOWING_TOOL_MARKS.add(f)
        return f

    return _mark(fn) if fn is not None else _mark


def _auto_generate_tool(fn: Callable[..., Any]) -> Tool:
    """把打标函数（``@flowing_tool``）包装为 `ScriptTool` 子类实例。
    内部 API，不属稳定契约。

    .. rubric:: 行为要点

    - 等价于 ``type("<PascalName>", (ScriptTool,), {"execute":
      staticmethod(fn)})`` 后实例化；元信息按「文件名 / docstring / 类型
      标注」提取（名字推断与装饰器参数断言规则见 :func:`flowing_tool`）。
    - :raises flowing.errors.MissingSchemaError: 参数缺类型标注。
    - :raises flowing.errors.AmbiguousToolError: 同一 ``.py`` 同时存在
      打标函数与 Tool 子类、或多个打标函数（由定向查找层抛出，不在本
      函数内）。

    .. seealso::

        - :class:`flowing.tool.ScriptTool` —— 两条定义路径的共同产物。
        - :func:`flowing_tool` —— 打标入口。
    """
    args_model = _infer_from_execute(fn)  # 元信息提取：签名构建模型
    # name ← 文件名去 .py 并 snake → kebab 规范化（TOOL.py/tool.py 通用名
    # 取目录名）；装饰器参数若给出仅作一致性断言（不符 -> NameMismatchError）
    name = infer_name(fn.__code__.co_filename, naming=TOOL_NAMING)
    declared = getattr(fn, "__flowing_tool_name__", None)
    if declared is not None and declared != name:
        raise NameMismatchError(declared, name, fn.__code__.co_filename)
    # description 三级回退链在打标函数形态下的落点：显式声明无通道
    # （装饰器无 description 形参）、无类 docstring —— 取函数 docstring 首段
    description = _whole_doc(fn.__doc__) or ""
    cls = type(kebab_to_pascal(name), (ScriptTool,), {
        "execute": staticmethod(fn),
        "name": name,
        "description": description,
        "args_model": args_model,
    })
    return cls()
