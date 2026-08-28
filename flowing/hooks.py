"""``flowing.hooks`` —— 实例级钩子基础设施。

.. rubric:: 功能介绍

本模块提供 Flowing 的**可编程扩展机制**（框架核心三大基础设施之一，与消息模型、
Turn 循环并列）的全部公共与扩展 API：

- :class:`HookRegistry`：Agent 实例级的钩子点注册表。每个 Agent 实例持有一个
  独立实例（``agent.hooks``），构造时预填全部**核心钩子点**（``by="core"``），
  扩展经 :meth:`HookRegistry.declare` 就地声明新钩子点。
- :class:`HookList`：单个钩子点的容器。提供统一注册入口（``__call__``）、
  ``__getitem__`` 三形态（``int`` / ``slice`` / ``str``）与统一分发入口
  :meth:`~HookList.dispatch`。
- :class:`HookEntry`：单个 handler 条目（``handler / by / tags / pattern /
  enabled``），满足 :class:`Togglable` 元素契约。
- ``ManagedList`` 与 ``Togglable``：分组管理的公共容器抽象，**canonical
  home 在 :mod:`flowing.lists`**（通用容器，恰好历史上定义在本模块，
  C1 裁决迁出；本模块 import + 再导出，旧引用保持有效）。真实使用方
  为 ``HookList`` 与 ``PromptBlockList``（两家族分界见
  :mod:`flowing.lists` 模块 docstring）。
- :class:`PatternRegistrar`：``hook["pattern"](handler)`` 语法糖的注册器。
- ``watch`` 是**独立的 watcher 通道**，不是普通钩子点：经
  ``self.hooks.watch(name, handler)`` 注册，value 为
  :class:`flowing.agent.FieldUpdate`；``Agent.watch`` 是它的
  ``(new, old)`` 回调糖。watcher 永远 fire-and-forget、返回值被忽略。
- :func:`on`：声明式钩子注册装饰器（``.fya`` 的 ``$script`` 与手写子类通用）。

.. rubric:: 设计动机

- **统一注册**：抛弃 v1 的 ``pipe()`` / ``on()`` / ``intercept()`` 三种显式语义。
  「框架知道 handler 想干什么」的假设不成立——真实 handler 常同时观察与阻断
  （Guardrail 先检查、命中才阻断）。统一为一种注册方式后，handler 用
  「返回值 / ``shortcut`` 字段 / ``raise Intercepted``」自行表达行为，
  **注册顺序即执行顺序**。
- **机制 vs 策略**：本模块只提供机制——注册入口、handler 签名、dispatch 算法、
  阻断语义、分组管理、钩子点声明。安全规则、审批 UI、重试策略等全部是策略，
  由 Composable / 扩展 / 应用层以 handler 形式注入，框架核心不含任何策略。
- **实例级**：钩子点与 handler 都挂在 Agent 实例上，仅对当前实例生效。
  不调用 ``use_skill()`` 的 Agent 没有 ``before_skill_load`` 钩子点——
  每个实例只承担它需要的复杂度。
- **声明即创建**：钩子点从「核心固定集」变为「核心预填 + 扩展 declare」。
  ``HookRegistry._hook_points`` 只在两处写入：``__init__`` 预填核心点、
  :meth:`~HookRegistry.declare` 声明扩展点；``__getattr__`` 只查找、不创建。

.. rubric:: handler 统一签名与三种合法出口

所有钩子 handler 签名统一为 ``(agent, value) -> value``：

- ``agent`` 是当前 Agent 实例；``value`` 类型由钩子点决定（见下方全集表）。
- 出口一：``return value``（改写后或原样）——传给下一个 handler。
- 出口二：在 value 上设置 ``shortcut`` 字段后返回——dispatch 链停止，
  调用方跳过默认逻辑（协商短路）。
- 出口三：``raise Intercepted("原因")``——硬阻断，链停止，整个操作标记为无效。
- **不 return 视为错误**：dispatch 对携带 value 的钩子点检测 handler 返回值，
  返回 ``None`` 时抛出 :class:`flowing.errors.FlowingError`（具体子类型由
  errors 模块定稿），消息含钩子点名与 handler 标识。
- 调度器对同步 / 异步 handler 透明：``inspect.isawaitable`` 检测后 ``await``。
  **全部钩子点（含创建 / 销毁 / 恢复管线）均接受 sync 或 async handler**
  （M-37 裁决：原「构造阶段仅同步 handler」限制已删除——四个生命周期钩子
  的 dispatch 点都在 async 管线内，限制的前提已不存在；注册时不再做
  同步性检查）。

.. rubric:: dispatch 统一算法

``HookList.dispatch`` 是全局唯一的钩子分发算法（扩展自行 dispatch 自己声明的
钩子点时调用的也是它）::

    async def dispatch(self, agent, value=None):
        for entry in self._items:                    # __iter__ 语义：跳过 enabled=False
            if entry.pattern is not None:
                if not fnmatch(getattr(value, self.match_on), entry.pattern):
                    continue                         # 不匹配 → 跳过，value 原样透传
            result = entry.handler(agent, value)
            if inspect.isawaitable(result):
                result = await result
            # 携带 value 的钩子点：result 为 None 视为 handler 编程错误，抛 FlowingError
            value = result
            if getattr(value, "shortcut", None) is not None:
                return value                         # 协商短路——链停止
        return value

异常规则（三种停止 / 错误路径，框架只区分「有意的阻止」与「意外错误」）：

- ``Intercepted``：dispatch 捕获后**立即重抛**，链停止，不当作错误处理；
  对应操作的 ``after_`` 钩子**不触发**（由调用方保证）；INFO 级日志；
  典型场景为审批拒绝、安全阻断、权限检查。
- **普通异常直接上抛**：不捕获、不通知、不继续后续 handler、**无任何兜底
  钩子**（v1 的 ``on_error`` 兜底设计已废弃）。钩子是代码逻辑，出错不继续。
- ``shortcut``：链停止但**不是错误**；对应操作的 ``after_`` 钩子**照常触发**
  （事情仍发生了，只是默认执行被替代，如缓存命中跳过工具执行）。

.. rubric:: 核心钩子点全集表（HookRegistry 预填，``by="core"``）

文档 14 落锤后：物理 Turn 结构已废除，Turn 仅为逻辑执行阶段——turn 族钩子
value 一律为 :class:`flowing.agent.TurnContext`（逻辑 turn 的执行期临时对象，
不落盘、不进树）；新增 ``on_provider_delta`` 流式观察点。
``after_turn`` 是所有路径的唯一收尾观察点（``after_turn_abort`` /
``after_turn_finished`` 已删除：路径分流由 handler 读 ``turn.aborted``
承担，不为无场景的「最后一刻」区分预留钩子点）。

============================ ====================== ======================== ==========================================
钩子点                       触发时机               value 类型               handler 能力与约束
============================ ====================== ======================== ==========================================
``before_create``             创建管线 ``setup()``   ``dict``（kwargs）       可改写 kwargs；sync/async 均可；
                             前                                              只能经 :func:`on` 声明（setup 尚未运行）；
                                                                            **create 管线专属**（recover 不触发）——
                                                                            setup() 不区分管线，「仅创建时做」的
                                                                            逻辑由本钩子承担
``after_create``              状态写盘、``_nodes``   无（handler 收 agent）   sync/async 均可；观察 / 收尾
                             注册完成后、工作循环
                             启动前
``before_recover``           恢复管线 ``setup()``   ``dict``（args）         可改写 args；恢复管线为异步上下文，
                             前                                              sync/async 均可
``after_recover``            ``Agent._restore()``   无（handler 收 agent）   观察 / 副作用；sync/async 均可
                             与 ``_nodes`` 注册完成
                             后、工作循环启动前
``before_destroy``           ``destroy()`` 管线内   无（handler 收 agent）   sync/async 均可
``after_destroy``            ``destroy()`` 收尾     无（handler 收 agent）   sync/async 均可
``before_turn``              逻辑 turn 开始、       ``TurnContext``          可改写 ``pending_messages`` /
                             批次挂树前                                      ``raise Intercepted``
                                                                             （阻断则批次丢弃不落盘）
``before_turn_append``       消息 append 到树前     ``flowing.message.       可改写 / ``raise Intercepted``
                                                    Message``
``after_turn_append``        消息 append 与落盘后   ``Message``              观察 / 改写返回值不进树（消息已落盘）
``before_provider_gen``             LLM 调用前             ``flowing.context.       可改写（v1 ``before_send`` /
                                                    Context``                ``after_send`` 已内化于此）
``after_provider_gen``              LLM 调用返回后         ``flowing.providers.     可改写（v1 ``before_receive`` /
                                                    ProviderResponse``       ``after_receive`` 已内化于此）；
                                                                             ``match_on="by"``，可按来源过滤
``on_provider_delta``        每个 delta 到达        ``flowing.providers.     纯观察（volatile，不落盘）；
                             （流式逐条；非流式合成 ProviderDelta``          ``provider_gen()`` 丢弃 dispatch 返回
                             一条全量）                                      值（改写无效——改写单条 delta
                                                                             无意义，需改写走
                                                                             ``after_provider_gen`` 改整条消息）；
                                                                             ``match_on="by"``，可按来源过滤；
                                                                             流式与否由 ``provider_gen(stream=...)``
                                                                             显式参数决定（S-17 最终裁决，
                                                                             不由订阅者存在性决定）
``before_tool_call``         工具执行前             ``flowing.tool.          可改写 / ``shortcut`` 短路 /
                                                    ToolCall``               ``raise Intercepted``
``after_tool_call``          工具执行后             ``flowing.tool.          可改写结果；shortcut 路径下照常触发；
                             （shortcut 路径照常）  ToolResult``             改写产物经 ``tool_call`` 收尾归一
                                                                             （D19：``Agent.tool_call``
                                                                             return 前幂等再跑
                                                                             ``normalize_output``，handler
                                                                             把 ``output`` 改成原料也会被
                                                                             归一）
``before_subagent_invoke``   子 Agent 唤起时        ``flowing.agent.         可改写 / ``raise Intercepted``；
                             resolve 校验后         SubagentInvocation``     挂在**父 Agent** 的 hooks 上
``after_subagent_invoke``    子 Agent 结果构造后、  ``SubagentInvocation``   可改写 result（先于交付，return 值
                             交付前                                           与 SUBAGENT 消息同源）；不接
                                                                             Intercepted；挂在父 Agent 的 hooks 上
``before_turn_abort``        abort 时触发一次       ``TurnContext``          观察 / 收尾前干预
``after_turn``               所有路径收尾           ``TurnContext``          ``finally`` 保证触发开始；
                             （含异常终止）                                  destroy 取消工作循环 Task 时
                                                                             cancel 可能落在 dispatch 的
                                                                             await 点上，after_turn 链
                                                                             被中断跳过（等待者不挂起由
                                                                             destroy 第 1 步兜底）；唯一
                                                                             收尾观察点（``value.aborted``
                                                                             区分异常终止）
``on_provider_error``           ``provider_gen()`` 内 LLM     ``flowing.agent.         **唯一可决策错误钩子**：handler 内执行
                             调用 异常              ProviderErrorContext``      动作（sleep / 改 ``self.model`` /
                                                                             ``abort_turn()``）并写
                                                                             ``ctx.can_continue``；
                                                                             ``ContextLengthError`` 不经过本钩子
============================ ====================== ======================== ==========================================

.. note::

   ``watch`` 通道由 ``Agent.__setattr__`` 和状态写透路径以
   **fire-and-forget** 方式通知（``asyncio.ensure_future``，不 await）：
   赋值不等待 watcher，watcher 的执行时序与赋值本身不保证先后。
   这是 watcher 与普通钩子的根本区别——其余钩子点都在管线上显式 await。

.. note::

   插件注入的托管变量（如 i18n 插件的 ``agent.i18n``）应以**插件自有
   对象**为载体：``agent.i18n.locale = "en"`` 走的是该对象自己的
   ``__setattr__`` / property，拦截、规范化、联动由插件在自己的类里
   实现。顶层槽位的写入（``agent.i18n = <别的对象>``）只能经
   ``watch`` 观察、不能拦截；观察后可纠正性回写（会二次触发
   监听）。

消息流转 / fork / 取消 / 子 Agent 返回的核心钩子点（同为预填集，``by="core"``；
value 类型与语义与 :class:`flowing.agent.Agent` 各方法的 dispatch 时序一致）：

============================ ====================== ======================== ==========================================
钩子点                       触发时机               value 类型               handler 能力与约束
============================ ====================== ======================== ==========================================
``before_enqueue``           消息入队前             ``flowing.message.       可改写 / ``raise Intercepted``
                                                    Message``                （拦截入队，队列不变）
``after_enqueue``            消息入队后             ``Message``              观察
``before_dequeue``           工作循环出队前           无（handler 收 agent）   观察队列
``after_dequeue``            出队后、回合开始前     ``list[Message]``        可改写（变换本逻辑 turn 消费的
                                                                             消息列表）
``before_fork``              ``fork()`` 切换前      ``str``（目标消息 id）   可改写 target / ``raise
                                                                             Intercepted`` 阻止
``after_fork``               ``fork()`` 切换后      ``str``（原 head 的消息  纯观察（日志 / 通知 UI 刷新分支）
                                                    id）
``before_cancel``            ``cancel()``/          ``flowing.agent.         ``raise Intercepted`` 阻止取消
                             ``stop()``             CancelContext``
                             置位 abort 前
``after_cancel``             信号置位后立即触发     ``CancelContext``        纯观察（日志 / 通知 / 审计）
============================ ====================== ======================== ==========================================

扩展自行 ``declare`` + 自行 dispatch 的钩子点（不在预填集内，未启用扩展的实例
访问它们抛 :class:`flowing.errors.UnknownHookPointError`）：

- Skill 扩展：``before_skill_load`` / ``after_skill_load``（``by="skill"``，
  ``match_on="name"``）。
- Comm 扩展：``on_signal``（``by="comm"``，``match_on="type"``）/
  ``on_event``（``by="comm"``，``match_on="topic"``）。
- Cron 扩展：``on_cron_trigger``（``by="cron"``，见 plugins/cron 规约）。
- Compact Composable：``on_compact``（``by="compact"``，见 composables/compact
  规约）。
- Retry Composable：``on_retry``（``by="retry"``，见 composables/retry 规约）。

.. rubric:: 两类钩子语义

- ``before_`` / ``after_``：Agent 自身控制流（主动操作）前后的拦截 / 加工 /
  通知点。
- ``on_``：外部触发（被动响应），**只描述触发方向，不承诺 handler 行为能力**——
  各钩子点独立定义（``on_provider_error`` 可决策、``on_signal`` 可改 payload、
  ``on_event`` 纯观察）。不得假设 ``on_`` handler 都只能观察。
- ``watch`` 属**外部触发**：赋值来源不一定是 Agent 内部
  （模式切换 Composable、i18n 插件、外部代码），watcher 被动响应、
  互不干扰。

.. rubric:: 声明独占、注册开放、谁声明谁 dispatch

- **声明独占**：同名钩子点只允许一个声明者（见 :meth:`HookRegistry.declare`
  的冲突规则）；``by`` 必填——框架核心 ``by="core"``，扩展用自身标识。
- **注册开放**：声明后任何代码都可向该钩子点挂 handler，无需再声明；
  声明者是「所有者」，注册者是「使用者」。
- **谁声明谁 dispatch**：框架只在创建 / 恢复 / 销毁管线与 Turn 循环的固定位置
  dispatch 核心钩子点；扩展自行 dispatch 自己声明的钩子点，调用方式与框架
  内部完全一致：``await hooks.<name>.dispatch(agent, value)``。

.. rubric:: 外部观测者接入（REPL / Web / 自建交互层）

钩子对**进程内任何拿到 Agent 活实例引用的代码**开放——REPL、HTTP 服务层、
测试代码与插件走的是同一个公开注册入口，无权限分级：

.. code-block:: python

    # 服务进程内（与 Runtime 同进程）：挂观察 handler 感知后台事件
    agent = runtime.get_node(agent_id)          # 同步查表，不触发现场恢复
    agent.hooks.after_turn(my_ui_renderer, by="web")
    agent.hooks.on_provider_error(my_alert, by="web")

- **注册时机不限于 ``setup()``**：dispatch 在触发时现场读 handler 列表，
  运行中途挂上即生效。
- **带自己的 ``by=``**（如 ``by="repl"`` / ``by="web"``）：获得完整批量
  管理能力（``remove_by_owner``、Togglable 启停），不与插件 handler 混淆。
- **同步 / 异步 handler 均接受**。
- **边界一（同进程）**：``cmd_serve`` 的 HTTP 端点集是封闭的
  （``POST message`` / ``GET snapshot`` / ``GET /healthz``），**没有
  「经 HTTP 挂钩子」的端点**——远端客户端想要推送通道，须继承内置实现
  或自写 server，在服务进程内挂钩后自行转发。
- **边界二（权力对等）**：外部 handler 与插件 handler 权力相同——挂在
  决策型钩子点（``before_turn`` / ``before_tool_call``）上同样可以改写
  value 或 ``raise Intercepted``。纯观测用途请挂在 ``after_*`` /
  ``on_*`` 通知型点上，并 ``return value`` 原样透传。
- **错误不是事件**：内部错误以异常上抛（``on_error`` 兜底钩子已废弃），
  唯一可决策的错误钩子是 ``on_provider_error``；观测后台错误的现成通道是
  ``flowing._unstable.logging.LoggingPlugin``（落盘 logging.jsonl）。

.. rubric:: 使用示例

.. code-block:: python

    # 手写子类：setup() 中注册
    async def setup(self):
        self.hooks.before_tool_call(self._audit, by="audit", tags=["security"])
        self.hooks.before_tool_call["payment-*"](self._guard, by="guardrail")
        self.watch("current_mode", self._on_mode_change)

    # 扩展声明并注入触发函数（同步；dispatch 点长在被注入的
    # agent.skill_load 内——见 flowing.plugins.skills.use_skill）
    def use_skill(agent):
        agent.hooks.declare("before_skill_load", by="skill")
        ...
        agent.skill_load = ...   # 注入加载函数（仅当 agent 尚无此函数才绑
        #                          定——「检查后跳过」共同约定；skill-load
        #                          工具条目另行 opt-in）

    # agent.skill_load 内部（async 上下文）触发钩子：
    #   await agent.hooks.before_skill_load.dispatch(agent, ctx)

``.fya`` 声明式入口（``$script`` 中的 ``@on`` 编译进 ``_init_hooks()``，
实例 ``__init__`` 阶段注册，早于 ``setup()``）：

.. code-block:: text

    ---
    $script:
    from flowing import on

    @on('before_tool_call')
    def _(self, tool_call):
        tool_call.args['lang'] = self.locale
        return tool_call

    @on('before_create')
    def _(self, kwargs):
        kwargs.setdefault('locale', 'zh')
        return kwargs

.. seealso::

    :class:`flowing.agent.Agent`（``hooks`` 属性、``watch`` 方法、`_init_hooks`）
    :class:`flowing.agent.TurnContext`（turn 族钩子 value）
    :class:`flowing.errors.Intercepted` / ``UnknownHookPointError`` / ``DuplicateHookPointError`` / ``FlowingError``
    :class:`flowing.context.PromptBlockList`（ManagedList 语义的另一使用方）
    ``flowing.plugins.skills.use_skill`` / ``flowing.composables.retry.use_retry``
    规约检查表：``01-spec覆盖角度.md`` §2.3（时序与钩子契约）
"""

import asyncio
import fnmatch
import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeAlias, TypeVar, overload

if TYPE_CHECKING:
    from flowing.agent import Agent

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
dispatch 经 ``inspect.isawaitable`` 透明处理（M-37：含构造阶段钩子点）。
此处用 ``Callable[..., Any]`` 承载是因为各钩子点 value 类型不同；
精确契约见模块 docstring 的「核心钩子点全集表」。
"""


# ``ManagedList`` / ``Togglable`` 的 canonical home 已迁至
# ``flowing.lists``（C1 裁决：通用容器抽象不是钩子专有）；此处
# import + 再导出（__all__ 保留），旧引用保持有效。
from flowing.lists import ManagedList, Togglable


@dataclass
class HookEntry:
    """单个 handler 条目（注册元信息 + 过滤条件 + 开关）。

    .. rubric:: 功能介绍

    ``HookList`` 的元素类型，满足 :class:`Togglable` 契约。由
    :meth:`HookList.__call__` / :meth:`PatternRegistrar.__call__` 构造；
    用户通常不直接实例化。

    .. rubric:: 设计动机

    分组管理（disable/enable/remove × tag/owner）需要注册时的元信息载体；
    pattern 过滤需要把过滤条件与 handler 绑定为一条记录，使 disable 等操作
    对「带 pattern 的 handler」同样生效。

    .. rubric:: 行为规约

    - ``pattern`` 非 ``None`` 时，dispatch 先按 ``fnmatch(getattr(value,
      hook_list.match_on), pattern)`` 过滤，不匹配则跳过本条目（value 原样
      透传给后续 handler）。
    - ``enabled=False`` 的条目保留在 ``HookList._items`` 中，但不出现在
      迭代与 dispatch 中。

    .. rubric:: 调用关系（审计）

    - 被调：无（纯数据 dataclass）
    - 实例化方：``flowing.hooks.HookList.__call__`` 与
      ``flowing.hooks.PatternRegistrar.__call__``（时机：每次注册
      handler）；``flowing.composables.retry.use_retry`` 后置条件断言
      链尾追加本类实例（时机：use_retry 安装时，文档级引用）

    .. seealso::

        :class:`HookList`、:class:`PatternRegistrar`、:class:`Togglable`
    """

    handler: HookHandler
    """钩子 handler，签名 ``(agent, value) -> value``（同步或返回 awaitable）。
    """
    by: str | None
    """来源 / 所有者单一标识（如 ``"retry"``、``"guardrail"``）；注册时省略
    则记为 ``None``。与钩子点声明的 ``by``（必填）是两个层面的字段。
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
    「仅当 value 的 ``match_on`` 属性匹配 pattern 时才执行」的 handler 注册。

    .. rubric:: 设计动机

    「按名称过滤注册」是高频需求（只对 ``payment-*`` 工具做审批）。独立
    Registrar 类型使 ``hook["pattern"]`` 与 ``hook(handler)`` 共享同一
    调用形态，注册产物仍是普通 :class:`HookEntry`（``pattern`` 字段非空），
    因此分组管理、注册顺序、dispatch 算法完全一致，无第二套语义。

    .. rubric:: 使用示例

    .. code-block:: python

        self.hooks.before_tool_call["payment-*"](self._guard, by="guardrail")
        # fnmatch 规则：* 任意字符、? 单字符、[abc] 字符集合、字面量精确匹配；
        # 匹配的是有效名称（声明 as 别名则匹配别名，否则匹配规范名）。

    .. rubric:: 行为规约

    - 调用即构造 ``HookEntry(handler=..., by=..., tags=..., pattern=<pattern>)``
      并追加到所属 :class:`HookList` 末尾；**返回被注册的原 handler**
      （S-18 最终裁决：装饰器形态不遮蔽被装饰名，与 ``@on`` /
      ``watch`` 先例一致——注册语句的返回值无使用场景；需要 entry
      句柄时经 ``HookList`` 的 int/slice 索引或批量管理 API 获取）。
    - 不匹配时 handler **不被调用**，value 原样透传，不影响链的传递。
    - pattern 过滤在 dispatch 时按 entry 惰性求值（而非注册时生成包装函数）；
      两种实现的对外行为等价，本规约定稿为 entry 惰性过滤，使
      ``entry.pattern`` 可观测、可随 disable/enable 整体管理。
    - 边缘情况：对 value 不具有 ``match_on`` 属性的钩子点做 pattern 注册，
      注册本身成功；dispatch 时 ``getattr`` 失败按 handler 普通异常规则
      直接上抛（属编程错误，不做兜底）。

    .. rubric:: 调用关系（审计）

    - 调用：无
    - 被调：见 :meth:`__call__`
    - 实例化方：``flowing.hooks.HookList.__getitem__``（str 形态；
      时机：每次 ``hook["pattern"]`` 访问）

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
        """构造注册器（S-30 裁决：显式带参构造，字段来源可查——与
        ``OnRegistrar`` 构造形态同构）。

        :param hook_list: 所属钩子点容器（``HookList.__getitem__`` 传入
            ``self``）。
        :param pattern: fnmatch 过滤模式（``__getitem__`` 的 str 下标）。

        .. rubric:: 调用关系（审计）

        - 被调：``flowing.hooks.HookList.__getitem__``（时机：每次
          ``hook["pattern"]`` 访问）
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

        .. rubric:: 行为规约

        等价于在所属 ``HookList`` 上注册并附加 ``pattern``；``by`` / ``tags``
        语义与 :meth:`HookList.__call__` 完全一致。**返回被注册的原
        handler**（S-18 最终裁决：装饰器形态下被装饰名不被遮蔽，与
        ``@on`` / ``watch`` 先例一致；需要 :class:`HookEntry` 句柄时经
        ``HookList`` 索引或批量管理 API 获取）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.hooks.HookEntry`` 构造与
          ``flowing.lists.ManagedList.append``（时机：每次 pattern 注册，
          见类 docstring「调用即构造 HookEntry 并追加到所属 HookList
          末尾」）
        - 被调：``flowing.agent.Agent.watch``（时机：每次 watch 调用，
          经 ``self.hooks.watch(name, wrapped_handler)``）；
          ``flowing.plugins.comm`` / ``flowing.plugins.cron`` 的
          ``@hooks.on_signal["..."]`` / ``@hooks.on_cron_trigger["..."]``
          装饰器注册（时机：插件注册 handler 时）

        .. seealso::

            :meth:`HookList.__call__`
        """
        entry = HookEntry(handler=handler, by=by, tags=tags or [], pattern=self._pattern)
        self._hook_list.append(entry)
        return handler   # S-18 裁决：返回原 handler（entry 经索引/管理 API 获取）


class HookList(ManagedList[HookEntry]):
    """单个钩子点的容器。

    .. rubric:: 功能介绍

    每个钩子点一个实例，由 :class:`HookRegistry` 持有。提供统一注册入口
    （``hook(handler, by=..., tags=...)``）、``__getitem__`` 三形态索引
    与统一分发入口 :meth:`dispatch`；分组管理能力继承自
    :class:`ManagedList`。

    .. rubric:: 设计动机

    「一个钩子点，一个注册方式」：v1 的 pipe/on/intercept 三语义已统一，
    注册顺序即执行顺序；名称索引与分组管理收敛到同一容器上，使
    「注册 → 过滤 → 分发 → 批量开关」全生命周期只有一个对象要面对。

    .. rubric:: 使用示例

    .. code-block:: python

        self.hooks.before_tool_call(self._audit, by="audit", tags=["security"])
        self.hooks.before_tool_call["payment-*"](self._guard, by="guardrail")

        entry = self.hooks.before_tool_call[0]        # int → HookEntry
        first_two = self.hooks.before_tool_call[:2]   # slice → list[HookEntry]
        self.hooks.before_tool_call.disable_by_owner("guardrail")

    .. rubric:: 行为规约

    - 注册入口：``__call__(handler, *, by=None, tags=None) -> HookHandler``
      （S-18 最终裁决：返回被注册的原 handler，装饰器形态不遮蔽名字；
      entry 句柄经 ``__getitem__`` 索引或批量管理 API 获取），
      追加到末尾（注册顺序即执行顺序）；``by`` 省略记为 ``None``（与
      :meth:`HookRegistry.declare` 的必填 ``by`` 是两个层面）。
    - ``__getitem__`` 三形态见下；``int`` / ``slice`` 基于**含 disabled 的
      完整底层列表**做位置索引（用于自省），与 ``__iter__`` /
      :meth:`dispatch` 的「跳过 disabled」语义互不干扰。
    - 构造阶段钩子点（``before_create`` / ``after_create`` / ``before_destroy``
      / ``after_destroy`` / ``before_recover`` / ``after_recover``）与运行期
      钩子点一样接受 sync 或 async handler（M-37 裁决）；注册时**不做**
      同步性检查，dispatch 经 ``inspect.isawaitable`` 透明处理。

    .. rubric:: 测试案例

    - 前置：空 ``before_tool_call``。操作：依次注册 ``h1``、``h2``、
      ``h3``，随后 ``disable_by_owner(h2.by)``。期望：``dispatch`` 只执行
      ``h1``、``h3``；``hook[1]`` 仍返回 ``h2`` 的 entry（含 disabled）。
    - 前置：注册 ``hook["payment-*"](guard)``。操作：``dispatch(agent,
      ToolCall(name="read-file", ...))``。期望：``guard`` 未被调用，
      返回值等于入参 value（原样透传）。

    .. rubric:: 调用关系（审计）

    - 调用：无
    - 被调：作为容器经 ``flowing.hooks.HookRegistry.__getattr__`` 返回
      （时机：每次 ``hooks.<name>`` 属性访问）
    - 实例化方：``flowing.hooks.HookRegistry.__init__``（时机：每次
      创建 Agent 实例，预填核心钩子点）与
      ``flowing.hooks.HookRegistry.declare``（时机：扩展声明新钩子点时）

    .. seealso::

        :class:`HookRegistry`、:class:`HookEntry`、:class:`PatternRegistrar`、
        :class:`ManagedList`
    """

    name: str
    """钩子点名（如 ``"before_tool_call"``），在所属 ``HookRegistry`` 内唯一。
    """
    by: str
    """钩子点的**声明者**标识（框架核心 ``"core"``，扩展用自身标识）；
    必填，见 :meth:`HookRegistry.declare` 的声明独占规则。
    """
    match_on: str
    """pattern 过滤时读取 value 的属性名，默认 ``"name"``；通信扩展使用
    ``"type"``（``on_signal``）/ ``"topic"``（``on_event``）。
    """

    def __init__(self, name: str, *, by: str, match_on: str = "name") -> None:
        """创建钩子点容器。通常只由 ``HookRegistry`` 预填与 ``declare()`` 调用。

        .. rubric:: 行为规约

        - ``by`` 必填关键字参数，不允许省略或显式传 ``None``——声明者身份是
          「声明独占」冲突判定的唯一依据。
        - ``match_on`` 必须是 value 类型具备的**属性名**字符串；本方法不校验
          （value 类型在 declare 时不可知），错误在 dispatch 时按普通异常上抛。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.hooks.HookRegistry.__init__``（时机：每次创建
          Agent 实例，预填核心钩子点）；
          ``flowing.hooks.HookRegistry.declare``（时机：每次声明新钩子点）

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

        .. rubric:: 功能介绍 / 设计动机

        全框架唯一的注册形态；不存在 ``pipe`` / ``intercept`` 等变体，
        handler 用返回值、``shortcut`` 字段或 ``raise Intercepted`` 表达行为。

        .. rubric:: 使用示例

        .. code-block:: python

            self.hooks.before_tool_call(self._audit, by="audit", tags=["audit"])
            self.hooks.on_provider_error(self._retry, by="retry")

        .. rubric:: 行为规约

        - 追加注册，**注册顺序即执行顺序**；**返回被注册的原 handler**
          （S-18 最终裁决：装饰器形态（``@hooks.before_tool_call``）下
          被装饰名不被遮蔽，与 ``@on`` / ``watch`` 先例一致；注册语句
          的返回值无使用场景——需要 :class:`HookEntry` 句柄时经
          ``__getitem__`` int/slice 索引或批量管理 API 获取）。
        - 同一 handler 可重复注册（执行多次），不去重。
        - ``by`` 省略记为 ``None``；``tags`` 省略记为 ``[]``。
        - 构造阶段钩子点同样接受 async handler（M-37 裁决），dispatch 统一
          经 ``inspect.isawaitable`` 处理；watcher 通道也一样——它由
          ``__setattr__`` fire-and-forget 通知，watcher 的 async 性
          与赋值语义无关。
        - 不校验 handler 签名（value 类型各异），签名错误在 dispatch 时按
          普通异常直接上抛。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.hooks.HookEntry`` 构造与
          ``flowing.lists.ManagedList.append``（时机：每次注册，见
          ``ManagedList.append`` docstring「统一的注册落点」）
        - 被调：``flowing.agent.Agent._init_hooks``（时机：每个 Agent
          实例 ``__init__`` 阶段，注册 ``@on`` 标记方法）；
          ``flowing.composables.retry.use_retry``（时机：use_retry
          安装时，注册 ``on_provider_error`` / ``before_turn`` handler）

        .. seealso::

            :meth:`dispatch`、:class:`PatternRegistrar`、:func:`on`
        """
        entry = HookEntry(handler=handler, by=by, tags=tags or [])
        self.append(entry)
        return handler   # S-18 裁决：返回原 handler（entry 经索引/管理 API 获取）

    @overload
    def __getitem__(self, index: int) -> HookEntry: ...
    @overload
    def __getitem__(self, index: slice) -> list[HookEntry]: ...
    @overload
    def __getitem__(self, index: str) -> PatternRegistrar: ...
    def __getitem__(
        self, index: int | slice | str
    ) -> HookEntry | list[HookEntry] | PatternRegistrar:
        """三形态索引：位置自省（int / slice）与按名称过滤注册（str）。

        .. rubric:: 功能介绍

        - ``int`` → :class:`HookEntry`：按索引获取单个条目（含 disabled）。
        - ``slice`` → ``list[HookEntry]``：按切片获取条目列表（含 disabled）。
        - ``str`` → :class:`PatternRegistrar`：``hook["pattern"](handler)``
          注册语法糖。

        .. rubric:: 行为规约

        - 内部按 ``isinstance(index, (int, slice))`` 判定：int/slice 走位置
          索引，其余（str）走 pattern 注册。
        - int/slice 基于含 disabled 的完整底层列表；越界抛 ``IndexError``。
        - str 形态**不读取**既有条目，只返回注册器；pattern 语法遵循
          ``fnmatch``（``*`` / ``?`` / ``[abc]`` / 字面量），匹配的是资源的
          **有效名称**（声明 ``as`` 别名则匹配别名，否则匹配规范名，
          kebab-case）。``watch`` 不是 HookList；注册用
          ``hooks.watch('locale', handler)`` 或 ``agent.watch``
          即对 :class:`flowing.agent.FieldUpdate` 的 ``name`` 做 pattern
          匹配，字面量字段名即精确匹配（M-41 裁决）。

        .. rubric:: 测试案例

        - 前置：``hook`` 含 3 个条目。操作：``hook["pay-*"](h)``。
          期望：返回值为 ``PatternRegistrar``；调用后 ``len`` 变 4 且末位
          entry 的 ``pattern == "pay-*"``。
        - 前置：同上。操作：``hook[-1]``。期望：返回该 entry 本身。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.hooks.PatternRegistrar`` 构造（str 形态；
          时机：每次 ``hook["pattern"]`` 访问）
        - 被调：``flowing.agent.Agent.watch``（时机：每次 watch 调用，
          经 ``self.hooks.watch(name, wrapped)``）；``flowing.plugins.comm``
          / ``flowing.plugins.cron`` 的 pattern 注册语法（时机：插件
          注册 handler 时）

        .. seealso::

            :class:`PatternRegistrar`
        """
        if isinstance(index, (int, slice)):  # 位置自省形态：基于含 disabled 的完整底层列表
            return self._items[index]  # 越界抛 IndexError（行为规约）
        # str 形态：不读取既有条目，返回 pattern 注册器（S-30：带参构造）
        return PatternRegistrar(self, index)

    async def dispatch(self, agent: Any, value: Any = None) -> Any:
        """统一分发算法：按注册顺序执行活跃 handler 的改写链。

        .. rubric:: 功能介绍

        全框架唯一的钩子分发实现。框架在创建 / 恢复 / 销毁管线与 Turn 循环
        固定位置 dispatch 核心钩子点；扩展 dispatch 自己声明的钩子点时调用
        的也是本方法（谁声明谁 dispatch）。

        .. rubric:: 使用示例

        .. code-block:: python

            # 框架内部（tool_call 流程）
            tool_call = await self.hooks.before_tool_call.dispatch(self, tool_call)
            if tool_call.shortcut is not None:
                result = tool_call.shortcut            # 协商短路：跳过工具执行
            else:
                result = await tool(resolved_args, caller=self,
                                    execution=execution)   # Tool.__call__ 调度层
            result = await self.hooks.after_tool_call.dispatch(self, result)

        .. rubric:: 行为规约

        - 迭代活跃条目（跳过 ``enabled=False``），逐条执行：
          ``entry.pattern`` 非空时先 ``fnmatch(getattr(value, self.match_on),
          entry.pattern)`` 过滤，不匹配则跳过（value 原样透传）。
        - ``result = entry.handler(agent, value)``；``inspect.isawaitable``
          检测为 awaitable 则 ``await``——同步 / 异步 handler 透明混用
          （M-37 裁决：构造阶段钩子点同样接受 async handler，注册时不做
          同步性检查）。
        - 首参 ``agent`` 是**钩子宿主对象**，类型不定（S-41 裁决①）：
          Agent 专属钩子点上是 ``Agent`` 实例；Workflow 自己的
          ``hooks``（``before_tool_call`` / ``after_tool_call``）上是
          ``Workflow`` 实例。dispatch 自身只透传，从不访问其属性；
          框架不假设其类型——核心层不反向依赖插件层的 ``Workflow``
          类，故标注 ``Any``；需要 Agent 能力的 handler 自行
          ``isinstance`` 判断。
        - 携带 value 的钩子点（``value`` 参数非 ``None`` 调入）要求每个
          handler 返回值：handler 返回 ``None`` 视为编程错误，抛
          :class:`flowing.errors.FlowingError`（消息含钩子点名与 handler
          标识）。无 value 的钩子点（``after_create`` 等）不施加该检查。
        - ``value = result`` 后检查 ``getattr(value, "shortcut", None)``：
          非 ``None`` 立即停止链并返回该 value（协商短路；对应操作的
          ``after_`` 钩子由调用方保证照常触发）。
        - :class:`flowing.errors.Intercepted`：捕获后立即**重抛**——链停止、
          后续 handler 不执行、不当作错误（对应操作的 ``after_`` 钩子不触发）。
        - **普通异常直接上抛**：不捕获、不通知、不继续后续 handler、无任何
          兜底钩子。
        - 无 handler（或全部 disabled / 被 pattern 过滤）时原样返回 ``value``。

        .. rubric:: 测试案例

        - 前置：``h1`` 把 ``tc.args["x"] = 1`` 后返回；``h2`` 原样返回。
          操作：``await hook.dispatch(agent, tc)``。期望：``h2`` 收到的
          value 含 ``x == 1``（改写链传递）。
        - 前置：``h1`` 设置 ``tc.shortcut = ToolResult(...)`` 并返回；另有
          ``h2``。操作：dispatch。期望：``h2`` 未执行，返回值含 shortcut。
        - 前置：``h1`` ``raise Intercepted("no")``；另有 ``h2``。操作：
          dispatch。期望：``Intercepted`` 上抛，``h2`` 未执行。
        - 前置：``h1`` ``raise ValueError``；另有 ``h2``。操作：dispatch。
          期望：``ValueError`` 原样上抛，``h2`` 未执行，无兜底。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.lists.ManagedList.__iter__``（时机：每次
          dispatch，迭代活跃条目）；``HookEntry.handler``（时机：每个
          活跃 handler 依次执行）
        - 被调：``flowing.runtime.Runtime.create_agent`` 管线第 4 步
          （``before_create``）与第 9 步（``after_create``）（时机：每次
          创建 Agent）；恢复管线第 5 步（``before_recover``）与第 9 步
          （``after_recover``）（时机：每次恢复）；
          ``flowing.agent.Agent`` 的 ``enqueue_message``
          （``before/after_enqueue``，每次入队）、``provider_gen``
          （``before_provider_gen`` / ``after_provider_gen`` / ``on_provider_error`` /
          ``on_provider_delta``，每次 LLM 调用）、``tool_call``
          （``before/after_tool_call``，每次工具调用）、``fork`` /
          ``cancel`` / ``stop`` / ``destroy`` / ``invoke_subagent`` /
          工作循环出队与 turn 循环（turn 族钩子，每个逻辑 turn）、
          ``__setattr__``（``watch`` 通道，fire-and-forget，每次实例
          属性赋值）；扩展自 dispatch：
          ``flowing.plugins.skills.use_skill``
          （``before/after_skill_load``，每次加载 Skill）、
          ``flowing.plugins.comm`` 句柄闭包（``on_signal`` /
          ``on_event``，每个信封 / 事件）、``flowing.plugins.cron``
          （``on_cron_trigger``，每次到点触发）、
          ``flowing.composables.retry``（``on_retry``，fire-and-forget，
          每次重试决策）

        .. seealso::

            :class:`flowing.errors.Intercepted`、
            :meth:`ManagedList.__iter__`
        """
        import fnmatch
        import inspect

        from flowing.errors import FlowingError

        for entry in self:  # 经 ManagedList.__iter__：跳过 enabled=False
            if entry.pattern is not None:  # 条件：pattern 条目先过滤
                if not fnmatch.fnmatch(getattr(value, self.match_on), entry.pattern):
                    continue  # 不匹配 → 跳过，value 原样透传
            result = entry.handler(agent, value)
            if inspect.isawaitable(result):  # 条件：sync/async handler 透明混用（M-37）
                result = await result
            if value is not None and result is None:
                # 携带 value 的钩子点：handler 不 return 视为编程错误
                raise FlowingError(
                    f"hook {self.name!r}: handler {entry.handler!r} returned None"
                )
            value = result
            if getattr(value, "shortcut", None) is not None:
                return value  # 协商短路——链停止，after_ 钩子由调用方保证照常触发
            # 异常路径：Intercepted 捕获后立即重抛（INFO 级日志点，logger 未具名）；
            # 普通异常直接上抛，不捕获、不通知、无兜底钩子
        return value


class HookRegistry:
    """Agent 实例级的钩子点注册表。

    .. rubric:: 功能介绍

    每个 Agent 实例在 ``__init__`` 中创建一个独立实例（``agent.hooks``），
    构造时预填全部**核心钩子点**（``by="core"``，全集见模块 docstring 表）；
    扩展经 :meth:`declare` 在 ``setup()`` 阶段就地声明自己的钩子点。

    .. rubric:: 设计动机

    - **实例级**：钩子只影响当前 Agent 实例，无全局钩子表——同类的两个
      Agent 可以有不同的钩子栈。
    - **声明即创建 + ``__getattr__`` 只查找**：钩子点集合是显式的，访问未
      声明的钩子点立即报错（而非静默创建空钩子点），让「没调用
      ``use_skill()`` 却访问 ``before_skill_load``」这类错误在开发期暴露。

    .. rubric:: 使用示例

    .. code-block:: python

        async def setup(self):
            use_skill(self)   # 内部 declare("before_skill_load", by="skill")
            # 声明后任何代码都可注册（注册开放）
            self.hooks.before_skill_load(self._audit_load, by="audit")

        # 不调用 use_skill 的实例：
        self.hooks.before_skill_load   # → UnknownHookPointError

    .. rubric:: 行为规约

    - ``_hook_points`` 只在两处写入：``__init__`` 预填核心点、
      :meth:`declare` 声明扩展点；任何其他路径不得写入。
    - 属性访问（``hooks.<name>``）经 ``__getattr__`` **只查找不创建**：
      未声明 → :class:`flowing.errors.UnknownHookPointError`。
    - 预填的核心钩子点
      :class:`HookList`（``match_on="name"``），value 为
      :class:`flowing.agent.FieldUpdate`；字段过滤走现成的 pattern
      注册语法（M-41 裁决）。
    - 不变量：同一钩子点名在注册表内唯一（声明独占，见 :meth:`declare`）。

    .. rubric:: 测试案例

    - 前置：新建 Agent 实例。操作：``agent.hooks.before_turn``。期望：
      返回预填的 ``HookList``，``by == "core"``，无 handler。
    - 前置：同上。操作：``agent.hooks.nonexistent_hook``。期望：
      ``UnknownHookPointError``。
    - 前置：同上。操作：``agent.hooks.declare("on_event", by="comm")`` 后
      ``agent.hooks.on_event``。期望：返回新 ``HookList``，``by == "comm"``。

    .. rubric:: 调用关系（审计）

    - 调用：无
    - 被调：``flowing.plugins.workflow`` 类型引用（时机：未见规约）；
      ``flowing.__init__`` re-export；``flowing.agent.Agent`` 的
      ``hooks: HookRegistry`` 属性类型标注
    - 实例化方：``flowing.agent.Agent.__init__``（时机：每次创建 Agent
      实例，每实例一个独立注册表）

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
    """未结算的 ``@on`` 标记（S-29 裁决）：（绑定方法, hook_name, by,
    tags, pattern)。``_init_hooks`` 收集时钩子点尚未声明的记录暂记
    于此；``declare()`` 创建同名钩子点时**冲刷**挂载（此刻无其它
    handler，``@on`` handler 天然排最前——「优先挂载」是时序的自然
    结果）；``setup()`` 后的 PENDING 检查发现本列表非空 →
    ``UnknownHookPointError``。内部 API，不属稳定契约。
    """

    def __init__(self) -> None:
        """构造并预填全部核心钩子点（``by="core"``）。

        .. rubric:: 行为规约

        - 预填集合为模块 docstring「核心钩子点全集表」的完整集合，
          各 ``HookList`` 初始为空（无 handler），``match_on="name"``。
        - ``_pending_on`` 初始为空（``@on`` 暂记列表，S-29）。
        - 本方法是同步的（在 ``Agent.__init__`` 骨架阶段调用）。
        - 扩展钩子点不在此预填——未启用扩展的实例不承载其复杂度。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.hooks.HookList.__init__``（时机：构造时预填
          全部核心钩子点）
        - 被调：``flowing.agent.Agent.__init__``（时机：每次创建 Agent
          实例，骨架阶段）

        .. seealso::

            :meth:`declare`、``flowing.agent.Agent._init_hooks``
        """
        self._hook_points = {}
        self._watchers: list[tuple[str, Callable[[Any, Any], Any]]] = []   # watcher 通道（非钩子）
        self._pending_on = []   # S-29：未结算的 @on 标记暂记列表
        # 预填核心钩子点（by="core"，初始无 handler；全集见模块
        # docstring「核心钩子点全集表」两张表）。after_provider_gen /
        # on_provider_delta 以 match_on="by" 声明——value
        # （ProviderResponse / ProviderDelta）携带 by 来源标记，
        # handler 可按 "_turn" / "_side" / 插件自定义值做 pattern
        # 过滤注册（用户裁决）；before_provider_gen 的 value 是 Context，
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

        .. rubric:: 设计动机

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

        .. rubric:: 行为规约

        - ``by`` 为必填关键字参数——框架核心 ``by="core"``，扩展用自身标识；
          不允许省略或传 ``None``（无法区分来源即视为冲突场景，直接拒绝）。
        - 同名 + 同 ``by``：**幂等**，返回已有 ``HookList``（``match_on``
          以首次声明为准，不校验后续传参是否一致）。
        - 同名 + 不同 ``by``：抛
          :class:`flowing.errors.DuplicateHookPointError`，消息含双方
          ``by``。
        - 返回的 ``HookList`` 由声明者自行 dispatch；框架不会代 dispatch
          扩展钩子点（谁声明谁 dispatch）。
        - 声明时机约束：应在 ``setup()``（或 ``use_xxx``）中完成；运行期
          Turn 循环内声明虽不禁止，但属扩展自身责任，框架不做时序保障。
        - **``@on`` 冲刷（S-29）**：创建钩子点（非幂等返回已有）时，把
          ``_pending_on`` 里同名记录全部挂载进新 ``HookList``（含
          pattern 的走 ``HookList[pattern]`` 通道）并从暂记列表移除——
          此刻尚无其它 handler，``@on`` handler 天然排最前（「优先
          挂载」是时序的自然结果）。幂等路径（同名 + 同 ``by``）不冲刷
          ——首次声明时已结算。

        :raises flowing.errors.DuplicateHookPointError:
            同名钩子点已被不同 ``by`` 声明。

        .. rubric:: 测试案例

        - 前置：空注册表。操作：``declare("on_signal", by="comm")`` 两次。
          期望：两次返回同一 ``HookList`` 对象。
        - 前置：已 ``declare("on_signal", by="comm")``。操作：
          ``declare("on_signal", by="other")``。期望：
          ``DuplicateHookPointError``，消息含 ``"comm"`` 与 ``"other"``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.hooks.HookList.__init__``（时机：每次声明新
          钩子点，声明即创建）
        - 被调：``flowing.plugins.skills.use_skill``（时机：use_skill
          启用时，``setup()`` 阶段）；``flowing.plugins.comm.use_comm``
          （时机：use_comm 启用时）；
          ``flowing.plugins.cron.use_cron``（时机：use_cron 启用时）；
          ``flowing.composables.retry.use_retry``（时机：use_retry
          安装时，声明 ``on_retry``）

        .. seealso::

            :class:`HookList`、:meth:`HookList.dispatch`、
            ``flowing.plugins.skills.use_skill``
        """
        from flowing.errors import DuplicateHookPointError

        existing = self._hook_points.get(name)
        if existing is not None:
            if existing.by == by:
                return existing  # 同名 + 同 by：幂等，match_on 以首次声明为准
            raise DuplicateHookPointError(name, existing.by, by)  # 同名 + 不同 by
        hook_list = HookList(name, by=by, match_on=match_on)  # 声明即创建
        self._hook_points[name] = hook_list  # _hook_points 唯二写入点之一
        # S-29：冲刷 _pending_on 里同名 @on 标记（此刻无其它 handler，
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
        """注册一个 **watcher**（不是普通钩子点）。

        .. rubric:: 功能介绍

        与 ``Agent.watch`` 对应的低层注册入口：把 ``(agent, value)``
        形态的 handler 存入 watcher 通道。watcher 由 ``_notify_watch``
        以 fire-and-forget 方式触发，与普通钩子不同：不参与改写 /
        ``Intercepted`` / ``shortcut``，返回值被忽略，异常只记录日志。

        :param name: fnmatch pattern，匹配 ``value.name`` 字段。
        :param handler: ``(agent, value) -> None``（sync/async 均可）。
        :return: handler（便于装饰器写法）。

        .. rubric:: 调用关系（审计）

        - 调用：无（注册进 ``_watchers``）
        - 被调：``flowing.agent.Agent.watch``（时机：每次 watch 调用，
          包装为 watcher handler 后委托本方法）
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
                    pass   # 观察者异常只记录日志，不影响赋值/写透
        asyncio.ensure_future(_run_watch())

    def __getattr__(self, name: str) -> HookList:
        """按名查找钩子点容器（只查找，不创建）。

        .. rubric:: 行为规约

        - 命中（含预填核心点与已 declare 的扩展点）→ 返回对应容器。
        - 未命中 → 抛 :class:`flowing.errors.UnknownHookPointError`，
          **不静默创建**——这是「声明即创建」的强制面。

        :raises flowing.errors.UnknownHookPointError:
            访问未声明的钩子点。

        .. rubric:: 调用关系（审计）

        - 调用：无（只查找 ``_hook_points``，不创建）
        - 被调：一切 ``hooks.<name>`` 属性访问的隐式入口——框架内全部
          dispatch 调用点与扩展注册（``use_skill`` / ``use_comm`` /
          ``use_cron`` / ``use_retry`` 等）均经本方法取容器（时机：
          每次按名访问钩子点）；``flowing._unstable.logging`` 枚举
          ``_hook_points`` 属内部 API 直读，不经本方法

        .. seealso::

            :meth:`declare`、:class:`flowing.errors.UnknownHookPointError`
        """
        from flowing.errors import UnknownHookPointError

        try:
            return self._hook_points[name]  # 命中（核心预填点与已 declare 扩展点）
        except KeyError:
            raise UnknownHookPointError(name) from None  # 只查找，不静默创建




class OnRegistrar:
    """``on(...)`` 返回的装饰器对象（S-29）：``__call__`` 无 pattern 装饰，
    ``__getitem__`` 收 pattern 后返回装饰器——镜像
    ``hooks.<name>(handler)`` / ``hooks.<name>[pattern](handler)`` 两种
    运行期注册形态。

    **内部 API，不属稳定契约**（作为 ``on()`` 返回值被用户瞬时消费，
    不应被保存或复用）。

    .. rubric:: 调用关系（审计）

    - 调用：无（仅向函数追加 ``__flowing_hooks__`` 记录）
    - 实例化方：``flowing.hooks.on``（时机：每次装饰器工厂调用）
    """

    def __init__(self, hook_name: str, by: "str | None",
                 tags: "list[str] | None") -> None:
        self.hook_name = hook_name
        self.by = by
        self.tags = tags

    def _mark(self, handler: HookHandler, pattern: "str | None") -> HookHandler:
        """追加一条标记记录并返回原函数（S-18：装饰器不遮蔽名字）。"""
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

    在 ``.fya`` 的 ``$script`` 块或手写 Agent 子类的类体中，把一个方法标记
    为钩子 handler；``Agent.__init__`` 阶段的 ``_init_hooks()`` 收集全部
    被标记方法并注册到**实例**的 ``hooks`` 上，早于 ``setup()`` 执行。

    .. rubric:: 设计动机

    两个动机：其一，``before_create`` 在 ``setup()`` 运行前触发，实例上尚
    无机会调用 ``self.hooks.before_create(...)``——``@on('before_create')``
    是注册创建期钩子的**唯一**方式；其二，``.fya`` 声明式入口需要与类体
    语义对齐的注册形态，使「声明式与手写子类生成完全相同的 Python 类模型」。

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
                self.logger.info(tool_call.name)
                return tool_call

            @on('on_signal')['agent-message-*']   # pattern 形态（S-29）
            def _(self, envelope): ...

    ``.fya`` 等价写法：

    .. code-block:: text

        ---
        $script:
        from flowing import on

        @on('before_tool_call')
        def _(self, tool_call):
            tool_call.args['lang'] = self.locale
            return tool_call

    .. rubric:: 行为规约（S-29 定稿）

    - 本装饰器**只做标记**：向函数的 ``__flowing_hooks__`` 属性 tuple
      追加一条记录 ``(hook_name, by, tags, pattern)``，不触碰任何注册表；
      返回**原函数**（S-18：不遮蔽名字，方法仍可直调、可覆写）。
      叠多个 ``@on`` → 多条记录，同一方法可挂在多个钩子点上。
    - **收集**（``Agent._init_hooks``，``__init__`` 阶段）：按
      ``type(self).__mro__`` 逐名解析——**派生优先**判覆写（子类覆写
      未标记的同名方法，基类标记不生效）；注册顺序**基类 → 派生类**。
      按绑定方法注册，dispatch 签名与其他 handler 统一：
      ``(agent, value) -> value``。
    - **两段式注册**：钩子点已存在（核心预填点）→ 立即注册；尚未声明
      （插件点，等 ``use_xxx()`` 在 ``setup()`` 里 declare）→ 记入实例
      ``hooks._pending_on`` 暂记。``HookRegistry.declare()`` 创建同名
      钩子点时冲刷挂载——此刻无其它 handler，``@on`` handler 天然排
      最前（「优先挂载」是时序的自然结果）。
    - **setup 后结算**：``setup()`` 返回后的 PENDING 检查发现
      ``_pending_on`` 非空 → 抛
      :class:`flowing.errors.UnknownHookPointError`（拼错的钩子点名
      或未启用对应插件，必须死在创建期，不做静默死信）。边界：
      ``@on`` 的目标钩子点必须在 setup 结束前被 declare（与
      「Composable 在 setup 里启用」约定一致）。
    - ``by`` 省略时记 ``None``，与 :meth:`HookList.__call__` 的默认完全
      一致——``@on`` 与括号调用是同语义的两种注册形态，默认值不分叉
      （M-40 裁决）。注意 ``remove_by_owner(None)`` 会精确匹配并删除
      **全部**匿名 handler（含其他来源注册的），需按来源批量管理时应
      显式给 ``by``。
    - 构造阶段钩子点（``before_create`` 等六个）与运行期钩子点一样接受
      同步或 async handler（M-37 裁决）；``_init_hooks()`` 不做同步性
      校验。

    .. rubric:: 测试案例

    - 前置：子类以 ``@on('before_create')`` 标记 ``m``。操作：
      ``runtime.create_agent(...)``。期望：``m`` 在 ``setup()`` 之前被
      调用，其返回的 kwargs 被 ``setup(**kwargs)`` 接收。
    - 前置：``@on('before_tool_call')`` 与 ``setup()`` 中
      ``self.hooks.before_tool_call(h2)`` 并存。操作：触发一次工具调用。
      期望：``@on`` 的 handler 先于 ``h2`` 执行。
    - 前置：``@on('before_create')`` 标记了 async 函数。操作：实例化。
      期望：正常注册并触发（M-37：构造阶段钩子同样接受 async handler，
      dispatch 统一 await）。
    - 前置：``@on('on_signal')`` 标记 + ``setup()`` 中 ``use_comm(self)``。
      操作：收到信号。期望：handler 经 declare 冲刷注册后被触发，且
      排在 setup 内后续注册的 handler 之前。
    - 前置：``@on('on_singal')``（拼错）。操作：``create_agent``。
      期望：PENDING 检查抛 ``UnknownHookPointError``，消息含
      ``"on_singal"`` 与方法名。
    - 前置：基类 ``@on('before_turn')`` 标记 ``m``，子类覆写 ``m``
      未标记。操作：实例化子类。期望：``m`` 不作为 handler 注册
      （覆写即覆盖）。

    .. rubric:: 调用关系（审计）

    - 调用：``OnRegistrar`` 构造（每次调用）；记录消费方为
      ``flowing.agent.Agent._init_hooks`` 与
      ``flowing.hooks.HookRegistry.declare``（冲刷）
    - 被调：无框架内调用方（装饰器由用户代码 / ``.fya`` ``$script``
      使用）；``flowing.__init__`` re-export

    .. seealso::

        :class:`HookRegistry`、:class:`OnRegistrar`、
        :meth:`HookList.__call__`、
        ``flowing.agent.Agent._init_hooks``
    """
    return OnRegistrar(hook_name, by=by, tags=tags)
