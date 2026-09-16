"""Flowing 统一异常层次（``flowing.errors``）。

.. rubric:: 功能介绍

本模块定义 Flowing 框架的全部具名异常类型，是框架核心层（非扩展、非应用层）
的公共契约。``FlowingError`` 是统一根，向下按职责分为十一个类别：配置、
注入、钩子、工具、注册表、资源、Provider、依赖、通信、格式、编译；另有
三个异常直接挂根、不属任何类别——``EntryNameConflictError`` （Agent 绑定
层别名冲突）、``FormatVersionError`` 与 ``CorruptionError`` （jsonl 持久化
文件；与声明式文件格式的 ``FormatError`` 分层，持久化层不专设中间层）。
外加一个刻意游离于普通错误语义之外的信号类 ``Intercepted`` （钩子 handler
的有意硬阻断信号，刻意不继承 ``FlowingError``）。

import 期作者笔误刻意用内置 ``ValueError``，不入本层次——如
``register_provider`` 装饰到非 ``Provider`` 子类或缺少非空 ``name`` 类属性
的对象。这类错误是装饰器用错对象（编程错误），不该被任何恢复逻辑捕获；
可恢复的冲突情形另有具名类型（``ProviderNameConflictError``）。

异常层次树（示意图，非表格——仅展示继承关系）:

.. code-block:: text

    Exception
    ├── Intercepted                      # 有意硬阻断信号（刻意不挂在 FlowingError 下）
    └── FlowingError                     # 框架统一异常根
        ├── ConfigError                  # 配置读取时机 / 配置命名空间
        │   ├── ConfigNotReadyError
        │   └── ConfigNamespaceConflictError
        ├── ProvideError                 # provide/inject 链
        │   └── MissingProvideError
        ├── HookError                    # 钩子点声明与访问
        │   ├── UnknownHookPointError
        │   └── DuplicateHookPointError
        ├── ToolError                    # 工具定义 / 注册 / 查找
        │   ├── MissingSchemaError
        │   ├── ToolNotFoundError      # 同挂 RegistryNotFoundError（多继承）
        │   ├── ToolNameConflictError  # 同挂 RegistryConflictError（多继承）
        │   ├── UnknownToolError
        │   ├── AmbiguousToolError
        │   ├── AmbiguousMcpSourceError
        │   └── MissingMcpSourceError
        ├── RegistryNotFoundError      # 注册表查找未命中（三注册表共用中间层）
        │   ├── AgentTypeNotFoundError
        │   ├── SkillNotFoundError
        │   └── ToolNotFoundError      # 同挂 ToolError（多继承）
        ├── RegistryConflictError      # 注册表键冲突（三注册表共用中间层）
        │   ├── AgentTypeConflictError
        │   ├── SkillNameConflictError
        │   └── ToolNameConflictError  # 同挂 ToolError（多继承）
        ├── ResourceError                # Resource 注册与访问
        │   ├── ResourceNameConflictError
        │   └── ResourceNotFoundError
        ├── ProviderError                # Provider 加载与调用
        │   ├── ContextLengthError       # token 超限；经 on_provider_error 分发，处置属策略
        │   ├── RequestTooLargeError     # 不可重试（HTTP 413 字节超限）
        │   ├── RateLimitedError         # 可重试（HTTP 429 瞬时限流）
        │   ├── QuotaExhaustedError      # 不可重试（429 配额耗尽）
        │   ├── ServerError              # 可重试（5xx）
        │   ├── NetworkError             # 可重试
        │   ├── ProviderTimeoutError     # 可重试
        │   ├── AuthenticationError      # 不可重试（401/403）
        │   ├── InvalidRequestError      # 不可重试（400）
        │   ├── ContentPolicyError       # 不可重试
        │   ├── MissingEnvironmentVariableError  # 加载期要求的环境变量缺失（内置加载器告警降级，类保留给严格校验）
        │   └── ProviderNameConflictError        # adapter 规范名重名注册（import 期）
        ├── DependencyError              # 插件依赖校验（install() 时增量）
        ├── CommError                    # 通信扩展（CommPlugin 总线）
        │   ├── DuplicateEndpointError
        │   ├── SignalDeliveryError
        │   └── SignalTimeoutError
        ├── EntryNameConflictError       # Agent 绑定层同 alias 冲突
        ├── FormatVersionError           # jsonl 持久化文件格式版本不受支持
        ├── CorruptionError              # jsonl 持久化文件中间行损坏
        ├── FormatError                  # 声明式文件格式 / Parsable 求值 / 保留属性
        │   ├── MissingFieldError
        │   ├── MissingContextError
        │   ├── NameMismatchError        # 声明 name 与推断名不符（一致性断言）
        │   └── ReservedAttributeError
        └── CompileError                 # .fya 显式编译（构建期）
            └── ArtifactModifiedError    # 编译产物被外部修改，报错中止不覆盖

.. rubric:: 使用示例

.. code-block:: python

    import asyncio

    from flowing.errors import (
        Intercepted, RateLimitedError,
    )

    # 策略层：on_provider_error handler 按错误分类决定是否重试
    async def retry_policy(agent, ctx):
        if isinstance(ctx.error, RateLimitedError):     # 可重试类（限流）
            await asyncio.sleep(ctx.error.retry_after or 1.0)
            ctx.can_continue = True                     # 写 True → 同一回合内重试
        return ctx

    # 安全层：before_tool_call handler 用 Intercepted 硬阻断（审批 / 权限）
    async def approval_policy(agent, tool_call):
        if not (await approval_service.approve(tool_call)):
            raise Intercepted("用户拒绝", payload={"tool": tool_call.name})
        return tool_call

    async def setup(self):
        self.hooks.on_provider_error(retry_policy, by="retry-policy")
        self.hooks.before_tool_call(approval_policy, by="approval-policy")

.. rubric:: 行为要点

- 错误分类与捕获：框架异常统一挂 ``FlowingError``，``except FlowingError``
  可一网打尽框架错误，不误捕 Python 内置异常与第三方库异常。分类中间层
  （``ProviderError`` / ``ToolError`` 等）只承载归属、不附加行为：按类别
  捕获用 ``except ProviderError``，精确捕获用具体子类。
- ``Intercepted`` 刻意不是 ``FlowingError`` 的子类：它是钩子 handler 的
  有意硬阻断信号（审批拒绝 / 安全阻断 / 权限检查等正常业务分支），捕获
  框架错误时不会误捕它。
- import 期作者笔误（如 ``register_provider`` 装饰到非 ``Provider`` 子类）
  刻意用内置 ``ValueError``：编程错误，不该被任何恢复逻辑捕获。
- Provider 调用期错误的可重试分类：``RateLimitedError`` / ``ServerError`` /
  ``NetworkError`` / ``ProviderTimeoutError`` 属可重试类；
  ``ContextLengthError`` / ``RequestTooLargeError`` / ``QuotaExhaustedError`` /
  ``AuthenticationError`` / ``InvalidRequestError`` / ``ContentPolicyError``
  不可重试。可重试性只是分类事实：框架核心不内置重试，是否重试、退避多久
  由 ``on_provider_error`` handler（如 ``use_retry()``）决定。
- Provider 调用期错误一律经 ``on_provider_error`` 分发：handler 写
  ``can_continue=True`` 则同一回合内重试，否则回合以 error 结局终止、
  Agent 存活；``after_turn`` 收尾钩子在所有路径（含异常路径）照常触发，
  已产生的消息照常持久化。``ContextLengthError`` 等原样重发必然重现的
  错误同样分发——压缩 / 换模型 / 仅观察等处置均属 handler 内部逻辑。
- ``MissingEnvironmentVariableError`` 是加载期错误类型（与调用期异常不
  在同一时序）：内置 providers.yaml 加载器**不抛**本异常——
  ``{{env.X}}`` 缺失时替换为空串并 ``warnings.warn`` 告警，缺失凭证的
  实际后果在首次调用时经 ``on_provider_error`` 分发；应用层自写的严格
  配置校验可自行抛出本类（fail-fast 语义保留给需要的加载方）。
- 错误钩子只有 ``on_provider_error``：``on_tool_error`` /
  ``on_subagent_error`` / ``on_error`` / ``before_error`` 均不存在。工具
  业务错误是 ``ToolResult(status="error")`` 正常产物（LLM 可见、不触发任何
  错误钩子）；工具代码崩溃与子 Agent 调用异常直接上抛，无错误钩子兜底；
  钩子 handler 的普通异常同样直接上抛（dispatch 不捕获、不继续后续
  handler）。
- 消息与字段：本模块所有异常的消息文本面向人读（日志 / 提示，不回喂
  LLM）；机器可读信息以结构化字段为权威，调用方不要解析消息文本。具名
  字段在构造时由参数确定（除显式标注默认值者）。
- 内置 ``KeyError`` / ``asyncio.CancelledError`` / ``RuntimeError`` 按
  Python 语义使用，不包装进本层次。
- 本模块的全部公开类型属跨版本稳定契约。

.. seealso::

    :class:`flowing.agent.TurnContext`
        逻辑 Turn 的执行期载体；``after_turn`` 收尾钩子在所有路径照常触发。
    :class:`flowing.hooks.HookRegistry`
        ``on_provider_error`` 钩子点的声明与 dispatch；``Intercepted`` 的
        重抛规则在 dispatch 算法中定义。
    :class:`flowing.tool.ToolResult`
        ``blocked`` / ``error`` 状态的结果载体；工具业务错误是正常产物而
        非异常。
    :class:`flowing.providers.Provider`
        Provider adapter 的异常抛出契约（十个调用期错误类型为互操作标准）。
    :mod:`flowing.composables.retry`
        可选的 ``use_retry()``——重试策略的唯一内置（非默认）提供方。
"""

from pathlib import Path
from typing import Any

__all__ = [
    "FlowingError",
    "ConfigError",
    "ConfigNotReadyError",
    "ConfigNamespaceConflictError",
    "ProvideError",
    "MissingProvideError",
    "HookError",
    "UnknownHookPointError",
    "DuplicateHookPointError",
    "RegistryNotFoundError",
    "RegistryConflictError",
    "AgentTypeNotFoundError",
    "AgentTypeConflictError",
    "SkillNotFoundError",
    "SkillNameConflictError",
    "ToolError",
    "MissingSchemaError",
    "ToolNotFoundError",
    "ToolNameConflictError",
    "UnknownToolError",
    "AmbiguousToolError",
    "AmbiguousMcpSourceError",
    "MissingMcpSourceError",
    "ResourceError",
    "ResourceNameConflictError",
    "ResourceNotFoundError",
    "ProviderError",
    "ContextLengthError",
    "RequestTooLargeError",
    "RateLimitedError",
    "QuotaExhaustedError",
    "ServerError",
    "NetworkError",
    "ProviderTimeoutError",
    "AuthenticationError",
    "InvalidRequestError",
    "ContentPolicyError",
    "MissingEnvironmentVariableError",
    "ProviderNameConflictError",
    "DependencyError",
    "CommError",
    "DuplicateEndpointError",
    "SignalDeliveryError",
    "SignalTimeoutError",
    "FormatError",
    "MissingFieldError",
    "MissingContextError",
    "ReservedAttributeError",
    "NameMismatchError",
    "CompileError",
    "ArtifactModifiedError",
    "FormatVersionError",
    "CorruptionError",
    "Intercepted",
]
# 注：EntryNameConflictError 刻意不列入 __all__；仍可经
# flowing.errors.EntryNameConflictError 显式导入。


class FlowingError(Exception):
    """框架统一异常根。

    .. rubric:: 功能介绍

    Flowing 框架抛出的全部具名异常（``Intercepted`` 除外）的共同基类。应用层用
    ``except FlowingError`` 可以一网打尽框架错误，而不会误捕 Python 内置异常与
    第三方库异常。

    .. rubric:: 行为要点

    - 本类不定义构造参数与字段；各子类自行定义结构化字段。
    - 框架不会把第三方库异常静默包装为本类后重抛：Provider adapter 必须显式
      归类为 ``ProviderError`` 的具体子类后抛出。
    - ``Intercepted`` 刻意不继承本类——它是钩子 handler 的有意硬阻断信号，
      捕获框架错误时不会误捕它。

    .. seealso::

        :class:`flowing.errors.Intercepted`
    """


# ---------------------------------------------------------------------------
# 配置类
# ---------------------------------------------------------------------------


class ConfigError(FlowingError):
    """配置读取时机与配置命名空间注册相关异常的分类中间层。

    .. rubric:: 功能介绍

    配置类异常的公共基类，覆盖两类场景：配置未就绪时读取（
    ``ConfigNotReadyError``）与多个扩展注册同一配置命名空间（
    ``ConfigNamespaceConflictError``）。配置错误属于部署 / 声明期错误，框架在
    出错点立即抛出（fail-fast），不静默降级。

    .. rubric:: 行为要点

    - 本类是分类中间层：框架实际抛出的是其具体子类；按类别捕获用
      ``except ConfigError``。
    - 配置错误不可重试：修正配置或修正调用位置后重新启动。

    .. seealso::

        :class:`flowing.errors.ConfigNotReadyError`
        :class:`flowing.errors.ConfigNamespaceConflictError`
        :meth:`flowing.runtime.Runtime.get_config`
    """


class ConfigNotReadyError(ConfigError):
    """配置就绪之前调用 ``get_config()`` 时抛出。

    .. rubric:: 功能介绍

    ``Runtime.get_config()`` 在配置就绪前被调用时抛出。就绪的时点是优先级链
    （环境变量 > 命令行 > 用户级 > 项目级 > 默认值）浅合并完成之时——典型未
    就绪场景是模块顶层（import 期）调用；合并完成后任何时机均可调用
    （``setup()``、钩子回调、工具 callable、``main()`` 后续代码）。

    .. rubric:: 使用示例

    .. code-block:: python

        # 模块顶层调用——配置未就绪，抛出 ConfigNotReadyError
        TIMEOUT = runtime.get_config("agent.timeout")

        # 配置就绪后任何时机可调用（如 setup() 中）
        async def setup(self):
            self.timeout = self.runtime.get_config("agent.timeout", default=60)

    .. rubric:: 行为要点

    - 无结构化字段；消息为固定的英文提示。
    - 编程错误：修正调用位置（改为在就绪后的时机读取），调用方不捕获。
    - 多 Runtime 场景按各自 Runtime 的就绪状态独立判定。

    .. seealso::

        :meth:`flowing.runtime.Runtime.get_config`
        :class:`flowing.errors.ConfigError`
    """

    def __init__(self) -> None:
        """构造异常实例（无参数，无结构化字段）。

        消息为固定的英文提示。
        """
        # 无字段叶子：固定英文提示消息
        super().__init__(
            "Configuration is not ready: read config only in setup() "
            "or hook callbacks, not at module top level"
        )


class ConfigNamespaceConflictError(ConfigError):
    """多个扩展注册同一配置命名空间时抛出。

    .. rubric:: 功能介绍

    ``Runtime.register_config_namespace(name, schema)`` 检测到命名空间已被其它
    扩展注册时抛出。命名空间注册声明「谁负责校验 / 提供默认值 / 类型转换」；
    两个扩展认领同一命名空间会产生两套冲突的校验规则，必须在注册时立即失败
    （fail-fast）。未被任何扩展注册的命名空间可自由读取，不触发本异常。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.register_config_namespace("i18n", I18nSchema)
        runtime.register_config_namespace("i18n", OtherSchema)   # 重复注册 → 抛出

    .. rubric:: 行为要点

    - ``namespace`` 字段为冲突的配置命名空间名（诊断信息，框架不做后续仲裁）。
    - 安装期编程错误：调用方不捕获。
    - 注册只声明校验职责，不做命名空间访问控制——任何代码可读任何命名空间。

    .. seealso::

        :meth:`flowing.runtime.Runtime.register_config_namespace`
        :class:`flowing.errors.ConfigError`
    """

    namespace: str
    """冲突的配置命名空间名（诊断信息，框架不做后续仲裁）。"""

    def __init__(self, namespace: str) -> None:
        """构造异常实例。

        :param namespace: 冲突的配置命名空间名；与 ``namespace`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Config namespace conflict: {namespace!r} is already registered")
        self.namespace = namespace


# ---------------------------------------------------------------------------
# 注入类
# ---------------------------------------------------------------------------


class ProvideError(FlowingError):
    """provide/inject 机制异常的分类中间层。

    .. rubric:: 功能介绍

    provide-inject 链相关异常的公共基类。inject 沿亲代链（Agent 子树 → Workflow
    → Runtime，Runtime 为链终点）逐级上溯查找，命中即返回；当前唯一子类是链
    上溯到终点仍未命中时抛出的 ``MissingProvideError``。

    .. rubric:: 行为要点

    - 本类是分类中间层：框架实际抛出的是其具体子类；按类别捕获用
      ``except ProvideError``。
    - inject 查找是实时的（不缓存）：运行期 ``provide`` 更新后再次 inject
      立即得到新值。

    .. seealso::

        :class:`flowing.errors.MissingProvideError`
        :meth:`flowing.agent.Agent.inject`
        :func:`flowing.runtime.inject_from`
    """


class MissingProvideError(ProvideError):
    """inject 沿亲代链上溯到终点（Runtime）仍未找到 key 时抛出。

    .. rubric:: 功能介绍

    ``inject(key)`` 查找失败时抛出的唯一异常：沿亲代链从当前节点逐级向根查找，
    到 Runtime 终点仍未命中即抛出。查找是实时的（不缓存），运行期 ``provide``
    更新后再次 inject 可得新值。

    .. rubric:: 使用示例

    .. code-block:: python

        async def setup(self):
            user_id = self.inject("user_id")   # 链上溯未命中 → 抛出 MissingProvideError

    .. rubric:: 行为要点

    - ``key`` 字段为未命中的 provide key（``InjectionKey[T]`` 退化为其 ``name``
      字符串）；仅诊断用途，不提供「最接近的 key」之类的猜测。
    - ``inject`` 没有默认值参数：需要「缺失时用默认值」的语义，请自行捕获本
      异常后回退默认值。
    - 结构错误、不可重试。
    - 启动期插件的静态依赖校验（缺失警告、成环报错）由 ``DependencyError``
      相关机制承担；本异常是运行期动态注入的兜底——两层互补。

    .. seealso::

        :meth:`flowing.agent.Agent.inject`
        :class:`flowing.params.InjectionKey`
        :class:`flowing.errors.DependencyError`
    """

    key: str
    """未命中的 provide key（``InjectionKey[T]`` 退化为其 ``name`` 字符串）；仅诊断用途。"""

    def __init__(self, key: str) -> None:
        """构造异常实例。

        :param key: 未命中的 provide key；与 ``key`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Missing provide value for key: {key!r}")
        self.key = key


# ---------------------------------------------------------------------------
# 钩子类
# ---------------------------------------------------------------------------


class HookError(FlowingError):
    """钩子点声明与访问异常的分类中间层。

    .. rubric:: 功能介绍

    钩子点声明（``declare()``）与访问（``hooks.<name>`` 查找）相关异常的公共
    基类：访问未声明钩子点（``UnknownHookPointError``）与同名钩子点声明冲突
    （``DuplicateHookPointError``）。插件加载 / 重载路径以 ``except HookError``
    统一兜捕钩子声明阶段的失败，而不误捕其它子系统的异常。

    .. rubric:: 行为要点

    - 本类是分类中间层：框架实际抛出的是其具体子类；按类别捕获用
      ``except HookError``。
    - 钩子 handler 的普通异常不属于本类——dispatch 不捕获、不继续后续
      handler，直接上抛，无兜底钩子。

    .. seealso::

        :class:`flowing.errors.UnknownHookPointError`
        :class:`flowing.errors.DuplicateHookPointError`
        :class:`flowing.hooks.HookRegistry`
    """


class UnknownHookPointError(HookError):
    """访问未声明的钩子点时抛出。

    .. rubric:: 功能介绍

    ``self.hooks.<name>`` 的按名查找只查找不创建：name 既不在框架预填的钩子点
    中、也未被任何扩展 ``declare()`` 时抛出。钩子点集合因此是显式契约——扩展
    必须先声明再使用，拼写错误在注册时即暴露，而不是静默创建一个永远不会被
    dispatch 的空钩子点。

    .. rubric:: 使用示例

    .. code-block:: python

        self.hooks.before_skill_load(handler)   # 未调用 use_skill(self) → 抛出

    .. rubric:: 行为要点

    - ``name`` 字段为被访问但未声明的钩子点名（精确字符串，不含相似名建议）。
    - 编程错误：调用方不捕获；修正钩子点名，或先 ``declare()`` / 启用对应扩展。
    - 未启用扩展的 Agent 访问其扩展钩子点即抛本异常——未启用扩展零开销。

    .. seealso::

        :meth:`flowing.hooks.HookRegistry.declare`
        :class:`flowing.errors.HookError`
    """

    name: str
    """被访问但未声明的钩子点名（精确字符串，不含相似名建议）。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 被访问但未声明的钩子点名；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Unknown hook point: {name!r} (not declared)")
        self.name = name


class DuplicateHookPointError(HookError):
    """``declare()`` 声明同名钩子点且来源无法区分时抛出。

    .. rubric:: 功能介绍

    两种冲突形态：同名 + 不同 ``by``；同名 + 双方 ``by=None``。同名 + 同
    ``by`` 是幂等返回已有的 ``HookList``，不抛出。

    .. rubric:: 使用示例

    .. code-block:: python

        self.hooks.declare("on_signal", by="comm", match_on="type")
        self.hooks.declare("on_signal", by="audit")                    # 同名不同 by → 抛出
        self.hooks.declare("on_signal", by="comm", match_on="type")    # 同名同 by → 幂等，不抛

    .. rubric:: 行为要点

    - 字段 ``name`` （冲突的钩子点名）、``existing_by`` （已注册声明者的 ``by``
      标识）、``new_by`` （本次冲突声明者的 ``by`` 标识）。
    - ``declare()`` 的 ``by`` 参数必填（框架核心预填钩子点固定为
      ``by="core"``）。
    - 安装期编程错误：调用方不捕获。
    - 钩子点声明独占：语义（value 类型、dispatch 时机）由首个声明者拥有。

    .. seealso::

        :meth:`flowing.hooks.HookRegistry.declare`
        :class:`flowing.errors.HookError`
    """

    name: str
    """冲突的钩子点名。"""
    existing_by: str | None
    """已注册声明者的 ``by`` 标识。"""
    new_by: str | None
    """本次冲突声明者的 ``by`` 标识。"""

    def __init__(self, name: str, existing_by: str | None, new_by: str | None) -> None:
        """构造异常实例。

        :param name: 冲突的钩子点名；与 ``name`` 字段一致。
        :param existing_by: 已注册声明者的 ``by`` 标识；与 ``existing_by`` 字段一致。
        :param new_by: 本次冲突声明者的 ``by`` 标识；与 ``new_by`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Duplicate hook point declaration: {name!r} (existing by={existing_by!r}, new by={new_by!r})")
        self.name = name
        self.existing_by = existing_by
        self.new_by = new_by


# ---------------------------------------------------------------------------
# 注册表类
# ---------------------------------------------------------------------------


class RegistryConflictError(FlowingError):
    """注册表键冲突异常的分类中间层（Tool / Agent 类型 / Skill 注册表共用）。

    .. rubric:: 功能介绍

    各资源注册表（``ToolRegistry`` / ``AgentRegistry`` / ``SkillRegistry``）
    注册通道的键冲突异常公共基类：``ns::name`` 全限定键重名注册时抛出的
    是本类的某个具体子类（``ToolNameConflictError`` /
    ``AgentTypeConflictError`` / ``SkillNameConflictError``）。按类别捕获
    用 ``except RegistryConflictError``，精确捕获用具体子类。

    .. rubric:: 行为要点

    - 本类是分类中间层：框架实际抛出的是其具体子类；本类自身不定义
      字段，冲突键由各子类的字段承载。
    - 冲突按 ``ns::name`` 全限定键判定：不同命名空间的同名资源允许共存。
    - 安装期错误：调用方不捕获；不提供覆盖开关。
    - Agent 绑定层的别名冲突不在本层：同 alias 重复绑定是
      ``EntryNameConflictError``（per-Agent、LLM 视角），与本层（注册
      表层、全局）分层。

    .. seealso::

        :class:`flowing.errors.AgentTypeConflictError`
        :class:`flowing.errors.SkillNameConflictError`
        :class:`flowing.errors.ToolNameConflictError`
        :class:`flowing.errors.RegistryNotFoundError`
    """


class RegistryNotFoundError(FlowingError):
    """注册表查找未命中异常的分类中间层（Tool / Agent 类型 / Skill 注册表共用）。

    .. rubric:: 功能介绍

    各资源注册表（``ToolRegistry`` / ``AgentRegistry`` / ``SkillRegistry``）
    查找通道的未命中异常公共基类：按规范名 / 限定名 / 路径形态引用一个
    注册表与文件查找链均无法解析的资源时，抛出的是本类的某个具体子类
    （``ToolNotFoundError`` / ``AgentTypeNotFoundError`` /
    ``SkillNotFoundError``）。按类别捕获用 ``except RegistryNotFoundError``
    （如「先探测再注册」的通用试探逻辑），精确捕获用具体子类。

    .. rubric:: 行为要点

    - 本类是分类中间层：框架实际抛出的是其具体子类；本类自身不定义
      字段，未命中的引用串由各子类的 ``name`` 字段承载。
    - 查找失败显式报错而非静默返回 ``None``；注册表内容在运行期不自动
      变化，不可重试。
    - 别名声道的未命中不在本层：Agent 绑定层按别名查找失败是
      ``UnknownToolError`` 等绑定层异常，与本层（注册表层规范名 /
      限定名）分层。

    .. seealso::

        :class:`flowing.errors.AgentTypeNotFoundError`
        :class:`flowing.errors.SkillNotFoundError`
        :class:`flowing.errors.ToolNotFoundError`
    """


class AgentTypeNotFoundError(RegistryNotFoundError):
    """Agent 类型引用在注册表与文件查找链均无法解析时抛出。

    .. rubric:: 功能介绍

    ``AgentRegistry.get()`` （``Runtime.get_agent_class()`` 的解析本体）
    对限定名 / 裸名 / 路径形态三种引用均无法解析时抛出——限定名只查
    注册表精确键；裸名查注册表裸名视图（``default::`` 优先
    ``builtin::``）与定向文件查找链；路径形态经候选链定位 ``.fya`` /
    手写 ``.py``。创建 / 恢复管线（``create_agent`` / ``recover_agent``）
    与 ``subagent-invoke`` 的类型解析失败即本异常。

    .. rubric:: 行为要点

    - ``name`` 字段为未命中的引用串原样（限定名 / 裸名 / 路径形态）。
    - 解析失败显式报错而非静默返回 ``None``；调用方可按需捕获（如先
      探测再注册，探测也可用 ``AgentRegistry.__contains__``）。
    - 不可重试：注册表内容在运行期不自动变化。

    .. seealso::

        :class:`flowing.agent_registry.AgentRegistry`
        :meth:`flowing.runtime.Runtime.get_agent_class`
        :class:`flowing.errors.RegistryNotFoundError`
    """

    name: str
    """未命中的 Agent 类型引用串（限定名 / 裸名 / 路径形态原样）。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 未命中的 Agent 类型引用串；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Agent type not found in registry: {name!r}")
        self.name = name


class AgentTypeConflictError(RegistryConflictError):
    """Agent 类型注册键（``ns::name`` 全限定键）重名注册时抛出。

    .. rubric:: 功能介绍

    ``AgentRegistry.register()`` （``Runtime.register_agent_type()`` 的
    注册本体）检测到同键已存在时抛出。注册键是注册表唯一键：静默覆盖
    会让先注册方的全部引用（含已落账 ``registry_key`` 的 Entry 装配）
    指向被换掉的实现；需要替换实现时应换键注册而非覆盖。

    与 ``EntryNameConflictError`` 分层：本类管注册表层的全限定键冲突
    （全局、跨 Agent）；它管 Agent 绑定层的别名冲突（per-Agent）。

    .. rubric:: 行为要点

    - ``key`` 字段为发生冲突的注册表全限定键（``ns::name``）。
    - 不同命名空间的同名类型允许共存（冲突按全限定键判定）。
    - 安装期错误：调用方不捕获；不提供覆盖开关。

    .. seealso::

        :meth:`flowing.agent_registry.AgentRegistry.register`
        :class:`flowing.errors.ToolNameConflictError`
        :class:`flowing.errors.EntryNameConflictError`
    """

    key: str
    """发生冲突的注册表全限定键（``ns::name``）。"""

    def __init__(self, key: str) -> None:
        """构造异常实例。

        :param key: 发生冲突的注册表全限定键；与 ``key`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Agent type conflict: {key!r} is already registered")
        self.key = key


class SkillNameConflictError(RegistryConflictError):
    """Skill 注册键（``ns::name`` 全限定键）重名注册时抛出。

    .. rubric:: 功能介绍

    ``SkillRegistry.register()`` 检测到同键已存在时抛出。注册键是注册表
    唯一键：静默覆盖会让先注册方的全部引用指向被换掉的实现；需要替换
    实现时应换键注册而非覆盖。

    .. rubric:: 行为要点

    - ``key`` 字段为发生冲突的注册表全限定键（``ns::name``）。
    - 不同命名空间的同名技能允许共存（冲突按全限定键判定）。
    - 安装期错误：调用方不捕获；不提供覆盖开关。

    .. seealso::

        :class:`flowing.plugins.skills.registry.SkillRegistry`
        :class:`flowing.errors.RegistryConflictError`
    """

    key: str
    """发生冲突的注册表全限定键（``ns::name``）。"""

    def __init__(self, key: str) -> None:
        """构造异常实例。

        :param key: 发生冲突的注册表全限定键；与 ``key`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Skill name conflict: {key!r} is already registered")
        self.key = key


class SkillNotFoundError(RegistryNotFoundError):
    """Skill 引用在技能注册表与文件查找链均无法解析时抛出。

    .. rubric:: 功能介绍

    ``SkillRegistry.get()`` 对限定名 / 裸名 / 路径形态三种引用均无法
    解析时抛出（含显式路径指向的定义文件不存在或形态非法）。
    ``use_skill`` 装配与 ``SkillLoadTool`` 的解析失败即本异常。

    .. rubric:: 行为要点

    - ``name`` 字段为未命中的引用串原样。
    - 不可重试：注册表内容在运行期不自动变化。

    .. seealso::

        :class:`flowing.plugins.skills.registry.SkillRegistry`
        :class:`flowing.errors.RegistryNotFoundError`
    """

    name: str
    """未命中的 Skill 引用串（限定名 / 裸名 / 路径形态原样）。"""
    detail: str | None
    """补充诊断（如已尝试的查找路径清单）；无补充时为 ``None``。"""

    def __init__(self, name: str, detail: str | None = None) -> None:
        """构造异常实例。

        :param name: 未命中的 Skill 引用串；与 ``name`` 字段一致。
        :param detail: 补充诊断文本（缺省 ``None``）。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Skill not found in registry: {name!r}"
                         + (f" ({detail})" if detail else ""))
        self.name = name
        self.detail = detail


# ---------------------------------------------------------------------------
# 工具类
# ---------------------------------------------------------------------------


class ToolError(FlowingError):
    """工具定义 / 注册 / 查找异常的分类中间层。

    .. rubric:: 功能介绍

    工具系统（``script`` / ``mcp`` / ``cli`` / ``request`` 四种类型）的定义期
    与查找期异常公共基类。与工具业务错误严格区分：业务错误是
    ``ToolResult(status="error")`` 正常产物，LLM 可见，不走异常通道；本层只
    承载「工具坏了」这一类（定义缺失、注册冲突、查找失败）。

    .. rubric:: 行为要点

    - 本类是分类中间层：框架实际抛出的是其具体子类；按类别捕获用
      ``except ToolError``。
    - 工具 ``execute()`` 内部崩溃不包装为本类——按普通异常直接上抛。

    .. seealso::

        :class:`flowing.tool.Tool`
        :class:`flowing.tool.ToolResult`
        :class:`flowing.tool.ToolRegistry`
    """


class MissingSchemaError(ToolError):
    """工具参数 schema 缺失且无法推断时抛出。

    .. rubric:: 功能介绍

    两种抛出场景：script 工具裸函数的参数缺少类型标注（无法自动提取 schema）；
    ``cli`` / ``request`` 工具的 ``.fya`` 未声明必填的 ``args`` （这两类没有自动
    推断来源）。``.fya`` 显式声明的 ``args`` 优先级最高，显式声明存在时本异常
    不会因类型标注缺失而抛出。

    .. rubric:: 行为要点

    - 字段 ``name`` （工具规范名）与 ``param`` （缺失类型标注的参数名；整体缺失
      时为 ``None``）。
    - 定义期错误：调用方不捕获（fail-fast）。
    - 不可重试。

    .. seealso::

        :class:`flowing.tool.ScriptTool`
        :mod:`flowing.params`
        :class:`flowing.errors.ToolError`
    """

    name: str
    """缺失 schema 的工具规范名。"""
    param: str | None
    """缺失类型标注的参数名；整体缺失（如 ``cli`` / ``request`` 未声明 ``args``）时为 ``None``。"""

    def __init__(self, name: str, param: str | None = None) -> None:
        """构造异常实例。

        :param name: 工具规范名；与 ``name`` 字段一致。
        :param param: 缺失类型标注的参数名；整体缺失时省略（默认 ``None``）。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(
            f"Missing params schema for tool {name!r}"
            + (f" (param {param!r})" if param is not None else "")
        )
        self.name = name
        self.param = param


class ToolNotFoundError(ToolError, RegistryNotFoundError):
    """按规范名在工具注册表中找不到工具时抛出。

    .. rubric:: 功能介绍

    ``ToolRegistry.get()`` 按规范名查找已注册工具失败时抛出。规范名是注册时的
    唯一标识；Agent 绑定层的别名（``ToolEntry`` 的 ``name_alias``）是另一个
    命名空间——按别名查找失败的异常是 ``UnknownToolError``。双挂
    ``RegistryNotFoundError``：跨注册表的统一探测可 ``except
    RegistryNotFoundError`` 一网打尽。

    .. rubric:: 行为要点

    - ``name`` 字段为未命中的工具规范名。
    - 查找失败显式报错而非静默返回 ``None``；调用方可按需捕获（如先探测再注册）。
    - 不可重试：注册表内容不会自动变化。

    .. seealso::

        :class:`flowing.tool.ToolRegistry`
        :class:`flowing.errors.UnknownToolError`
        :class:`flowing.errors.RegistryNotFoundError`
    """

    name: str
    """未命中的工具规范名。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 未命中的工具规范名；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Tool not found in registry: {name!r}")
        self.name = name


class ToolNameConflictError(ToolError, RegistryConflictError):
    """工具规范名重名注册时抛出。

    .. rubric:: 功能介绍

    ``ToolRegistry.register()`` 检测到同名规范名时抛出。重名一律不允许，无论
    工具类型——包括同一 MCP 服务器不同配置的场景（须用不同规范名）。规范名是
    注册表唯一键：静默覆盖会让先注册方的所有 Agent 绑定指向被换掉的实现；需要
    相同实例时应复用已有注册而非重复注册。双挂 ``RegistryConflictError``：跨
    注册表的统一冲突捕获可 ``except RegistryConflictError`` 一网打尽。

    Agent 绑定层的别名冲突（同 alias）由 ``EntryNameConflictError`` 承载，与本
    类（注册表层规范名冲突）分层。

    .. rubric:: 行为要点

    - ``name`` 字段为发生冲突的工具规范名。
    - 安装期错误：调用方不捕获。
    - 不提供覆盖开关（与 Provider adapter 注册表不同——工具注册表在 Runtime
      实例内，重注册无正当场景）。

    .. seealso::

        :meth:`flowing.tool.ToolRegistry.register`
        :class:`flowing.errors.EntryNameConflictError`
    """

    name: str
    """发生冲突的工具规范名。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 发生冲突的工具规范名；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Tool name conflict: {name!r} is already registered")
        self.name = name


class EntryNameConflictError(FlowingError):
    """Agent 绑定层同 alias 冲突：tool / skill / subagent 条目统一报错。

    .. rubric:: 功能介绍

    三层能力描述的 Agent 级绑定层（tool / skill / subagent 条目，key 均为别名）
    中，同一 Agent 内同 alias 重复声明或添加时抛出。覆盖两条入口：声明式
    （``.fya`` 的 ``tools:`` / ``skills:`` / ``subagents:`` 列表出现两条同
    alias 条目）与编程式（``Agent.add_tool()`` 等）。同一生命周期内重复永远
    非法，不提供覆盖开关。冲突判定是 per-Agent 的：不同 Agent 各引各的同名
    资源互不冲突。

    与 ``ToolNameConflictError`` 分层：本类管 Agent 绑定层的别名冲突（per-Agent、
    LLM 视角——LLM 只看别名）；它管注册表层的规范名冲突（全局、跨 Agent）。

    .. rubric:: 使用示例

    .. code-block:: python

        self.add_tool("make-payment", alias="pay")
        self.add_tool("other-payment", alias="pay")   # 同 alias 重复添加 → 抛出

    .. rubric:: 行为要点

    - 字段 ``alias`` （发生冲突的别名）与 ``kind`` （冲突所在的绑定层：
      ``"tool"`` / ``"skill"`` / ``"subagent"``）。
    - 定义期 / 安装期编程错误：调用方不捕获。
    - 插件挂载前的「先查后跳」（如 ``use_skill`` 保留用户定义）是合法规避，
      只有未经检查的盲目写入才触发本异常。

    .. seealso::

        :class:`flowing.errors.ToolNameConflictError`
        :meth:`flowing.agent.Agent.add_tool`
    """

    alias: str
    """发生冲突的别名。"""
    kind: str
    """冲突所在的绑定层：``"tool"`` / ``"skill"`` / ``"subagent"``。"""

    def __init__(self, alias: str, kind: str) -> None:
        """构造异常实例。

        :param alias: 发生冲突的别名；与 ``alias`` 字段一致。
        :param kind: 冲突所在的绑定层（``"tool"`` / ``"skill"`` / ``"subagent"``）；
          与 ``kind`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Entry alias conflict: {alias!r} (kind={kind!r})")
        self.alias = alias
        self.kind = kind


class UnknownToolError(ToolError):
    """``tool_call()`` 按别名在 Agent 绑定表中找不到工具时抛出。

    .. rubric:: 功能介绍

    LLM 发起的工具调用仅按别名查找 Agent 级绑定表（``ToolEntry`` 的别名），
    不回退规范名；别名未命中时抛出（典型场景：LLM 编造了不在 ``Context.tools``
    中的名字）。不回退规范名是刻意的：不同 Agent 对同一工具可注册不同别名与
    覆写，回退会绕开 Agent 级绑定层。

    .. rubric:: 行为要点

    - ``name`` 字段为 LLM 给出的未命中别名。
    - 框架核心不捕获本异常（直接上抛出工具调用循环）；应用层可在
      ``before_tool_call`` 前置校验或自行捕获。
    - 不可自动重试：LLM 可在后续回合修正调用名，是否重试属应用策略。
    - 本异常只在别名完全不存在时抛出：停用（``enabled=False``）只影响 LLM
      可见性，不影响本异常的判定。

    .. seealso::

        :meth:`flowing.agent.Agent.tool_call`
        :class:`flowing.tool.ToolEntry`
        :class:`flowing.errors.ToolNotFoundError`
    """

    name: str
    """未命中的工具别名（LLM 可见名）。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 未命中的工具别名；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Unknown tool alias: {name!r}")
        self.name = name


class AmbiguousToolError(ToolError):
    """工具 ``.py`` 文件的定义形态歧义时抛出。

    .. rubric:: 功能介绍

    script 工具的定向查找中，同一 ``.py`` 文件出现以下任一情况即抛出，框架
    无法判定用户意图：同时含 ``@flowing_tool`` 打标函数与 Tool 子类；或含多个
    打标函数（违反「每文件至多一个」规则）。两种形态生成不同的 Tool 定义路径，
    按序选其一或隐式合并都会让行为依赖文件内容的出现顺序。

    .. rubric:: 行为要点

    - ``path`` 字段为产生歧义的 ``.py`` 文件路径（字符串形式）。
    - 定义期错误：调用方不捕获（fail-fast）。
    - ``.tool.fya`` 与同名 ``.py`` 并存不属于歧义（``.fya`` 优先）；
      ``callable:`` 指向已装饰函数属声明通道互斥的违反，抛 ``FormatError``。

    .. seealso::

        :class:`flowing.tool.ScriptTool`
        :func:`flowing.tool.flowing_tool`
        :class:`flowing.errors.ToolError`
    """

    path: str
    """产生歧义的 ``.py`` 文件路径（字符串形式）。"""

    def __init__(self, path: str) -> None:
        """构造异常实例。

        :param path: 产生歧义的 ``.py`` 文件路径；与 ``path`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Ambiguous tool definitions in file: {path}")
        self.path = path


class AmbiguousMcpSourceError(ToolError):
    """MCP 工具同时声明 ``command`` （stdio）与 ``url`` （远程）时抛出。

    .. rubric:: 功能介绍

    ``type: mcp`` 的工具必须声明恰好一种连接来源：``command`` 表示 stdio 本地
    子进程，``url`` 表示远程服务。两者并存时无法判定连接方式，抛出本异常。

    .. rubric:: 行为要点

    - ``name`` 字段为声明冲突的 MCP 工具规范名。
    - 定义期错误：调用方不捕获（fail-fast）。
    - 与 ``MissingMcpSourceError`` （两者均未声明）构成「恰好一个来源」的对偶
      约束。

    .. seealso::

        :class:`flowing.errors.MissingMcpSourceError`
        :class:`flowing.errors.ToolError`
    """

    name: str
    """声明冲突的 MCP 工具规范名。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 声明冲突的 MCP 工具规范名；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"MCP tool {name!r} declares both command and url")
        self.name = name


class MissingMcpSourceError(ToolError):
    """MCP 工具的 ``command`` 与 ``url`` 均未声明时抛出。

    .. rubric:: 功能介绍

    ``type: mcp`` 的工具必须声明一种连接来源；两者皆缺时没有任何可推断的默认
    连接方式，抛出本异常。

    .. rubric:: 行为要点

    - ``name`` 字段为缺失来源声明的 MCP 工具规范名。
    - 定义期错误：调用方不捕获（fail-fast）。
    - 与 ``AmbiguousMcpSourceError`` （两者同时声明）构成对偶约束。

    .. seealso::

        :class:`flowing.errors.AmbiguousMcpSourceError`
        :class:`flowing.errors.ToolError`
    """

    name: str
    """缺失来源声明的 MCP 工具规范名。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 缺失来源声明的 MCP 工具规范名；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"MCP tool {name!r} declares neither command nor url")
        self.name = name


# ---------------------------------------------------------------------------
# 子 Agent 与资源类
# ---------------------------------------------------------------------------


class ResourceError(FlowingError):
    """Resource 注册与访问异常的分类中间层。

    .. rubric:: 功能介绍

    Resource（Runtime 持有的跨 Agent / 跨任务共享实例，如数据库连接池）的注册
    与读取异常公共基类：重名注册（``ResourceNameConflictError``）与访问未注册
    实例（``ResourceNotFoundError``）。Resource 与 provide/inject 正交：值跟随
    Agent 生命周期用 provide，值跨越多个 Agent 与任务用 Resource；Resource 不
    走 inject 链，错误类型独立于 ``ProvideError``。

    .. rubric:: 行为要点

    - 本类是分类中间层：框架实际抛出的是其具体子类；按类别捕获用
      ``except ResourceError``。
    - ``get_resource(name, type_hint=...)`` 的 ``type_hint`` 仅供 IDE 推断，
      运行时不做 isinstance 校验，不存在「类型不匹配」异常。

    .. seealso::

        :meth:`flowing.runtime.Runtime.register_resource`
        :meth:`flowing.runtime.Runtime.get_resource`
        :meth:`flowing.agent.Agent.get_resource`
    """


class ResourceNameConflictError(ResourceError):
    """Resource 重名注册时抛出。

    .. rubric:: 功能介绍

    ``Runtime.register_resource(name, instance)`` 检测到同名时抛出。Resource 是
    全局共享实例：静默覆盖会让已持有旧实例引用的 Agent 与新读取方看到不同对象，
    同名即编程错误。注册应在启动根 Agent（``mount()``）前完成。

    .. rubric:: 行为要点

    - ``name`` 字段为发生冲突的 Resource 名。
    - 启动期错误：调用方不捕获。
    - 不可重试。

    .. seealso::

        :meth:`flowing.runtime.Runtime.register_resource`
        :class:`flowing.errors.ResourceError`
    """

    name: str
    """发生冲突的 Resource 名。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 发生冲突的 Resource 名；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Resource name conflict: {name!r} is already registered")
        self.name = name


class ResourceNotFoundError(ResourceError):
    """访问未注册的 Resource 时抛出。

    .. rubric:: 功能介绍

    ``Runtime.get_resource(name)`` （及 ``Agent.get_resource`` 便捷委托）找不到
    已注册实例时抛出。Resource 获取无感（不在 ``args`` / inject 中声明），缺失
    只能在读取点暴露；显式异常比返回 ``None`` 更能防止下游 ``AttributeError``
    式的次生错误。

    .. rubric:: 行为要点

    - ``name`` 字段为未命中的 Resource 名。
    - 调用方可按需捕获（如可选能力的降级场景）。
    - 不可重试。

    .. seealso::

        :meth:`flowing.runtime.Runtime.get_resource`
        :class:`flowing.errors.ResourceError`
    """

    name: str
    """未命中的 Resource 名。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 未命中的 Resource 名；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Resource not found: {name!r}")
        self.name = name


# ---------------------------------------------------------------------------
# Provider 类
# ---------------------------------------------------------------------------


class ProviderError(FlowingError):
    """Provider 加载与调用异常的分类中间层（adapter 互操作标准）。

    .. rubric:: 功能介绍

    Provider adapter 抛出的全部归类异常的公共基类。adapter 必须把 SDK / HTTP
    层的原生异常归类为本层的具体子类后抛出——这是 adapter 互操作标准的一部分：
    回合层的错误决策（``on_provider_error`` → ``can_continue``）依赖稳定的错误
    分类，策略层不需要 import 每个 SDK 的异常类型。

    .. rubric:: 行为要点

    - 通用字段：``provider`` （Provider 条目名）、``model`` （模型 ID）、
      ``status_code`` （HTTP 状态码）、``request_id`` （服务端请求 ID）、
      ``retry_after`` （服务端建议的重试等待秒数）——均默认 ``None``，供日志、
      计费与策略层消费。字段置于基类：``Retry-After`` 语义可出现在任意响应
      （503/529 过载同样携带），重试 handler 可无条件消费
      ``retry_after or 默认退避``，不按类型特判。
    - 本类是分类中间层：adapter 应抛出具体子类，而不是本类实例。
    - 调用期错误的可重试分类：``RateLimitedError`` / ``ServerError`` /
      ``NetworkError`` / ``ProviderTimeoutError`` 属可重试类；
      ``ContextLengthError`` / ``RequestTooLargeError`` / ``QuotaExhaustedError`` /
      ``AuthenticationError`` / ``InvalidRequestError`` / ``ContentPolicyError``
      不可重试——可重试性只是分类事实，是否重试由 ``on_provider_error`` handler
      （如 ``use_retry()``）决定，框架核心不内置重试。
    - ``provider_gen()`` 不捕获、不重试任何 Provider 异常，统一在逻辑 Turn 层
      接住并经 ``on_provider_error`` 分发（处置属 handler 内部逻辑）。
    - 消息文本面向人读（不回喂 LLM）；结构化字段面向代码。

    .. seealso::

        :class:`flowing.providers.Provider`
        :mod:`flowing.composables.retry`
    """

    provider: str | None
    """Provider 条目名（``providers.yaml`` 中的 key，条目名即身份标识）；加载期错误（如应用层严格校验抛出的 ``MissingEnvironmentVariableError``）同样填写。"""
    model: str | None
    """调用时使用的模型 ID（``ModelConfig.model``）；加载期异常可为 ``None``。"""
    status_code: int | None
    """HTTP 状态码（如 429 / 500 / 503）；非 HTTP 来源的错误（连接失败、加载期失败等）为 ``None``。"""
    request_id: str | None
    """服务端返回的请求 ID（如 ``x-request-id`` 响应头）；未提供为 ``None``。"""
    retry_after: float | None
    """服务端建议的重试等待秒数（``Retry-After`` 响应头）；未建议为 ``None``，由策略层使用自己的默认退避。429 之外的响应（503/529 过载）同样可能携带，故置于基类。"""

    def __init__(
        self,
        message: str = "",
        *,
        provider: str | None = None,
        model: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        """构造 Provider 错误实例。

        :param message: 面向人读的错误消息（不回喂 LLM）。
        :param provider: Provider 条目名；默认 ``None``。
        :param model: 调用时使用的模型 ID；默认 ``None``。
        :param status_code: HTTP 状态码；默认 ``None``。
        :param request_id: 服务端请求 ID；默认 ``None``。
        :param retry_after: 服务端建议的重试等待秒数；默认 ``None``。
        """
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after = retry_after


class ContextLengthError(ProviderError):
    """上下文长度溢出（token 数超限；经 ``on_provider_error`` 分发，处置属策略）。

    .. rubric:: 功能介绍

    组装的 ``Context`` 超出模型 ``context_window`` 时由 adapter 在请求 / 响应
    解析时抛出。与其它 Provider 调用期异常一样经 ``on_provider_error`` 分发：
    原样重发必然重现同样失败，因此默认策略（``use_retry``）不对它重试；但
    压缩会话历史、换用更大窗口模型、或仅观察记录，都是 handler 的合法处置。
    压缩 / 截断属策略，由 handler 在处置中自行完成（如压缩 Composable），
    不在错误路径内自动发生。

    .. rubric:: 行为要点

    - 字段继承 ``ProviderError`` （``provider`` / ``model``）。
    - 经 ``on_provider_error`` 分发：默认 ``can_continue=False`` 时回合以
      error 结局终止（``after_turn`` 收尾钩子照常触发，已产生的消息照常
      持久化），Agent 存活；handler 完成压缩 / 换模型等动作后可置
      ``can_continue=True`` 回合内重试。
    - 与 ``RequestTooLargeError`` 区分：本类是 token 数超限，后者是请求体字节数
      超限（HTTP 413），恢复路径不同。

    .. seealso::

        :class:`flowing.errors.RequestTooLargeError`
        :class:`flowing.errors.ProviderError`
        :class:`flowing.context.Context`
    """


class RequestTooLargeError(ProviderError):
    """请求体字节超限（HTTP 413，不可重试，经 ``on_provider_error``）。

    .. rubric:: 功能介绍

    请求体字节数超限（典型：多模态附件过大）时由 adapter 抛出，与
    ``ContextLengthError`` 的 token 数超限相区分。两者恢复路径不同：token
    超限靠压缩会话历史；字节超限压缩历史无用，必须剥离媒体附件后重发。注意
    部分 Provider（如 Vertex）会把 prompt 过长也返回 413，adapter 归类时以消息
    内容辅助判别——归类依据是语义而非状态码。

    .. rubric:: 行为要点

    - 字段继承 ``ProviderError``。
    - 经 ``on_provider_error`` 分发：handler 可据此剥离媒体附件后重发（置
      ``can_continue=True``），也可保持 ``can_continue=False`` 让回合终止。
    - 不可重试（原样重发必然重现）；分类属机制，恢复动作属策略。

    .. seealso::

        :class:`flowing.errors.ContextLengthError`
        :class:`flowing.errors.ProviderError`
    """


class RateLimitedError(ProviderError):
    """Provider 限流（HTTP 429 瞬时限流，可重试类）。

    .. rubric:: 功能介绍

    临时限流：等一等能成功。是否重试、退避多久由策略层决定（``use_retry()``
    或自定义 ``on_provider_error`` handler）；框架核心只负责分类。429 通常携带
    ``Retry-After`` 之类的服务端建议，结构化进 ``retry_after`` 字段，策略层不必
    解析各家 SDK 的响应头格式。

    .. rubric:: 行为要点

    - 字段继承 ``ProviderError``；``retry_after`` 为服务端建议的重试等待秒数，
      ``None`` 表示服务端未建议。
    - 经 ``on_provider_error`` 分发；可重试（次数与退避由策略决定，框架无默认
      值）。
    - 未启用任何重试 handler 时，回合直接以 error 结局终止——框架不提供默认
      容错。
    - 与 ``QuotaExhaustedError`` 区分：同为 429，本类是「太快了」，后者是「没
      额度了」（不可重试）。

    .. seealso::

        :class:`flowing.errors.QuotaExhaustedError`
        :class:`flowing.errors.ProviderError`
        :mod:`flowing.composables.retry`
    """


class QuotaExhaustedError(ProviderError):
    """Provider 配额 / 余额耗尽（HTTP 429 或专用错误码，不可重试类）。

    .. rubric:: 功能介绍

    与 ``RateLimitedError`` 同为 429 但语义相反：「你太快了」（等一等能成功）
    与「你没额度了」（等多久都不会成功，需要人介入——充值、换 key、换
    provider）。重试配额耗尽是纯浪费：每次重试必然失败。刻意不做
    ``RateLimitedError`` 的子类：继承会让 ``except RateLimitedError`` 与
    isinstance 重试判定误捕本类——类型树本身即是重试策略的判定表。

    .. rubric:: 行为要点

    - 字段继承 ``ProviderError``。
    - 经 ``on_provider_error`` 分发；不可重试。
    - 框架不检测余额数值、不触发充值流程：本类只是分类信号，恢复动作（换
      provider / 提示用户）属策略层。

    .. seealso::

        :class:`flowing.errors.RateLimitedError`
        :class:`flowing.errors.ProviderError`
    """


class ServerError(ProviderError):
    """Provider 服务端错误（HTTP 5xx，可重试类）。

    .. rubric:: 功能介绍

    服务端临时故障（5xx 通常自愈），分类为可重试；重试与否由策略层决定。与
    4xx 请求错误（请求本身有问题，重试无意义）分开两类，使策略层可以按类判断。

    .. rubric:: 行为要点

    - 字段继承 ``ProviderError``；``status_code`` 记录具体的 5xx 码，
      ``retry_after`` 可能随 503/529 过载响应携带。
    - 经 ``on_provider_error`` 分发；可重试（策略决定）。

    .. seealso::

        :class:`flowing.errors.InvalidRequestError`
        :class:`flowing.errors.ProviderError`
    """


class NetworkError(ProviderError):
    """网络层错误（连接失败 / DNS / TLS 等，可重试类）。

    .. rubric:: 功能介绍

    请求未到达 Provider 或响应中断的网络抖动。adapter 负责把 httpx / aiohttp
    等库的连接异常归类为本类。网络错误与 HTTP 层错误（有响应状态码）的可恢复
    策略不同（通常立即或短退避重试），独立分类避免策略层做字符串匹配。

    .. rubric:: 行为要点

    - 字段继承 ``ProviderError`` （``status_code`` 为 ``None``——非 HTTP 响应
      错误）。
    - 经 ``on_provider_error`` 分发；可重试（策略决定）。

    .. seealso::

        :class:`flowing.errors.ProviderTimeoutError`
        :class:`flowing.errors.ProviderError`
    """


class ProviderTimeoutError(ProviderError):
    """Provider 调用超时（可重试类）。

    .. rubric:: 功能介绍

    请求超过 adapter 的超时预算时抛出。命名自带归属（Provider 层超时），刻意
    不继承内置 ``TimeoutError``——``except TimeoutError`` （内置）不会捕获本类。

    .. rubric:: 行为要点

    - 字段继承 ``ProviderError``。
    - 经 ``on_provider_error`` 分发；可重试（策略决定）。
    - 不继承内置 ``TimeoutError`` （``isinstance(e, builtins.TimeoutError)``
      为假）。

    .. seealso::

        :class:`flowing.errors.NetworkError`
        :class:`flowing.errors.ProviderError`
    """


class AuthenticationError(ProviderError):
    """凭证错误（HTTP 401 / 403，不可重试）。

    .. rubric:: 功能介绍

    API key 无效、过期或权限不足。凭证问题不自愈，分类为不可重试——默认重试
    策略（``use_retry()``）对本类直接放行（不写 ``can_continue``）。凭证修复
    需要人工介入（换 key、改配置）；应用层可自定义 handler 做「换凭证后重试」，
    不可重试只是分类事实的默认值，策略仍可覆盖。

    .. rubric:: 行为要点

    - 字段继承 ``ProviderError``。
    - 经 ``on_provider_error`` 分发；默认不可重试（策略可覆盖）。
    - 凭证内容本身不进异常字段与消息文本（API key 不进消息、不落盘）。

    .. seealso::

        :class:`flowing.errors.MissingEnvironmentVariableError`
            凭证引用的环境变量缺失（加载期）的对偶异常。
        :class:`flowing.errors.ProviderError`
    """


class InvalidRequestError(ProviderError):
    """请求本身非法（HTTP 400，不可重试）。

    .. rubric:: 功能介绍

    请求参数违反 API 约束（非法字段、不支持的参数组合等）时抛出。重试必然重现
    同样失败，分类为不可重试。模型能力不兼容（如无视觉能力的模型收到图片）在
    API 调用时也可能以此类报错——框架核心不做能力预校验。

    .. rubric:: 行为要点

    - 字段继承 ``ProviderError``。
    - 经 ``on_provider_error`` 分发；默认不可重试。

    .. seealso::

        :class:`flowing.errors.ContextLengthError`
        :class:`flowing.errors.ProviderError`
    """


class ContentPolicyError(ProviderError):
    """内容安全策略拒绝（不可重试）。

    .. rubric:: 功能介绍

    Provider 侧内容审查（输入或输出触发安全策略）拒绝生成时抛出。内容拒绝与
    限流 / 故障语义完全不同：不是暂时不可用，而是「这个内容不行」；应用层通常
    需要改写输入或告知用户，独立分类便于策略层区分处理。

    .. rubric:: 行为要点

    - 字段继承 ``ProviderError``。
    - 经 ``on_provider_error`` 分发；默认不可重试（改写输入是应用层决策，非
      框架重试范畴）。

    .. seealso::

        :class:`flowing.errors.ProviderError`
    """


class MissingEnvironmentVariableError(ProviderError):
    """要求的环境变量在加载期不存在（由需要严格校验的加载方抛出）。

    .. rubric:: 功能介绍

    表示「配置引用了必须存在的环境变量，而它没有设置」的加载期错误。
    **内置 providers.yaml 加载器不抛本异常**——它对 ``{{env.VAR}}`` 做
    纯字符串替换时，变量缺失替换为空串并 ``warnings.warn`` 告警、加载
    不中断（多条目配置只用一个时，其余条目的环境变量不必齐备）；缺失
    凭证的实际后果（如 401）在该条目首次调用时经 ``on_provider_error``
    暴露。本类保留为公共错误类型：应用层自写的严格配置校验需要
    fail-fast 语义时可自行抛出。非 ``{{env.`` 前缀的 ``{{`` 保持原样
    （不报错不替换），不触发本异常。

    .. rubric:: 使用示例

    .. code-block:: python

        # 应用层自己的严格校验（内置加载器不走这条路）
        if required_var not in os.environ:
            raise MissingEnvironmentVariableError(required_var, entry)

    .. rubric:: 行为要点

    - 字段 ``var_name`` （缺失的环境变量名，不含 ``{{env.`` 前缀）与 ``entry``
      （引用该变量的 Provider 条目名）。
    - 加载期异常：与调用期异常不在同一时序，不经 ``on_provider_error``。
    - 部署错误：调用方不捕获，修正环境后重新启动。
    - ``ModelConfig`` 字段中的环境变量引用走 Parsable 运行时求值（每次
      ``provider_gen()`` 前），其失败按普通求值异常处理，不属本类。

    .. seealso::

        :func:`flowing.providers.load_provider_candidates`
            内置加载器的宽松口径（空串 + 告警）。
        :class:`flowing.errors.AuthenticationError`
        :class:`flowing.errors.ProviderError`
        :class:`flowing.parsable.Parsable`
    """

    var_name: str
    """缺失的环境变量名（不含 ``{{env.`` 前缀）。"""
    entry: str
    """引用该变量的 Provider 条目名。"""

    def __init__(self, var_name: str, entry: str) -> None:
        """构造异常实例。

        :param var_name: 缺失的环境变量名；与 ``var_name`` 字段一致。
        :param entry: 引用该变量的 Provider 条目名；与 ``entry`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Missing environment variable {var_name!r} referenced by provider entry {entry!r}")
        self.var_name = var_name
        self.entry = entry


class ProviderNameConflictError(ProviderError):
    """Provider adapter 规范名重名注册（未指定 ``override=True``）时抛出。

    .. rubric:: 功能介绍

    ``register_provider()`` 检测到注册表键（adapter 的 ``name`` 类属性）已存在
    且未声明覆盖时抛出。发生在 import 期 / Runtime 初始化的自动发现阶段
    （``FLOWING_PROVIDER_MODULES`` 与 ``flowing_provider_*`` 扫描会 import 第三方
    包），与调用期异常不在同一时序，不经 ``on_provider_error``。两个包撞同名
    adapter 是真实可发生的集成情形，需要可精确 ``except`` 的具名类型。显式
    ``override=True`` 是唯一合法覆盖通道（后 import 者胜出并产生警告）。

    .. rubric:: 行为要点

    - ``name`` 字段为发生冲突的 adapter 规范名（``Provider.name`` 类属性）。
    - 基类通用字段 ``provider`` / ``model`` 无上下文可填（注册期无条目）。
    - import 期错误：调用方不捕获（部署 / 集成错误，改名或显式 override 后重来）。
    - 与 ``ToolNameConflictError`` 的分层：本类管进程级 adapter 注册表（有
      override 通道）；工具注册表在 Runtime 实例内、无 override 通道。
    - 作者笔误（装饰到非 ``Provider`` 子类 / 缺 ``name`` 的对象）不走本类——
      刻意抛内置 ``ValueError`` （编程错误，不该被恢复逻辑捕获）。

    .. seealso::

        :class:`flowing.errors.ProviderError`
        :class:`flowing.errors.ToolNameConflictError`
        :func:`flowing.providers.register_provider`
    """

    name: str
    """发生冲突的 adapter 规范名（``Provider.name`` 类属性）。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 发生冲突的 adapter 规范名；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(
            f"Provider adapter name conflict: {name!r} "
            "(use override=True to replace)"
        )
        self.name = name


# ---------------------------------------------------------------------------
# 依赖类
# ---------------------------------------------------------------------------


class DependencyError(FlowingError):
    """插件依赖图成环时抛出（``Runtime.install()`` 时增量校验）。

    .. rubric:: 功能介绍

    ``Runtime.install()`` 每次安装插件后对当前已装集合的依赖图做增量校验：已装子图
    成环（A 依赖 B、B 依赖 A）即抛出本异常，报错现场即引入环的那次 ``install()``。
    依赖缺失只产生 ``warnings.warn`` 警告、不抛本异常（「声明了依赖但实际
    用不上」是合法形态，``install()`` 可分批）；运行期真用到缺失依赖时由
    ``MissingProvideError`` 兜底。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.install(PluginA())   # A 声明依赖 "b"——缺失 → 仅警告，不抛
        runtime.install(PluginB())   # B 声明依赖 "a"——已装子图成环 → 抛出

    .. rubric:: 行为要点

    - 字段 ``plugin`` （环闭合点所在插件名）与 ``missing`` （构成环回边的依赖名
      列表）。
    - 安装期错误：调用方不捕获。
    - 与 ``MissingProvideError`` 互补：本类是启动期静态依赖校验，后者是运行期
      动态注入兜底。

    .. seealso::

        :meth:`flowing.runtime.Runtime.install`
        :class:`flowing.errors.MissingProvideError`
    """

    plugin: str
    """环闭合点所在插件名（即引入环的那次 ``install()`` 的插件）。"""
    missing: list[str]
    """构成环回边的依赖名列表（即使依赖环闭合的那条依赖）。"""

    def __init__(self, plugin: str, missing: list[str]) -> None:
        """构造异常实例。

        :param plugin: 环闭合点所在插件名（即引入环的那次 ``install()`` 的插件）；
          与 ``plugin`` 字段一致。
        :param missing: 构成环回边的依赖名列表（即使依赖环闭合的那条依赖）；
          与 ``missing`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Plugin {plugin!r} has unresolved dependencies: {missing}")
        self.plugin = plugin
        self.missing = missing


# ---------------------------------------------------------------------------
# 通信类（CommPlugin）
# ---------------------------------------------------------------------------


class CommError(FlowingError):
    """通信扩展（CommPlugin 总线）异常的分类中间层。

    .. rubric:: 功能介绍

    单进程内通信总线（端点注册、信号投递、请求-回复）相关异常的公共基类。通信
    是内置扩展（必须显式 ``runtime.install(CommPlugin())`` 才存在），但其异常类型
    属于框架统一层次——未启用扩展时这些类型只是不被实例化。

    .. rubric:: 行为要点

    - 本类是分类中间层：框架实际抛出的是其具体子类；按类别捕获用
      ``except CommError``。
    - 总线是 fire-and-forget 语义的基础设施：``publish`` 对订阅者异常静默容错，
      不产出本层异常。
    - 通信消息永不进 LLM context；通信层错误不转为 EVENT 消息。

    .. seealso::

        :class:`flowing.plugins.comm.Communication`
        :class:`flowing.plugins.comm.CommPlugin`
    """


class DuplicateEndpointError(CommError):
    """通信端点 ID 重复注册时抛出。

    .. rubric:: 功能介绍

    ``Communication.register_endpoint(endpoint_id, handler)`` 检测到端点 ID 已
    存在时抛出。端点 ID 是路由唯一键：静默覆盖会让旧 handler 的信号凭空消失。
    注册不幂等——重复注册同一 ID 即抛错。注销不存在的端点走自然 ``KeyError``
    （不特殊处理、不包装）。

    .. rubric:: 行为要点

    - ``endpoint_id`` 字段为发生冲突的端点 ID。
    - 编程错误：调用方不捕获。

    .. seealso::

        :meth:`flowing.plugins.comm.Communication.register_endpoint`
        :class:`flowing.errors.CommError`
    """

    endpoint_id: str
    """发生冲突的端点 ID。"""

    def __init__(self, endpoint_id: str) -> None:
        """构造异常实例。

        :param endpoint_id: 发生冲突的端点 ID；与 ``endpoint_id`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Duplicate endpoint id: {endpoint_id!r}")
        self.endpoint_id = endpoint_id


class SignalDeliveryError(CommError):
    """信号目标端点不存在时抛出。

    .. rubric:: 功能介绍

    ``Communication.send()`` / ``request()`` 按目标端点 ID 路由失败时抛出——
    点对点信号的投递失败是调用方可修正的错误（端点未注册、ID 拼错）。``send``
    是「立即返回」语义，路由失败属于同步可判定的错误，发送阶段当场抛出。与
    ``publish`` 的广播容错（单订阅者异常静默忽略）形成有意的语义对比。

    .. rubric:: 行为要点

    - 字段 ``target`` （未命中的目标端点 ID）与 ``signal_type`` （投递失败的信号
      类型，即信号消息的 ``type`` 字段）。
    - 调用方可捕获（如降级为日志）；是否重试由应用层决定（框架不重试）。

    .. seealso::

        :meth:`flowing.plugins.comm.Communication.send`
        :class:`flowing.errors.SignalTimeoutError`
        :class:`flowing.errors.CommError`
    """

    target: str
    """未命中的目标端点 ID。"""
    signal_type: str
    """投递失败的信号类型（即信号消息的 ``type`` 字段）。"""

    def __init__(self, target: str, signal_type: str) -> None:
        """构造异常实例。

        :param target: 未命中的目标端点 ID；与 ``target`` 字段一致。
        :param signal_type: 投递失败的信号类型；与 ``signal_type`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Signal delivery failed: target endpoint {target!r} not found (type={signal_type!r})")
        self.target = target
        self.signal_type = signal_type


class SignalTimeoutError(CommError):
    """``request()`` 等待回复超时时抛出。

    .. rubric:: 功能介绍

    请求-回复模式下，``correlation_id`` 匹配的回复在 ``timeout`` 秒内未到达即
    抛出，pending future 随之清理。审批等交互场景的「超时」是正常业务分支
    （典型用法：审批超时 → ``raise Intercepted("审批超时")`` 阻断工具调用），
    需要类型化异常供 handler 捕获，而非裸 ``asyncio.TimeoutError``。

    .. rubric:: 使用示例

    .. code-block:: python

        try:
            result = await self.comm_handler.request(
                target="ui-main", type="permission_request",
                payload={"tool_name": tool_call.name}, timeout=120.0,
            )
        except SignalTimeoutError:
            raise Intercepted("审批超时")

    .. rubric:: 行为要点

    - 字段 ``target`` （请求的目标端点 ID）与 ``timeout`` （实际使用的超时秒数）。
    - 调用方通常捕获（业务分支）；是否重试由应用层决定。
    - ``CommHandle.destroy()`` 后 pending ``request()`` 的 await 收到的是
      ``asyncio.CancelledError`` （内置），不是本类。

    .. seealso::

        :meth:`flowing.plugins.comm.CommHandle.request`
        :class:`flowing.errors.Intercepted`
        :class:`flowing.errors.CommError`
    """

    target: str
    """请求的目标端点 ID。"""
    timeout: float
    """实际使用的超时秒数。"""

    def __init__(self, target: str, timeout: float) -> None:
        """构造异常实例。

        :param target: 请求的目标端点 ID；与 ``target`` 字段一致。
        :param timeout: 实际使用的超时秒数；与 ``timeout`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Signal request to {target!r} timed out after {timeout}s")
        self.target = target
        self.timeout = timeout


# ---------------------------------------------------------------------------
# 文件格式类
# ---------------------------------------------------------------------------


class FormatError(FlowingError):
    """声明式文件格式与求值异常的分类中间层。

    .. rubric:: 功能介绍

    ``.fya`` 声明解析、创建管线 PENDING 检查、Parsable 求值上下文、保留属性名
    等「声明与格式」层异常的公共基类。声明式与命令式两种 Agent 形式生成完全
    相同的 Python 类模型，格式错误必须在类生成 / 实例化早期暴露，归为一层便于
    工具（如 ``flowing compile``）统一报告。

    .. rubric:: 行为要点

    - 本类是分类中间层：框架实际抛出的是其具体子类；按类别捕获用
      ``except FormatError``。
    - 声明错误：调用方不捕获（fail-fast）。

    .. seealso::

        :class:`flowing.parsable.Parsable`
        :meth:`flowing.runtime.Runtime.create_agent`
    """


class MissingFieldError(FormatError):
    """创建管线 PENDING 检查点仍有未赋值字段时抛出。

    .. rubric:: 功能介绍

    ``.fya`` 中以 ``_`` 标记的延迟定义字段解析为 ``PENDING`` 哨兵；``setup()``
    结束后、``after_create`` 钩子前的 PENDING 检查点发现仍有 ``PENDING`` 字段
    （典型：唯一必填的类属性 ``system_prompt`` 未赋值）时抛出。PENDING 是「延迟
    定义承诺」——声明层允许先占位，但管线必须在固定检查点兑现承诺；缺失时
    fail-fast 优于带着 ``None`` 进入 Turn 循环。恢复管线同样检查。

    .. rubric:: 行为要点

    - 字段 ``field`` （检查点处仍为 ``PENDING`` 的字段名）与 ``agent_type``
      （所属 Agent 的类型名）。
    - 声明 / ``setup()`` 实现错误：调用方不捕获。
    - ``PENDING`` 与 ``_UNSET`` 语义不同：后者是参数默认值判定哨兵，不参与
      本检查。

    .. seealso::

        :data:`flowing.parsable.PENDING`
        :meth:`flowing.runtime.Runtime.create_agent`
        :class:`flowing.errors.FormatError`
    """

    field: str
    """检查点处仍为 ``PENDING`` 的字段名。"""
    agent_type: str
    """所属 Agent 的类型名。"""

    def __init__(self, field: str, agent_type: str) -> None:
        """构造异常实例。

        :param field: 检查点处仍为 ``PENDING`` 的字段名；与 ``field`` 字段一致。
        :param agent_type: 所属 Agent 的类型名；与 ``agent_type`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Required field {field!r} of agent type {agent_type!r} is still PENDING")
        self.field = field
        self.agent_type = agent_type


class MissingContextError(FormatError):
    """未绑定实例的 Parsable 被强制求值时抛出。

    .. rubric:: 功能介绍

    类级别（未绑定实例）或手动创建未绑定 Agent 实例的 ``Parsable`` 被强制
    求值——调用 ``resolve()`` 而不传上下文，或读取 ``resolved`` 属性——时抛出：
    求值需要实例属性 / env / config 渲染上下文，无绑定则上下文缺失。求值面内
    （框架在固定时机对绑定实例求值）自动进行；求值面外（用户手动求值类属性）
    必须显式 ``resolve(context)`` 或先绑定。隐式返回原始模板会让 prompt 里
    出现未渲染的 ``{{ }}``，比报错更难排查。

    .. rubric:: 使用示例

    .. code-block:: python

        class MyAgent(Agent):
            system_prompt = Parsable("你好，{{ user_name }}")

        MyAgent.system_prompt.resolved                    # 未绑定 → 抛出
        MyAgent.system_prompt.resolve({"user_name": "甲"})   # 显式上下文 → 正常

    .. rubric:: 行为要点

    - 无结构化字段；消息为固定的英文提示。
    - 用法错误：改用显式 ``resolve(context)``；调用方不捕获。
    - ``str()`` 只展示模板源、不求值、不抛本异常（未绑定也可以 ``str()``）；
      ``repr()`` 始终显示原始模板，同样不触发求值。
    - ``FILE_REF`` 形式在未绑定时即使传了 ``context`` 映射也会抛出（路径
      解析需要绑定实例的 runtime）。

    .. seealso::

        :class:`flowing.parsable.Parsable`
        :class:`flowing.errors.FormatError`
    """

    def __init__(self) -> None:
        """构造异常实例（无参数，无结构化字段）。

        消息为固定的英文提示。
        """
        # 无字段叶子：固定英文提示消息
        super().__init__(
            "Parsable is not bound to an instance: call resolve(context) "
            "with an explicit context instead"
        )


class ReservedAttributeError(FormatError):
    """Agent 实例属性命名为框架保留名时抛出。

    .. rubric:: 功能介绍

    Parsable 渲染上下文由框架注入四个保留名：``env`` （绑定 ``os.environ``）、
    ``config`` （Runtime 配置）、``agent`` 与 ``self`` （实例自身入口）。Agent
    实例属性占用这些名字会覆盖注入值，框架在求值前检测到即抛出——保留名冲突是
    静默错误的高发源，显式保留名单加检测比「合并时谁覆盖谁」的隐式规则更清晰。

    .. rubric:: 行为要点

    - ``name`` 字段为被占用的保留属性名（``"env"`` / ``"config"`` / ``"agent"``
      / ``"self"`` 之一）。
    - 声明错误：调用方不捕获（fail-fast）。

    .. seealso::

        :class:`flowing.parsable.Parsable`
        :class:`flowing.errors.FormatError`
    """

    name: str
    """被占用的框架保留属性名（``"env"`` / ``"config"`` / ``"agent"`` / ``"self"`` 之一）。"""

    def __init__(self, name: str) -> None:
        """构造异常实例。

        :param name: 被占用的框架保留属性名；与 ``name`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Reserved attribute name occupied: {name!r}")
        self.name = name


class NameMismatchError(FormatError):
    """声明的 ``name`` 与按规则推断的名字不一致时抛出。

    .. rubric:: 功能介绍

    ``name`` 不是框架机制字段：Agent 的身份名一律由路径 / 注册名 / 类名推断
    （见 ``Agent.class_name`` 的推断规则），但 ``.fya`` 与手写子类中不禁止用户
    写 ``name``——写了就作为一致性断言校验，与推断值不符即抛出本异常，消息同时
    给出声明值、推断值与来源位置。名实分离（文件名是 ``foo.fya``、内部自称
    ``payment``）是排查困惑的高发源。

    .. rubric:: 行为要点

    - 字段 ``declared`` （用户声明的 ``name`` 值）、``inferred`` （按路径 / 类名
      规则推断出的名字）、``source`` （冲突来源：``.fya`` 的路径或手写子类的
      限定类名）。
    - 校验是相等断言而非命名来源：声明与推断一致时无任何效果（名字仍然来自
      推断）。
    - 声明错误：调用方不捕获（fail-fast）。

    .. seealso::

        :class:`flowing.agent.Agent`
        :class:`flowing.errors.FormatError`
    """

    declared: str
    """用户声明的 ``name`` 值。"""

    inferred: str
    """按路径 / 类名规则推断出的名字。"""

    source: str
    """冲突来源（``.fya`` 的路径或手写子类的限定类名）。"""

    def __init__(self, declared: str, inferred: str, source: str) -> None:
        """构造异常实例。

        :param declared: 用户声明的 ``name`` 值；与 ``declared`` 字段一致。
        :param inferred: 按路径 / 类名规则推断出的名字；与 ``inferred`` 字段一致。
        :param source: 冲突来源（``.fya`` 的路径或手写子类的限定类名）；与
          ``source`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Declared name {declared!r} does not match inferred name {inferred!r} (source: {source})")
        self.declared = declared
        self.inferred = inferred
        self.source = source


# ---------------------------------------------------------------------------
# 编译类（.fya 显式编译，构建期）
# ---------------------------------------------------------------------------


class CompileError(FlowingError):
    """``.fya`` 显式编译失败的分类中间层。

    .. rubric:: 功能介绍

    ``flowing compile`` / :mod:`flowing.compiler` 构建期异常的归属层，只承载
    归属、不附加行为。当前子类：``ArtifactModifiedError``。

    .. rubric:: 行为要点

    - 本类是分类中间层：框架实际抛出的是其具体子类；按类别捕获用
      ``except CompileError``。

    .. seealso::

        :class:`flowing.errors.ArtifactModifiedError`
        :mod:`flowing.compiler`
    """


class ArtifactModifiedError(CompileError):
    """编译产物被外部修改——``py_hash`` 不匹配，报错中止、不静默覆盖。

    .. rubric:: 功能介绍

    显式编译的防覆盖闸：产物同目录 ``.flowing.meta.yaml`` 中记录的 ``py_hash``
    （AST 口径，格式化改动不触发）与现有同目录 ``.py`` 的实际 AST hash 不一致
    → 该产物被人手工改过，编译器报错中止而非覆盖。已产出的其他文件不回滚；
    解决冲突后重新编译可继续。

    .. rubric:: 行为要点

    - ``path`` 字段为冲突的编译产物路径。
    - 调用方不捕获；``flowing compile`` 把它映射为运行时错误退出码。

    .. seealso::

        :class:`flowing.errors.CompileError`
        :mod:`flowing.compiler`
    """

    def __init__(self, path: "Path") -> None:
        """构造异常实例。

        :param path: 冲突的编译产物路径；与 ``path`` 字段一致。
        """
        self.path = path
        super().__init__(f"compiled artifact was modified externally; refusing to overwrite: {path}")


# ---------------------------------------------------------------------------
# 持久化类（persistence 为内部模块，不专设中间层，直挂根）
# ---------------------------------------------------------------------------


class FormatVersionError(FlowingError):
    """jsonl 持久化文件的格式版本高于当前框架支持时抛出。

    .. rubric:: 功能介绍

    ``RecordStore.replay`` 判读首行 ``{"type": "meta", "format_version"}`` 时，
    文件声明的版本高于当前框架支持的 :data:`flowing.persistence.FORMAT_VERSION`
    即抛出（文件比框架新，静默读是数据风险——典型场景是用旧框架读新文件的部署
    降级事故）。版本不高于当前支持的存量文件（含无版本首行、按版本 0 处理的
    文件）在读取时迁移到当前格式，不抛本异常。

    .. rubric:: 行为要点

    - 字段 ``path`` （文件路径）、``found`` （文件声明的版本）、``supported``
      （框架当前支持版本）。
    - 部署错误：调用方不捕获（fail-fast）。
    - 与 ``CorruptionError`` （数据损坏）精确区分。

    .. seealso::

        :mod:`flowing.persistence`
        :class:`flowing.errors.CorruptionError`
    """

    path: Path
    """版本不受支持的持久化文件路径。"""
    found: int
    """文件首行声明的格式版本号。"""
    supported: int
    """框架当前支持的格式版本号（``FORMAT_VERSION``）。"""

    def __init__(self, path: Path, found: int, supported: int) -> None:
        """构造异常实例。

        :param path: 版本不受支持的持久化文件路径；与 ``path`` 字段一致。
        :param found: 文件声明的格式版本号；与 ``found`` 字段一致。
        :param supported: 框架当前支持的格式版本号；与 ``supported`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(
            f"Unsupported format version {found} in {path} "
            f"(this framework supports up to {supported})"
        )
        self.path = path
        self.found = found
        self.supported = supported


class CorruptionError(FlowingError):
    """jsonl 持久化文件中间行损坏时抛出。

    .. rubric:: 功能介绍

    ``RecordStore.replay`` 按行解析时发现中间行（非撕裂末行）JSON 损坏即
    抛出。撕裂末行（崩溃半截写产物，无换行结尾）是合法容忍路径，截断丢弃、
    不抛本异常；中间行损坏意味着已提交数据受损，属事故而非正常窗口——「中间行
    损坏报警不容忍」是持久化层的既定约定。

    .. rubric:: 行为要点

    - 字段 ``path`` （损坏行所在文件路径）与 ``lineno`` （损坏行的 1 基行号）。
    - 数据事故：调用方不捕获（需人工介入）；抛出前框架记录 ``path:lineno``
      的错误日志。
    - 与 ``FormatVersionError`` （版本问题）精确区分。

    .. seealso::

        :mod:`flowing.persistence`
        :class:`flowing.errors.FormatVersionError`
    """

    path: Path
    """损坏行所在的持久化文件路径。"""
    lineno: int
    """损坏行的 1 基行号。"""

    def __init__(self, path: Path, lineno: int) -> None:
        """构造异常实例。

        :param path: 损坏行所在的持久化文件路径；与 ``path`` 字段一致。
        :param lineno: 损坏行的 1 基行号；与 ``lineno`` 字段一致。
        """
        # 消息为自然语言关键提示，结构化字段为权威
        super().__init__(f"Corrupted record line at {path}:{lineno}")
        self.path = path
        self.lineno = lineno


# ---------------------------------------------------------------------------
# 信号类
# ---------------------------------------------------------------------------


class Intercepted(Exception):
    """钩子 handler 的有意硬阻断信号（刻意不继承 ``FlowingError``）。

    .. rubric:: 功能介绍

    handler 的三种合法出口之一（另两种：返回 value、设置 ``shortcut`` 字段
    短路）。``raise Intercepted`` 表示「有意的阻止」——审批拒绝、安全阻断、权限
    检查等正常业务分支，而不是意外错误。dispatch 层捕获本信号后原样重抛：处理
    链停止、后续 handler 不执行、对应的 ``after_`` 钩子不触发（整个操作标记为
    无效）；不当作错误处理（INFO 级日志，非 ERROR）。``tool_call()`` 路径把本
    信号转换为 ``ToolResult.blocked(reason)``——LLM 收到
    ``tool_status="blocked"``、内容为 ``[TextBlock(reason)]`` 的 TOOL 消息。

    与 ``shortcut`` 字段的边界：shortcut 是协商式替代（``after_`` 钩子照常
    触发），本信号是硬阻断（``after_`` 钩子不触发）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.errors import Intercepted

        async def approval(agent, tool_call):
            if not (await approval_service.approve(tool_call)):
                raise Intercepted("用户拒绝", payload={"tool": tool_call.name})
            return tool_call

        async def setup(self):
            self.hooks.before_tool_call(approval, by="approval")

    .. rubric:: 行为要点

    - ``reason`` 为阻断原因（面向人读）；``tool_call()`` 路径下进入 LLM 可见的
      blocked 结果。
    - ``payload`` 为结构化附加信息，框架不解释其内容，供审计 / 日志消费；默认
      ``None``。
    - 刻意不继承 ``FlowingError``：泛化的 ``except FlowingError`` 不会误吞阻断
      信号。
    - 本信号不触发任何错误钩子，也不进入 ``on_provider_error`` 决策树；与重试
      无关。
    - 在「没有对应工具调用」的钩子点（如 ``before_turn``）抛出时，由该操作的
      发起路径决定如何呈现，框架不保证统一的 LLM 可见形式。

    .. seealso::

        :class:`flowing.hooks.HookList`
            处理链的 dispatch 语义（改写链 / shortcut / 本信号重抛）。
        :class:`flowing.tool.ToolResult`
            ``ToolResult.blocked(...)`` 的载体。
    """

    reason: str
    """阻断原因（面向人读）；``tool_call()`` 路径下进入 LLM 可见的 blocked 结果。"""
    payload: Any
    """结构化附加信息（如 ``{"tool": ...}``），供审计 / 日志消费；框架不解释其内容。默认 ``None``。"""

    def __init__(self, reason: str, payload: Any = None) -> None:
        """构造阻断信号实例。

        :param reason: 阻断原因（面向人读）；同时作为 ``str(exception)`` 的内容。
        :param payload: 结构化附加信息，框架不解释其内容；默认 ``None``。
        """
        super().__init__(reason)
        self.reason = reason
        self.payload = payload
