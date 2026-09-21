"""``flowing.tool.core`` —— Tool 基类与调用四件：``ToolCall`` / ``ToolResult`` / ``ToolDefinition`` / ``ToolEntry``。

三层能力描述的 Tool 形态核心：可执行对象（:class:`Tool` 基类）、LLM 可见
声明（:class:`ToolDefinition`）、Agent 级绑定（:class:`ToolEntry`）、
调用与结果（:class:`ToolCall` / :class:`ToolResult`，状态四值见
:data:`ToolStatus`、类型判别值 :data:`ToolType`）。另承载子系统共享的
命名规则表 :data:`TOOL_NAMING` 与 execute 签名建模入口
``_infer_from_execute``（内部 API）。公开符号经 ``flowing.tool``
re-export；子系统的全局约定（调用时序、``builtin::``
命名空间、参数优先级、错误通道）见 ``flowing.tool`` 包 docstring。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import asyncio
import contextlib
import inspect
import logging
import warnings

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError, create_model

from flowing.errors import FormatError, Intercepted, MissingSchemaError
from flowing.media import normalize_output, output_to_blocks
from flowing.message import (
    ContentBlock,
    Message,
    MessageKind,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
)
from flowing.params import _coerce, apply_param_overrides
from flowing.parsable import Parsable
from flowing.paths import NamingRules

if TYPE_CHECKING:
    from flowing.agent import Agent, Execution
    from flowing.runtime import Runtime

_logger = logging.getLogger("flowing.tool")


def _strip_unsupported_background(tool: "Tool") -> None:
    """非 script 型工具的 ``background`` 声明处置（内部 API，不属稳定契约）。

    ``background`` 后台化标记仅 script 型受支持（script 经类属性 / ``.fya``
    落属性；``.fya`` 的非 script 型声明在加载期 ``FormatError``）。三个具体
    工具类（cli / request / mcp）构造尾部统一调本函数：发现 truthy
    ``background`` 属性 → 告警「不支持」并强制置 ``False``。
    """
    if getattr(tool, "background", False):
        warnings.warn(
            f"background is not supported for {type(tool).__name__} (only script tools); forced to False")
        tool.background = False

ToolStatus = Literal["completed", "pending", "blocked", "cancelled", "error"]
"""工具执行结果的状态四值。

- ``"completed"``：正常完成，返回值承载在 ``output`` 字段。
- ``"pending"``：异步收据。三种形态产生：``execute`` 返回
  ``asyncio.Task``；``execute`` 是 async generator（首个 ``yield``
  即收据内容）；``background = True`` 标记的普通 async ``execute``。
  框架只回收据，真正结果稍后以独立消息到达（不阻塞逻辑 Turn）。
- ``"blocked"``：被钩子硬阻断的产物——``before_tool_call`` 拦截
  （工具未执行）／ ``after_tool_call`` 拦截（工具已执行完、结果被丢弃）
  ／ ``execute`` 内 ``raise Intercepted`` （执行被中断于中途）；只能经
  ``ToolResult.blocked`` 工厂产生。
- ``"error"``：执行抛普通异常的正常产物——LLM 可见、不触发任何错误
  钩子（核心错误钩子仅 ``on_provider_error``，见 :mod:`flowing.hooks`）。
"""

ToolType = Literal["script", "mcp", "cli", "request"]
"""四种工具类型判别值（``.fya`` 的 ``type:`` 字段取值）。

- ``"script"``：Python callable（`ScriptTool` 子类 / 裸函数 /
  ``callable:`` 指针指向），全局单例注册——所有 Agent 共享同一实例。
- ``"mcp"``：MCP 服务器（本地 stdio 进程或远程端点），每个声明
  独立实例。
- ``"cli"``：命令行工具（Jinja2 命令模板），每个声明独立实例。
- ``"request"``：HTTP/HTTPS 请求工具，每个声明独立实例。

script 是单例是因为其业务逻辑是用户代码，不应实例化多次；其余三类
只是参数化配置，没有用户代码。
"""

TOOL_NAMING = NamingRules(
    suffixes=(".tool.fya", ".fya", ".py"),
    generic_names=frozenset({"TOOL.fya", "TOOL.py", "tool.fya", "tool.py"}),
)
"""Tool 资源的路径形态身份名推断规则表（:class:`flowing.paths.NamingRules`）。

``TOOL.fya`` / ``TOOL.py`` / ``tool.fya`` / ``tool.py`` 通用文件名命中时
身份名取目录名；其余按后缀剥离取文件名（``.tool.fya`` 先于 ``.fya``），
结果经 snake → kebab 规范化。使用方：:func:`flowing.parser.normalize_entries`
（``naming=TOOL_NAMING``）与 name 断言的推断侧（`ToolRegistry.get`）。

.. seealso:: :data:`flowing.runtime.AGENT_NAMING`、
    :data:`flowing.plugins.skills.SKILL_NAMING`
"""


def _infer_from_execute(execute: Callable[..., Any]) -> "type[BaseModel]":
    """从 ``execute()`` 签名构建 Pydantic 参数模型。内部 API，不属稳定契约。

    .. rubric:: 行为要点

    - 来源：参数类型标注 → 字段类型（``str`` / ``int`` / ``float`` /
      ``bool`` / ``list`` / ``dict`` 等直接映射，复杂标注交 Pydantic）；
      默认值 → 字段 default（有默认 → 可选，无 → 必填）；``caller``
      参数跳过（框架注入，不是 LLM 参数）；``*args`` / ``**kwargs``
      形态跳过（不进 schema）。
    - 构建经 ``pydantic.create_model`` （与 ``.fya`` 桥接
      :func:`flowing.params.schema_to_model` 同一建模入口）。
    - :raises flowing.errors.MissingSchemaError: 任一业务参数缺类型标注
      （构建不出字段类型）。

    .. seealso::

        - :class:`flowing.tool.ScriptTool` —— 调用点。
    """
    fields: dict[str, Any] = {}
    for param_name, param in inspect.signature(execute).parameters.items():
        if param_name in ("self", "caller"):
            continue  # caller 为框架注入，不是 LLM 参数
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue  # **kwargs（如 FinishTool 动态字段）不进 schema
        if param.annotation is inspect.Parameter.empty:
            raise MissingSchemaError(f"parameter lacks a type annotation: {param_name}")
        if param.default is inspect.Parameter.empty:
            fields[param_name] = (param.annotation, ...)   # 无默认 → 必填
        else:
            fields[param_name] = (param.annotation, param.default)  # 有默认 → 可选
    ArgsModel: type[BaseModel] = create_model("InferredArgs", **fields)
    return ArgsModel


@dataclass
class ToolCall:
    """LLM 发出的单次工具调用——``before_tool_call`` 钩子的 value 类型。

    .. rubric:: 功能介绍

    `ToolCall` 是「LLM 想调什么」的纯数据载体：从 PROVIDER 消息的工具调用
    块（``ToolCallBlock``）解析而来，经 ``before_tool_call`` 钩子链传递，
    最终被解包为零散参数喂给 ``Tool.execute()``。`ToolCall` 只是调用意图，
    不是可执行对象；``execute()`` 不接收 `ToolCall` 整体入参。

    .. rubric:: 使用示例

    ``before_tool_call`` handler 统一签名 ``(agent, value)``——方法形态
    ``(self, tool_call)`` 的 ``self`` 即承载 Agent：

    .. code-block:: python

        from flowing import Intercepted, ToolResult, on

        @on("before_tool_call")
        async def _approve(self, tool_call):
            tool_call.args["request_id"] = self.node_id   # 改写参数
            if tool_call.name == "delete-file":
                raise Intercepted("删除文件需要人工审批")  # 硬阻断 → blocked
            if (hit := self._cache.get(tool_call.name)):
                tool_call.shortcut = ToolResult(           # 短路：跳过执行
                    status="completed", output=hit)
            return tool_call

    .. rubric:: 行为要点

    - handler 三种合法出口：返回（可能改写的）`ToolCall` / 把
      `ToolResult` 放进 ``shortcut`` 字段短路 / ``raise Intercepted``
      硬阻断；普通异常直接上抛，没有会捕获它的兜底钩子。
    - `ToolCall` 不做参数校验、不认识 specified——参数聚合全部发生在
      其后的 ``_normalize()`` （见 :mod:`flowing.agent`）。
    - ``args`` 只包含 LLM 原始传入值（键可能是别名）；hook 改写后出现的
      同名键会在 ``resolve()`` 中被 specified 覆盖（优先级见模块
      docstring 的参数优先级约定）。

    .. seealso::

        - :class:`flowing.tool.ToolResult` —— 调用的结果载体。
        - :class:`flowing.tool.ToolEntry` —— ``resolve()`` 的参数聚合。
        - :mod:`flowing.hooks` —— dispatch 算法与 ``shortcut`` 契约。
    """

    id: str
    """Provider 侧的调用 ID（如 OpenAI ``tool_call_id``）。它是与结果侧
    ``kind=TOOL`` 消息 ``tool_call_id`` 字段严格配对的依据，也是恢复时
    扫描孤立 tool_call 的匹配键。编程路径（cron / workflow / 手动构造）
    由调用方生成合成字符串——推荐形如 ``<来源类型名>-<随机 hex>``
    （workflow → ``workflow-…``、cron → ``cron-…``）的有语义前缀；
    仅作追踪，不参与配对。
    """
    name: str
    """LLM 看到的工具名——即 `ToolEntry.name_alias` （别名），不是规范名。
    ``Agent.tool_call()`` 仅按此名查找工具绑定表，不回退规范名。
    """
    args: dict[str, Any]
    """LLM 原始传入的参数字典（键为 LLM 可见名，可能是 ``param_aliases``
    中的别名）。``before_tool_call`` handler 可直接改写本字典；别名 → 规范名
    的映射、specified 聚合在 ``_normalize()`` 中发生，不在本对象上。
    """
    shortcut: "ToolResult | None" = None
    """钩子短路字段：初值 ``None``，钩子链以「非 ``None`` 即短路」门控。
    handler 把一个 `ToolResult` 放进本字段后返回——钩子链停止、工具默认
    执行被替代（该 `ToolResult` 直接作为本次调用的结果，典型场景：缓存
    命中跳过工具执行），``after_tool_call`` 照常触发。与 ``raise
    Intercepted`` 的区别：shortcut 是正常收场、结果由 handler 提供，
    Intercepted 是硬阻断、结果为 ``blocked``、``after_tool_call`` 不触发。
    shortcut 产物不经 ``Tool.__call__``，其 ``output`` 可为原料形态——
    由 ``Agent.tool_call`` 收尾的 `normalize_output` 幂等归一。
    """

    @classmethod
    def from_block(cls, block: ToolCallBlock) -> "ToolCall":
        """从消息层 `ToolCallBlock` 解析为 `ToolCall` （唯一官方转换点）。

        .. rubric:: 功能介绍

        剥离 ContentBlock 的通用字段（``type``），产出 ``ToolCall(id,
        name, args, shortcut=None)``。逻辑 Turn 循环遍历响应 content 的
        tool_call block 时，经本方法提取后传给 ``Agent.tool_call()``。

        .. rubric:: 行为要点

        - 纯函数：不修改入参 block，不产生副作用。
        - 返回值的 ``shortcut`` 恒为 ``None`` （短路是钩子链运行期状态，
          不来自消息）。

        .. seealso::

            - :class:`flowing.message.ToolCallBlock` —— 上游（消息层）。
            - :meth:`flowing.tool.ToolResult.as_message` —— 反向转换：
              结果 → 消息。
        """
        # shortcut 恒为 None 初值（短路是钩子链运行期状态，不来自消息）
        return cls(id=block.id, name=block.name, args=block.args)


@dataclass
class ToolResult:
    """工具执行结果——LLM 可见的调用回执。

    .. rubric:: 功能介绍

    `ToolResult` 是工具调用链的最终产物：无论成功、失败、异步还是被
    阻断，对 LLM 与消息树都呈现为同一个结构。核心结果字段为
    ``status`` / ``output`` / ``error`` 三者；``output`` 是唯一结果字段，
    构造期收原料（基础值 / 载体四类 / 裸 ``bytes`` / ``Path`` / 第三方
    库对象均可，经 `normalize_output` 归一）。``name`` / ``tool_call_id`` /
    ``production`` 是产出元信息（哪个工具的哪次调用、以哪种形态产出），
    由 ``Agent.tool_call`` 与后台投递驱动接线，供 ``on_tool_yields``
    钩子 handler 过滤与关联。

    .. rubric:: 使用示例

    工具作者通常不直接构造 `ToolResult`——框架自动包装：

    .. code-block:: python

        async def execute(self, *, order_id: str, caller: Agent) -> dict:
            return {"tx": "abc"}      # → ToolResult(status="completed",
                                      #              output={"tx": "abc"})

        async def execute(self, *, url: str):
            return [f"已截取 {url}", Image(path=png_path)]   # 媒体返回

    .. rubric:: 行为要点

    - ``status="error"`` 是正常产物而非异常：LLM 应当看到工具失败并自行
      决策（重表述、换工具、向用户报告），因此 error 结果不触发任何错误
      钩子——核心错误钩子仅 ``on_provider_error`` （机制 vs 策略）。
    - ``status="blocked"`` 与 ``"error"`` 区分：blocked 表示工具根本没
      执行（被审批 / 守卫阻断），error 表示执行了但失败；二者对 LLM 的
      语义与审计含义不同，不可合并。``status == "blocked"`` 的实例只能
      经 `ToolResult.blocked` 工厂产生——来源：``before_tool_call`` /
      ``after_tool_call`` 拦截（工具未执行或结果被丢弃），或 ``execute``
      内主动抛出 ``Intercepted`` （执行被硬阻断于中途）。
    - ``pending`` 承载异步边界（三种形态：返回 ``asyncio.Task`` /
      async generator 首 yield / ``background`` 标记）：框架只回收据，
      不阻塞逻辑 Turn 等待异步任务。
    - 出 ``Agent.tool_call`` 的 ``output`` 恒为五形态之一——``None`` /
      基础值 / 单块 / 纯基础 list / 混合 list（其中块只有 ``TextBlock`` /
      ``StructBlock`` / ``MediaBlock``）；归一点 = `Tool.__call__` +
      ``Agent.tool_call`` 收尾（幂等再归一，封 shortcut / 钩子改写两缝）。
    - 配对元数据（``tool_call_id`` / ``tool_status``）在消息层，不在本
      对象上（见 `as_message`）。
    - `ToolResult` 不携带 trace 等观测数据——观测走钩子与快照层，不进
      LLM 可见结构。例外：``duration`` 是预留字段（当前实现不写入，恒为
      ``None``），即使有值也不进入 ``as_message`` 产物（LLM 不可见）。
    - 边缘情况：深层埋藏的非 JSON 对象归一化期放行，`as_message` 塑形时
      ``StructBlock`` 构造校验失败 → ``ValueError`` （诚实失败点）。

    .. seealso::

        - :class:`flowing.tool.ToolCall` —— 调用意图载体。
        - :func:`flowing.tool.normalize_output` —— 归一化（幂等）。
        - :func:`flowing.tool.output_to_blocks` —— 塑形统一出口。
        - :meth:`flowing.tool.Tool.__call__` —— 自动包装的调度层。
        - :mod:`flowing.errors` —— ``Intercepted`` 与普通异常的边界。
    """

    status: ToolStatus
    """执行状态四值之一，见 `ToolStatus`。
    """
    output: Any = None
    """唯一结果字段。构造期收原料；经 `normalize_output` 归一后、出
    ``Agent.tool_call`` 恒为五形态之一。``completed`` 时承载返回值；
    ``pending`` 时为 ``None`` （收据）；``error`` 时可为 ``None``；
    ``blocked`` 时为 ``None`` 或阻断原因 str。
    """
    error: str | None = None
    """错误描述，仅 ``status == "error"`` 时有值。对 LLM 可见（LLM 应能
    据此自我纠正）；不触发错误钩子。
    """
    duration: float | None = None
    """执行时长（秒）——预留字段，当前实现不写入（恒为 ``None``）。即使
    有值也不进入 ``as_message`` 产物（LLM 不可见）。
    """
    background_task_id: str | None = None
    """后台任务注册键：``status="pending"`` 且经
    `Agent.track_background_task` 注册时非 ``None``；其余状态恒
    ``None``。调用方 / LLM 可据此按 id 取消或查询后台任务。
    """
    name: str | None = None
    """产出本结果的工具别名（LLM 命名空间）。由 ``Agent.tool_call`` 在
    工具执行返回后接线；``on_tool_yields`` 钩子的 ``match_on="name"``
    pattern 过滤依据。未经 ``Agent.tool_call`` 管线的结果为 ``None``。
    """
    tool_call_id: str | None = None
    """配对锚：产出本结果的 ``ToolCall.id``。由 ``Agent.tool_call`` 在
    工具执行返回后接线（先于 ``as_message`` 的消息字段接线）；后台
    分段 / 终值携带同一 id，供 handler 关联同一次调用。
    """
    production: str | None = None
    """产出形态四值：``"sync"`` （同步结果）/ ``"receipt"`` （后台工具
    收据）/ ``"segment"`` （后台流分段）/ ``"final"`` （Task 终值与
    终止通知——异常 / 取消）。``"final"`` 仅覆盖可明确判定的流终止
    事件：async generator 正常耗尽不产生 ``"final"`` （最后一个
    yield 只能在耗尽后追认，已作为 ``"segment"`` 投递），handler 不得
    依赖「每个异步工具都有 final」做收尾；需要收尾标记的工具由作者
    自行 yield 终态标记。
    """

    @classmethod
    def blocked(cls, reason: str | None = None) -> "ToolResult":
        """构造「被钩子硬阻断」的结果（``status="blocked"`` 的唯一来源）。

        .. rubric:: 功能介绍

        当 ``before_tool_call`` / ``after_tool_call`` 链中任一 handler
        ``raise Intercepted``，或 ``execute()`` 内部主动抛出
        ``Intercepted`` 时，框架捕获 ``Intercepted`` 并调用本工厂生成
        阻断结果：``before_tool_call`` 拦截时工具未执行；``after_tool_call``
        拦截时工具已执行完、结果被丢弃；``execute`` 内拦截时执行被中断
        于中途。统一由本工厂生成，保证所有阻断路径的产物结构一致（LLM
        可据此向用户说明「该操作被拦截」而不是「执行失败」）。

        :param reason: 阻断原因（通常取 ``Intercepted`` 的消息），LLM 可见。
        :return: ``status="blocked"``、``error`` 为 ``None`` 的 `ToolResult`。

        .. rubric:: 行为要点

        - 后置条件：返回实例满足 ``status == "blocked"`` 且 ``error is
          None``；``reason`` 非空时作为 ``output`` （str 基础值），
          ``as_message`` 塑形为 ``[TextBlock(reason)]``——LLM 可见形态即
          文本块。
        - 本工厂不记录审计日志——审计由 handler 或快照层负责。

        .. seealso::

            - :class:`flowing.errors.Intercepted` —— 触发本工厂的阻断信号异常。
        """
        return cls(status="blocked", output=reason if reason else None, error=None)

    def as_message(self, tool_call_id: str, *, source: str | None = None) -> Message:
        """把本结果塑形为 ``kind=TOOL`` 的消息，供落消息级树。

        .. rubric:: 功能介绍

        逻辑 Turn 收尾时，框架将每次工具调用的 `ToolResult` 经本方法转为
        一条 ``Message(kind=TOOL, ...)``，以 ``parent_id`` 链入消息级树
        并在消息完整后 append 落盘。配对元数据上移到消息字段
        （``tool_call_id`` / ``tool_status``），``content`` 只含纯内容块
        ——塑形（五形态 → content 块列表）委托模块级统一出口
        `output_to_blocks`（与后台投递驱动、`invoke_subagent` 交付段
        消费同一实现）。

        ``tool_call_id`` 在本方法接线：`Agent.tool_call` 管线是全库唯一
        同时持有 ``ToolCall`` 与 `ToolResult` 的点，由它在收尾转换时传入
        ``tool_call.id``，完成与 ``ToolCallBlock.id`` 的严格配对
        （``ToolResult.tool_call_id`` 字段是同一 id 在结果本体上的副本，
        供产物级钩子 handler 使用）。

        :param tool_call_id: 配对的目标调用 id（``ToolCall.id`` /
          ``ToolCallBlock.id``）。
        :param source: 消息 ``source`` 字段；缺省填 ``"tool_result"``
          （与异步工具结果 EVENT 消息的既有约定同值；工具身份经
          ``tool_call_id`` 配对反查，不由 source 携带）。
        :return: ``kind=TOOL``、``tool_call_id`` / ``tool_status`` 接线、
          ``content`` 为 `output_to_blocks` 产物（纯内容块，无协议块）的
          新 `Message`；``synthetic=False``、``turn_end=False``。

        .. rubric:: 行为要点

        - 前置：``self.output`` 已是五形态之一（出 ``Agent.tool_call`` 恒
          成立）；深层埋藏的非 JSON 对象在塑形时 ``StructBlock`` 构造
          校验失败 → ``ValueError`` （框架错误通道）。
        - ``ToolResult.error`` 仅 ``status="error"`` 时非 ``None``，本方法
          无需自行判断，直接透传给 `output_to_blocks` （末尾追加
          ``TextBlock(error)``）。
        - 本方法不负责 append 落盘与 ``parent_id`` 接线——那是
          ``Agent._append_message`` 的职责；本方法只产出未接线的 `Message`。
        - 边缘情况：``status="pending"`` 也产生消息（收据消息）——返回
          Task 路径 ``output=None`` → ``content=[]`` （空 tool_result 的
          API 层兜底属 adapter 职责）；async gen 路径 ``output=首 yield``
          → content 带内容，并（注册键非 ``None`` 时）末尾附加「后台任务
          ID」文本块（不动作者 yield 的内容）；异步任务真正完成时的结果
          由框架另行产生多块 EVENT 消息，与本收据互不覆盖。

        .. seealso::

            - :func:`flowing.tool.output_to_blocks` —— 塑形统一出口。
            - :class:`flowing.message.Message` —— 消息级树的节点结构。
            - :meth:`flowing.agent.Agent._append_message` —— 落树时序。
        """
        # 塑形统一出口 output_to_blocks；配对元数据（tool_call_id /
        # tool_status）在消息字段，content 只含纯内容块
        blocks = output_to_blocks(self.output, error=self.error)  # error 仅 error 态非 None，直接透传
        if self.status == "pending" and self.background_task_id:
            # pending 收据附加「后台任务 ID」块（不动作者 yield 的内容——
            # 附加块而非并入，避免污染作者数据；LLM/调用方可据此按 id 引用）
            blocks = [*blocks,
                      TextBlock(text=f"background task ID: {self.background_task_id}")]
        return Message(
            kind=MessageKind.TOOL,
            tool_call_id=tool_call_id,   # 由 Agent.tool_call 管线接线（ToolCall.id），见 docstring
            tool_status=self.status,
            content=blocks,
            # source 缺省 "tool_result"（与异步 EVENT 结果同值）
            source=source if source is not None else "tool_result",
            synthetic=False,
            turn_end=False,
        )


@dataclass
class ToolDefinition:
    """LLM 可见的工具声明——可序列化的纯数据，不含任何执行逻辑。

    .. rubric:: 功能介绍

    三层能力描述中的「LLM 可见声明」层：字段为 ``name`` / ``description`` /
    ``params_schema`` / ``output_schema`` / ``strict``。它出现在
    ``Context.tools`` 中，是 Provider adapter 组装各家 function-calling
    schema 的唯一来源（adapter 为白名单语义——只取 ``name`` /
    ``description`` / ``params`` 等已知字段构造，多带的字段天然不会被
    映射）。

    工具作者通常不直接构造本类（`ScriptTool` 类属性声明 + 框架自动生成
    是主路径）；本类独立存在的理由是：同一工具在不同 Agent 上需要不同
    LLM 视图（见 `ToolEntry`），视图必须是可自由复制、覆写的纯数据，
    不能与执行对象纠缠。

    .. rubric:: 使用示例

    .. code-block:: python

        definition = ToolDefinition(
            name="finish",
            description="结束当前任务并返回结构化结果。",
            params_schema={                       # JSON Schema properties（展开式）
                "summary": {"type": "string", "default": "",
                            "description": "任务执行的简短摘要"},
            },
        )

    .. rubric:: 行为要点

    - 不变量：可 JSON 序列化（``params_schema`` 即 JSON Schema
      properties dict）；不得持有 callable、连接等运行时对象。
    - 本类不做参数校验——LLM 视角校验在 ``Agent._normalize``、内部校验
      在 `Tool.__call__` （校验模型于工具创建时编译）。
    - ``strict`` 语义：``True`` （默认）时工具层按 ``params_schema``
      严格约束 LLM 入参——LLM 传出未定义参数即校验失败；``False`` 时
      工具层不限制参数（未知键原样放行，用于「参数由下游自行校验」的
      工具）。

    .. seealso::

        - :meth:`flowing.tool.ToolDefinition.clone_with_overrides` —— 覆写
          生成新声明的唯一机制。
        - :class:`flowing.tool.ToolEntry` —— 覆写数据的持有者。
        - :mod:`flowing.params` —— 声明层规则与桥接子集。
    """

    name: str
    """规范工具名（kebab-case）。LLM 看到的名字以 `ToolEntry.name_alias`
    为准——本字段经 ``clone_with_overrides`` 覆写后才对 LLM 生效。
    """
    description: str
    """给 LLM 的工具说明（已是 Parsable 渲染后的文本）。
    """
    params_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    """参数声明表，键为规范参数名、值为 JSON Schema property dict；
    参数是否必填由有无 ``default`` 派生（有 ``default`` → 可选）。
    来源两条：Python 层 ``BaseModel`` 子类经 ``model_json_schema()``
    派生，``.fya`` 层展开式声明经 :func:`flowing.params.expand_args_schema`
    归一化；执行层校验模型经 :func:`flowing.params.schema_to_model`
    桥接。``default`` 只接受 JSON 兼容类型——复杂对象（DB 连接、HTTP
    客户端）走标识符引用 + ``caller`` 获取。
    """
    output_schema: dict[str, Any] | None = None
    """返回值结构声明（JSON Schema 片段）；``None`` 表示不约束。

    来源：工具 ``.fya`` 的 ``output:`` 字段；MCP 服务器的
    ``outputSchema`` 自动填入本字段。用途：随 ``llm_definition()`` 产物
    携带（adapter 白名单取用，不映射进各家 function-calling schema）；
    `RequestTool` 执行时按 ``properties`` 从 JSON 响应提取字段。当前
    实现不据此做结果校验（MCP 的 ``outputSchema`` 同样只存储不校验）。
    Agent 侧 ``output:`` 覆写并入 `ToolEntry.override_params` （以字段名为
    键进 LLM 视图的参数表），不落本字段。
    """
    strict: bool = True
    """是否在工具层严格约束 LLM 入参；``False`` 用于「参数由下游自行校验」
    的工具。
    """

    def clone_with_overrides(
        self,
        name: str | None = None,
        override_params: dict[str, dict[str, Any]] | None = None,
        override_description: str | None = None,
        *,
        specified_params: set[str] | None = None,
    ) -> "ToolDefinition":
        """生成覆写后的新 `ToolDefinition`，原始对象不变。

        .. rubric:: 功能介绍

        `ToolEntry.llm_definition()` 的唯一覆写机制：别名、参数局部覆写、
        描述覆写、隐藏参数移除，全部经本方法一次性完成。「返回新对象，
        原始不变」是绑定层不污染全局注册表的结构性保证——`ToolRegistry`
        中的定义永不被 Agent 级覆写修改。

        :param name: 新名字（通常传 `ToolEntry.name_alias`）；``None`` 保持
          原名。
        :param override_params: ``{规范参数名: {JSON Schema 关键字: 新值}}``
          稀疏补丁——只覆写出现的关键字，未出现的参数与关键字保持原值
          （深合并回填）。补丁应用委托
          :func:`flowing.params.apply_param_overrides`——关键字超出桥接
          子集时抛 :class:`flowing.errors.FormatError` （声明笔误
          fail-fast，即尽早报错、不静默容忍）。requiredness 不主动
          推断：没写 ``default`` 沿用
          基底；显式给 ``default`` 变可选。
        :param override_description: 描述覆写；``None`` 保持原描述。
        :param specified_params: 要从 LLM 视图中移除的参数名集合（调用方
          传入 ``set(specified.keys())``——注入表达式也是 specified 的
          一种值形态）。
        :return: 新的 `ToolDefinition`；``self`` 不被修改。

        .. rubric:: 行为要点

        - 后置条件：返回值与 ``self`` 是不同对象；``self.params_schema``
          内容不变。
        - 边缘情况：``override_params`` 中出现 ``params_schema`` 不存在
          的键 → 视为新增参数（`FinishTool` 动态 schema 即依赖此语义）；
          ``specified_params`` 中出现不存在的键 → 静默忽略。
        - 本方法不做参数别名应用（``param_aliases`` 的改名由
          `ToolEntry.llm_definition()` 第 4 步在返回值上完成）。

        .. seealso::

            - :meth:`flowing.tool.ToolEntry.llm_definition` —— 本方法的
              唯一框架调用点。
        """
        params: dict[str, dict[str, Any]] = {k: dict(v) for k, v in self.params_schema.items()}
        if specified_params:
            for key in specified_params:
                params.pop(key, None)  # 不存在的键静默忽略（行为要点）；
            # specified 参数由 specified 值兜底，requiredness 无需维护
        if override_params:
            params = apply_param_overrides(params, override_params)
            # 非法关键字 fail-fast / 未知键新增参数 / 稀疏回填，语义见该函数
        return ToolDefinition(
            name=name or self.name,
            description=override_description or self.description,
            params_schema=params,
            output_schema=self.output_schema,  # 透传：覆写不触及
            strict=self.strict,
        )


def _whole_doc(doc: str | None) -> str | None:
    """docstring 整体提取：``inspect.cleandoc``
    全文、去首尾空白；无内容 → ``None``。内部 API。"""
    if not doc:
        return None
    return inspect.cleandoc(doc).strip() or None


def _first_paragraph(doc: str | None) -> str | None:
    """docstring 首段提取（其它内部用途）：cleandoc 后按空行切首段，
    段内换行折叠为空格；无内容 → ``None``。内部 API。"""
    if not doc:
        return None
    paragraph = inspect.cleandoc(doc).split("\n\n", 1)[0].strip()
    return " ".join(paragraph.splitlines()) or None


def _apply_param_aliases(
    definition: ToolDefinition,
    param_aliases: dict[str, str],
) -> ToolDefinition:
    """把 LLM 可见 schema 的参数名从规范名改为别名（`ToolEntry.llm_definition`
    第 4 步的唯一可调用物）。内部 API，不属稳定契约。

    .. rubric:: 行为要点

    - ``param_aliases`` 方向为 ``LLM 别名 → 规范名``；本函数对
      ``params_schema`` 键做反向改名——仅改名，property 内容原样，
      其余字段（name/description/output_schema/strict）透传。
    - 撞名（改名结果与既有键撞车，含两个规范名经别名映射到同一名称）→
      :class:`flowing.errors.FormatError` （绑定声明笔误，fail-fast）；
      不反向查重（``param_aliases`` 自身的别名重复不在此校验）。
    - 映射到 schema 中不存在的规范名 → 静默跳过；``param_aliases`` 为空
      时原样返回入参（不复制）。
    """
    if not param_aliases:
        return definition
    reverse = {canonical: alias for alias, canonical in param_aliases.items()}
    renamed: dict[str, dict[str, Any]] = {}
    for key, prop in definition.params_schema.items():
        new_key = reverse.get(key, key)
        if new_key in renamed:
            raise FormatError(
                f"parameter alias collision after mapping: {new_key!r} (param_aliases conflict with existing parameters)")
        renamed[new_key] = prop
    return ToolDefinition(
        name=definition.name,
        description=definition.description,
        params_schema=renamed,
        output_schema=definition.output_schema,
        strict=definition.strict,
    )


@dataclass
class ToolEntry:
    """Agent 对工具的一次「用法声明」——三层能力描述中的 Agent 级绑定层。

    .. rubric:: 功能介绍

    `ToolEntry` 回答「这个 Agent 如何使用这个 Tool」：LLM 看到的别名、
    参数覆写、指定值、参数别名。每个 Agent 实例持有自己的 entry 集合，
    互不共享——同一工具在不同 Agent 上可以呈现不同的 LLM 视图（如
    ``FinishTool`` 在不同 Agent 上的不同 schema），覆写发生在 Agent 级
    绑定层，而不是全局注册表。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing import ToolEntry
        from flowing.parsable import Parsable

        entry = ToolEntry(
            name_alias="pay",
            name_ori="make-payment",
            override_params={            # JSON Schema 稀疏补丁（只写要改的字段）
                "amount": {"description": "支付金额（元），上限 50000"},
                "currency": {"default": "USD"},
            },
            specified={
                # 固定值与注入表达式都是 specified
                "currency": Parsable("USD"),
                "user_id": Parsable("{{ self.inject('user_id') }}"),
            },
        )

    ``.fya`` 等价声明（``agent.fya`` 的 ``tools:`` 条目）：

    .. code-block:: yaml

        tools:
          - make-payment as pay:
              description: "发起支付"
              args:
                amount: {description: "支付金额（元），上限 50000"}  # 稀疏补丁
                currency: USD                  # 裸值 → specified（固定值）
                user_id: "{{ self.inject('user_id') }}"   # 注入表达式 → specified
                working_dir as cwd: _          # as 改名 + 空补丁（_ 语义见下）
        ---
        $tools.pay.args.cwd.description:      # 深层块：向空补丁逐字段写入
          本订单的工作目录

    .. rubric:: 行为要点

    - 装配判别（``.fya`` ``tools:`` 条目与 ``Agent.add_tool`` 的程序化
      body 走同一判别代码，执行点 = :meth:`flowing.agent.Agent.add_tool`）。
      ``args:`` 下每个参数：值是 dict → ``override_params`` 稀疏补丁
      （JSON Schema 关键字，只写要改的字段，未出现的字段从基底回填）；
      键含 ``<name> as <alias>`` → ``param_aliases`` （改名可与补丁 /
      指定值叠加）；其它值（含 ``{{ self.inject('key') }}`` 注入表达式）
      → ``specified`` （LLM 不可见，调用时以调用方 Agent 为上下文现场
      求值，注入表达式在此沿 provide 链上溯）；值是 ``_`` （``PENDING``）
      → 空补丁：装配时解析为空，深层块（如示例 ``$tools.pay.args.cwd.description:``）可逐字段填充，未被填充则合成时从基底定义全量回填，
      不报错。
    - ``visible=False`` 时条目不进 ``Context.tools`` （LLM 不可见），但
      编程式路径仍可经注册表访问——可见性与可执行性分离。
    - entry 不持有 Tool 实例引用——执行时按 ``name_ori`` 现场查
      `ToolRegistry`。
    - 不变量：``specified`` 中的参数（固定值与注入表达式）对 LLM 不可见；
      其值优先级最高（防 LLM 篡改通道）。

    .. seealso::

        - :class:`flowing.tool.ToolRegistry` —— 规范名 → Tool 的全局表。
        - :class:`flowing.tool.ToolDefinition` —— 覆写产物类型。
        - :meth:`flowing.agent.Agent.add_tool` —— 条目装配入口。
    """


    name_alias: str
    """LLM 看到的工具名（别名）。工具调用仅按别名查找，不回退规范名——
    不同 Agent 对同一工具注册了不同别名 / 覆写，回退会绕开 Agent 级绑定。
    """
    name_ori: str
    """规范名——`ToolRegistry` 中的 key，查找可执行对象的唯一依据。
    """
    override_description: Parsable | None = None
    """覆写 LLM 看到的描述；``None`` 使用注册表原描述。是 `Parsable`：
    ``llm_definition()`` 时以调用方 Agent 为上下文自动求值（声明期可写
    模板 / 表达式，组装时拿到渲染后字符串；与 SubagentEntry 的
    description 覆写同律）。
    """
    override_params: dict[str, dict[str, Any]] | None = None
    """参数局部覆写：``{规范参数名: {子属性: 新值}}``，只写与默认不同的
    字段。Agent 侧 ``output:`` 覆写也并入本字典（以字段名为键），对
    ``llm_definition()`` 与 ``resolve()`` 完全透明。
    """
    specified: dict[str, Parsable] = field(default_factory=dict)
    """指定值参数（LLM 不可见）。默认空 dict。值为 `Parsable`，在
    ``resolve()`` 时以调用方 Agent 局部变量为上下文惰性求值。两种值形态：
    固定值（``Parsable("USD")``）与注入表达式
    （``Parsable("{{ self.inject('user_id') }}")``——求值时沿 provide
    链上溯，链断裂抛 ``MissingProvideError``）。
    """
    param_aliases: dict[str, str] = field(default_factory=dict)
    """LLM 参数名 → 规范参数名。默认空 dict。LLM 看到别名，``resolve()``
    第一步映射回规范名。
    """
    visible: bool = True
    """是否对 LLM 可见；``False`` 时不进 ``Context.tools``，但仍可编程式调用。
    """

    def llm_definition(self, runtime: Runtime, agent: Agent) -> ToolDefinition:
        """生成本 Agent 视角下 LLM 可见的 `ToolDefinition` （四步，顺序为不变量）。

        .. rubric:: 功能介绍

        上下文组装（``Agent._assemble_context()``）时对每个 ``visible``
        entry 调用本方法，产物进入 ``Context.tools``。每次调用都重新
        求值，不缓存结果。

        .. rubric:: 行为要点

        1. 从 ``runtime.tool_registry`` 按 ``name_ori`` 取规范 Tool 的
           默认 ``definition``；
        2. 计算 ``hidden = set(self.specified.keys())``——specified 参数
           （固定值与注入表达式）对 LLM 不可见；
        3. ``override_description`` 非 ``None`` 时以 ``agent`` 为上下文
           当场求值（Parsable 自动求值），随后
           ``definition.clone_with_overrides(self.name_alias,
           self.override_params, <渲染后描述>, specified_params=hidden)``
           生成新定义（原始定义不变）；
        4. 应用参数别名：把 LLM 可见 schema 中的参数名从规范名改为
           ``param_aliases`` 中的别名。

        :param runtime: 当前 Runtime（取其 ``tool_registry``）。
        :param agent: 调用方 Agent（``override_description`` 的 Parsable
          渲染上下文）。
        :return: 覆写后的新 `ToolDefinition`；注册表中的原始定义不变。
        :raises flowing.errors.ToolNotFoundError: ``name_ori`` 不在注册
          表中（创建管线应已保证不触发；运行时出现即注册表被外部改动的
          信号）。

        .. seealso::

            - :meth:`flowing.tool.ToolDefinition.clone_with_overrides` ——
              第 3 步的覆写语义。
        """
        tool = runtime.tool_registry.get(self.name_ori)
        hidden = set(self.specified.keys())   # specified（固定值/注入表达式）对 LLM 不可见
        # override_description 是 Parsable：以调用方 agent 为上下文现场 resolve
        # （求值面内）；None 时保持注册表原描述
        description = (str(self.override_description.resolve(agent))
                       if self.override_description is not None else None)
        definition = tool.definition.clone_with_overrides(
            self.name_alias, self.override_params, description,
            specified_params=hidden)
        # 第 4 步参数别名应用（规范名 → param_aliases 别名）：唯一可调用物
        # _apply_param_aliases（仅改名不改内容；撞名 → FormatError）
        return _apply_param_aliases(definition, self.param_aliases)

    def resolve(
        self,
        agent: Agent,
        args: dict[str, Any],
        params_schema: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """把 LLM args 与 specified（含注入表达式）聚合为 ``execute()`` 的完整参数。

        .. rubric:: 功能介绍

        本方法在 ``Agent._normalize()`` 内部被调用（``before_tool_call``
        钩子之后），产出按规范名组织的最终参数字典。只收参数声明表
        ``params_schema`` （``tool.definition.params_schema``），不持有
        `Tool` 引用——`ToolEntry` 保持在「绑定 / 声明」层，不依赖「执行」
        层；调用方（``Agent._normalize``）已持有 Tool 实例，顺手传入
        声明表即可。

        .. rubric:: 行为要点

        1. LLM args：逐键经 ``param_aliases`` 映射回规范名；
        2. specified：`Parsable` 以 ``agent`` 局部变量为上下文惰性求值
           （固定值直给；注入表达式 ``{{ self.inject('key') }}`` 在此沿
           provide 链上溯），并按 ``params_schema`` 中对应 property 做
           `_coerce` 兼容转换后覆盖同名字段；声明表无此键时跳过转换、
           保留原值（最终由 `Tool.__call__` 的内部校验兜底报错）。

        schema 默认值不在本方法填充——由 ``_normalize()`` 第 3 步填充。

        :param agent: 调用方 Agent（provide 链上溯与 Parsable 渲染上下文）。
        :param args: LLM 原始参数（键可能是别名）。
        :param params_schema: 规范参数名 → JSON Schema property 的声明表，
          取 ``tool.definition.params_schema`` （覆写后的 LLM 视图不适用
          ——本方法一律按注册表规范定义）。
        :return: 规范名 → 值的完整参数字典，供 `Tool.__call__` 按
          ``execute()`` 签名匹配分发。
        :raises flowing.errors.MissingProvideError: 注入表达式中的 key
          沿 provide 链上溯不到任何提供者（调用时求值抛出）。

        .. seealso::

            - :meth:`flowing.agent.Agent.inject` —— 注入表达式的
              provide 链上溯执行点。
            - :meth:`flowing.parsable.Parsable.resolve` —— 惰性求值。
            - :func:`flowing.params._coerce` —— 兼容类型转换。
        """
        resolved: dict[str, Any] = {}
        for key, value in args.items():  # 第 1 步：LLM args 逐键别名 → 规范名
            resolved[self.param_aliases.get(key, key)] = value
        for key, parsable in self.specified.items():  # 第 2 步：specified 惰性求值后覆盖（固定值/注入表达式同路）
            value = parsable.resolve(agent)  # -> Any（注入表达式 {{ self.inject(...) }} 在求值中沿 provide 链上溯）
            prop = params_schema.get(key)  # -> property dict | None（声明表由调用方传入）
            if prop is not None:
                value = _coerce(value, prop)  # 声明表无此键时跳过转换，内部校验兜底
            resolved[key] = value
        return resolved


class Tool:
    """可执行对象基类——三层能力描述中的「执行」层。

    .. rubric:: 功能介绍

    `Tool` 只对执行负责：持有一份默认 `ToolDefinition`，暴露
    ``execute()``；框架调度层经 ``__call__`` 统一调用。四种工具类型
    （script / mcp / cli / request）均以本类（或其子类）为最终产物。
    调度职责（awaitable / async generator 检测、Task 包装、caller 注入、
    返回值包装）集中在 ``__call__``，让 ``execute()`` 保持「零散参数进、
    普通值出」的最简单签名——工具作者不需要知道 `ToolResult` 的存在。

    .. rubric:: 使用示例

    直接子类化通常只用于内置工具；应用代码应使用 `ScriptTool`：

    .. code-block:: python

        class FinishTool(Tool):
            definition = ToolDefinition(name="finish", ...)

            async def execute(self, *, summary: str = "", caller: Agent) -> dict:
                ...

    .. rubric:: 行为要点

    - `Tool` 不认识钩子、不查注册表、不做参数聚合——这些都在
      ``Agent.tool_call()`` / ``_normalize()`` 一侧。
    - 实例属性开放：``.fya`` 中的非保留字段（如 ``requires_approval``）
      直接成为 tool 对象的普通属性，框架不解析、不据此做任何自动行为。
    - 不变量：``self._execution`` 仅在 ``__call__`` 调度期间非 ``None``；
      工具内部可检查 ``self._execution.cancel.is_set()`` 以响应 cancel
      （协作式取消，见 :mod:`flowing.agent` 的 `Execution` 契约）。

    .. seealso::

        - :class:`flowing.tool.ScriptTool` —— script 工具的作者基类。
        - :class:`flowing.tool.ToolRegistry` —— 注册与查找。
    """

    definition: ToolDefinition
    """默认 LLM 声明。类属性或实例属性（`ScriptTool.__init__` 自动生成）。
    Agent 级覆写不修改本对象（见 `ToolEntry`）。
    """
    registry_key: str | None = None
    """注册表全键（``ns::name``），``ToolRegistry.register`` 时回写；未注册
    实例为 ``None``。Entry 装配对文件派生工具落账本字段为 ``name_ori``
    （含目录派生命名空间的限定键，热路径精确命中）；注册表命中
    （``default::`` / ``builtin::``）的条目仍记裸名。内部 API。
    """
    _has_caller: bool
    """注册 / 实例化时经 ``inspect`` 检测 ``execute()`` 签名是否声明
    ``caller`` 参数的缓存标记。内部 API，不属稳定契约。
    """
    _execution: Execution | None
    """当前调度的执行追踪对象（cancel 信号载体），仅 ``__call__`` 期间有效。
    内部 API，不属稳定契约。
    """
    _args_model: type[BaseModel]
    """内部校验用的 Pydantic 模型——工具创建时编译一次、终身复用。来源：
    Python 层 ``args_model`` 声明直接用（声明即模型）；未声明时从
    ``execute()`` 签名构建（``_infer_from_execute``）；``.fya`` 声明经
    :func:`flowing.params.schema_to_model` 桥接。由各子类 ``__init__``
    在 ``definition`` 落定后赋值；覆写 ``__init__`` 的子类必须调
    ``super().__init__()`` 或自行赋值，否则 ``__call__`` 的内部校验无
    模型可用。与 LLM 可见 JSON Schema 同源（见 :mod:`flowing.params`），
    永不漂移。不做与 ``execute()`` 签名的一致性检查——签名差异可能是
    合法写法（``**kwargs`` 透传、装饰器包装），真写岔由调用时内部校验
    或直接测试暴露。内部 API，不属稳定契约。
    """


    async def execute(self, **kwargs: Any) -> Any:
        """工具业务逻辑入口——零散参数 + 可选 ``caller``，不接收 `ToolCall`。

        .. rubric:: 功能介绍

        子类覆写本方法。参数来自 `ToolEntry.resolve()` 聚合后的完整字典，
        由 ``__call__`` 按签名匹配分发；声明 ``caller: Agent`` 参数时框架
        自动传入调用方 Agent（用于注入表达式求值 / ``get_resource`` /
        访问调用方状态）。同步函数同样合法（``__call__`` 做 awaitable
        检测）。

        .. rubric:: 使用示例

        .. code-block:: python

            async def execute(self, *, order_id: str, amount: float,
                              caller: Agent) -> dict:
                return await payment_service.charge(
                    order_id=order_id, amount=amount)

        .. rubric:: 行为要点

        - 返回值由 ``__call__`` 自动包装：普通值 → ``completed``；抛异常
          → ``error``；返回 ``asyncio.Task`` → ``pending`` （收据，框架
          不等待）。
        - 不自行构造 `ToolResult`；不处理 specified（固定值 / 注入表达式
          已由调度层聚合进参数）；复杂对象（连接池、客户端）不进参数
          ——以标识符字符串传入，经 ``caller`` 获取真实对象。
        - 长时间运行的工具应周期性检查 ``self._execution.cancel.is_set()``
          以支持协作式取消。

        .. seealso::

            - :meth:`flowing.tool.Tool.__call__` —— 调度层职责。
            - :class:`flowing.tool.ToolResult` —— 自动包装产物。
        """
        ...

    async def __call__(
        self,
        resolved_args: dict[str, Any],
        *,
        caller: Agent | None = None,
        execution: Execution | None = None,
        tool_call: ToolCall | None = None,
    ) -> ToolResult:
        """框架调度层统一入口——用户永不覆写。

        .. rubric:: 功能介绍

        ``Agent.tool_call()`` 在 ``_normalize()`` 之后经本方法执行工具。
        职责固定五项：

        1. 形态检测：``inspect.isasyncgen`` （async generator 后台形态
           ——首 yield 收据 + 后台驱动）→ ``inspect.isawaitable``——同步
           ``execute`` 直接调用，异步 ``execute`` await；
        2. Task 包装与取消竞速：异步执行包装为 ``asyncio.Task`` 并关联
           ``execution``——在途等待与取消信号（``execution.cancel`` +
           调用方 Agent 的 ``_turn_abort``）竞速；信号先置位 → 中断在途
           执行（CancelledError 注入 ``execute`` 的 await 点），产出
           ``status="cancelled"`` 结果（LLM 可见「被取消」，不走 error
           通道）。后台形态（background 标记 / Task 返回 / async gen）
           不包竞速——其取消经 Execution 注册表置位；
        3. caller 自动传入：依 ``_has_caller`` （注册时 inspect 检测）
           决定是否传 ``caller=``；
        4. 内部校验：caller 注入之后、``execute`` 之前，聚合终值按
           ``self._args_model`` （创建时编译的 Pydantic 产物）校验。
           specified（固定值 / 注入表达式求值结果）与默认值属可信来源，
           本步失败是框架 / 宿主配置错误——异常在 ``try`` 之外上抛框架
           错误通道并记日志，不被 ``except Exception`` 吞成
           ``ToolResult(error)``、不进入 LLM 可见文本（不泄漏隐藏参数
           的存在）；
        5. 结果归一与包装：返回值（含直接构造 `ToolResult` 返回的
           ``output``）经 `normalize_output` 归一——浅层判别、幂等；
           归一化中的违禁块（``ToolCallBlock`` / ``ThinkingBlock``）
           ``ValueError`` 属作者 bug，与职责 4 同走框架错误通道在
           ``try`` 之外上抛；``execute`` 内普通异常 →
           ``ToolResult(error)`` （不触发错误钩子）；``execute`` 内抛出的
           :class:`flowing.errors.Intercepted` → ``ToolResult.blocked``
           （硬阻断信号语义即 blocked，与 ``before_tool_call`` 拦截同一
           出口、同一 reason 塑形——「有意拒绝」与「意外故障」不进同一
           LLM 可见通道）；三种后台形态 → ``ToolResult(pending)``：
           ① ``execute`` 是 async generator（首 yield = 收据内容）；
           ② 普通 async ``execute`` + ``background = True`` （仅 script
           型，不 await，直接落 Task 分支）；③ 返回 ``asyncio.Task``。
           后台形态的投递驱动经 ``caller._drive_background`` 接缝移交
           调用方（逐段 / 终值在调用方侧归一、过 ``on_tool_yields``、
           塑形为 EVENT 入队；注册与按 id 取消 / 查询 / destroy 覆盖
           同在调用方侧）；``caller=None`` 的编程路径不驱动（async
           generator 直接关闭，仅返 pending 收据）。

        :param resolved_args: `ToolEntry.resolve()` 产出并经默认值填充的
          规范名参数字典；已经过 LLM 视角校验，但尚未过内部校验（本方法
          职责 4）。
        :param caller: 调用方 Agent；仅当 ``execute`` 声明了 ``caller``
          参数时实际传入。
        :param execution: 本次调用的执行追踪对象；挂到 ``self._execution``
          供工具内部检查 abort 信号。
        :param tool_call: 本次调用的 `ToolCall`（可选）；随后台驱动接缝
          透传给调用方，供后台分段 / 终值接线 ``ToolResult.name`` /
          ``tool_call_id`` 元信息。
        :return: 包装后的 `ToolResult`。
        :raises pydantic.ValidationError: 内部校验失败（specified /
          默认值的配置错误）——框架错误通道，非 LLM 可见产物。

        .. rubric:: 行为要点

        - 前置条件：``resolved_args`` 已经过 LLM 视角校验（``_normalize``
          第 1 步）与默认值填充（第 3 步）。
        - enqueue 契约：普通 ``async def execute`` 被 await 到底，结果只
          作为返回值交给 ``Agent.tool_call``，不 enqueue。三种后台形态
          （async generator / ``background`` 标记 / 返回 ``asyncio.Task``）
          返回 ``pending`` 收据：后续 yield 与 Task 终值由调用方驱动逐段
          enqueue EVENT（``source="tool_result"``）；``background`` 标记
          复用 Task 分支。因此工具结果是否入队，只由 ``execute`` 的返回
          形态决定，与调用上下文无关。
        - 不重试、不超时兜底、不审批——重试由可选的 ``use_retry()``
          提供，审批在 ``before_tool_call``。
        - 边缘情况：``execution.cancel`` 在 ``execute`` 运行期间被置位
          → 本方法不强制中断 Task，工具自行协作退出；工具不响应时由
          cancel / stop 族的上层策略处理。

        .. seealso::

            - :meth:`flowing.agent.Agent.tool_call` —— 上游调用点（其收尾
              对 shortcut / 钩子改写产物幂等再归一）。
            - :class:`flowing.agent.Execution` —— cancel / pause 信号契约。
        """
        self._execution = execution  # cancel 注入落点；仅调度期间非 None
        if not hasattr(self, "_has_caller"):
            # 逃生舱写法（直接子类化 Tool + 类属性 definition、无 __init__，
            # 见类 docstring 的 FinishTool 示例）没有创建期检测点——就地检测
            # 并实例级缓存；正常路径（ScriptTool 等四个子类）在 __init__ 已落定
            self._has_caller = "caller" in inspect.signature(self.execute).parameters
        if self._has_caller:
            resolved_args["caller"] = caller  # caller 自动传入（execute 声明了该参数时）
        if not hasattr(self, "_args_model"):
            # 同上逃生舱：创建期未定模型的直接子类，就地按 execute 签名构建
            # 并缓存（与 ScriptTool.__init__ 同一建模入口，编译一次终身复用）
            self._args_model = _infer_from_execute(self.execute)
        # 职责 4：内部校验——caller 注入之后、try 之外。
        # specified/inject/默认值的配置错误 -> 上抛框架错误通道 + 日志，
        # 不被下方 except Exception 吞成 ToolResult(error)、不进 LLM 可见文本
        # （不泄漏隐藏参数的存在）；日志只记工具名，不记参数值（防敏感值泄露）
        try:
            self._args_model.model_validate(resolved_args)
        except ValidationError:
            _logger.exception(
                "tool %s failed internal validation (specified/inject/default-value misconfiguration)",
                getattr(self, "definition", None) and self.definition.name
                or type(self).__name__)
            raise
        try:
            result = self.execute(**resolved_args)  # -> Any（同步）/ awaitable（异步）/ async gen（后台形态）
            if inspect.isasyncgen(result):
                # async generator 形态——等待首 yield（准备完成哨兵，
                # 可空）作 pending 收据；剩余部分 ensure_future 后台驱动（B3/
                # 每个后续 yield 逐段投递 EVENT）。首 yield 前异常
                # 在此上抛，按 except 顺序分派：Intercepted → blocked（1833）；
                # 其余 → error（工具调用错误，LLM 立即可见，不挂 pending）
                try:
                    receipt = await anext(result)
                except StopAsyncIteration:
                    receipt = None                # 条件性空 yield：无收据（纯副作用）
                if caller is not None:
                    task_id = caller._drive_background(
                        self, result, "asyncgen", tool_call)   # 后台驱动经调用方接缝启动（注册 + 投递在调用方侧）
                    return ToolResult(status="pending", output=receipt,
                                      background_task_id=task_id)
                # 编程路径（无 caller）：不注册、不驱动——后台主体不执行；
                # 弃置的 generator 须 aclose() 关闭（否则 GC 触发
                # "async generator ignored GeneratorExit" 警告）
                await result.aclose()
                return ToolResult(status="pending", output=receipt,
                                  background_task_id=None)
            if inspect.isawaitable(result):
                if getattr(self, "background", False):
                    # background 标记——普通 async execute 的后台化入口，
                    # 不 await，落下方 Task/pending 分支（仅 script 型受支持：
                    # .fya 非 script 型声明加载期 FormatError，三个具体工具类
                    # 构造尾部 _strip_unsupported_background 告警并强制 False）
                    value = asyncio.ensure_future(result)
                else:
                    task = asyncio.ensure_future(result)
                    # 取消竞速（script 前台 / cli / request / mcp 全覆盖的默认
                    # 行为）：在途等待与取消信号竞速——信号先置位则中断在途
                    # 执行（CancelledError 注入 execute 的 await 点，工具的
                    # try/finally 照常跑），产出 cancelled 结果（LLM 可见
                    # 「被取消」，不走 error 通道）；后台形态（background
                    # 标记 / Task 返回 / async gen）不包竞速——它们的取消经
                    # Execution 注册表置位（cancel_children 通道）
                    sig_events = []
                    if execution is not None:
                        sig_events.append(execution.cancel)
                    turn_abort = getattr(caller, "_turn_abort", None)   # 鸭子类型调用方（测试替身）可无此字段
                    if turn_abort is not None:
                        sig_events.append(turn_abort)
                    if not sig_events:   # 编程式直调（无执行条目、无调用方）：无竞速
                        value = await task
                    else:
                        signals = [asyncio.ensure_future(ev.wait())
                                   for ev in sig_events]
                        done, _ = await asyncio.wait(
                            {task, *signals}, return_when=asyncio.FIRST_COMPLETED)
                        for sig in signals:
                            sig.cancel()
                        if task in done:
                            value = task.result()   # 异常原样上抛（下方 except 通道）
                        else:
                            task.cancel()   # CancelledError 注入在途 await 点
                            with contextlib.suppress(asyncio.CancelledError):
                                await task   # 等其收尾（工具的 finally / 清理）
                            if task.cancelled():
                                return ToolResult(status="cancelled", output=None)
                            # 工具捕获取消并返回了部分产物（如 bash 的已收集输出 +
                            # 中断提示）：带进 cancelled 结果返回（LLM 可见中断前
                            # 的部分输出）
                            return ToolResult(status="cancelled", output=task.result())
            else:
                value = result
        except Intercepted as exc:
            # execute 内抛出的硬阻断信号（如工具内部下游扩展钩子的拦截）：语义
            # 即 blocked——与 before_tool_call 拦截同一出口、同一 reason 塑形，
            # 不当业务异常吞成 error（「有意拒绝」与「意外故障」不进同一通道）
            return ToolResult.blocked(reason=str(exc))
        except Exception as exc:
            # 异常路径：包装为 error 结果，不触发错误钩子（LLM 可见的正常产物）
            return ToolResult(status="error", error=str(exc))
        finally:
            self._execution = None  # 不变量：_execution 仅 __call__ 期间有效
        if inspect.isasyncgen(value):
            # 嵌套形态：async def execute 返回 async gen 对象
            # ——与 execute 自身即 async gen 同一条后台管线。
            # 本分支在 try 之外——首 yield 前异常在此自行按 except 顺序分派
            try:
                receipt = await anext(value)
            except StopAsyncIteration:
                receipt = None
            except Intercepted as exc:
                return ToolResult.blocked(reason=str(exc))
            except Exception as exc:
                return ToolResult(status="error", error=str(exc))
            if caller is not None:
                task_id = caller._drive_background(
                    self, value, "asyncgen", tool_call)   # 后台驱动经调用方接缝启动
                return ToolResult(status="pending", output=receipt,
                                  background_task_id=task_id)
            await value.aclose()
            return ToolResult(status="pending", output=receipt,
                              background_task_id=None)
        if isinstance(value, asyncio.Task):
            # fire-and-forget 收据；后台驱动经调用方接缝启动（完成回调取
            # 终值 → 归一 → 塑形 → 标注块 + 结果块的多块 EVENT 入队；
            # 任务异常 → 标注块 + 错误文本块，与同步 error 同语义）
            if caller is not None:
                task_id = caller._drive_background(
                    self, value, "task", tool_call)   # 注册（强引用 + 按 id 取消/查询 + destroy 覆盖）
                return ToolResult(status="pending", output=None,
                                  background_task_id=task_id)
            return ToolResult(status="pending", output=None)
        # 职责 5：归一化在 try 之外——浅层判别、幂等；
        # 违禁块（ToolCallBlock/ThinkingBlock）ValueError 属作者 bug，
        # 上抛框架错误通道，不被吞成 ToolResult(error)
        value = await normalize_output(value)
        return ToolResult(status="completed", output=value)


def _has_forbidden_block(value: Any) -> bool:
    """浅层违禁块检测（与 `normalize_output` 的浅层判别同口径——顶层值或
    list/tuple 成员；深层埋藏由塑形期 ``StructBlock`` 构造校验兜底）。
    内部 API，不属稳定契约。"""
    if isinstance(value, (ToolCallBlock, ThinkingBlock)):
        return True
    if isinstance(value, (list, tuple)):
        return any(_has_forbidden_block(v) for v in value)
    return False


