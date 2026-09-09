"""``flowing.hooks`` —— Agent 实例级钩子系统：注册、分发、分组管理与声明式注册。

.. rubric:: 功能介绍

本模块是 Flowing 的可编程扩展机制（与消息模型、逻辑 Turn 循环并列的三大
基础设施之一）：在 Agent 实例上注册处理函数（handler），让扩展代码在框架
运行的固定时点（创建 / 恢复 / 销毁、逻辑回合、LLM 调用、工具执行、消息
入队 / 出队、fork、取消等）介入或观察。每个 Agent 实例持有一个独立的钩子
注册表（``agent.hooks``），钩子只影响声明它的实例，不存在全局钩子表。

模块组成：

- :class:`HookRegistry`：Agent 实例级的钩子点注册表。构造时预填 27 个
  核心钩子点（``by="core"``，见下方全集表）；扩展经
  :meth:`HookRegistry.declare` 声明自己的钩子点。
- :class:`HookList`：单个钩子点的容器。提供统一注册入口
  （``hook(handler, by=..., tags=...)``）、三形态索引（``int`` / ``slice`` /
  ``str``）与统一分发入口 :meth:`~HookList.dispatch`。
- :class:`HookEntry`：单条 handler 记录，字段为 ``handler`` / ``by`` /
  ``tags`` / ``pattern`` / ``enabled``。
- :class:`PatternRegistrar`：``hook["pattern"](handler)`` 按名称过滤注册
  的语法糖返回对象。
- :func:`on`：声明式钩子注册装饰器，用于 ``.fya`` 的 ``$script`` 块与
  手写 Agent 子类（在 ``__init__`` 阶段注册，早于 ``setup()``）。
- :class:`ManagedList` / :class:`Togglable`：分组管理的公共容器抽象，
  canonical home 在 :mod:`flowing.lists`，本模块再导出（:class:`HookList`
  继承 ``ManagedList``，见 :mod:`flowing.lists` 模块 docstring）。
- ``watch`` 通道：与钩子点并列的独立 watcher 机制，监听实例属性赋值
  事件（见行为要点的「watcher 通道」）。

核心钩子点全集（本模块最重要的扩展契约）：

每个 Agent 实例的钩子注册表在构造时预填下列 27 个核心钩子点，声明者
``by="core"``，初始不含任何 handler。触发时机、value 类型与 handler 能力
如下两张表（逐条以 ``flowing.agent.Agent`` 与 ``flowing.runtime`` 各
dispatch 点为准）：

.. list-table:: 生命周期 / 回合 / Provider / 工具 / 子 Agent 钩子点
   :header-rows: 1
   :widths: 20 26 22 32

   * - 钩子点
     - 触发时机
     - value 类型
     - handler 能力与约束
   * - ``before_create``
     - 创建管线执行 ``setup()`` 之前
     - ``dict`` （创建 kwargs）
     - 可改写 kwargs，改写结果传给 ``setup(**kwargs)``；同步或异步 handler
       均可；只能经 :func:`on` 装饰器声明（此时 ``setup()`` 尚未运行，无法
       用 ``hooks`` 注册）；仅创建管线触发，恢复管线不触发
   * - ``after_create``
     - 状态写盘与 ``_nodes`` 注册完成后、常驻工作循环启动前
     - 无（handler 只收 ``agent`` 参数）
     - 观察 / 收尾；同步或异步 handler 均可
   * - ``before_recover``
     - 恢复管线执行 ``setup()`` 之前
     - ``dict`` （恢复 args）
     - 可改写 args，改写结果传给 ``setup(**args)``；同步或异步 handler
       均可
   * - ``after_recover``
     - ``Agent._restore()`` 与 ``_nodes`` 注册完成后、工作循环启动前
     - 无（handler 只收 ``agent`` 参数）
     - 观察 / 副作用；同步或异步 handler 均可
   * - ``before_destroy``
     - ``destroy()`` 管线内、子节点递归销毁之前
     - 无（handler 只收 ``agent`` 参数）
     - 同步或异步 handler 均可
   * - ``after_destroy``
     - ``destroy()`` 收尾（``_nodes`` 摘除之后）
     - 无（handler 只收 ``agent`` 参数）
     - 同步或异步 handler 均可
   * - ``before_turn``
     - 逻辑 turn 开始、出队批次挂树之前
     - :class:`flowing.agent.TurnContext`
     - 可改写 ``pending_messages`` （把消息附加进本回合待挂树批次）；
       ``raise Intercepted`` 阻断则整个批次丢弃、不落盘
   * - ``before_turn_append``
     - 消息挂树与落盘之前
     - :class:`flowing.message.Message`
     - 可改写消息；``raise Intercepted``
   * - ``after_turn_append``
     - 消息挂树与落盘之后
     - :class:`flowing.message.Message`
     - 观察；此后再改写返回值不会进树（消息已落盘）
   * - ``before_provider_gen``
     - 每次 LLM 调用之前
     - :class:`flowing.context.Context`
     - 可改写完整 Context
   * - ``after_provider_gen``
     - LLM 调用返回之后
     - :class:`flowing.providers.ProviderResponse`
     - 可改写响应；``match_on="by"``：value 携带来源标记（``"_turn"`` /
       ``"_side"`` / 插件自定义值），可按来源 pattern 过滤注册
   * - ``on_provider_delta``
     - 每个流式 delta 到达时（流式逐条；非流式合成一条全量文本 delta）
     - :class:`flowing.providers.ProviderDelta`
     - 纯观察：delta 不落盘（volatile），dispatch 的返回值被丢弃、改写
       无效（需改写整条消息走 ``after_provider_gen``）；``match_on="by"``
       可按来源过滤；流式与否由 ``provider_gen(stream=...)`` 显式参数
       决定，与订阅者存在与否无关
   * - ``before_tool_call``
     - 工具执行之前
     - :class:`flowing.tool.ToolCall`
     - 可改写（含设置 ``shortcut`` 字段协商短路）；``raise Intercepted``
       则工具不执行、生成 ``blocked`` 结果
   * - ``after_tool_call``
     - 工具执行之后（shortcut 短路路径照常触发）
     - :class:`flowing.tool.ToolResult`
     - 可改写结果（改写产物经 ``Agent.tool_call`` 收尾的
       ``normalize_output`` 归一）；``raise Intercepted`` 会生成
       ``blocked`` 结果、不向工作循环传播
   * - ``before_subagent_invoke``
     - 子 Agent 唤起时、参数 resolve 校验之后
     - :class:`flowing.agent.SubagentInvocation`
     - 可改写 ``args`` / ``prompt``；``raise Intercepted`` 硬阻断唤起
       （同步上抛，未创建实例）；挂在亲代 Agent 的 hooks 上
   * - ``after_subagent_invoke``
     - 子 Agent 结果构造之后、交付之前
     - :class:`flowing.agent.SubagentInvocation`
     - 可改写 ``result`` （改写后的结果用于构造交付消息）；不接
       ``Intercepted`` （其异常按普通异常上抛）；挂在亲代 Agent 的 hooks 上
   * - ``before_turn_abort``
     - 回合被 abort 时触发一次（abort 判定收口处）
     - :class:`flowing.agent.TurnContext`
     - 观察 / 收尾前干预
   * - ``after_turn``
     - 所有路径收尾（含异常终止）
     - :class:`flowing.agent.TurnContext`
     - 唯一收尾观察点，handler 读 ``turn.aborted`` 区分异常终止；handler
       异常不中断等待者交付（先交付再上抛）
   * - ``on_provider_error``
     - ``provider_gen()`` 内 LLM 调用抛异常时
     - :class:`flowing.agent.ProviderErrorContext`
     - 唯一可决策的错误钩子：handler 在内部执行退避等待（sleep） / 换模型 /
       ``abort_turn()`` 等动作并写 ``ctx.can_continue`` （为 ``True`` 则
       同一回合内重试）；观察 / 换模型 / 压缩后重试等处置均属 handler 内部逻辑

.. list-table:: 消息队列 / fork / 取消钩子点
   :header-rows: 1
   :widths: 20 26 22 32

   * - 钩子点
     - 触发时机
     - value 类型
     - handler 能力与约束
   * - ``before_enqueue``
     - 消息入队之前
     - :class:`flowing.message.Message`
     - 可改写消息；``raise Intercepted`` 则拦截入队（队列不变）
   * - ``after_enqueue``
     - 消息入队之后
     - :class:`flowing.message.Message`
     - 观察（日志 / 审计）
   * - ``before_dequeue``
     - 工作循环出队之前（队列已确认非空）
     - 无（handler 只收 ``agent`` 参数）
     - 观察队列
   * - ``after_dequeue``
     - 出队之后、回合开始之前
     - ``list[Message]``
     - 可改写（变换本逻辑 turn 消费的消息列表）
   * - ``before_fork``
     - ``fork()`` 切换 head 之前
     - ``str`` （目标消息 id）
     - 可改写 target；``raise Intercepted`` 阻止切换
   * - ``after_fork``
     - ``fork()`` 切换 head 之后
     - ``str`` （切换前的原 head 消息 id）
     - 纯观察（日志 / 通知 UI 刷新）
   * - ``before_cancel``
     - ``cancel()`` / ``stop()`` 置位取消信号之前
     - :class:`flowing.agent.CancelContext`
     - ``raise Intercepted`` 阻止取消（信号不置位）
   * - ``after_cancel``
     - 取消信号置位后立即触发
     - :class:`flowing.agent.CancelContext`
     - 纯观察（日志 / 通知 / 审计）

扩展声明集（不在预填集内；未启用对应扩展的实例访问这些钩子点抛
:class:`flowing.errors.UnknownHookPointError`）：

- ``before_skill_load`` / ``after_skill_load``：Skill 扩展声明，
  ``by="skill"``，``match_on="name"`` （见 :mod:`flowing.plugins.skills`）。
- ``on_signal``：Comm 扩展声明，``by="comm"``，``match_on="type"``
  （见 :mod:`flowing.plugins.comm`）。
- ``on_event``：Comm 扩展声明，``by="comm"``，``match_on="topic"``。
- ``on_cron_trigger``：Cron 扩展声明，``by="cron"``，``match_on="source"``
  （见 :mod:`flowing.plugins.cron`）。
- ``on_compact``：Compact Composable 声明，``by="compact"``
  （见 :mod:`flowing.composables.compact`）。
- ``on_retry``：Retry Composable 声明，``by="retry"``
  （见 :mod:`flowing.composables.retry`）。

.. rubric:: 使用示例

手写 Agent 子类在 ``setup()`` 中注册 handler，或经 :func:`on` 装饰器在类体
中声明（``@on`` 注册发生在 ``__init__`` 阶段、早于 ``setup()``，是注册
创建期钩子的唯一方式）：

.. code-block:: python

    from flowing import Agent, on
    from flowing.errors import Intercepted

    class OrderAgent(Agent):
        @on('before_create')
        def _setup_kwargs(self, kwargs):
            kwargs.setdefault('locale', 'zh')
            return kwargs

        @on('before_tool_call')
        def _guard_payments(self, tool_call):
            if tool_call.name.startswith("payment-"):
                raise Intercepted("支付类工具需要人工审批")
            return tool_call

        async def setup(self):
            self.hooks.after_turn(self._audit_turn, by="audit", tags=["security"])
            self.hooks.before_tool_call["payment-*"](self._guard, by="guardrail")

        # 示例用的 handler 方法，由使用者按需实现：无 value 钩子点的
        # handler 只收 agent；带 value 钩子点的 handler 收 (agent, value)
        # 并返回 value
        def _audit_turn(self, agent):
            return None

        def _guard(self, agent, tool_call):
            return tool_call

``.fya`` 声明式入口等价写法（``$script`` 中的 ``@on`` 在实例 ``__init__``
阶段注册，早于 ``setup()``）：

.. code-block:: text

    ---
    $script:
    from flowing import on

    @on('before_tool_call')
    def _(self, tool_call):
        tool_call.args['lang'] = self.locale
        return tool_call

.. rubric:: 行为要点

handler 统一签名与三种合法出口：

- 所有钩子 handler 签名统一为 ``(agent, value) -> value``：``agent`` 是钩子
  宿主对象（Agent 专属钩子点上是当前 Agent 实例；Workflow 自带钩子点上是
  ``Workflow`` 实例），``value`` 的类型由钩子点决定（见上方全集表）。无
  value 的钩子点（如 ``after_create``）handler 只收 ``agent`` 一个参数。
- 出口一：``return value`` （改写后或原样）——结果传给下一个 handler，最终
  返回给 dispatch 的调用方。
- 出口二：在 value 上设置 ``shortcut`` 字段后返回——分发链停止，调用方跳过
  默认逻辑（如缓存命中时直接采用 handler 提供的工具结果）；对应操作的
  ``after_`` 钩子照常触发。
- 出口三：``raise Intercepted``——硬阻断信号，分发链停止、整个操作标记为
  无效、对应操作的 ``after_`` 钩子不触发。:class:`flowing.errors.Intercepted`
  刻意不是 ``FlowingError`` 的子类，捕获框架错误时不会误捕它。
- 携带 value 的钩子点要求每个 handler 返回值：返回 ``None`` 视为编程错误，
  抛 :class:`flowing.errors.FlowingError` （消息含钩子点名与 handler 标识）。
  无 value 的钩子点不施加该检查。
- 同步与异步 handler 透明混用：分发器检测到 awaitable 结果就 ``await``。
  全部钩子点（含创建 / 销毁 / 恢复管线）均接受同步或异步 handler；注册时
  不校验 handler 签名，签名错误在分发时按普通异常上抛。

dispatch 统一算法（:meth:`HookList.dispatch` 是全局唯一的钩子分发实现）：

- 按注册顺序遍历活跃条目（``enabled=False`` 的条目跳过），每个条目先按
  其 ``pattern`` 过滤（用 ``fnmatch`` 匹配 value 的 ``match_on`` 字段），
  不匹配则跳过、value 原样透传给后续 handler。
- 普通异常直接上抛：不捕获、不通知、不继续后续 handler、无任何兜底钩子。
  ``Intercepted`` 被捕获后立即重抛（链停止、后续 handler 不执行），只记
  INFO 级日志。
- 没有活跃 handler（或全部被过滤）时原样返回传入的 value。

两类钩子语义：

- ``before_`` / ``after_`` 钩子点：Agent 自身控制流（主动操作）前后的拦截、
  加工与通知点。
- ``on_`` 钩子点：外部触发（被动响应）。命名只描述触发方向，不承诺 handler
  能力——各 ``on_`` 钩子点独立定义（``on_provider_error`` 可决策、
  ``on_signal`` 可改 payload、``on_provider_delta`` 纯观察）。不要假设
  ``on_`` handler 都只能观察。

watcher 通道（``watch``，与钩子点并列的扩展契约）：

- ``self.hooks.watch(name, handler)`` 注册 ``(agent, value)`` 形态的
  watcher；``Agent.watch`` 是 ``(new, old)`` 回调糖。``name`` 按
  ``fnmatch`` pattern 匹配 value（:class:`flowing.agent.FieldUpdate`，
  含 ``name`` / ``new`` / ``old`` 字段）的 ``name`` 属性，字面量即精确匹配。
- watcher 以 fire-and-forget 方式触发（``asyncio.ensure_future``，不
  await）：赋值语句不等待 watcher 执行，watcher 的执行时序与赋值本身不
  保证先后——这是 watcher 与普通钩子的根本区别（其余钩子点都在异步管线
  上被显式 await）。
- watcher 是纯观察：修改 ``new`` 无效（赋值已经发生，watcher 的返回值被
  忽略）；``raise Intercepted`` 与普通异常同处理——终止本次 watcher 链、
  记录日志，不干扰赋值。
- 无运行中 event loop 时 watcher 静默跳过（赋值照常）。
- 顶层槽位的赋值只能观察、不能拦截（拦截请用 Python 原生的 property
  setter）；插件托管变量应以插件自有对象为载体（如 ``agent.i18n.locale =
  "en"`` 走该对象自己的 ``__setattr__``）。

声明独占、注册开放、谁声明谁 dispatch：

- 同名钩子点只允许一个声明者（冲突规则见 :meth:`HookRegistry.declare`）；
  ``by`` 必填——框架核心 ``by="core"``，扩展用自身标识。
- 声明后任何代码都可以向该钩子点挂 handler，无需再声明：声明者是
  「所有者」，注册者是「使用者」。
- 框架只在创建 / 恢复 / 销毁管线与 Turn 循环的固定位置 dispatch 核心钩子
  点；扩展自行 dispatch 自己声明的钩子点，调用方式与框架内部一致
  （``await hooks.<name>.dispatch(agent, value)``）。

注册时机与外部观察者：

- 注册不限于 ``setup()``：分发在触发时现场读 handler 列表，运行中途挂上
  即生效。REPL / HTTP 服务层 / 测试代码与插件走同一个公开注册入口，无
  权限分级；外部 handler 与插件 handler 权力相同（挂在决策型钩子点上同样
  可以改写 value 或 ``raise Intercepted``，纯观察用途请挂 ``after_*`` /
  ``on_*`` 点并原样返回 value）。
- 带自己的 ``by=`` （如 ``by="web"``）可获得整组管理能力
  （``remove_by_owner`` / 启停），不与插件 handler 混淆。
- 钩子只能从进程内代码注册：框架没有经 HTTP 挂钩子的端点，需要远程推送
  通道的服务层自行在进程内挂钩后转发。
- 错误不是事件：内部错误以异常上抛（无 ``on_error`` 兜底钩子），唯一可
  决策的错误钩子是 ``on_provider_error``；观测后台错误的现成通道是
  :mod:`flowing._unstable.logging` （落盘 logging.jsonl）。

``by=`` 惯例与整组管理：

- ``by`` 是整组管理的锚点：同一来源的 handler 共用同一个 ``by``，可以整组
  停用（``disable_by_owner``）或整组移除（``remove_by_owner``）。
- 注册顺序即执行顺序：同一钩子点上先注册的 handler 先执行；不做去重，
  同一 handler 重复注册、每次触发都会执行多次。

.. seealso::

   :class:`flowing.agent.Agent` （``hooks`` 属性、``watch`` 方法）
   :class:`flowing.agent.TurnContext` （turn 族钩子的 value 类型）
   :class:`flowing.errors.Intercepted` （handler 的硬阻断信号）
   :class:`flowing.errors.UnknownHookPointError` /
   :class:`flowing.errors.DuplicateHookPointError` （钩子点访问与声明错误）
   :class:`flowing.lists.ManagedList` （分组管理容器抽象）
   :mod:`flowing.plugins.skills` / :mod:`flowing.plugins.comm` /
   :mod:`flowing.plugins.cron` （扩展钩子点的声明方）
   :mod:`flowing.composables.retry` / :mod:`flowing.composables.compact`
   （Composable 钩子点声明方）
"""

import asyncio
import fnmatch
import inspect
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeAlias, TypeVar, overload

from flowing.errors import (
    DuplicateHookPointError,
    FlowingError,
    Intercepted,
    UnknownHookPointError,
)

if TYPE_CHECKING:
    from flowing.agent import Agent

_logger = logging.getLogger(__name__)
"""模块级 logger：Intercepted 重抛的 INFO 日志点与 watcher 异常记录点共用。"""

__all__ = [
    "HookRegistry",
    "HookList",
    "HookEntry",
    "HookHandler",
    "ManagedList",
    "Togglable",
    "PatternRegistrar",
    "on",
]

T = TypeVar("T")

HookHandler: TypeAlias = Callable[..., Any]
"""handler 统一签名 ``(agent, value) -> value``；同步返回或返回 awaitable 均可，
分发器检测到 awaitable 结果就 ``await`` （含构造阶段钩子点）。
此处用 ``Callable[..., Any]`` 承载是因为各钩子点 value 类型不同；
精确契约见模块 docstring 的「核心钩子点全集」。
"""


# ``ManagedList`` / ``Togglable`` 的 canonical home 在 ``flowing.lists``
# （通用容器抽象不是钩子专有）；此处 import + 再导出（__all__ 保留），
# :class:`HookList` 继承 ``ManagedList``。
from flowing.lists import ManagedList, Togglable


@dataclass
class HookEntry:
    """单个 handler 条目：注册元信息 + 过滤条件 + 开关。

    .. rubric:: 功能介绍

    :class:`HookList` 的元素类型，满足 :class:`Togglable` 契约。由
    :meth:`HookList.__call__` / :meth:`PatternRegistrar.__call__` 构造；
    用户通常不直接实例化，而是通过注册与分组管理 API 间接持有。

    pattern 过滤条件与 handler 绑定为一条记录，使 disable / enable /
    remove 等分组操作对「带 pattern 的 handler」同样生效。

    .. rubric:: 行为要点

    - ``pattern`` 非 ``None`` 时，分发先按
      ``fnmatch(getattr(value, hook_list.match_on), pattern)`` 过滤，
      不匹配则跳过本条目（value 原样透传给后续 handler）。
    - ``enabled=False`` 的条目仍保留在容器中（注册顺序不变），但不出现在
      迭代与分发中；之后调用 ``enable_by_owner`` / ``enable_by_tag``
      可恢复。

    .. seealso::

        :class:`HookList`、:class:`PatternRegistrar`、:class:`Togglable`
    """

    handler: HookHandler
    """钩子 handler，签名 ``(agent, value) -> value`` （同步或返回 awaitable）。
    """
    by: str | None
    """来源 / 所有者单一标识（如 ``"retry"``、``"guardrail"``）；注册时省略
    则记为 ``None``。与钩子点声明的 ``by`` （必填）是两个层面的字段。
    """
    tags: list[str] = field(default_factory=list)
    """任意标签列表，供批量分组管理。
    """
    pattern: str | None = None
    """fnmatch 过滤模式；仅经 ``hook["pattern"](handler)`` 注册时非 ``None``。
    """
    enabled: bool = True
    """启用标记；``ManagedList`` 的 disable/enable 直接翻转本字段。
    """


class PatternRegistrar:
    """``hook["pattern"](handler)`` 语法糖返回的注册器。

    .. rubric:: 功能介绍

    :meth:`HookList.__getitem__` 传入 ``str`` 时返回本对象；调用它即完成
    「仅当 value 的 ``match_on`` 属性匹配 pattern 时才执行」的 handler
    注册。

    「按名称过滤注册」是高频需求（只对 ``payment-*`` 工具做审批）。
    独立 Registrar 类型使 ``hook["pattern"]`` 与 ``hook(handler)`` 共享
    同一调用形态，注册产物仍是普通 :class:`HookEntry` （``pattern`` 字段
    非空），因此分组管理、注册顺序、分发算法完全一致，无第二套语义。

    .. rubric:: 使用示例

    .. code-block:: python

        self.hooks.before_tool_call["payment-*"](self._guard, by="guardrail")
        # fnmatch 规则：* 任意字符、? 单字符、[abc] 字符集合、字面量精确匹配

    .. rubric:: 行为要点

    - 调用即构造 ``HookEntry(handler=..., by=..., tags=..., pattern=<pattern>)``
      并追加到所属 :class:`HookList` 末尾；返回被注册的原 handler（装饰器
      形态不遮蔽被装饰名；需要 :class:`HookEntry` 句柄时经 ``HookList``
      的 ``int`` / ``slice`` 索引或批量管理 API 获取）。
    - 不匹配时 handler 不被调用，value 原样透传，不影响链的传递。
    - pattern 过滤在分发时按 entry 惰性求值（而非注册时生成包装函数），
      使 ``entry.pattern`` 可观测、可随 disable / enable 整体管理。
    - 边缘情况：对 value 不具有 ``match_on`` 属性的钩子点做 pattern 注册，
      注册本身成功；分发时 ``getattr`` 失败按 handler 普通异常规则直接
      上抛（属编程错误，不做兜底）。

    .. seealso::

        :meth:`HookList.__getitem__`、:class:`HookEntry`、
        :attr:`HookList.match_on`
    """

    _hook_list: "HookList"
    """所属钩子点容器。内部 API，不属稳定契约。

    （注解加引号：``HookList`` 定义在本类之后，裸名注解会在类体执行期
    触发 ``NameError``。）
    """
    _pattern: str
    """fnmatch 过滤模式。内部 API，不属稳定契约。
    """

    def __init__(self, hook_list: "HookList", pattern: str) -> None:
        """构造注册器。

        :param hook_list: 所属钩子点容器（``HookList.__getitem__`` 传入
            ``self``）。
        :param pattern: fnmatch 过滤模式（``__getitem__`` 的 ``str`` 下标）。
        """
        self._hook_list = hook_list
        self._pattern = pattern

    def __call__(
        self,
        handler: HookHandler,
        *,
        by: str | None = None,
        tags: list[str] | None = None,
    ) -> HookHandler:
        """以本 Registrar 的 pattern 注册 handler。

        .. rubric:: 行为要点

        等价于在所属 ``HookList`` 上注册并附加 ``pattern``；``by`` /
        ``tags`` 语义与 :meth:`HookList.__call__` 完全一致。返回被注册的
        原 handler（装饰器形态下被装饰名不被遮蔽；需要
        :class:`HookEntry` 句柄时经 ``HookList`` 索引或批量管理 API
        获取）。

        .. seealso::

            :meth:`HookList.__call__`
        """
        entry = HookEntry(handler=handler, by=by, tags=tags or [], pattern=self._pattern)
        self._hook_list.append(entry)
        return handler   # 返回原 handler（entry 经索引/管理 API 获取）



class HookList(ManagedList[HookEntry]):
    """单个钩子点的容器。

    .. rubric:: 功能介绍

    每个钩子点一个实例，由 :class:`HookRegistry` 持有。提供统一注册入口
    （``hook(handler, by=..., tags=...)``）、``__getitem__`` 三形态索引
    与统一分发入口 :meth:`dispatch`；分组管理能力继承自
    :class:`ManagedList`。

    注册顺序即执行顺序：同一钩子点上先注册的 handler 先执行。

    .. rubric:: 使用示例

    .. code-block:: python

        self.hooks.before_tool_call(self._audit, by="audit", tags=["security"])
        self.hooks.before_tool_call["payment-*"](self._guard, by="guardrail")

        entry = self.hooks.before_tool_call[0]        # int → HookEntry
        first_two = self.hooks.before_tool_call[:2]   # slice → list[HookEntry]
        self.hooks.before_tool_call.disable_by_owner("guardrail")

    .. rubric:: 行为要点

    - 注册入口：``__call__(handler, *, by=None, tags=None) -> HookHandler``，
      返回被注册的原 handler，追加到末尾（注册顺序即执行顺序）；``by``
      省略记为 ``None`` （与 :meth:`HookRegistry.declare` 的必填 ``by`` 是
      两个层面）。
    - ``__getitem__`` 三形态见下；``int`` / ``slice`` 基于含 disabled 的
      完整底层列表做位置索引（用于自省），与 ``__iter__`` / :meth:`dispatch`
      的「跳过 disabled」语义互不干扰。
    - 构造阶段钩子点（``before_create`` / ``after_create`` /
      ``before_destroy`` / ``after_destroy`` / ``before_recover`` /
      ``after_recover``）与运行期钩子点一样接受同步或异步 handler；注册时
      不做同步性检查，分发统一处理 awaitable。

    .. seealso::

        :class:`HookRegistry`、:class:`HookEntry`、:class:`PatternRegistrar`、
        :class:`ManagedList`
    """

    name: str
    """钩子点名（如 ``"before_tool_call"``），在所属 ``HookRegistry`` 内唯一。
    """
    by: str
    """钩子点的声明者标识（框架核心 ``"core"``，扩展用自身标识）；必填，
    见 :meth:`HookRegistry.declare` 的声明独占规则。
    """
    match_on: str
    """pattern 过滤时读取 value 的属性名，默认 ``"name"``；通信扩展使用
    ``"type"`` （``on_signal``）/ ``"topic"`` （``on_event``）。
    """

    def __init__(self, name: str, *, by: str, match_on: str = "name") -> None:
        """创建钩子点容器。通常只由 ``HookRegistry`` 预填与 ``declare()`` 调用。

        .. rubric:: 行为要点

        - ``by`` 必填关键字参数，不允许省略或显式传 ``None``——声明者身份是
          「声明独占」冲突判定的唯一依据。
        - ``match_on`` 必须是 value 类型具备的属性名字符串；本方法不校验
          （value 类型在声明时不可知），错误在分发时按普通异常上抛。

        .. seealso::

            :meth:`HookRegistry.declare`
        """
        self.name = name
        self.by = by
        self.match_on = match_on
        self._items = []

    def __call__(
        self,
        handler: HookHandler,
        *,
        by: str | None = None,
        tags: list[str] | None = None,
    ) -> HookHandler:
        """注册 handler 到本钩子点（统一注册入口）。

        .. rubric:: 功能介绍

        全框架唯一的注册形态：handler 用返回值、``shortcut`` 字段或
        ``raise Intercepted`` 表达行为。

        .. rubric:: 使用示例

        .. code-block:: python

            self.hooks.before_tool_call(self._audit, by="audit", tags=["audit"])
            self.hooks.on_provider_error(self._retry, by="retry")

        .. rubric:: 行为要点

        - 追加注册，注册顺序即执行顺序；返回被注册的原 handler（装饰器形态
          （``@hooks.before_tool_call``）下被装饰名不被遮蔽；需要
          :class:`HookEntry` 句柄时经 ``__getitem__`` ``int`` / ``slice``
          索引或批量管理 API 获取）。
        - 同一 handler 可重复注册（每次触发执行多次），不去重。
        - ``by`` 省略记为 ``None``；``tags`` 省略记为 ``[]``。
        - 构造阶段钩子点同样接受 async handler，分发统一 ``await``；watcher
          通道也一样——它由 ``__setattr__`` fire-and-forget 通知，watcher
          的 async 性与赋值语义无关。
        - 不校验 handler 签名（value 类型各异），签名错误在分发时按普通异常
          直接上抛。

        .. seealso::

            :meth:`dispatch`、:class:`PatternRegistrar`、:func:`on`
        """
        entry = HookEntry(handler=handler, by=by, tags=tags or [])
        self.append(entry)
        return handler   # 返回原 handler（entry 经索引/管理 API 获取）

    @overload
    def __getitem__(self, index: int) -> HookEntry: ...
    @overload
    def __getitem__(self, index: slice) -> list[HookEntry]: ...
    @overload
    def __getitem__(self, index: str) -> PatternRegistrar: ...
    def __getitem__(
        self, index: int | slice | str
    ) -> HookEntry | list[HookEntry] | PatternRegistrar:
        """三形态索引：位置自省（``int`` / ``slice``）与按名称过滤注册（``str``）。

        .. rubric:: 功能介绍

        - ``int`` → :class:`HookEntry`：按索引获取单个条目（含 disabled）。
        - ``slice`` → ``list[HookEntry]``：按切片获取条目列表（含 disabled）。
        - ``str`` → :class:`PatternRegistrar`：``hook["pattern"](handler)``
          注册语法糖。

        .. rubric:: 行为要点

        - 内部按 ``isinstance(index, (int, slice))`` 判定：int/slice 走位置
          索引，其余（str）走 pattern 注册。
        - int/slice 基于含 disabled 的完整底层列表；越界抛 ``IndexError``。
        - str 形态不读取既有条目，只返回注册器；pattern 语法遵循
          ``fnmatch`` （``*`` / ``?`` / ``[abc]`` / 字面量），匹配 value 的
          ``match_on`` 属性（``before_tool_call`` 上即 ``ToolCall.name``）。
        - watch 通道不是 HookList：注册用 ``hooks.watch('locale', handler)``
          或 ``agent.watch``，对 :class:`flowing.agent.FieldUpdate` 的
          ``name`` 做 pattern 匹配，字面量字段名即精确匹配。

        .. seealso::

            :class:`PatternRegistrar`
        """
        if isinstance(index, (int, slice)):  # 位置自省形态：基于含 disabled 的完整底层列表
            return self._items[index]  # 越界抛 IndexError
        # str 形态：不读取既有条目，返回 pattern 注册器
        return PatternRegistrar(self, index)

    async def dispatch(self, agent: Any, value: Any = None) -> Any:
        """统一分发算法：按注册顺序执行活跃 handler 的改写链。

        .. rubric:: 功能介绍

        全框架唯一的钩子分发实现。框架在创建 / 恢复 / 销毁管线与 Turn 循环
        固定位置 dispatch 核心钩子点；扩展 dispatch 自己声明的钩子点时调用
        的也是本方法（谁声明谁 dispatch）。

        .. rubric:: 使用示例

        .. code-block:: python

            # 扩展 dispatch 自己声明的钩子点（谁声明谁 dispatch）
            result = await agent.hooks.on_event.dispatch(agent, payload)

        .. rubric:: 行为要点

        - 迭代活跃条目（跳过 ``enabled=False``），逐条执行：
          ``entry.pattern`` 非空时先 ``fnmatch(getattr(value, self.match_on),
          entry.pattern)`` 过滤，不匹配则跳过（value 原样透传）。
        - ``result = entry.handler(agent, value)``；检测为 awaitable 则
          ``await``——同步 / 异步 handler 透明混用。
        - 首参 ``agent`` 是钩子宿主对象：Agent 专属钩子点上是 ``Agent``
          实例；Workflow 自己的 ``hooks`` （``before_tool_call`` /
          ``after_tool_call``）上是 ``Workflow`` 实例。dispatch 自身只透传、
          从不访问其属性；需要 Agent 能力的 handler 自行 ``isinstance``
          判断。
        - 携带 value 的钩子点（``value`` 参数非 ``None`` 调入）要求每个
          handler 返回值：handler 返回 ``None`` 视为编程错误，抛
          :class:`flowing.errors.FlowingError` （消息含钩子点名与 handler
          标识）。无 value 的钩子点（``after_create`` 等）不施加该检查。
        - ``value = result`` 后检查 ``getattr(value, "shortcut", None)``：
          非 ``None`` 立即停止链并返回该 value（协商短路；对应操作的
          ``after_`` 钩子由调用方保证照常触发）。
        - :class:`flowing.errors.Intercepted`：捕获后立即重抛——链停止、
          后续 handler 不执行、不当作错误（对应操作的 ``after_`` 钩子不
          触发）。
        - 普通异常直接上抛：不捕获、不通知、不继续后续 handler、无任何兜底
          钩子。
        - 无 handler（或全部 disabled / 被 pattern 过滤）时原样返回
          ``value``。

        .. seealso::

            :class:`flowing.errors.Intercepted`、
            :meth:`ManagedList.__iter__`
        """
        for entry in self:  # 经 ManagedList.__iter__：跳过 enabled=False
            if entry.pattern is not None:  # 条件：pattern 条目先过滤
                if not fnmatch.fnmatch(getattr(value, self.match_on), entry.pattern):
                    continue  # 不匹配 → 跳过，value 原样透传
            try:
                result = entry.handler(agent, value)
                if inspect.isawaitable(result):  # 条件：sync/async handler 透明混用
                    result = await result
            except Intercepted as exc:
                # Intercepted：捕获后立即重抛——链停止、后续 handler 不执行、
                # 不当错误处理（INFO 级日志，非 ERROR）
                _logger.info("hook %r intercepted: %s", self.name, exc)
                raise
            # 普通异常不经本 except：直接上抛，不捕获、不通知、无兜底钩子
            if value is not None and result is None:
                # 携带 value 的钩子点：handler 不 return 视为编程错误
                raise FlowingError(
                    f"hook {self.name!r}: handler {entry.handler!r} returned None"
                )
            value = result
            if getattr(value, "shortcut", None) is not None:
                return value  # 协商短路——链停止，after_ 钩子由调用方保证照常触发
        return value


class HookRegistry:
    """Agent 实例级的钩子点注册表。

    .. rubric:: 功能介绍

    每个 Agent 实例在 ``__init__`` 中创建一个独立实例（``agent.hooks``），
    构造时预填全部核心钩子点（``by="core"``，全集见模块 docstring 的两张
    表）；扩展经 :meth:`declare` 在 ``setup()`` 阶段就地声明自己的钩子点。

    钩子只影响当前 Agent 实例，无全局钩子表——同类的两个 Agent 可以有
    不同的钩子栈。钩子点集合是显式的：访问未声明的钩子点立即抛
    ``UnknownHookPointError`` （而非静默创建空钩子点），让「没调用
    ``use_skill()`` 却访问 ``before_skill_load``」这类错误在开发期暴露。

    .. rubric:: 使用示例

    .. code-block:: python

        async def setup(self):
            use_skill(self)   # 内部 declare("before_skill_load", by="skill")
            # 声明后任何代码都可注册（注册开放）
            self.hooks.before_skill_load(self._audit_load, by="audit")

        # 不调用 use_skill 的实例：
        self.hooks.before_skill_load   # → UnknownHookPointError

    .. rubric:: 行为要点

    - 钩子点集合只在两处写入：``__init__`` 预填核心点、
      :meth:`declare` 声明扩展点；任何其他路径不得写入。
    - 属性访问（``hooks.<name>``）经 ``__getattr__`` 只查找不创建：
      未声明 → :class:`flowing.errors.UnknownHookPointError`。
    - 预填的核心钩子点是 :class:`HookList` 实例，初始不含 handler；
      ``match_on`` 默认为 ``"name"``，仅 ``after_provider_gen`` 与
      ``on_provider_delta`` 为 ``"by"`` （value 携带来源标记，可按来源
      pattern 过滤注册）。
    - 不变量：同一钩子点名在注册表内唯一（声明独占，见 :meth:`declare`）。

    .. seealso::

        :class:`HookList`、
        :class:`flowing.errors.UnknownHookPointError`、
        :class:`flowing.errors.DuplicateHookPointError`
    """

    _hook_points: dict[str, HookList]
    """钩子点名 → 容器。只在 ``__init__`` 与 ``declare()`` 两处写入。
    内部 API，不属稳定契约。
    """

    _pending_on: list[tuple[HookHandler, str, "str | None", "list[str] | None", "str | None"]]
    """未结算的 ``@on`` 标记（(handler, hook_name, by, tags, pattern) 元组）：
    ``_init_hooks`` 收集时钩子点尚未声明的记录暂记于此；``declare()``
    创建同名钩子点时冲刷挂载（此刻尚无其它 handler，``@on`` handler 天然
    排最前）；``setup()`` 后的 PENDING 检查发现本列表非空 → 抛
    ``UnknownHookPointError``。内部 API，不属稳定契约。
    """

    def __init__(self) -> None:
        """构造并预填全部核心钩子点（``by="core"``）。

        .. rubric:: 行为要点

        - 预填集合为模块 docstring「核心钩子点全集」的完整集合，
          各 ``HookList`` 初始为空（无 handler）；``match_on`` 默认为
          ``"name"``，仅 ``after_provider_gen`` / ``on_provider_delta``
          为 ``"by"`` （value 携带来源标记，可按来源 pattern 过滤注册）。
        - 本方法是同步的（在 ``Agent.__init__`` 骨架阶段调用）。
        - 扩展钩子点不在此预填——未启用扩展的实例不承载其复杂度。

        .. seealso::

            :meth:`declare`
        """
        self._hook_points = {}
        self._watchers: list[tuple[str, Callable[[Any, Any], Any]]] = []   # watcher 通道（非钩子）
        self._pending_on = []   # 未结算的 @on 标记暂记列表
        # 预填核心钩子点（by="core"，初始无 handler；全集见模块
        # docstring「核心钩子点全集」两张表）。after_provider_gen /
        # on_provider_delta 以 match_on="by" 声明——value
        # （ProviderResponse / ProviderDelta）携带 by 来源标记，
        # handler 可按 "_turn" / "_side" / 插件自定义值做 pattern
        # 过滤注册；before_provider_gen 的 value 是 Context，
        # 暂不携带 by（注释约定：如需过滤副线，后续给 Context 加字段再开）
        self._hook_points["before_create"] = HookList("before_create", by="core")
        self._hook_points["after_create"] = HookList("after_create", by="core")
        self._hook_points["before_recover"] = HookList("before_recover", by="core")
        self._hook_points["after_recover"] = HookList("after_recover", by="core")
        self._hook_points["before_destroy"] = HookList("before_destroy", by="core")
        self._hook_points["after_destroy"] = HookList("after_destroy", by="core")
        self._hook_points["before_turn"] = HookList("before_turn", by="core")
        self._hook_points["before_turn_append"] = HookList("before_turn_append", by="core")
        self._hook_points["after_turn_append"] = HookList("after_turn_append", by="core")
        self._hook_points["before_provider_gen"] = HookList("before_provider_gen", by="core")
        self._hook_points["after_provider_gen"] = HookList("after_provider_gen", by="core", match_on="by")
        self._hook_points["on_provider_delta"] = HookList("on_provider_delta", by="core", match_on="by")
        self._hook_points["before_tool_call"] = HookList("before_tool_call", by="core")
        self._hook_points["after_tool_call"] = HookList("after_tool_call", by="core")
        self._hook_points["before_subagent_invoke"] = HookList("before_subagent_invoke", by="core")
        self._hook_points["after_subagent_invoke"] = HookList("after_subagent_invoke", by="core")
        self._hook_points["before_turn_abort"] = HookList("before_turn_abort", by="core")
        self._hook_points["after_turn"] = HookList("after_turn", by="core")
        self._hook_points["on_provider_error"] = HookList("on_provider_error", by="core")
        self._hook_points["before_enqueue"] = HookList("before_enqueue", by="core")
        self._hook_points["after_enqueue"] = HookList("after_enqueue", by="core")
        self._hook_points["before_dequeue"] = HookList("before_dequeue", by="core")
        self._hook_points["after_dequeue"] = HookList("after_dequeue", by="core")
        self._hook_points["before_fork"] = HookList("before_fork", by="core")
        self._hook_points["after_fork"] = HookList("after_fork", by="core")
        self._hook_points["before_cancel"] = HookList("before_cancel", by="core")
        self._hook_points["after_cancel"] = HookList("after_cancel", by="core")

    def declare(self, name: str, *, by: str, match_on: str = "name") -> HookList:
        """声明一个新钩子点（声明独占、注册开放）。

        .. rubric:: 功能介绍

        扩展（Composable / 插件的 ``use_xxx``）在 Agent 实例的 ``setup()``
        阶段就地声明自己的钩子点，并自行在使用处 dispatch。

        钩子点从「核心固定集」变为「声明即创建」：Skill 扩展声明
        ``before_skill_load``，Comm 扩展声明 ``on_signal``——不调用这些
        扩展的 Agent 实例没有这些钩子点。声明者是钩子点「所有者」，
        注册者是「使用者」；声明后任何代码都可挂 handler，无需再声明。

        .. rubric:: 使用示例

        .. code-block:: python

            def use_comm(agent):
                agent.hooks.declare("on_signal", by="comm", match_on="type")
                agent.hooks.declare("on_event",  by="comm", match_on="topic")

            # 之后任意代码（含其他扩展）可直接注册：
            agent.hooks.on_signal["permission_request"](handler, by="guard")

        .. rubric:: 行为要点

        - ``by`` 为必填关键字参数——框架核心 ``by="core"``，扩展用自身标识；
          不允许省略或传 ``None``。
        - 同名 + 同 ``by``：幂等，返回已有 ``HookList`` （``match_on``
          以首次声明为准，不校验后续传参是否一致）。
        - 同名 + 不同 ``by``：抛
          :class:`flowing.errors.DuplicateHookPointError`，消息含双方
          ``by``。
        - 返回的 ``HookList`` 由声明者自行 dispatch；框架不会代 dispatch
          扩展钩子点（谁声明谁 dispatch）。
        - 声明时机约束：应在 ``setup()`` （或 ``use_xxx``）中完成；运行期
          Turn 循环内声明虽不禁止，但属扩展自身责任，框架不做时序保障。
        - ``@on`` 冲刷：创建钩子点（而非走「同名同 by 幂等返回已有」路径）时，把 ``_pending_on``
          里同名记录全部挂载进新 ``HookList`` （含 pattern 的走
          ``HookList[pattern]`` 通道）并从暂记列表移除——此刻尚无其它
          handler，``@on`` handler 天然排最前。幂等路径（同名 + 同 ``by``）
          不冲刷——首次声明时已结算。

        :raises flowing.errors.DuplicateHookPointError:
            同名钩子点已被不同 ``by`` 声明。

        .. seealso::

            :class:`HookList`、:meth:`HookList.dispatch`、
            ``flowing.plugins.skills.use_skill``
        """
        existing = self._hook_points.get(name)
        if existing is not None:
            if existing.by == by:
                return existing  # 同名 + 同 by：幂等，match_on 以首次声明为准
            raise DuplicateHookPointError(name, existing.by, by)  # 同名 + 不同 by
        hook_list = HookList(name, by=by, match_on=match_on)  # 声明即创建
        self._hook_points[name] = hook_list  # _hook_points 唯二写入点之一
        # 冲刷 _pending_on 里同名 @on 标记（此刻无其它 handler，
        # @on handler 天然排最前）；幂等路径上方已 return，不会走到这里
        for bound, hook_name, on_by, tags, pattern in list(self._pending_on):
            if hook_name != name:
                continue
            if pattern is None:
                hook_list(bound, by=on_by, tags=tags)
            else:
                hook_list[pattern](bound, by=on_by, tags=tags)
            self._pending_on.remove((bound, hook_name, on_by, tags, pattern))
        return hook_list

    def watch(self, name: str, handler: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
        """注册一个 watcher（不是普通钩子点）。

        .. rubric:: 功能介绍

        与 ``Agent.watch`` 对应的低层注册入口：把 ``(agent, value)``
        形态的 handler 存入 watcher 通道。watcher 由 ``_notify_watch``
        以 fire-and-forget 方式触发，与普通钩子不同：不参与改写 /
        ``Intercepted`` / ``shortcut``，返回值被忽略，异常只记录日志。

        :param name: fnmatch pattern，匹配 ``value.name`` 字段。
        :param handler: ``(agent, value) -> None`` （同步或异步均可）。
        :return: handler（便于装饰器写法）。
        """
        self._watchers.append((name, handler))
        return handler

    def _notify_watch(self, agent: Any, value: Any) -> None:
        """fire-and-forget 触发 watcher 通道（内部 API，不属稳定契约）。

        与普通钩子的 dispatch 不同：本方法不在任何 async 管线上被
        ``await``，而是自己调度一个后台任务；无运行中 event loop 时
        静默跳过。watcher handler 的返回值被忽略，异常只记录日志。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return   # 无运行中 event loop：跳过 watcher（赋值/写透照常）
        watchers = list(self._watchers)
        async def _run_watch() -> None:
            for pattern, handler in watchers:
                if not fnmatch.fnmatch(getattr(value, "name", ""), pattern):
                    continue
                try:
                    result = handler(agent, value)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    # 观察者异常只记录日志，不影响后续 watcher 与赋值/写透
                    _logger.exception("watcher %r raised for %r", pattern, getattr(value, "name", ""))
        asyncio.ensure_future(_run_watch())

    def __getattr__(self, name: str) -> HookList:
        """按名查找钩子点容器（只查找，不创建）。

        .. rubric:: 行为要点

        - 命中（含预填核心点与已 declare 的扩展点）→ 返回对应容器。
        - 未命中 → 抛 :class:`flowing.errors.UnknownHookPointError`，
          不静默创建——这是「声明即创建」的强制面。

        :raises flowing.errors.UnknownHookPointError:
            访问未声明的钩子点。

        .. seealso::

            :meth:`declare`、:class:`flowing.errors.UnknownHookPointError`
        """
        try:
            return self._hook_points[name]  # 命中（核心预填点与已 declare 扩展点）
        except KeyError:
            raise UnknownHookPointError(name) from None  # 只查找，不静默创建




class OnRegistrar:
    """``on(...)`` 返回的装饰器对象。

    .. rubric:: 功能介绍

    由 :func:`on` 构造：``__call__`` 是无 pattern 的装饰形态，
    ``__getitem__`` 收 pattern 后返回装饰器——镜像运行期
    ``hooks.<name>(handler)`` / ``hooks.<name>[pattern](handler)`` 两种
    注册形态。两种形态都只向函数追加一条标记记录并返回原函数。

    作为 ``on()`` 的返回值被用户瞬时消费，不应被保存或复用；本类不属
    稳定契约。

    .. seealso::

        :func:`on`
    """

    def __init__(self, hook_name: str, by: "str | None",
                 tags: "list[str] | None") -> None:
        """构造装饰器对象（由 :func:`on` 调用）。

        :param hook_name: 目标钩子点名。
        :param by: 来源标识（``@on`` 的 ``by`` 参数，省略为 ``None``）。
        :param tags: 标签列表（``@on`` 的 ``tags`` 参数，省略为 ``None``）。
        """
        self.hook_name = hook_name
        self.by = by
        self.tags = tags

    def _mark(self, handler: HookHandler, pattern: "str | None") -> HookHandler:
        """追加一条标记记录并返回原函数（装饰器不遮蔽名字）。"""
        marks = getattr(handler, "__flowing_hooks__", None)
        if marks is None:
            marks = ()
            setattr(handler, "__flowing_hooks__", marks)
        # 记录形：（hook_name, by, tags, pattern）；tuple 追加允许叠多个 @on
        setattr(handler, "__flowing_hooks__",
                marks + ((self.hook_name, self.by, self.tags, pattern),))
        return handler

    def __call__(self, handler: HookHandler) -> HookHandler:
        """无 pattern 形态：``@on('before_tool_call')``。"""
        return self._mark(handler, None)

    def __getitem__(self, pattern: str) -> Callable[[HookHandler], HookHandler]:
        """pattern 形态：``@on('on_signal')['agent-*']``——与运行期
        ``hooks.on_signal['agent-*'](handler)`` 同语义（按该钩子点的
        ``match_on`` 字段 fnmatch 过滤）。"""

        def decorator(handler: HookHandler) -> HookHandler:
            return self._mark(handler, pattern)

        return decorator


def on(
    hook_name: str,
    *,
    by: str | None = None,
    tags: list[str] | None = None,
) -> OnRegistrar:
    """声明式钩子注册装饰器（``@on('hook_name')`` / ``@on('hook_name')['pattern']``）。

    .. rubric:: 功能介绍

    在 ``.fya`` 的 ``$script`` 块或手写 Agent 子类的类体中，把一个方法
    标记为钩子 handler；``Agent.__init__`` 阶段的 ``_init_hooks()`` 收集
    全部被标记方法并注册到实例的 ``hooks`` 上，早于 ``setup()`` 执行。

    ``before_create`` 在 ``setup()`` 运行前触发，实例上尚无从调用
    ``self.hooks.before_create(...)``——``@on('before_create')`` 是注册
    创建期钩子的唯一方式。``.fya`` 声明式入口与类体语义对齐，使声明式与
    手写子类生成完全相同的 Python 类模型。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing import on

        class OrderAgent(Agent):
            @on('before_create')
            def _(self, kwargs):
                kwargs.setdefault('locale', 'zh')
                return kwargs          # 改写创建 kwargs

            @on('before_tool_call', by="audit", tags=["audit"])
            def _(self, tool_call):
                tool_call.args['request_id'] = self.node_id
                return tool_call

            @on('on_signal')['agent-message-*']   # pattern 形态
            def _(self, envelope):
                return envelope

    ``.fya`` 等价写法：

    .. code-block:: text

        ---
        $script:
        from flowing import on

        @on('before_tool_call')
        def _(self, tool_call):
            tool_call.args['lang'] = self.locale
            return tool_call

    .. rubric:: 行为要点

    - 本装饰器只做标记：向函数的 ``__flowing_hooks__`` 属性 tuple 追加
      一条记录 ``(hook_name, by, tags, pattern)``，不触碰任何注册表；返回
      原函数（不遮蔽名字，方法仍可直调、可覆写）。叠多个 ``@on`` → 多条
      记录，同一方法可挂在多个钩子点上。
    - 收集（``Agent._init_hooks``，``__init__`` 阶段）：按
      ``type(self).__mro__`` 逐名解析——派生优先判覆写（子类覆写未标记的
      同名方法，基类标记不生效）；注册顺序为基类 → 派生类。注册未绑定
      函数，分发签名与其他 handler 统一为 ``(agent, value) -> value``。
    - 两段式注册：钩子点已存在（核心预填点）→ 立即注册；尚未声明（插件
      点，等 ``use_xxx()`` 在 ``setup()`` 里 declare）→ 记入实例的暂记列表
      （内部机制，最终行为见下一条）。``HookRegistry.declare()`` 创建同名
      钩子点时冲刷挂载——此刻无其它 handler，``@on`` handler 天然排最前。
    - setup 后结算：``setup()`` 返回后的 PENDING 检查发现暂记列表非空 →
      抛 :class:`flowing.errors.UnknownHookPointError` （拼错的
      钩子点名或未启用对应插件，必须死在创建期，不做静默死信）。``@on``
      的目标钩子点必须在 setup 结束前被 declare（与「Composable 在
      setup 里启用」约定一致）。
    - ``by`` 省略时记 ``None``，与 :meth:`HookList.__call__` 的默认一致
      ——``@on`` 与括号调用是同语义的两种注册形态。注意
      ``remove_by_owner(None)`` 会精确匹配并删除全部匿名 handler（含
      其他来源注册的），需按来源批量管理时应显式给 ``by``。
    - 构造阶段钩子点（``before_create`` 等）与运行期钩子点一样接受同步
      或异步 handler；``_init_hooks()`` 不做同步性校验。

    .. seealso::

        :class:`HookRegistry`、:meth:`HookList.__call__`、
        ``flowing.agent.Agent._init_hooks``
    """
    return OnRegistrar(hook_name, by=by, tags=tags)
