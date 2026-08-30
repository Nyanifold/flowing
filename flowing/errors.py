"""Flowing 统一异常层次（``flowing.errors``）。

.. rubric:: 功能介绍

本模块定义 Flowing 框架的**全部具名异常类型**，是框架核心层（非扩展、非应用层）
的公共契约：``FlowingError`` 为统一根，向下按职责分为配置、注入、钩子、工具、
子 Agent 与资源、Provider、依赖、通信、文件格式、编译十个类别（另有
``EntryNameConflictError`` 跨正交直接挂根；``StateKeyError`` 已退役——
D6 删拦截 + D8 读回 KeyError/AttributeError 后无触发点）；外加一个刻意游离于
普通错误语义之外的信号类——``Intercepted``（钩子 handler 的有意硬阻断）。
（M-07 裁决：原 ``TurnAborted`` 内部信号类已删除——终止决策点与收尾点同在
``_run_turn`` 一个函数内，``break`` + ``finally`` 即可表达，无需异常载体。）

豁免声明（P3-14 裁决）：**import 期作者笔误刻意用内置 ``ValueError``，不入
具名层次**——如 ``register_provider`` 装饰到非 ``Provider`` 子类 / 缺非空
``name`` 类属性的对象。这类错误是装饰器用错对象（编程错误），不该被任何
恢复逻辑捕获；可恢复的冲突情形另有具名类型（``ProviderNameConflictError``）。

异常层次树（定稿）::

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
        │   ├── ToolNotFoundError
        │   ├── ToolNameConflictError
        │   ├── UnknownToolError
        │   ├── AmbiguousToolError
        │   ├── AmbiguousMcpSourceError
        │   └── MissingMcpSourceError
        ├── ResourceError                # Resource 注册与访问
        │   ├── ResourceNameConflictError
        │   └── ResourceNotFoundError
        ├── ProviderError                # Provider 加载与调用（核心机制，不可替换）
        │   ├── ContextLengthError       # 不可重试；不经 on_provider_error，直接上抛
        │   ├── RequestTooLargeError     # 不可重试（413 字节超限；M-06 新增）
        │   ├── RateLimitedError         # 可重试类（429 瞬时限流）
        │   ├── QuotaExhaustedError      # 不可重试（429 配额耗尽；M-06 新增）
        │   ├── ServerError              # 可重试类（5xx）
        │   ├── NetworkError             # 可重试类
        │   ├── ProviderTimeoutError     # 可重试类（原 TimeoutError，M-02 裁决改名）
        │   ├── AuthenticationError      # 不可重试（401/403）
        │   ├── InvalidRequestError      # 不可重试（400）
        │   ├── ContentPolicyError       # 不可重试
        │   ├── MissingEnvironmentVariableError  # 条目加载时 {{env.X}} 缺失
        │   └── ProviderNameConflictError  # adapter 规范名重名注册（import 期，P3-14）
        ├── DependencyError              # 插件依赖校验（mount 单点）
        ├── CommError                    # 通信扩展（CommPlugin 总线）
        │   ├── DuplicateEndpointError
        │   ├── SignalDeliveryError
        │   └── SignalTimeoutError
        ├── EntryNameConflictError       # Agent 绑定层同 alias 冲突（直接挂根）
        ├── FormatVersionError           # jsonl 持久化文件格式版本不受支持（直接挂根，X6 澄清新增）
        ├── CorruptionError              # jsonl 持久化文件中间行损坏（直接挂根，X7 澄清新增）
        ├── FormatError                  # 声明式文件格式 / Parsable 求值 / 保留属性
        │   ├── MissingFieldError
        │   ├── MissingContextError
        │   ├── NameMismatchError        # 声明 name 与推断名不符（一致性断言）
        │   └── ReservedAttributeError
        └── CompileError                 # .fya 显式编译（flowing.compiler 构建期）
            └── ArtifactModifiedError    # 产物 py_hash 不匹配，报错中止不覆盖

.. rubric:: 设计动机

- **统一根 + 分类中间层**：应用层与 Composable 可以按类别（``except ProviderError``）
  或按具体类型（``except RateLimitedError``）捕获；分类层只承载归属，不附加行为。
  每个错误类型有唯一归属分类，正文中散见的错误引用都能索引到本模块。
- **机制 vs 策略**：框架核心只定义「错误类型是什么、在哪抛出、谁能捕获」（机制）；
  「该不该重试、重试几次、审批什么」（策略）由扩展 / Composable / 应用层决定。
  十个 Provider 调用期错误类型是 Provider adapter 互操作标准，**不允许替代**；
  重试策略不在核心，由可选的 ``use_retry()`` Composable 提供。
- **``Intercepted`` 刻意不继承 ``FlowingError``**：它表达「有意的阻止」（审批拒绝、
  安全阻断、权限检查），是正常业务流而非意外错误；dispatch 对它 catch 后**重抛**，
  不当作错误处理。若挂在 ``FlowingError`` 下，泛化的 ``except FlowingError`` 会
  误吞阻断信号，因此保持为 ``Exception`` 的直接子类。
- **已废弃类型不进入本模块**：``InjectionError``（改名 ``MissingProvideError``）、
  ``QueryErrorAction``（被 ``ProviderErrorContext.can_continue`` 取代）、
  ``MissingPluginError``（被「依赖声明 + mount 校验」体系覆盖）、
  ``InterceptResult``（v1 返回值包装）。错误钩子仅 ``on_provider_error``；
  ``on_tool_error`` / ``on_subagent_error`` / ``on_error`` / ``before_error``
  均不存在——工具业务错误是 ``ToolResult(status="error")`` 正常产物，工具代码
  崩溃与子 Agent 调用异常**直接上抛**，无错误钩子兜底。

.. rubric:: 错误处理决策树（逻辑 Turn 内）

Turn 是逻辑执行阶段（载体为 ``flowing.agent.TurnContext`` 执行期临时对象），
同时是故障边界。决策树如下::

    provider_gen() 内 Provider 调用抛异常
    ├── ContextLengthError
    │     → 不经 on_provider_error，直接上抛出 Turn 循环（收尾钩子照常，见下）
    └── 其它 Exception
          → 构造 ProviderErrorContext(error, provider, model, can_continue=False)
          → dispatch on_provider_error
          ├── handler 写 can_continue=True（内部已 sleep / 改 self.model）
          │     → continue 重试（改模型后重试自动用新模型）
          ├── handler 调 agent.abort_turn()
          │     → continue 后下一次 provider_gen() 开头检测标志 → 回合按取消路径结束
          └── 无 handler / 未写 / False
                → break 终止内层循环（决策点与收尾点同在 ``_run_turn``
                一个函数内）→ 逻辑 Turn 静默终止，Agent 存活

    收尾不变量（所有路径，含异常路径）：
    物质收尾先行（TurnContext 置空 current_turn = None——head 随每条消息
    挂树即时前移，回合末无结算写入；此后钩子抛异常不再
    楔死 Agent）；随后 after_turn 钩子
    照常触发；已产生的消息已逐条持久化（消息级树 append-only）；
    交付段 resolve 全部 waiters 后 Agent 回到空闲等待下一条消息。

    工具 execute() 崩溃 / 子 Agent 调用异常 / 模板渲染异常
    → 直接上抛，无错误钩子（ToolResult(status="error") 是业务错误正常产物，
      LLM 可见，不触发任何错误钩子）。

    钩子 handler 普通异常 → 直接上抛（dispatch 不捕获、不通知、不继续后续
    handler，无兜底钩子）。handler 不 return value 视为错误，dispatch 检测并报告。

    Intercepted → dispatch catch 后原样重抛；tool_call() 路径转换为
    ToolResult.blocked(...)（LLM 可见 TOOL 消息 content=[TextBlock(reason)]、
    tool_status="blocked"），对应 after_ 钩子不触发（整个操作标记无效）。

**三条路径区分**（框架只区分「有意的阻止」与「意外错误」）：

- ``Intercepted``：INFO 级日志；不触发错误钩子；LLM 收到 blocked 结果。
- 普通异常：ERROR 级日志；直接上抛；工具错误经 ``ToolResult`` 让 LLM 感知，
  Provider 错误 LLM 不可见（LLM 不参与基础设施故障诊断）。
- ``shortcut`` 短路（value 上的通用字段）：替代默认路径，``after_`` 钩子照常触发；
  不是异常，与本模块无交集。

.. rubric:: 使用示例

.. code-block:: python

    from flowing.errors import (
        FlowingError, ProviderError, RateLimitedError, AuthenticationError,
        Intercepted, MissingProvideError,
    )

    # 应用层按类别兜底（例如在嵌入场景的 main() 外层）
    try:
        runtime = await flowing.launch("@/")
        await runtime
    except ProviderError as e:
        logger.error("Provider 调用失败: %s", e)

    # use_retry 风格的 on_provider_error handler（策略层，可选、非默认）
    async def retry_handler(agent, ctx):
        if isinstance(ctx.error, AuthenticationError):
            return ctx                      # 凭证错误不重试
        if isinstance(ctx.error, RateLimitedError):
            await asyncio.sleep(ctx.error.retry_after or 1.0)
            ctx.can_continue = True
        return ctx

    # 工具审批 handler：拒绝时硬阻断
    async def approval_handler(agent, tool_call):
        response = await approval_service.request(tool_call)
        if response.action == "deny":
            raise Intercepted("用户拒绝", payload={"tool": tool_call.name})
        return tool_call

对应的 ``.fya`` 声明（工具声明非保留字段 ``requires_approval``，框架不解析，
由审批 handler 自行读取；``$script`` 中注册 handler）::

    # tools/delete-file.tool.fya
    type: script
    callable: ./ops.py::delete_file
    requires_approval: true        # 非保留字段 → tool.requires_approval == True

.. rubric:: 行为规约

- 本模块所有类型**跨版本稳定**（属稳定契约清单）。
- 所有具名字段在构造时必填（除显式标注默认值者），异常消息字符串由框架格式化，
  调用方应读取结构化字段而非解析消息文本。
- 非行为：框架不提供错误→动作映射表、不内置重试次数与退避参数、不把基础设施
  错误转为 EVENT 消息让 LLM 感知、不在 dispatch 层做任何兜底捕获。
- 边缘情况：``ContextLengthError`` 是唯一绕过 ``on_provider_error`` 的调用期异常
  （不可重试是事实而非策略）；``MissingEnvironmentVariableError`` 在 Provider
  条目**加载时**抛出，与调用期异常不在同一时序；内置 ``KeyError`` /
  ``asyncio.CancelledError`` / ``RuntimeError`` 按 Python 语义使用，
  不包装进本层次。

.. rubric:: 测试案例

- 前置：无任何 ``on_provider_error`` handler 的 Agent；操作：Provider 抛
  ``RateLimitedError``；期望：逻辑 Turn 静默终止，Agent 存活可继续消费队列，
  异常不上抛到 Runtime。
- 前置：handler 写 ``can_continue=True``；操作：同上；期望：回合内重试。
- 前置：Provider 抛 ``ContextLengthError``；操作：同上；期望：``on_provider_error``
  未被触发，异常直接上抛出 Turn 循环。
- 前置：``before_tool_call`` handler ``raise Intercepted("用户拒绝")``；操作：
  LLM 发起该工具调用；期望：后续 handler 不执行，``after_tool_call`` 不触发，
  LLM 收到 ``ToolResult.blocked`` 结果。

.. seealso::

    :class:`flowing.agent.TurnContext`
        逻辑 Turn 的执行期临时对象；``aborted`` 标记供收尾钩子区分正常结束与
        异常终止。
    :class:`flowing.hooks.HookRegistry`
        ``on_provider_error`` 钩子点的声明与 dispatch；``Intercepted`` 的重抛规则
        在 dispatch 算法中定义。
    :class:`flowing.tool.ToolResult`
        ``blocked`` / ``error`` 状态的结果载体；工具业务错误是正常产物而非异常。
    :class:`flowing.providers.Provider`
        Provider adapter 的异常抛出契约（十个调用期错误类型为互操作标准）。
    :mod:`flowing.composables.retry`
        可选的 ``use_retry()`` Composable——重试策略的唯一内置（非默认）提供方。
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
# 注：EntryNameConflictError 刻意不在 __all__（py-spec 原样，44 个名字；
# StateKeyError 已退役删除）；仍可经 flowing.errors.EntryNameConflictError
# 显式导入。


class FlowingError(Exception):
    """框架统一异常根。

    .. rubric:: 功能介绍

    Flowing 框架抛出的全部具名异常（``Intercepted`` 除外）的共同基类。
    应用层可用 ``except FlowingError`` 一网打尽框架异常，而不误捕
    Python 内置异常与第三方库异常。

    .. rubric:: 设计动机

    统一根使「框架错误」与「环境错误」在类型层面可分；分类中间层
    （``ProviderError`` 等）挂在它之下，只承载归属、不附加行为。
    ``Intercepted`` 刻意不在此层次内（见模块 docstring）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.errors import FlowingError

        try:
            await agent.query("你好")
        except FlowingError as e:
            logger.error("框架错误: %s", e)

    .. rubric:: 行为规约

    - 不自定义构造签名与字段；子类各自定义结构化字段。
    - 非行为：框架不会把第三方库异常静默包装为 ``FlowingError`` 后重抛；
      Provider adapter 必须显式归类为 ``ProviderError`` 子类后抛出。
    - 测试案例：前置：任一框架具名异常实例 ``e``；操作：
      ``isinstance(e, FlowingError)``；期望：``True``（``Intercepted`` 除外）。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（框架内无捕获点；``except FlowingError`` 兜底属应用层
      用法，时机：未见规约）
    - 实例化方：``flowing.plugins.workflow`` 的 ``resolve_workflow`` 路径
      （时机：Workflow 定义路径缺失或形态不合法，plugins/workflow.pyi:748）；
      ``flowing.plugins.skills`` 技能条目解析路径（时机：按 name 查找失败
      或条目非法，plugins/skills.pyi:533/858）；基类本体主要由各子类承载

    .. seealso::

        :class:`flowing.errors.Intercepted`
            有意硬阻断信号，刻意不继承本类。
    """


# ---------------------------------------------------------------------------
# 配置类
# ---------------------------------------------------------------------------


class ConfigError(FlowingError):
    """配置类异常中间层。

    .. rubric:: 功能介绍

    配置读取时机与配置命名空间注册相关异常的共同基类。启动期 / 声明期错误，
    均 fail fast。

    .. rubric:: 设计动机

    配置错误全部是编程 / 部署错误（不可自愈），归为一类便于启动期统一捕获并
    给出「修正配置后重启」的指引；与运行期错误（Provider 等）在层次上分开。

    .. rubric:: 行为规约

    - 中间层，不直接实例化抛出。
    - 统一五要素：字段由子类定义；抛出时机为配置读取 / 命名空间注册时；
      调用方不 catch（fail fast）；不可重试；属机制（框架强制时机约束与
      命名空间唯一性）。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（框架内无捕获点，时机：未见规约）
    - 实例化方：``无``（中间层，行为规约声明「不直接实例化抛出」；实际
      抛出均为子类 ``ConfigNotReadyError`` / ``ConfigNamespaceConflictError``）

    .. seealso::

        :class:`flowing.errors.ConfigNotReadyError`
        :class:`flowing.errors.ConfigNamespaceConflictError`
        :meth:`flowing.runtime.Runtime.get_config`
    """


class ConfigNotReadyError(ConfigError):
    """在配置未就绪时调用 ``get_config()``。

    .. rubric:: 功能介绍

    模块顶层（``import`` 时、优先级链合并未完成）调用 ``Runtime.get_config()``
    时抛出，强制「配置只在 ``setup()`` 与钩子回调中读取」的时机约束。

    .. rubric:: 设计动机

    配置优先级链（环境变量 > 命令行 > 用户级 > 项目级 > 默认值）在 launch
    过程中才完成浅合并；模块顶层读取会得到半成品值且难以排查，故以异常
    显式禁止，而非返回不确定的值。

    .. rubric:: 使用示例

    .. code-block:: python

        # ✗ 模块顶层——抛出 ConfigNotReadyError
        TIMEOUT = runtime.get_config("agent.timeout")

        # ✓ setup() 中读取
        async def setup(self):
            self.timeout = self.get_config("agent.timeout", 60)

    .. rubric:: 行为规约

    - 五要素：无自定义字段；抛出时机为 ``get_config()`` 调用且配置未就绪；
      调用方不 catch（编程错误，修正调用位置）；不可重试；属机制。
    - 边缘情况：多 Runtime 场景按各自 Runtime 的就绪状态独立判定。
    - 测试案例：前置：launch 完成前；操作：模块顶层调 ``get_config``；期望：
      抛 ``ConfigNotReadyError``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（编程错误，调用方不 catch，fail fast）
    - 实例化方：``flowing.runtime.Runtime.get_config``（时机：配置未就绪
      ——模块顶层 / import 时调用，runtime.pyi:1164）

    .. seealso::

        :meth:`flowing.runtime.Runtime.get_config`
        :class:`flowing.errors.ConfigError`
    """

    def __init__(self) -> None:
        # 无字段叶子：固定英文提示消息（X14 澄清）
        super().__init__(
            "Configuration is not ready: read config only in setup() "
            "or hook callbacks, not at module top level"
        )


class ConfigNamespaceConflictError(ConfigError):
    """多个扩展注册同一配置命名空间。

    .. rubric:: 功能介绍

    ``Runtime.register_config_namespace(name, schema)`` 检测到命名空间已被
    其它扩展注册时抛出。未被任何扩展注册的命名空间静默保留、可自由读取，
    不触发本异常。

    .. rubric:: 设计动机

    命名空间注册是「谁负责校验 / 提供默认值 / 类型转换」的声明，两名扩展
    认领同一命名空间会产生两套冲突的校验规则，必须在注册时 fail fast。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.register_config_namespace("i18n", I18nSchema)
        runtime.register_config_namespace("i18n", OtherSchema)  # 抛出

    .. rubric:: 行为规约

    - 五要素：字段 ``namespace``；抛出时机为重复注册；调用方不 catch
      （安装期编程错误）；不可重试；属机制。
    - 非行为：不做命名空间访问控制——任何代码可读任何命名空间，注册只声明
      校验职责。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（安装期编程错误，调用方不 catch）
    - 实例化方：``flowing.runtime.Runtime.register_config_namespace``
      （时机：命名空间已被其它扩展注册，runtime.pyi:1328）；
      ``flowing.runtime.Runtime.use`` 插件安装路径（时机：插件注册了
      已被占用的配置命名空间，runtime.pyi:703）

    .. seealso::

        :meth:`flowing.runtime.Runtime.register_config_namespace`
        :class:`flowing.errors.ConfigError`
    """

    namespace: str
    """冲突的配置命名空间名。行为边界：仅为诊断信息，框架不做后续仲裁。
    
    .. seealso:: :class:`flowing.errors.ConfigNamespaceConflictError`
    """

    def __init__(self, namespace: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.runtime.Runtime.register_config_namespace`` 的
          raise（时机：命名空间重复注册时，runtime.pyi:1328）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Config namespace conflict: {namespace!r} is already registered")
        self.namespace = namespace


# ---------------------------------------------------------------------------
# 注入类
# ---------------------------------------------------------------------------


class ProvideError(FlowingError):
    """provide/inject 链异常中间层。

    .. rubric:: 功能介绍

    provide-inject 机制相关异常的共同基类。inject 沿 ``_parent_id`` 链上溯
    （Agent 子树 > Workflow > Runtime，Runtime 为链终点），命中即返回。

    .. rubric:: 设计动机

    provide/inject 是框架核心三大基础设施之一；其失败一律视为结构错误
    （声明缺失），与可自愈的运行期错误分类隔离。

    .. rubric:: 行为规约

    - 中间层，不直接实例化抛出。
    - 非行为：inject 不做跨节点类型校验（``InjectionKey[T]`` 的类型信息不跨
      节点传递，key 在 ``_provided`` 中始终是 ``str``）。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（框架内无按本类捕获点，时机：未见规约）
    - 实例化方：``无``（中间层，不直接实例化抛出；由子类
      ``MissingProvideError`` 承载）

    .. seealso::

        :class:`flowing.errors.MissingProvideError`
        :meth:`flowing.agent.Agent.inject`
        :func:`flowing.runtime.inject_from`
    """


class MissingProvideError(ProvideError):
    """inject 沿链上溯到终点（Runtime）仍未找到 key，且无默认值。

    .. rubric:: 功能介绍

    ``inject(key)`` 查找失败的唯一异常。``inject(key, default=...)`` 提供
    非 ``None`` 默认值时不抛出，返回默认值。旧名 ``InjectionError`` 已废弃。

    .. rubric:: 设计动机

    插件依赖的静态校验在 ``mount()`` 由 ``DependencyError`` 承担；运行时动态
    场景（Agent 运行中 inject 未注册的值）由本异常兜底——「声明 + 校验」与
    「运行时兜底」两层互补。

    .. rubric:: 使用示例

    .. code-block:: python

        # .fya 中 ToolEntry / SubagentEntry 的注入表达式（R-4）
        # args:
        #   user_id: "{{ self.inject('user_id') }}"   # 找不到 → MissingProvideError

        async def setup(self):
            user_id = self.inject("user_id")                 # 缺失 → 抛出
            locale = self.inject("locale", default="zh")     # 缺失 → "zh"

    .. rubric:: 行为规约

    - 五要素：字段 ``key``；抛出时机为 inject 链上溯未命中且无 default；
      调用方一般不 catch（``Agent.inject`` 内部按 default 判定后重抛）；
      不可重试（结构错误）；属机制。
    - 边缘情况：``default=None`` 不能表达「默认 None」——判定以
      ``default is not None`` 为准；inject 每次调用实时沿链查找、不缓存，
      运行时 provide 更新后再次 inject 可得新值。
    - 测试案例：前置：链上无任何节点 provide ``"x"``；操作：
      ``agent.inject("x")``；期望：抛 ``MissingProvideError`` 且 ``e.key == "x"``。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.inject``（时机：内部按 default 判定后
      重抛——见本类行为规约）；框架内无最终捕获点，沿调用链上抛
    - 实例化方：``flowing.runtime`` inject 链终点（时机：上溯至 Runtime
      终点仍未命中，runtime.pyi:526 ``raise MissingProvideError(key)``）；
      ``flowing.agent.Agent.inject``（时机：链上溯未命中且无 default，
      agent.pyi:968/2460）；``flowing.tool.ToolEntry`` 创建管线 inject
      解析（时机：inject 列表 key 未命中，tool.pyi:638）；
      ``flowing.plugins.skills`` / ``flowing.plugins.cron`` /
      ``flowing.plugins.comm`` 的 ``inject("…Plugin")`` 句柄获取
      （时机：对应插件未安装时，plugins/skills.pyi:1055、
      plugins/cron.pyi:212、plugins/comm.pyi:1077）

    .. seealso::

        :meth:`flowing.agent.Agent.inject`
        :class:`flowing.params.InjectionKey`
        :class:`flowing.errors.DependencyError` —— 启动期插件依赖校验（静态层），与本异常（运行时兜底）互补。
    """

    key: str
    """未命中的 provide key（``InjectionKey[T]`` 退化为其 ``name`` 字符串）。
    行为边界：仅诊断用途；不提供「最接近的 key」之类的猜测信息。
    
    .. seealso:: :class:`flowing.errors.MissingProvideError`
    """

    def __init__(self, key: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.runtime`` inject 链终点 /
          ``flowing.agent.Agent.inject`` 等路径的 raise（时机：链上溯
          未命中且无 default，runtime.pyi:526、agent.pyi:2460）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Missing provide value for key: {key!r}")
        self.key = key


# ---------------------------------------------------------------------------
# 钩子类
# ---------------------------------------------------------------------------


class HookError(FlowingError):
    """钩子基础设施异常中间层。

    .. rubric:: 功能介绍

    钩子点声明（``declare``）与访问（``__getattr__`` 查找）相关异常的共同基类。

    .. rubric:: 设计动机

    钩子点的「声明独占、注册开放」纪律需要类型化的冲突报告；访问未声明钩子点
    必须报错而非静默创建——保证未启用扩展的 Agent 零开销（不调用 ``use_skill()``
    的 Agent 没有 ``before_skill_load``）。中间层的场景依据（M-03 裁决）：
    插件加载语义为**隔离降级 + 保留热重载**——加载方/重载路径需要以
    ``except HookError`` 统一兜捕「钩子声明阶段」的失败而不误捕其它子系统
    （见 :mod:`flowing.plugins` 模块 docstring 行为规约）。

    .. rubric:: 行为规约

    - 中间层，不直接实例化抛出。
    - 非行为：钩子 handler 的普通**异常不属于本类**——直接上抛，无兜底钩子。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.plugins`` 插件加载 / 重载路径（时机：``install``
      失败时以 ``except HookError`` 统一兜捕钩子声明阶段的失败，
      plugins/__init__.pyi:29-34）
    - 实例化方：``无``（中间层，不直接实例化抛出；由子类
      ``UnknownHookPointError`` / ``DuplicateHookPointError`` 承载）

    .. seealso::

        :class:`flowing.errors.UnknownHookPointError`
        :class:`flowing.errors.DuplicateHookPointError`
        :class:`flowing.hooks.HookRegistry`
    """


class UnknownHookPointError(HookError):
    """访问未声明的钩子点。

    .. rubric:: 功能介绍

    ``self.hooks.<name>`` 的 ``__getattr__`` 只查找不创建；name 不在核心预填
    钩子点、也未被任何扩展 ``declare()`` 时抛出。

    .. rubric:: 设计动机

    「只查找不创建」使钩子点集合成为显式契约：扩展必须先声明再使用，拼写错误
    在注册时即暴露，而非静默创建一个永远不会被 dispatch 的空钩子点。

    .. rubric:: 使用示例

    .. code-block:: python

        self.hooks.before_skill_load(handler)   # 未调用 use_skill(self) → 抛出

    .. rubric:: 行为规约

    - 五要素：字段 ``name``；抛出时机为钩子点访问；调用方不 catch（编程错误）；
      不可重试；属机制。
    - 测试案例：前置：未装 SkillPlugin 的 Agent；操作：访问
      ``agent.hooks.before_skill_load``；期望：抛 ``UnknownHookPointError``
      且 ``e.name == "before_skill_load"``。

    .. rubric:: 调用关系（审计）

    - 被调：经 ``HookError`` 中间层由插件加载路径统一兜捕（时机：
      install 期声明阶段失败，plugins/__init__.pyi:29）；直接调用点
      不 catch（编程错误）
    - 实例化方：``flowing.hooks.HookRegistry.__getattr__``（时机：访问
      未声明的钩子点——只查找不创建，hooks.pyi:1002-1005）

    .. seealso::

        :meth:`flowing.hooks.HookRegistry.declare`
        :class:`flowing.errors.HookError`
    """

    name: str
    """被访问但未声明的钩子点名。行为边界：精确字符串，不含相似名建议。
    
    .. seealso:: :class:`flowing.errors.UnknownHookPointError`
    """

    def __init__(self, name: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.hooks.HookRegistry.__getattr__`` 的 raise
          （时机：访问未声明钩子点，hooks.pyi:1005）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Unknown hook point: {name!r} (not declared)")
        self.name = name


class DuplicateHookPointError(HookError):
    """``declare()`` 声明同名钩子点且来源无法区分。

    .. rubric:: 功能介绍

    两种冲突形态：同名 + 不同 ``by``；同名 + 双方 ``by=None``。同名 + 同
    ``by`` 是幂等返回已有 HookList，不抛出。

    .. rubric:: 设计动机

    声明独占：钩子点的语义（value 类型、dispatch 时机）由声明者拥有；两个
    来源声明同名钩子点而语义可能不同，必须 fail fast 并在消息中携带双方
    ``by`` 以便定位。

    .. rubric:: 使用示例

    .. code-block:: python

        agent.hooks.declare("on_signal", by="comm", match_on="type")
        agent.hooks.declare("on_signal", by="audit")   # 抛出（同名不同 by）
        agent.hooks.declare("on_signal", by="comm", match_on="type")  # 幂等，不抛

    .. rubric:: 行为规约

    - 五要素：字段 ``name`` / ``existing_by`` / ``new_by``；抛出时机为
      ``declare()`` 冲突；调用方不 catch（安装期编程错误）；不可重试；属机制。
    - 边缘情况：``by`` 不可省略——框架核心预填钩子点 ``by="core"``。
    - 测试案例：前置：已 ``declare("x", by="a")``；操作：``declare("x", by="b")``；
      期望：抛 ``DuplicateHookPointError``，消息含 ``"a"`` 与 ``"b"``。

    .. rubric:: 调用关系（审计）

    - 被调：经 ``HookError`` 中间层由插件加载路径统一兜捕（时机：
      install 期声明阶段失败，plugins/__init__.pyi:29）；直接调用点
      不 catch（安装期编程错误）
    - 实例化方：``flowing.hooks.HookRegistry.declare``（时机：同名钩子点
      声明冲突——同名不同 ``by`` 或双方 ``by=None``，hooks.pyi:978）

    .. seealso::

        :meth:`flowing.hooks.HookRegistry.declare`
        :class:`flowing.errors.HookError`
    """

    name: str
    """冲突的钩子点名。
    
    .. seealso:: :class:`flowing.errors.DuplicateHookPointError`
    """
    existing_by: str | None
    """已注册声明者的 ``by`` 标识。
    
    .. seealso:: :class:`flowing.errors.DuplicateHookPointError`
    """
    new_by: str | None
    """本次冲突声明者的 ``by`` 标识。
    
    .. seealso:: :class:`flowing.errors.DuplicateHookPointError`
    """

    def __init__(self, name: str, existing_by: str | None, new_by: str | None) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.hooks.HookRegistry.declare`` 的 raise（时机：
          同名钩子点声明冲突，hooks.pyi:978）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Duplicate hook point declaration: {name!r} (existing by={existing_by!r}, new by={new_by!r})")
        self.name = name
        self.existing_by = existing_by
        self.new_by = new_by


# ---------------------------------------------------------------------------
# 工具类
# ---------------------------------------------------------------------------


class ToolError(FlowingError):
    """工具定义 / 注册 / 查找异常中间层。

    .. rubric:: 功能介绍

    工具系统（四种类型 ``script`` / ``mcp`` / ``cli`` / ``request``）的
    定义期与查找期异常共同基类。与工具**业务错误**严格区分：后者是
    ``ToolResult(status="error")`` 正常产物，LLM 可见，不走异常通道。

    .. rubric:: 设计动机

    「工具坏了」（定义缺失、注册冲突、LLM 编造别名）与「工具返回了错误结果」
    是两条路径：前者 fail fast 或上抛，后者是 Agent-工具交互协议的正常一环，
    恢复方是 LLM。本中间层只承载前者。

    .. rubric:: 行为规约

    - 中间层，不直接实例化抛出。
    - 非行为：工具 ``execute()`` 内部崩溃不包装为本类——按普通异常直接上抛。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（框架内无按本类捕获点，时机：未见规约）
    - 实例化方：``无``（中间层，不直接实例化抛出；由工具定义 / 注册 /
      查找各子类承载）

    .. seealso::

        :class:`flowing.tool.Tool`
        :class:`flowing.tool.ToolResult`
        :class:`flowing.tool.ToolRegistry`
    """


class MissingSchemaError(ToolError):
    """工具参数 schema 缺失且无法推断。

    .. rubric:: 功能介绍

    两种抛出时机：script 工具裸函数的参数缺少类型标注（``inspect`` 无法提取
    schema）；``cli`` / ``request`` 工具的 ``.fya`` 未声明必填的 ``args``
    （这两类无自动推断来源）。

    .. rubric:: 设计动机

    LLM 可见的 ``ToolDefinition.params`` 是调用的前提；「猜一个 schema」会让
    LLM 拿到错误的契约。与其隐式降级，不如定义期报错。

    .. rubric:: 使用示例

    .. code-block:: python

        # tools/backup.tool.fya（type: cli）——args 缺失 → 抛出
        # type: cli
        # command: "pg_dump {{ database }}"
        # （应补 args 声明：database 参数带 type/description）

    .. rubric:: 行为规约

    - 五要素：字段 ``name``（工具名）与 ``param``（缺失标注的参数名，可为
      ``None`` 表示整体缺失）；抛出时机为工具定义加载 / 注册；调用方不
      catch（定义期错误）；不可重试；属机制。
    - 边缘情况：``.fya`` 显式声明的 ``args`` 优先级最高，覆盖一切推断来源——
      显式声明存在时本异常不因类型标注缺失而抛出。
    - 测试案例：前置：裸函数 ``def run(cmd): ...`` 无标注且无 .fya 声明；
      操作：注册该工具；期望：抛 ``MissingSchemaError`` 且 ``e.param == "cmd"``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（定义期错误，fail fast，调用方不 catch）
    - 实例化方：``flowing.tool`` 工具定义加载 / 注册路径（时机：script
      裸函数参数缺类型标注，tool.pyi:866/1348；``cli`` / ``request``
      工具未声明必填 ``args``，tool.pyi:1034/1113/1331）

    .. seealso::

        :class:`flowing.tool.ScriptTool`
        :mod:`flowing.params`
        :class:`flowing.errors.ToolError`
    """

    name: str
    """缺失 schema 的工具规范名。
    
    .. seealso:: :class:`flowing.errors.MissingSchemaError`
    """
    param: str | None
    """缺失类型标注的参数名；整体缺失（如 cli/request 未声明 args）时为 ``None``。
    
    .. seealso:: :class:`flowing.errors.MissingSchemaError`
    """

    def __init__(self, name: str, param: str | None = None) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.tool`` 工具定义加载 / 注册路径的 raise（时机：
          schema 缺失且无法推断，tool.pyi:866/1034/1113/1331/1348）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(
            f"Missing params schema for tool {name!r}"
            + (f" (param {param!r})" if param is not None else "")
        )
        self.name = name
        self.param = param


class ToolNotFoundError(ToolError):
    """``ToolRegistry.get()`` 按规范名找不到工具。

    .. rubric:: 功能介绍

    Runtime 全局工具注册表（规范名 → Tool 实例）查找失败时抛出。规范名是
    注册时的唯一标识，与 Agent 级别名（``ToolEntry.name_alias``）是两个
    命名空间。

    .. rubric:: 设计动机

    「规范名直查注册表」是 Workflow ``tool_call()`` 等路径的基础操作，找不到
    必须显式报错；查找失败不应静默返回 ``None`` 后在调用点变成 ``TypeError``。

    .. rubric:: 使用示例

    .. code-block:: python

        tool = runtime.tool_registry.get("make-payment")   # 未注册 → 抛出

    .. rubric:: 行为规约

    - 五要素：字段 ``name``（规范名）；抛出时机为注册表查找；调用方按需
      catch（如先探测再注册的场景）；不可重试；属机制。
    - 测试案例：前置：空注册表；操作：``registry.get("x")``；期望：抛
      ``ToolNotFoundError`` 且 ``e.name == "x"``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（调用方按需 catch——如先探测再注册；框架内无固定
      捕获点，时机：未见规约）
    - 实例化方：``flowing.tool.ToolRegistry.get``（时机：规范名未注册，
      tool.pyi:1289）；``flowing.tool.ToolEntry`` 绑定解析（时机：
      ``name_ori`` 不在注册表中，tool.pyi:598）

    .. seealso::

        :class:`flowing.tool.ToolRegistry`
        :class:`flowing.errors.UnknownToolError` —— 按**别名**在 Agent 绑定表查找失败的异常（另一命名空间）。
    """

    name: str
    """未命中的工具规范名。
    
    .. seealso:: :class:`flowing.errors.ToolNotFoundError`
    """

    def __init__(self, name: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.tool.ToolRegistry.get`` 的 raise（时机：规范名
          未注册，tool.pyi:1289）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Tool not found in registry: {name!r}")
        self.name = name


class ToolNameConflictError(ToolError):
    """工具规范名重名注册。

    .. rubric:: 功能介绍

    ``ToolRegistry.register()`` 检测到同名规范名时抛出。**重名永远不被允许，
    无论工具类型**——包括同一 MCP 服务器不同配置的场景（须用不同规范名）。

    .. rubric:: 设计动机

    规范名是注册表唯一键；静默覆盖会让先注册方的所有 Agent 绑定指向被换掉
    的实现，行为不可预期。需要相同实例时应复用已有注册而非重复注册。

    Agent **绑定层**的别名冲突（``add_tool`` / ``skills:`` /
    ``subagents:`` 同 alias）由 :class:`EntryNameConflictError` 承载，
    与本类（注册表层规范名冲突）分层。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.tool_registry.register(MyTool())            # 规范名 "make-payment"
        runtime.tool_registry.register(OtherPaymentTool())  # 同名 → 抛出

    .. rubric:: 行为规约

    - 五要素：字段 ``name``；抛出时机为注册；调用方不 catch（安装期错误）；
      不可重试；属机制。
    - 非行为：不提供 ``override=True`` 式的覆盖开关（与 Provider adapter 的
      全局注册策略不同——工具注册表在 Runtime 实例内，重注册无正当场景）。
    - 测试案例：前置：已注册 ``"x"``；操作：再注册同名工具；期望：抛
      ``ToolNameConflictError``，原注册保持不变。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（安装期错误，调用方不 catch）
    - 实例化方：``flowing.tool.ToolRegistry.register``（时机：规范名
      重名注册——无论工具类型，tool.pyi:1276）

    .. seealso::

        :meth:`flowing.tool.ToolRegistry.register`
        :class:`flowing.errors.ToolError`
    """

    name: str
    """发生冲突的工具规范名。
    
    .. seealso:: :class:`flowing.errors.ToolNameConflictError`
    """

    def __init__(self, name: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.tool.ToolRegistry.register`` 的 raise（时机：
          规范名重名注册，tool.pyi:1276）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Tool name conflict: {name!r} is already registered")
        self.name = name


class EntryNameConflictError(FlowingError):
    """Agent 绑定层同 alias 冲突：tool / skill / subagent 条目统一 fail-fast。

    .. rubric:: 功能介绍

    能力三正交的 Agent 级绑定层（``_tool_entries`` / ``_skill_entries`` /
    ``_subagent_entries``，key 均为**别名**）中，同 alias 重复声明/添加
    时抛出。覆盖两条入口路径：

    1. 声明式：``.fya`` 的 ``tools:`` / ``skills:`` / ``subagents:`` 列表
       中出现两条同 alias 条目；
    2. 编程式：``Agent.add_tool()`` 等（时机：``setup()`` 或运行期）。

    .. rubric:: 设计动机

    「都报错，不覆盖」（用户裁决，取代 skill 原「后声明覆盖先声明」的
    ManagedList 语义）：插件的 ``setup()`` 也在 ``.fya`` 中声明（``$script``），
    声明式与编程式写法背后是同一个作者——写重了就是笔误，静默覆盖会把
    「用户定义优先」的正当场景与笔误混在一起，无法区分。每次生命周期从
    空条目表重放 ``setup()``（recover 不感知上一次效果），因此同一生命
    周期内的重复永远非法，没有例外路径。

    与 :class:`ToolNameConflictError` 分层：本类管 Agent **绑定层**的别名
    冲突；它管**注册表层**的规范名冲突（全局、跨 Agent）。命名空间落地后
    （``flowing.runtime`` 模块 docstring §7a），本类的冲突判定是 **LLM
    视角**的：LLM 看不到命名空间（有别名时连规范名也看不到），因此同一
    Agent 下两个不同命名空间的同名资源必须给其一起别名；不同 Agent 各引
    各的同名资源不冲突。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # agent.fya——两条同 alias 条目 → 解析期抛出
        tools:
          - make-payment as pay: {}
          - other-payment as pay: {}

    .. rubric:: 行为规约

    - 五要素：字段 ``alias`` / ``kind``；抛出时机为声明解析或绑定写入；
      调用方不 catch（定义期/安装期编程错误）；不可重试；属机制。
    - 非行为：不提供覆盖开关；与「检查后跳过」策略不冲突——插件挂载
      前**先查后跳**（如 ``use_skill`` 保留用户定义）是合法规避，只有
      未经检查的盲目写入才触发本异常。
    - 测试案例：前置：``.fya`` ``skills:`` 两条同 alias；操作：``use_skill``
      → 期望：抛 ``EntryNameConflictError``，``kind == "skill"``。
    - 测试案例：前置：同一 Agent 引用 ``myplugin::search``（插件注册）与
      ``./tools/search``（文件资源），规范名同为 ``search`` 且均未起别名
      → 期望：抛 ``EntryNameConflictError``；给其一 ``as`` 别名后正常
      （不同命名空间同名资源本身允许共存）。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（定义期错误，调用方不 catch）
    - 实例化方：``flowing.agent.Agent.add_tool``（同 alias 重复
      添加）；``.fya`` ``tools:`` / ``skills:`` / ``subagents:`` 解析路径
      （``use_skill`` 的条目填充循环等，同 alias 重复声明）

    .. seealso::

        :class:`flowing.errors.ToolNameConflictError` —— 注册表层规范名冲突。
        :meth:`flowing.agent.Agent.add_tool`
        :class:`flowing.plugins.skills.SkillEntry`
        :class:`flowing.subagents.SubagentEntry`
    """

    alias: str
    """发生冲突的别名。
    """
    kind: str
    """冲突所在的绑定层：``"tool"`` / ``"skill"`` / ``"subagent"``。
    """

    def __init__(self, alias: str, kind: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``Agent.add_tool`` 与 ``.fya`` 三列表解析路径的 raise
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Entry alias conflict: {alias!r} (kind={kind!r})")
        self.alias = alias
        self.kind = kind


class UnknownToolError(ToolError):
    """``tool_call()`` 按别名在 ``_tool_entries`` 中找不到工具。

    .. rubric:: 功能介绍

    LLM 发起的工具调用**仅按别名**查找 Agent 级绑定表（``ToolEntry.name_alias``），
    不回退规范名；别名未命中（典型场景：LLM 编造了不在 ``Context.tools``
    中的名字）时抛出。

    .. rubric:: 设计动机

    不回退规范名是刻意的：不同 Agent 对同一工具可注册不同别名与覆写，回退
    规范名会绕开 Agent 级绑定层（其存在理由正是 per-Agent 的 LLM 可见声明）。

    .. rubric:: 使用示例

    .. code-block:: python

        # LLM 输出 tool_call(name="delete_everything") 但该别名未绑定 → 抛出
        await agent.tool_call(ToolCall(name="delete_everything", args={}))

    .. rubric:: 行为规约

    - 五要素：字段 ``name``（LLM 给出的别名）；抛出时机为 ``tool_call()``
      别名查找；框架核心不 catch（直接上抛出工具调用循环），应用层可在
      ``before_tool_call`` 前置校验或自行捕获；不可自动重试（LLM 侧可在
      后续回合修正调用名，但属应用策略）；属机制。
    - 边缘情况：``ToolEntry.enabled=False`` 的条目仍在绑定表中——编程式
      调用可达，本异常只在别名完全不存在时抛出（可见性与可执行性分离）。
    - 测试案例：前置：``_tool_entries`` 仅含别名 ``"pay"``；操作：
      ``tool_call()`` 以 ``name="payment"``（规范名）调用；期望：抛
      ``UnknownToolError``（不回退规范名）。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（框架核心不 catch，直接上抛出工具调用循环；应用层
      可在 ``before_tool_call`` 前置校验或自行捕获）
    - 实例化方：``flowing.agent.Agent.tool_call``（时机：LLM 工具调用
      按别名在 ``_tool_entries`` 查找未命中——不回退规范名，
      agent.pyi:2340；tool.pyi:43）

    .. seealso::

        :meth:`flowing.agent.Agent.tool_call`
        :class:`flowing.tool.ToolEntry`
        :class:`flowing.errors.ToolNotFoundError`
    """

    name: str
    """未命中的工具别名（LLM 可见名）。
    
    .. seealso:: :class:`flowing.errors.UnknownToolError`
    """

    def __init__(self, name: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.agent.Agent.tool_call`` 的 raise（时机：别名
          在 ``_tool_entries`` 未命中，agent.pyi:2340）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Unknown tool alias: {name!r}")
        self.name = name


class AmbiguousToolError(ToolError):
    """工具 ``.py`` 文件的定义形态歧义。

    .. rubric:: 功能介绍

    script 工具的定向查找中，同一 ``.py`` 文件出现以下任一情况即抛出：
    同时含打标函数（``@flowing_tool``）与 Tool 子类；或含**多个**打标
    函数（「每文件至多一个」规则的违反）；框架无法判定用户意图。

    .. rubric:: 设计动机

    两种形态生成不同的 Tool 定义路径（打标函数自动提升 vs 类实例化）；按序
    选其一或多个打标函数隐式合并，都会让工具行为依赖文件内容的出现顺序，
    不可预期。

    .. rubric:: 使用示例

    .. code-block:: python

        # tools/make_payment.py——同时存在两者 → 抛出
        @flowing_tool
        def make_payment(amount: float): ...
        class MakePayment(ScriptTool): ...

    .. rubric:: 行为规约

    - 五要素：字段 ``path``（冲突文件路径）；抛出时机为工具定向查找 /
      实例化；调用方不 catch（定义期错误）；不可重试；属机制。
    - 边缘情况：``.tool.fya``（含 ``type: script``）与同名 ``.py`` 并存
      不属于歧义——``.fya`` 优先（并告警），不抛本异常；``TOOL.fya`` 的
      ``callable:`` 指向**已装饰**函数也不属本类——那是声明通道互斥的
      违反，抛 :class:`flowing.errors.FormatError`。
    - 测试案例：前置：单文件同时含打标函数与 Tool 子类；操作：Agent 声明
      引用该工具；期望：抛 ``AmbiguousToolError``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（定义期错误，调用方不 catch）
    - 实例化方：``flowing.tool.ToolRegistry.get`` 的 script
      工具定向查找 / 实例化路径（时机：同一 ``.py`` 文件含打标函数与
      Tool 子类、或多个打标函数）

    .. seealso::

        :class:`flowing.tool.ScriptTool`
        :func:`flowing.tool.flowing_tool`
        :class:`flowing.errors.ToolError`
    """

    path: str
    """产生歧义的 ``.py`` 文件路径（字符串形式）。
    
    .. seealso:: :class:`flowing.errors.AmbiguousToolError`
    """

    def __init__(self, path: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.tool`` script 工具定向查找路径的 raise（时机：
          同一 ``.py`` 同时含同名裸函数与 Tool 子类，tool.pyi:868/1349）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Ambiguous tool definitions in file: {path}")
        self.path = path


class AmbiguousMcpSourceError(ToolError):
    """MCP 工具的 ``command``（stdio）与 ``url``（远程）同时声明。

    .. rubric:: 功能介绍

    MCP 来源识别规则：``command`` → stdio 本地进程；``url`` → 远程服务。
    两者并存无法判定连接方式，抛出本异常。

    .. rubric:: 设计动机

    两种来源的生命周期管理完全不同（子进程 vs HTTP 会话）；「两个都用」
    或「任选其一」都会产生不可预期的连接行为。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # tools/github.tool.fya——command 与 url 并存 → 抛出
        type: mcp
        command: "npx -y @mcp/github"
        url: "https://mcp.example.com/github"

    .. rubric:: 行为规约

    - 五要素：字段 ``name``（工具规范名）；抛出时机为 MCP 工具定义加载；
      调用方不 catch（定义期错误）；不可重试；属机制。
    - 测试案例：前置：``.fya`` 同时声明两字段；操作：加载工具定义；期望：
      抛 ``AmbiguousMcpSourceError``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（定义期错误，调用方不 catch）
    - 实例化方：``flowing.tool`` MCP 工具定义加载路径（时机：
      ``command`` 与 ``url`` 同时声明，tool.pyi:986）

    .. seealso::

        :class:`flowing.errors.MissingMcpSourceError`
            两者均未声明的对偶异常。
        :class:`flowing.errors.ToolError`
    """

    name: str
    """声明冲突的 MCP 工具规范名。
    
    .. seealso:: :class:`flowing.errors.AmbiguousMcpSourceError`
    """

    def __init__(self, name: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.tool`` MCP 工具定义加载路径的 raise（时机：
          ``command`` 与 ``url`` 同时声明，tool.pyi:986）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"MCP tool {name!r} declares both command and url")
        self.name = name


class MissingMcpSourceError(ToolError):
    """MCP 工具的 ``command`` 与 ``url`` 均未声明。

    .. rubric:: 功能介绍

    ``type: mcp`` 的工具必须声明一种连接来源；两者皆缺时抛出。

    .. rubric:: 设计动机

    与 ``AmbiguousMcpSourceError`` 对偶，构成「恰好一个来源」的完整约束；
    缺来源时没有任何可推断的默认连接方式。

    .. rubric:: 行为规约

    - 五要素：字段 ``name``；抛出时机为 MCP 工具定义加载；调用方不 catch；
      不可重试；属机制。
    - 测试案例：前置：``type: mcp`` 且无 ``command`` / ``url``；操作：加载；
      期望：抛 ``MissingMcpSourceError``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（定义期错误，调用方不 catch）
    - 实例化方：``flowing.tool`` MCP 工具定义加载路径（时机：
      ``command`` 与 ``url`` 均未声明，tool.pyi:987）

    .. seealso::

        :class:`flowing.errors.AmbiguousMcpSourceError`
        :class:`flowing.errors.ToolError`
    """

    name: str
    """缺失来源声明的 MCP 工具规范名。
    
    .. seealso:: :class:`flowing.errors.MissingMcpSourceError`
    """

    def __init__(self, name: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.tool`` MCP 工具定义加载路径的 raise（时机：
          ``command`` 与 ``url`` 均未声明，tool.pyi:987）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"MCP tool {name!r} declares neither command nor url")
        self.name = name


# ---------------------------------------------------------------------------
# 子 Agent 与资源类
# ---------------------------------------------------------------------------


class ResourceError(FlowingError):
    """Resource 注册与访问异常中间层。

    .. rubric:: 功能介绍

    Resource（Runtime 持有的跨 Agent / 跨任务共享实例，如 DB 连接池）的
    注册与读取异常共同基类。读写通道对称：``register_resource`` /
    ``get_resource`` 均为 Runtime 实例方法。

    .. rubric:: 设计动机

    Resource 与 provide/inject 正交：值跟随 Agent 生命周期 → provide；
    值跨越多个 Agent 与任务 → Resource。Resource 不走 inject 链，因此其
    错误类型独立于 ``ProvideError``。
    另注：子 Agent 调用不产生专属异常类型——``invoke_subagent()`` 路径的
    异常按普通异常**直接上抛**（``on_subagent_error`` 钩子不存在）。

    .. rubric:: 行为规约

    - 中间层，不直接实例化抛出。
    - 非行为：``get_resource(name, type_hint=...)`` 的 ``type_hint`` 仅供
      IDE 推断，运行时**不做** isinstance 校验，故不存在「类型不匹配」异常。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（框架内无按本类捕获点，时机：未见规约）
    - 实例化方：``无``（中间层，不直接实例化抛出；由
      ``ResourceNameConflictError`` / ``ResourceNotFoundError`` 承载）

    .. seealso::

        :meth:`flowing.runtime.Runtime.register_resource`
        :meth:`flowing.runtime.Runtime.get_resource`
        :meth:`flowing.agent.Agent.get_resource`
    """


class ResourceNameConflictError(ResourceError):
    """Resource 重名注册。

    .. rubric:: 功能介绍

    ``Runtime.register_resource(name, instance)`` 检测到同名时抛出。
    注册必须在启动根 Agent（``mount()``）前完成。

    .. rubric:: 设计动机

    Resource 是全局共享实例，静默覆盖会让已持有旧实例引用的 Agent 与新
    读取方看到不同对象；同名即编程错误。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.register_resource("db", create_pool(dsn))
        runtime.register_resource("db", create_pool(other_dsn))  # 抛出

    .. rubric:: 行为规约

    - 五要素：字段 ``name``；抛出时机为注册；调用方不 catch（启动期错误）；
      不可重试；属机制。
    - 测试案例：前置：已注册 ``"db"``；操作：同名再注册；期望：抛
      ``ResourceNameConflictError``，原实例不被替换。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（启动期错误，调用方不 catch）
    - 实例化方：``flowing.runtime.Runtime.register_resource``（时机：
      同名 Resource 再注册——须在 ``mount()`` 前完成注册，
      runtime.pyi:1337/1345）

    .. seealso::

        :meth:`flowing.runtime.Runtime.register_resource`
        :class:`flowing.errors.ResourceError`
    """

    name: str
    """发生冲突的 Resource 名。
    
    .. seealso:: :class:`flowing.errors.ResourceNameConflictError`
    """

    def __init__(self, name: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.runtime.Runtime.register_resource`` 的 raise
          （时机：同名 Resource 再注册，runtime.pyi:1345）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Resource name conflict: {name!r} is already registered")
        self.name = name


class ResourceNotFoundError(ResourceError):
    """访问未注册的 Resource。

    .. rubric:: 功能介绍

    ``Runtime.get_resource(name)``（及 ``Agent.get_resource`` 便捷委托）
    找不到已注册实例时抛出。

    .. rubric:: 设计动机

    Resource 获取完全无感（不在 ``args`` / ``inject`` 中声明），因此缺失
    只能在读取点暴露；显式异常比返回 ``None`` 更能防止下游
    ``AttributeError`` 式的次生错误。

    .. rubric:: 使用示例

    .. code-block:: python

        async def execute(self, query: str, caller=None):
            db = caller.get_resource("db")   # 未注册 → 抛出

    .. rubric:: 行为规约

    - 五要素：字段 ``name``；抛出时机为读取；调用方按需 catch（如可选
      能力的降级场景）；不可重试；属机制。
    - 测试案例：前置：无注册；操作：``runtime.get_resource("db")``；期望：
      抛 ``ResourceNotFoundError`` 且 ``e.name == "db"``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（调用方按需 catch——如可选能力降级；框架内无固定
      捕获点，时机：未见规约）
    - 实例化方：``flowing.runtime.Runtime.get_resource``（时机：name
      未注册，runtime.pyi:1248/1256）；``flowing.agent.Agent.get_resource``
      便捷委托（时机：同上，agent.pyi:2500）

    .. seealso::

        :meth:`flowing.runtime.Runtime.get_resource`
        :class:`flowing.errors.ResourceError`
    """

    name: str
    """未命中的 Resource 名。
    
    .. seealso:: :class:`flowing.errors.ResourceNotFoundError`
    """

    def __init__(self, name: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.runtime.Runtime.get_resource``（及
          ``flowing.agent.Agent.get_resource`` 委托）的 raise（时机：
          name 未注册，runtime.pyi:1256、agent.pyi:2500）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Resource not found: {name!r}")
        self.name = name


# ---------------------------------------------------------------------------
# Provider 类
# ---------------------------------------------------------------------------


class ProviderError(FlowingError):
    """Provider 加载与调用异常中间层（核心机制，不可替换）。

    .. rubric:: 功能介绍

    Provider adapter 抛出的全部归类异常之基类。adapter 必须把 SDK / HTTP
    层的原生异常归类为本层子类后抛出——这是 adapter 互操作标准的一部分。

    .. rubric:: 设计动机

    回合层的错误决策（``on_provider_error`` → ``can_continue``）依赖稳定的
    错误分类；若各 adapter 直接抛原生异常，策略层（``use_retry`` 等）将
    不得不 import 每个 SDK 的异常类型，机制与策略一起泄漏。

    .. rubric:: 使用示例

    .. code-block:: python

        # adapter 内的归类职责（示意）
        try:
            resp = await self._client.chat.completions.create(...)
        except RateLimitError as e:                    # SDK 原生异常
            raise RateLimitedError(
                str(e), provider=self.name, model=model.model,
                retry_after=_parse_retry_after(e),
            ) from e

    .. rubric:: 行为规约

    - 中间层本身可被实例化抛出的场景不存在；adapter 必须使用具体子类。
    - 通用字段 ``provider`` / ``model`` 供日志、计费与策略层消费——异常的
      传播路径经过 ``on_provider_error`` 钩子分发，handler 远离抛出上下文，
      归属信息必须由异常自身携带（M-06 裁决；对照先例 kimi-code / pi 由
      调用方上下文承载，flowing 的钩子分发模型下不适用）。
    - ``status_code`` / ``request_id`` / ``retry_after`` 置于基类而非具体
      子类（M-06 裁决）：``Retry-After`` 语义可出现在任意响应（503/529
      过载同样携带），重试 handler 应能无条件消费
      ``ctx.error.retry_after or 默认退避``，不按类型特判。
    - 消息文本面向人读（不回喂 LLM；喂模型的错误由上层另行构造），
      结构化字段面向代码。
    - 非行为：框架核心不内置重试——可重试性只是分类事实，是否重试由
      ``on_provider_error`` handler（如 ``use_retry()``）决定。
    - 不变量：``provider_gen()`` 不捕获、不重试任何 Provider 异常；统一在逻辑
      Turn 层接住（``ContextLengthError`` 除外，见模块 docstring 决策树）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn`` 逻辑 Turn 错误决策树
      （时机：``provider_gen()`` 内 Provider 调用抛异常——构造
      ``ProviderErrorContext`` 后 dispatch ``on_provider_error``；
      ``ContextLengthError`` 除外直接上抛，见模块 docstring 决策树与
      agent.pyi:1830-1831）；``flowing.composables.retry`` 默认 handler
      的 isinstance 分类判定（时机：每次 ``on_provider_error`` dispatch，
      composables/retry.pyi:307-311）
    - 实例化方：各 Provider adapter（时机：SDK / HTTP 原生异常归类后
      抛出——互操作标准，分类表见 ``flowing.providers`` 包 docstring
      「Provider 异常分类」一节；中间层本体不直接
      实例化，adapter 必须使用具体子类）

    .. seealso::

        :class:`flowing.providers.Provider`
        :meth:`flowing.agent.Agent.query`
        :mod:`flowing.composables.retry`
    """

    provider: str | None
    """Provider 条目名（``providers.yaml`` 中的 key，条目名即身份标识）。
    加载期异常（如 ``MissingEnvironmentVariableError``）同样填写。
    
    .. seealso:: :class:`flowing.errors.ProviderError`
    """
    model: str | None
    """调用时使用的模型 ID（``ModelConfig.model``）；加载期异常可为 ``None``。
    
    .. seealso:: :class:`flowing.errors.ProviderError`
    """
    status_code: int | None
    """HTTP 状态码（如 429 / 500 / 503）；非 HTTP 来源的错误（连接失败、
    加载期失败等）为 ``None``。供日志、诊断与策略层按码细分
    （参考 kimi-code 先例：status code 是重试白名单与 UI 文案的核心依据）。
    
    .. seealso:: :class:`flowing.errors.ProviderError`
    """
    request_id: str | None
    """服务端返回的请求 ID（如 ``x-request-id`` 响应头）；未提供为 ``None``。
    供日志归因与工单定位。
    
    .. seealso:: :class:`flowing.errors.ProviderError`
    """
    retry_after: float | None
    """服务端建议的重试等待秒数（``Retry-After`` 响应头）；未建议为
    ``None``，策略层使用自身默认退避。429 之外的响应（503/529 过载）
    同样可能携带，故置于基类。
    
    .. seealso:: :class:`flowing.errors.RateLimitedError`、
    :data:`flowing.composables.retry.RETRYABLE_ERRORS`
    """

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
        """
        .. rubric:: 调用关系（审计）

        - 调用：``Exception.__init__``（时机：每次构造，传递面向人读的
          ``message``——模块行为规约「异常消息字符串由框架格式化」）
        - 被调：各 Provider adapter 归类抛子类时经子类构造间接调用
          （时机：SDK / HTTP 原生异常归类；本层不直接实例化——行为规约）
        """
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after = retry_after


class ContextLengthError(ProviderError):
    """上下文长度溢出（不可重试；不经 ``on_provider_error``，直接上抛）。

    .. rubric:: 功能介绍

    组装的 ``Context`` 超出模型 ``context_window`` 时由 adapter 在请求 / 响应
    解析时抛出。是 Provider 异常分类中**唯一**绕过 ``on_provider_error`` 的类型。

    .. rubric:: 设计动机

    「上下文太长」重试必然重现同样失败——不可重试是事实而非策略，因此框架
    把它硬编码为直接上抛，不给策略层误判空间。压缩 / 截断属策略，由钩子层
    （如 ``before_provider_gen`` 中的压缩 Composable）在**下一次**调用前处理，
    不在错误路径内自动发生。

    .. rubric:: 使用示例

    .. code-block:: python

        # 应用层或外层调用方捕获
        try:
            result = await agent.query(long_document)
        except ContextLengthError:
            await agent.chain.remove(old_message_ids)   # 消息级树手术，策略层决定

    .. rubric:: 行为规约

    - 五要素：字段继承 ``provider`` / ``model``；抛出时机为 adapter 请求 /
      响应解析；调用方：逻辑 Turn 循环**不**将其送入 ``on_provider_error``，
      直接上抛给 ``query()`` 等待方 / 调用代码，由应用层决定是否 catch；
      不可重试（框架强制）；属机制（不可替换）。
    - 边缘情况：直接上抛不改变逻辑 Turn 收尾不变量——``after_turn`` 钩子
      照常触发，已产生消息保持消息级树 append-only 持久化。
    - 测试案例：前置：注册一个写 ``can_continue=True`` 的 ``on_provider_error``
      handler；操作：Provider 抛 ``ContextLengthError``；期望：handler 未被
      调用，异常上抛出 Turn 循环。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn``（时机：逻辑 Turn 循环不
      送入 ``on_provider_error``，直接上抛给 ``query()`` 等待方 / 调用
      代码，agent.pyi:691/1831）；``flowing.context`` / ``flowing.message``
      分层兜底引用（时机：context 组装超限的前置检测兜底，
      context.pyi:973/1030，message.pyi:332/597）
    - 实例化方：Provider adapter（时机：请求 / 响应解析发现组装的
      ``Context`` 超出模型 ``context_window``——本类行为规约）

    .. seealso::

        :class:`flowing.errors.RequestTooLargeError`
            字节超限（HTTP 413）的对偶分类，恢复路径不同。
        :class:`flowing.errors.ProviderError`
        :class:`flowing.context.Context`
        :class:`flowing.message.MessageChain`
    """


class RequestTooLargeError(ProviderError):
    """请求体字节超限（HTTP 413，不可重试类；经 ``on_provider_error``）。

    .. rubric:: 功能介绍

    请求体**字节数**超限（典型：多模态附件过大），与
    :class:`ContextLengthError` 的 **token 数**超限相区分。

    .. rubric:: 设计动机

    两者恢复路径本质不同（M-06 裁决，kimi-code 先例）：token 超限靠压缩
    会话历史；字节超限压缩历史无用，必须剥离媒体附件重发。注意 status
    code 不可尽信——部分 Provider（如 Vertex）会把 prompt 过长也返回
    413，adapter 归类时须以消息内容辅助判别。压缩与媒体剥离都是后续
    钩子的职责，本类只作为分类信号存在——框架核心不内置任何恢复机制。

    .. rubric:: 行为规约

    - 五要素：字段继承基类；抛出时机为 Provider 返回 413（或等价语义）；
      调用方走 ``on_provider_error``（handler 可据此触发媒体剥离后重发，
      也可 ``can_continue=False`` 上抛给用户）；不可重试（原样重发必然
      重现）；分类属机制。
    - 边缘情况：Provider 把 token 超限误报为 413 时，adapter 应归类为
      :class:`ContextLengthError` 而非本类——归类依据是语义而非状态码。
    - 测试案例：前置：注册一个剥离最大附件后置 ``can_continue=True`` 的
      handler；操作：Provider 返回 413；期望：handler 被调用，重发成功。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn`` 的 ``on_provider_error``
      决策树（时机：provider_gen() 内 Provider 调用抛异常）；``flowing.composables.retry`` 默认 handler（时机：每次 ``on_provider_error``
      dispatch——本类在 ``NON_RETRYABLE_ERRORS`` 中直接放行不重试，
      composables/retry.pyi:173/182）
    - 实例化方：Provider adapter（时机：Provider 返回 413 或等价语义
      ——归类依据是语义而非状态码）

    .. seealso::

        :class:`flowing.errors.ContextLengthError`
            token 超限的对偶分类（不经 ``on_provider_error``，直接上抛）。
        :class:`flowing.errors.ProviderError`
    """


class RateLimitedError(ProviderError):
    """Provider 限流（HTTP 429，可重试类）。

    .. rubric:: 功能介绍

    临时限流。是否重试、退避多久由策略层决定（``use_retry()`` 或自定义
    ``on_provider_error`` handler）；框架核心只负责分类。

    .. rubric:: 设计动机

    429 通常携带 ``Retry-After`` 之类的服务端建议；把它结构化为
    ``retry_after`` 字段，使策略层不必解析各家 SDK 的响应头格式。

    .. rubric:: 使用示例

    .. code-block:: python

        async def retry_handler(agent, ctx):
            if isinstance(ctx.error, RateLimitedError):
                await asyncio.sleep(ctx.error.retry_after or 1.0)
                ctx.can_continue = True
            return ctx

    .. rubric:: 行为规约

    - 五要素：字段继承基类 ``retry_after``（秒，``None`` 表示服务端未建议）；
      抛出时机为 Provider 调用返回 429 且语义为**瞬时限流**（配额耗尽见
      :class:`QuotaExhaustedError`）；调用方：逻辑 Turn 层捕获后进入
      ``on_provider_error`` 决策；可重试（策略决定次数与退避，框架无默认值）；
      分类属机制，重试属策略。
    - 边缘情况：未启用任何重试 handler 时，逻辑 Turn 直接静默终止——
      框架不提供默认容错。
    - 测试案例：前置：``use_retry()`` 已启用；操作：连续两次 429 后成功；
      期望：回合正常完成，``retry_after`` 被 handler 读取。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn`` 的 ``on_provider_error``
      决策树（时机：provider_gen() 内 Provider 调用抛异常）；``flowing.composables.retry`` 默认 handler（时机：每次 ``on_provider_error``
      dispatch——``RETRYABLE_ERRORS`` 成员，isinstance 判定后计数与
      退避，composables/retry.pyi:156/238/307）
    - 实例化方：Provider adapter（时机：Provider 调用返回 429 且语义
      为瞬时限流）

    .. seealso::

        :class:`flowing.errors.QuotaExhaustedError`
            同为 429 但**不可重试**的对偶分类。
        :class:`flowing.errors.ProviderError`
        :mod:`flowing.composables.retry`
    """


class QuotaExhaustedError(ProviderError):
    """Provider 配额 / 余额耗尽（HTTP 429 或专用错误码，不可重试类）。

    .. rubric:: 功能介绍

    与 :class:`RateLimitedError` 同为 429 但语义相反：「你太快了」（等一等
    能成功）vs「你没额度了」（等多久都不会成功，需要人介入——充值、换
    key、换 provider）。

    .. rubric:: 设计动机

    重试配额耗尽是纯浪费：每次重试烧延迟预算且必然失败，用户几分钟
    后才看到第一秒就已确定的「余额不足」。**刻意不做** ``RateLimitedError``
    的子类（M-06 裁决，kimi-code 先例）：继承会让 ``except RateLimitedError``
    与 ``isinstance`` 重试判定误捕本类——类型树本身即是重试策略的判定表。

    .. rubric:: 行为规约

    - 五要素：字段继承基类；抛出时机为 adapter 识别到配额语义——优先读
      结构化错误码（如 ``exceeded_current_quota_error``），退化到账单措辞
      正则（``insufficient_quota`` / ``billing`` 等）；调用方走
      ``on_provider_error``；**不可重试**；分类属机制。
    - 非行为：框架不检测余额数值、不触发充值流程；本类只是分类信号，
      恢复动作（换 provider / 提示用户）属策略层。
    - 测试案例：前置：Provider 返回 429 且 body 含 ``insufficient_quota``；
      操作：``provider_gen()``；期望：adapter 抛 ``QuotaExhaustedError`` 而非
      ``RateLimitedError``，``isinstance(e, RateLimitedError)`` 为假。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn`` 的 ``on_provider_error``
      决策树（时机：provider_gen() 内 Provider 调用抛异常）；``flowing.composables.retry`` 默认 handler（时机：每次 ``on_provider_error``
      dispatch——``NON_RETRYABLE_ERRORS`` 成员，composables/retry.pyi:183）
    - 实例化方：Provider adapter（时机：识别到配额 / 余额耗尽语义——
      优先结构化错误码，退化账单措辞正则，429 或专用错误码）

    .. seealso::

        :class:`flowing.errors.RateLimitedError`
            同为 429 但可重试的对偶分类。
        :class:`flowing.errors.ProviderError`
    """


class ServerError(ProviderError):
    """Provider 服务端错误（HTTP 5xx，可重试类）。

    .. rubric:: 功能介绍

    服务端临时故障。分类为可重试；重试与否由策略层决定。

    .. rubric:: 设计动机

    5xx 与 4xx 的可恢复性本质不同：前者通常自愈，后者（请求本身有问题）
    重试无意义。分开两类使策略层可以按类判断。

    .. rubric:: 行为规约

    - 五要素：字段继承基类（``status_code`` 记录 5xx 码；``retry_after``
      可能随 503/529 过载响应携带）；抛出时机为 Provider 返回 5xx；
      调用方走 ``on_provider_error``；可重试（策略决定）；分类属机制。
    - 测试案例：前置：无重试 handler；操作：Provider 持续 500；期望：
      逻辑 Turn 静默终止，Agent 存活。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn`` 的 ``on_provider_error``
      决策树（时机：provider_gen() 内 Provider 调用抛异常）；``flowing.composables.retry`` 默认 handler（时机：每次 ``on_provider_error``
      dispatch——``RETRYABLE_ERRORS`` 成员，composables/retry.pyi:157/311）
    - 实例化方：Provider adapter（时机：Provider 返回 5xx 服务端错误）

    .. seealso::

        :class:`flowing.errors.InvalidRequestError`
            4xx 请求错误（不可重试）的对偶分类。
        :class:`flowing.errors.ProviderError`
    """


class NetworkError(ProviderError):
    """网络层错误（连接失败 / DNS / TLS 等，可重试类）。

    .. rubric:: 功能介绍

    请求未到达 Provider 或响应中断的网络抖动。adapter 负责把 httpx /
    aiohttp 等库的连接异常归类为本类。

    .. rubric:: 设计动机

    网络错误与 HTTP 层错误（有响应状态码）的可恢复策略不同（通常立即或
    短退避重试）；独立分类避免策略层做字符串匹配。

    .. rubric:: 行为规约

    - 五要素：字段继承 ``provider`` / ``model``；抛出时机为传输层失败；
      调用方走 ``on_provider_error``；可重试（策略决定）；分类属机制。
    - 测试案例：前置：目标不可达；操作：发起 ``provider_gen()``；期望：adapter 抛
      ``NetworkError``（而非 httpx 原生异常直出）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn`` 的 ``on_provider_error``
      决策树（时机：provider_gen() 内 Provider 调用抛异常）；``flowing.composables.retry`` 默认 handler（时机：每次 ``on_provider_error``
      dispatch——``RETRYABLE_ERRORS`` 成员，composables/retry.pyi:158/311）
    - 实例化方：Provider adapter（时机：传输层失败——连接失败 / DNS /
      TLS 等，httpx / aiohttp 连接异常归类）

    .. seealso::

        :class:`flowing.errors.ProviderTimeoutError`
        :class:`flowing.errors.ProviderError`
    """


class ProviderTimeoutError(ProviderError):
    """Provider 调用超时（可重试类）。

    .. rubric:: 功能介绍

    请求超过 adapter 超时预算时抛出。

    .. rubric:: 设计动机

    命名自带归属（Provider 层超时），与内置 ``TimeoutError`` 无遮蔽关系
    （M-02 裁决：原名 ``TimeoutError`` 改为现名，避免与内置同名引发的
    ``except`` 误捕）。

    .. rubric:: 行为规约

    - 五要素：字段继承 ``provider`` / ``model``；抛出时机为调用超时；
      调用方走 ``on_provider_error``；可重试（策略决定）；分类属机制。
    - 非行为：本类**不**继承内置 ``TimeoutError``，``except TimeoutError``
      （内置）不会捕获它。
    - 测试案例：操作：adapter 超时抛出；期望：``isinstance(e,
      ProviderTimeoutError)`` 为真且 ``isinstance(e, builtins.TimeoutError)``
      为假。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn`` 的 ``on_provider_error``
      决策树（时机：provider_gen() 内 Provider 调用抛异常）；``flowing.composables.retry`` 默认 handler（时机：每次 ``on_provider_error``
      dispatch——``RETRYABLE_ERRORS`` 成员，composables/retry.pyi:159/311）
    - 实例化方：Provider adapter（时机：请求超过 adapter 超时预算）

    .. seealso::

        :class:`flowing.errors.NetworkError`
        :class:`flowing.errors.ProviderError`
    """


class AuthenticationError(ProviderError):
    """凭证错误（HTTP 401 / 403，不可重试）。

    .. rubric:: 功能介绍

    API key 无效、过期或权限不足。凭证问题不自愈，分类为不可重试——
    ``use_retry()`` 内置 handler 对本类直接放行（不写 ``can_continue``）。

    .. rubric:: 设计动机

    凭证修复需要人工介入（换 key、改配置），自动重试只会放大失败；但框架
    不阻止应用层自定义 handler 做「换凭证后重试」——不可重试是分类事实的
    默认值，策略仍可覆盖。

    .. rubric:: 行为规约

    - 五要素：字段继承 ``provider`` / ``model``；抛出时机为 Provider 返回
      401 / 403；调用方走 ``on_provider_error``；不可重试（默认，策略可覆盖）；
      分类属机制。
    - 边缘情况：凭证内容本身**不进**异常字段与消息文本（敏感信息边界：
      API key 不进消息、不落盘）。
    - 测试案例：前置：错误 key；操作：``provider_gen()``；期望：抛
      ``AuthenticationError``，且 ``str(e)`` 不含 key 原文。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn`` 的 ``on_provider_error``
      决策树（时机：provider_gen() 内 Provider 调用抛异常）；``flowing.composables.retry`` 默认 handler（时机：每次 ``on_provider_error``
      dispatch——``NON_RETRYABLE_ERRORS`` 成员，对本类直接放行不写
      ``can_continue``，composables/retry.pyi:180）
    - 实例化方：Provider adapter（时机：Provider 返回 401 / 403 凭证
      错误）

    .. seealso::

        :class:`flowing.errors.MissingEnvironmentVariableError`
            凭证引用的环境变量缺失（加载期）的对偶异常。
        :class:`flowing.errors.ProviderError`
    """


class InvalidRequestError(ProviderError):
    """请求本身非法（HTTP 400，不可重试）。

    .. rubric:: 功能介绍

    请求参数违反 API 约束（非法字段、不支持的参数组合等）。重试必然重现
    同样失败，分类为不可重试。

    .. rubric:: 设计动机

    与 ``ContextLengthError`` 同为「请求构造问题」，但后者因「必然重现 + 需要
    上下文手术」被框架硬编码为绕过 ``on_provider_error``；本类保留在决策树内，
    由策略层决定（默认不写 ``can_continue`` 即终止）。

    .. rubric:: 行为规约

    - 五要素：字段继承 ``provider`` / ``model``；抛出时机为 Provider 返回
      400；调用方走 ``on_provider_error``；不可重试（默认）；分类属机制。
    - 边缘情况：模型能力不兼容（如无视觉能力的模型收到图片）在 API 调用时
      以此类报错——框架核心不做能力预校验（最小行为约定）。
    - 测试案例：前置：构造非法参数的请求；操作：``provider_gen()``；期望：抛
      ``InvalidRequestError`` 且经过 ``on_provider_error``。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn`` 的 ``on_provider_error``
      决策树（时机：provider_gen() 内 Provider 调用抛异常）；``flowing.composables.retry`` 默认 handler（时机：每次 ``on_provider_error``
      dispatch——``NON_RETRYABLE_ERRORS`` 成员，composables/retry.pyi:181）
    - 实例化方：Provider adapter（时机：Provider 返回 400 请求非法）

    .. seealso::

        :class:`flowing.errors.ContextLengthError`
        :class:`flowing.errors.ProviderError`
    """


class ContentPolicyError(ProviderError):
    """内容安全策略拒绝（不可重试）。

    .. rubric:: 功能介绍

    Provider 侧内容审查（输入或输出触发安全策略）拒绝生成时抛出。

    .. rubric:: 设计动机

    内容拒绝与限流 / 故障语义完全不同：不是暂时不可用，而是「这个内容不行」；
    应用层通常需要改写输入或告知用户，独立分类便于策略层区分处理。

    .. rubric:: 行为规约

    - 五要素：字段继承 ``provider`` / ``model``；抛出时机为 Provider 内容
      审查拒绝；调用方走 ``on_provider_error``；不可重试（默认——改写输入是
      应用层决策，非框架重试范畴）；分类属机制。
    - 测试案例：前置：触发审查的输入；操作：``provider_gen()``；期望：抛
      ``ContentPolicyError``，默认路径下逻辑 Turn 静默终止。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn`` 的 ``on_provider_error``
      决策树（时机：provider_gen() 内 Provider 调用抛异常）；``flowing.composables.retry`` 默认 handler（时机：每次 ``on_provider_error``
      dispatch——``NON_RETRYABLE_ERRORS`` 成员，composables/retry.pyi:182）
    - 实例化方：Provider adapter（时机：Provider 内容安全审查拒绝生成）

    .. seealso::

        :class:`flowing.errors.ProviderError`
    """


class MissingEnvironmentVariableError(ProviderError):
    """Provider 条目加载时 ``{{env.VAR}}`` 引用的环境变量不存在。

    .. rubric:: 功能介绍

    ``providers.yaml`` 中的 ``{{env.VAR}}`` 是纯字符串替换（非 Jinja2 /
    Parsable），在条目**加载时**一次性求值；变量缺失即在加载时报本异常。
    与调用期异常不在同一时序，不经过 ``on_provider_error``。

    .. rubric:: 设计动机

    凭证引用是静态替换、总在启动时一次完成；缺失时 fail fast 优于「运行到
    第一次调用才 401」。非 ``{{env.`` 前缀的 ``{{`` 保持原样（不报错不替换），
    不触发本异常。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # ~/.config/flowing/providers.yaml
        deepseek-team:
          adapter: deepseek
          api_key: "{{env.DEEPSEEK_TEAM_KEY}}"   # 变量缺失 → 加载时抛出

    .. rubric:: 行为规约

    - 五要素：字段 ``var_name`` / ``entry``（Provider 条目名）；抛出时机为
      providers.yaml 条目加载；调用方不 catch（部署错误，修正环境后重启）；
      不可重试；属机制。
    - 边缘情况：``ModelConfig`` 字段中的环境变量引用走 Parsable 运行时求值
      （每次 ``provider_gen()`` 前），其失败按普通求值异常处理，**不**属本类。
    - 测试案例：前置：条目含 ``{{env.X}}`` 且环境中无 ``X``；操作：启动
      Runtime 加载 providers.yaml；期望：抛 ``MissingEnvironmentVariableError``
      且 ``e.var_name == "X"``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（加载期 fail fast，不经 ``on_provider_error``，与调用期
      异常不在同一时序）
    - 实例化方：``flowing.providers.provider`` 的 providers.yaml 条目加载
      路径（:func:`flowing.providers.provider.load_provider_candidates`；
      时机：``{{env.VAR}}`` 字符串替换时变量缺失）

    .. seealso::

        :class:`flowing.errors.AuthenticationError`
        :class:`flowing.errors.ProviderError`
        :class:`flowing.parsable.Parsable`
    """

    var_name: str
    """缺失的环境变量名（不含 ``{{env.`` 前缀）。
    
    .. seealso:: :class:`flowing.errors.MissingEnvironmentVariableError`
    """
    entry: str
    """引用该变量的 Provider 条目名。
    
    .. seealso:: :class:`flowing.errors.MissingEnvironmentVariableError`
    """

    def __init__(self, var_name: str, entry: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.providers.provider`` providers.yaml 条目加载路径
          （:func:`flowing.providers.provider.load_provider_candidates`）
          的 raise（时机：``{{env.VAR}}`` 替换时变量缺失）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Missing environment variable {var_name!r} referenced by provider entry {entry!r}")
        self.var_name = var_name
        self.entry = entry


class ProviderNameConflictError(ProviderError):
    """Provider adapter 规范名重名注册（未指定 ``override=True``）。

    .. rubric:: 功能介绍

    ``register_provider()`` 检测到注册表键（adapter 的 ``name`` 类属性）
    已存在且未声明覆盖时抛出。发生在 **import 期 / Runtime 初始化的
    自动发现阶段**，与调用期异常不在同一时序，不经 ``on_provider_error``。

    .. rubric:: 设计动机

    adapter 注册表是进程级全局表，第三方包经 ``FLOWING_PROVIDER_MODULES``
    / ``flowing_provider_*`` 自动发现汇入——两个包撞同名 adapter 是真实
    可发生的集成情形（非纯作者笔误），需要可精确 ``except`` 的具名类型；
    冲突家族与 :class:`ToolNameConflictError` /
    :class:`EntryNameConflictError` 同构（P3-14 裁决补齐）。显式
    ``override=True`` 是唯一合法覆盖通道（后 import 者胜出并产生警告）。

    .. rubric:: 行为规约

    - 五要素：字段 ``name``（冲突的 adapter 规范名）；抛出时机为
      ``register_provider`` 装饰器执行（import 期）；调用方不 catch
      （部署/集成错误，改名或显式 override 后重来）；不可重试；属机制。
    - 基类通用字段 ``provider`` / ``model`` 无上下文可填（注册期无
      条目）；冲突名由 ``name`` 承载。
    - 与 :class:`ToolNameConflictError` 的分层：本类管**进程级 adapter
      注册表**（有 override 通道）；工具注册表在 Runtime 实例内、无
      override 通道（重注册无正当场景）。
    - 作者笔误（装饰到非 ``Provider`` 子类 / 缺 ``name`` 的对象）**不走
      本类**——刻意抛内置 ``ValueError``（见模块 docstring 豁免声明）。
    - 测试案例：前置：已注册 ``name="deepseek"``；操作：
      ``@register_provider`` 注册另一个同名类；期望：抛
      ``ProviderNameConflictError`` 且 ``e.name == "deepseek"``，原注册
      保持不变。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（import 期 fail fast；测试可精确断言）
    - 实例化方：``flowing.providers.provider.register_provider`` 的
      内部 ``_register``（时机：键已存在且 ``override=False``）

    .. seealso::

        :class:`flowing.errors.ProviderError`
        :class:`flowing.errors.ToolNameConflictError`
        :class:`flowing.errors.EntryNameConflictError`
    """

    name: str
    """发生冲突的 adapter 规范名（``Provider.name`` 类属性）。

    .. seealso:: :class:`flowing.errors.ProviderNameConflictError`
    """

    def __init__(self, name: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``register_provider`` 的内部 ``_register``（时机：同名
          冲突且未 override）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(
            f"Provider adapter name conflict: {name!r} "
            "(use override=True to replace)"
        )
        self.name = name


# ---------------------------------------------------------------------------
# 依赖类
# ---------------------------------------------------------------------------


class DependencyError(FlowingError):
    """插件依赖校验失败（``mount()`` 单点校验）。

    .. rubric:: 功能介绍

    ``Runtime.use()`` 时的 ``_check_dependencies()`` 检测到依赖成环（依赖缺失只警告不抛，R9）
    （声明的插件未 ``use()``）或依赖图成环时抛出。取代旧设计
    ``MissingPluginError``（实参顺序即依赖保障时代的报错，已废弃）。

    .. rubric:: 设计动机

    插件按 R4 只**声明** ``dependencies = [...]``，检查是框架的单点职责；
    校验放在 mount（「初始化 → 运行」边界）而非每次 ``use()`` 收尾——
    ``use()`` 可分批，提前校验会误报。

    .. rubric:: 使用示例

    .. code-block:: python

        class GuardrailPlugin(Plugin):
            dependencies = ["flowing.comm"]          # R4：只声明

        runtime.use(GuardrailPlugin())                # 未装 CommPlugin
        await runtime.mount("@/root.fya")             # → 抛出 DependencyError

    .. rubric:: 行为规约

    - 五要素：字段 ``plugin``（发起声明的插件名）与 ``missing``（缺失的依赖
      名列表；成环时为环上的依赖名列表）；抛出时机为 ``mount()``；调用方不
      catch（安装期错误）；不可重试；属机制。
    - 边缘情况：运行时动态场景的 inject 失败不走本类，走
      ``MissingProvideError``（静态校验与运行时兜底两层互补）。
    - 测试案例：前置：插件声明依赖未安装；操作：``mount()``；期望：抛
      ``DependencyError`` 且 ``e.missing == ["flowing.comm"]``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（安装期错误，调用方不 catch；``mount()`` 失败不
      创建任何节点）
    - 实例化方：``flowing.runtime.Runtime._check_dependencies``
      （时机：``mount()`` 开头单点校验——依赖缺失或依赖图成环，
      runtime.pyi:774/1732-1739）

    .. seealso::

        :meth:`flowing.runtime.Runtime.mount`
        :class:`flowing.errors.MissingProvideError`
    """

    plugin: str
    """声明依赖的插件名。
    
    .. seealso:: :class:`flowing.errors.DependencyError`
    """
    missing: list[str]
    """缺失（或成环）的依赖名列表。
    
    .. seealso:: :class:`flowing.errors.DependencyError`
    """

    def __init__(self, plugin: str, missing: list[str]) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.runtime.Runtime._check_dependencies`` 的
          raise（时机：``mount()`` 开头校验发现依赖缺失或成环，
          runtime.pyi:1732-1739）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Plugin {plugin!r} has unresolved dependencies: {missing}")
        self.plugin = plugin
        self.missing = missing


# ---------------------------------------------------------------------------
# 通信类（CommPlugin）
# ---------------------------------------------------------------------------


class CommError(FlowingError):
    """通信扩展（CommPlugin 总线）异常中间层。

    .. rubric:: 功能介绍

    单进程内通信总线（端点注册、信号投递、请求-回复）相关异常的共同基类。
    总线是 fire-and-forget 语义的基础设施；``publish`` 对订阅者异常静默
    容错，不产出本层异常。

    .. rubric:: 设计动机

    通信是内置扩展（必须显式 ``runtime.use(CommPlugin())`` 才存在），但其
    异常类型属于框架统一层次——未启用扩展时这些类型只是不被实例化。

    .. rubric:: 行为规约

    - 中间层，不直接实例化抛出。
    - 非行为：通信消息永不进 LLM context；通信层错误不转为 EVENT 消息。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（框架内无按本类捕获点，时机：未见规约）
    - 实例化方：``无``（中间层，不直接实例化抛出；由
      ``DuplicateEndpointError`` / ``SignalDeliveryError`` /
      ``SignalTimeoutError`` 承载）

    .. seealso::

        :class:`flowing.plugins.comm.Communication`
        :class:`flowing.plugins.comm.CommHandle`
        :class:`flowing.plugins.comm.CommPlugin`
    """


class DuplicateEndpointError(CommError):
    """通信端点 ID 重复注册（注册不幂等）。

    .. rubric:: 功能介绍

    ``Communication.register_endpoint(endpoint_id, handler)`` 检测到端点 ID
    已存在时抛出。端点 ID 语义化命名规则为 ``use_comm(name=...)`` 显式
    指定、缺省回退 ``agent.node_id``（``simplename`` 已删除，A15 裁决）
    （可加用途后缀，如 ``f"{agent.node_id}-guardrail"``）。

    .. rubric:: 设计动机

    端点 ID 是路由唯一键；静默覆盖会让旧 handler 的信号凭空消失。注销
    不存在端点则走自然 ``KeyError``（不特殊处理、不包装）。

    .. rubric:: 使用示例

    .. code-block:: python

        comm.register_endpoint("payment-guard", handler)
        comm.register_endpoint("payment-guard", other_handler)   # 抛出

    .. rubric:: 行为规约

    - 五要素：字段 ``endpoint_id``；抛出时机为端点注册；调用方不 catch
      （编程错误）；不可重试；属机制。
    - 测试案例：前置：已注册 ``"x"``；操作：同名再注册；期望：抛
      ``DuplicateEndpointError``，原 handler 不被替换。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（编程错误，调用方不 catch）
    - 实例化方：``flowing.plugins.comm.Communication.register_endpoint``
      （时机：端点 ID 已存在，plugins/comm.pyi:366）；
      ``flowing.plugins.comm.CommHandle`` 构造（时机：attach 构造句柄时
      端点 ID 冲突，plugins/comm.pyi:442）

    .. seealso::

        :meth:`flowing.plugins.comm.Communication.register_endpoint`
        :class:`flowing.errors.CommError`
    """

    endpoint_id: str
    """发生冲突的端点 ID。
    
    .. seealso:: :class:`flowing.errors.DuplicateEndpointError`
    """

    def __init__(self, endpoint_id: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.plugins.comm.Communication.register_endpoint``
          / ``CommHandle`` 构造的 raise（时机：端点 ID 已存在，
          plugins/comm.pyi:366/442）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Duplicate endpoint id: {endpoint_id!r}")
        self.endpoint_id = endpoint_id


class SignalDeliveryError(CommError):
    """信号目标端点不存在。

    .. rubric:: 功能介绍

    ``Communication.send()`` / ``request()`` 查端点表路由失败时抛出。
    ``send`` 是「立即返回」语义——路由失败属于同步可判定错误，当场抛出。

    .. rubric:: 设计动机

    点对点信号的投递失败是调用方可修正的错误（端点未注册、ID 拼错）；
    与 ``publish`` 的广播容错（单订阅者异常静默忽略）形成有意的语义对比。

    .. rubric:: 行为规约

    - 五要素：字段 ``target`` / ``signal_type``；抛出时机为 send/request
      路由；调用方可 catch（如降级为日志）；是否重试由应用层决定（框架
      不重试）；属机制。
    - 测试案例：前置：无端点 ``"ui-main"``；操作：``send(target="ui-main", ...)``；
      期望：抛 ``SignalDeliveryError`` 且 ``e.target == "ui-main"``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（调用方可 catch 降级为日志等；框架不重试，时机：
      未见规约的固定捕获点）
    - 实例化方：``flowing.plugins.comm.Communication.send`` /
      ``request``（时机：路由查找 target 端点不存在——发送阶段当场
      抛出，plugins/comm.pyi:485/553）；
      ``flowing.plugins.comm.CommHandle.send`` / ``request``（时机：
      同上，plugins/comm.pyi:823/873）

    .. seealso::

        :meth:`flowing.plugins.comm.Communication.send`
        :class:`flowing.errors.SignalTimeoutError`
        :class:`flowing.errors.CommError`
    """

    target: str
    """未命中的目标端点 ID。
    
    .. seealso:: :class:`flowing.errors.SignalDeliveryError`
    """
    signal_type: str
    """投递失败的信号类型（信封 ``type`` 字段）。
    
    .. seealso:: :class:`flowing.errors.SignalDeliveryError`
    """

    def __init__(self, target: str, signal_type: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.plugins.comm.Communication.send`` / ``request``
          与 ``CommHandle.send`` / ``request`` 的 raise（时机：target
          端点不存在，plugins/comm.pyi:485/553/823/873）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Signal delivery failed: target endpoint {target!r} not found (type={signal_type!r})")
        self.target = target
        self.signal_type = signal_type


class SignalTimeoutError(CommError):
    """``request()`` 等待回复超时。

    .. rubric:: 功能介绍

    请求-回复模式下，``correlation_id`` 匹配的回复在 ``timeout`` 内未到达
    时抛出，pending future 随之清理。

    .. rubric:: 设计动机

    审批等交互场景的「超时」是正常业务分支（典型用法：审批超时 →
    ``raise Intercepted("审批超时")`` 阻断工具调用），需要类型化的异常
    供 handler 捕获，而非裸 ``asyncio.TimeoutError``。

    .. rubric:: 使用示例

    .. code-block:: python

        try:
            result = await agent.comm_handler.request(
                target="ui-main", type="permission_request",
                payload={"tool_name": tool_call.name}, timeout=120.0,
            )
        except SignalTimeoutError:
            raise Intercepted("审批超时")

    .. rubric:: 行为规约

    - 五要素：字段 ``target`` / ``timeout``；抛出时机为 request 超时；
      调用方通常 catch（业务分支）；是否重试由应用层决定；属机制。
    - 边缘情况：``CommHandle.destroy()`` 后 pending ``request()`` 的 await
      收到的是 ``asyncio.CancelledError``（内置），不是本类。
    - 测试案例：前置：目标端点永不回复；操作：``request(timeout=0.05)``；
      期望：约 0.05s 后抛 ``SignalTimeoutError``，无泄漏的 pending future。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（业务分支由调用方 catch；典型用法是 handler 内
      catch 后 ``raise Intercepted("审批超时")``——属用户 / 扩展代码）
    - 实例化方：``flowing.plugins.comm.Communication.request`` /
      ``flowing.plugins.comm.CommHandle.request``（时机：
      ``correlation_id`` 匹配的回复在 ``timeout`` 内未到达，
      plugins/comm.pyi:554/874）

    .. seealso::

        :meth:`flowing.plugins.comm.CommHandle.request`
        :class:`flowing.errors.Intercepted`
        :class:`flowing.errors.CommError`
    """

    target: str
    """请求的目标端点 ID。
    
    .. seealso:: :class:`flowing.errors.SignalTimeoutError`
    """
    timeout: float
    """实际使用的超时秒数。
    
    .. seealso:: :class:`flowing.errors.SignalTimeoutError`
    """

    def __init__(self, target: str, timeout: float) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.plugins.comm.Communication.request`` /
          ``CommHandle.request`` 的 raise（时机：等待回复超过
          ``timeout``，plugins/comm.pyi:554/874）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Signal request to {target!r} timed out after {timeout}s")
        self.target = target
        self.timeout = timeout


# ---------------------------------------------------------------------------
# 文件格式类
# ---------------------------------------------------------------------------


class FormatError(FlowingError):
    """声明式文件格式与求值异常中间层。

    .. rubric:: 功能介绍

    ``.fya`` 声明解析、创建管线 PENDING 检查、Parsable 求值上下文、保留属性
    名等「声明与格式」层异常的共同基类。

    .. rubric:: 设计动机

    声明式与命令式两种 Agent 形式生成完全相同的 Python 类模型，格式错误
    必须在类生成 / 实例化早期暴露，归类为一层便于 tooling（如
    ``flowing compile``）统一报告。

    .. rubric:: 行为规约

    - 中间层，不直接实例化抛出。
    - 非行为：YAML 与具名块同字段冲突等解析期报错的具体类型在本层内
      由实现细化，初版仅约定归入 ``FormatError`` 层次。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（框架内无按本类捕获点，时机：未见规约）
    - 实例化方：中间层，行为规约声明「不直接实例化抛出」；
      ``flowing.plugins.skills`` 技能定义加载路径声明
      ``:raises flowing.errors.FormatError:``（时机：定义文件存在但缺少
      必填字段，plugins/skills.pyi:535）——与「中间层不直接实例化」
      规约的关系见 facts/errors.md 存疑记录

    .. seealso::

        :class:`flowing.parsable.Parsable`
        :meth:`flowing.runtime.Runtime.create_agent`
    """


class MissingFieldError(FormatError):
    """创建管线 PENDING 检查点仍有未赋值字段。

    .. rubric:: 功能介绍

    ``.fya`` 中以 ``_`` 标记的延迟定义字段解析为 ``PENDING`` 哨兵；
    ``setup()`` 结束后、``after_create`` 钩子前的 PENDING 检查点发现仍为
    ``PENDING`` 的字段（典型：唯一必填的类属性 ``system_prompt`` 未赋值）
    时抛出。

    .. rubric:: 设计动机

    PENDING 是「延迟定义承诺」——声明层允许先占位，但管线必须在固定检查点
    兑现承诺；缺失时 fail fast 优于带着 ``None`` 的 system prompt 进入
    Turn 循环。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # payment.fya——system_prompt 未赋值（PENDING 检查失败 → 抛出）
        # system_prompt: _  标记后 setup() 中必须 self.system_prompt = ...

    .. rubric:: 行为规约

    - 五要素：字段 ``field``（未赋值字段名）与 ``agent_type``（Agent 类型名）；
      抛出时机为创建管线 PENDING 检查点（恢复管线同样检查）；调用方不
      catch（声明 / setup 实现错误）；不可重试；属机制。
    - 边缘情况：``PENDING`` 与 ``_UNSET`` 语义不同——``_UNSET`` 是「未设置」
      的参数默认值判定哨兵，不参与本检查。
    - 测试案例：前置：字段标记为 ``_`` 且 ``setup()`` 未赋值；操作：
      ``create_agent()``；期望：抛 ``MissingFieldError`` 且 ``e.field``
      等于该字段名。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（声明 / setup 实现错误，调用方不 catch，fail fast）
    - 实例化方：``flowing.runtime.Runtime.create_agent`` 创建管线
      PENDING 检查点（时机：``setup()`` 结束后、``after_create`` 钩子前
      仍有 ``PENDING`` 字段——恢复管线同样检查，agent.pyi:1514/1520）；
      ``flowing.parsable`` 的 PENDING 求值路径（时机：PENDING 检查点，
      parsable.pyi:448/452）

    .. seealso::

        :data:`flowing.parsable.PENDING`
        :meth:`flowing.runtime.Runtime.create_agent`
        :class:`flowing.errors.FormatError`
    """

    field: str
    """检查点处仍为 ``PENDING`` 的字段名。
    
    .. seealso:: :class:`flowing.errors.MissingFieldError`
    """
    agent_type: str
    """所属 Agent 的类型名（字符串类型名）。
    
    .. seealso:: :class:`flowing.errors.MissingFieldError`
    """

    def __init__(self, field: str, agent_type: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：创建管线 PENDING 检查点的 raise（时机：``setup()``
          结束后、``after_create`` 前仍有 ``PENDING`` 字段，
          agent.pyi:1514/1520、parsable.pyi:452）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Required field {field!r} of agent type {agent_type!r} is still PENDING")
        self.field = field
        self.agent_type = agent_type


class MissingContextError(FormatError):
    """未绑定实例的 Parsable 被强制求值。

    .. rubric:: 功能介绍

    类级别（``_instance=None``）或手动创建未绑定 Agent 实例的 ``Parsable``
    调用 ``str()``（触发自动 ``resolve()``）时抛出——求值需要实例属性 /
    env / config 渲染上下文，无绑定则上下文缺失。

    .. rubric:: 设计动机

    Parsable 的求值面契约：求值面内（框架在 ``_assemble_context()`` 等固定
    时机对绑定实例求值）自动；求值面外（用户手动 ``str()`` 类属性）必须
    显式 ``resolve(context)`` 或先绑定。隐式返回原始模板会让 prompt 里出现
    未渲染的 ``{{ }}``，比报错更难排查。

    .. rubric:: 使用示例

    .. code-block:: python

        class MyAgent(Agent):
            system_prompt = Parsable("你好，{{ user_name }}")

        str(MyAgent.system_prompt)                 # 未绑定 → 抛出
        MyAgent.system_prompt.resolve({"user_name": "甲"})   # 显式上下文 → 正常

    .. rubric:: 行为规约

    - 五要素：无自定义字段；抛出时机为未绑定 Parsable 的 ``resolve()`` /``resolved``（``__str__`` 只展示模板源，不求值不抛错）；
      调用方不 catch（用法错误，改用显式 ``resolve(context)``）；不可重试；
      属机制。
    - 边缘情况：``repr()`` 始终显示原始模板（``Parsable(source=..., type=...)``），
      不触发求值、不抛本异常。
    - 测试案例：前置：未绑定 Parsable；操作：``str(p)``；期望：抛
      ``MissingContextError``；``repr(p)`` 正常返回。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（用法错误，调用方不 catch——改用显式
      ``resolve(context)``）
    - 实例化方：``flowing.parsable.Parsable.resolved`` 及无参
      ``resolve()``（时机：未绑定实例的 Parsable 被强制求值，
      parsable.pyi:764/810/822/859）

    .. seealso::

        :class:`flowing.parsable.Parsable`
        :class:`flowing.errors.FormatError`
    """

    def __init__(self) -> None:
        # 无字段叶子：固定英文提示消息（X14 澄清）
        super().__init__(
            "Parsable is not bound to an instance: call resolve(context) "
            "with an explicit context instead"
        )


class ReservedAttributeError(FormatError):
    """Agent 实例属性命名为框架保留名（``env`` / ``config`` / ``agent`` / ``self``）。

    .. rubric:: 功能介绍

    Parsable 渲染上下文的框架注入名 ``env``（绑定 ``os.environ``）、
    ``config``（Runtime 配置）、``agent`` / ``self``（实例自身入口）
    由框架注入；Agent 实例属性占用这些名字会覆盖注入值，框架检测到即抛出。

    .. rubric:: 设计动机

    保留名冲突是静默错误的高发源（模板里 ``{{ env.HOME }}`` 突然读到了
    实例属性）；显式保留名单 + 定义期检测比「合并时谁覆盖谁」的隐式规则
    更清晰。

    .. rubric:: 行为规约

    - 五要素：字段 ``name``（被占用的保留名）；抛出时机为 Agent 类生成 /
      实例化时的属性检查；调用方不 catch（声明错误）；不可重试；属机制。
    - 测试案例：前置：``setup()`` 中 ``self.env = {...}``；操作：
      ``create_agent()``；期望：抛 ``ReservedAttributeError`` 且
      ``e.name == "env"``。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（声明错误，调用方不 catch）
    - 实例化方：``flowing.parsable`` 渲染上下文摊平检测路径（时机：
      Agent 类生成 / 实例化时检测到实例属性占用 ``env`` / ``config`` / ``agent`` / ``self``
      保留名，parsable.pyi:108/743/766/922）

    .. seealso::

        :class:`flowing.parsable.Parsable`
        :class:`flowing.errors.FormatError`
    """

    name: str
    """被占用的框架保留属性名（``"env"`` 或 ``"config"``）。
    
    .. seealso:: :class:`flowing.errors.ReservedAttributeError`
    """

    def __init__(self, name: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.parsable`` 渲染上下文摊平检测路径的 raise
          （时机：实例属性占用 ``env`` / ``config`` 保留名，
          parsable.pyi:743/766/922）
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Reserved attribute name occupied: {name!r}")
        self.name = name


class NameMismatchError(FormatError):
    """声明的 ``name`` 与按规则推断的名字不一致。

    .. rubric:: 功能介绍

    ``name`` 不是框架机制字段（Agent 的身份名一律由路径 / 注册名 / 类名
    推断，见 :class:`flowing.agent.Agent` 的 ``class_name``），但 ``.fya``
    与手写子类中**不禁止**用户写 ``name``——写了就作为一致性断言校验：
    与推断值不符即抛出本异常，消息同时给出声明值、推断值与来源位置。

    .. rubric:: 设计动机

    名实分离（文件名是 ``foo.fya``、内部自称 ``payment``）是排查困惑的
    高发源；不禁止声明是为了保留「自我描述」的可读性，但必须与推断一致，
    否则静默分叉会让 glob 展开结果与磁盘文件名对不上。

    .. rubric:: 行为规约

    - 五要素：字段 ``declared``（声明值）/ ``inferred``（推断值）/
      ``source``（来源：``.fya`` 路径或类限定名）；抛出时机为 Agent 类
      生成 / 解析期校验；调用方不 catch（声明错误）；不可重试；属机制。
    - 校验是**相等断言**，不是命名来源：声明与推断一致时无任何效果
      （名字仍然来自推断）。
    - 测试案例：前置：``foo.fya`` 内写 ``name: payment``（文件名单文件
      形态推断为 ``foo``）；操作：解析该 ``.fya``；期望：抛
      ``NameMismatchError`` 且 ``e.declared == "payment"``、
      ``e.inferred == "foo"``。

    .. rubric:: 调用关系（审计）

    - 调用：``无``（仅平行字段赋值）
    - 被调：``无``（声明错误，调用方不 catch）
    - 实例化方：三条定义期校验路径——``.fya`` Agent 解析层 / 手写 Agent
      子类定义期（Agent 身份名）；``TOOL.fya`` / ``ScriptTool`` 子类 /
      ``@flowing_tool`` 装饰器参数（Tool 规范名，见 :mod:`flowing.tool`）；
      ``.skill.fya`` / frontmatter（Skill 规范名，见
      :mod:`flowing.plugins.skills`）——时机均为类/对象生成时比对声明
      ``name`` 与推断名

    .. seealso::

        :class:`flowing.agent.Agent` —— ``class_name`` 与名字推断规则。
        :class:`flowing.errors.FormatError`
    """

    declared: str
    """用户声明的 ``name`` 值。"""

    inferred: str
    """按路径 / 类名规则推断出的名字。"""

    source: str
    """冲突来源（``.fya`` 的 ``@/`` 路径或手写子类的限定类名）。"""

    def __init__(self, declared: str, inferred: str, source: str) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``.fya`` 解析层 / 手写子类定义期校验路径的 raise
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Declared name {declared!r} does not match inferred name {inferred!r} (source: {source})")
        self.declared = declared
        self.inferred = inferred
        self.source = source


# ---------------------------------------------------------------------------
# 编译类（.fya 显式编译，构建期；S-04 裁决新增）
# ---------------------------------------------------------------------------


class CompileError(FlowingError):
    """``.fya`` 显式编译失败的中间层。

    .. rubric:: 功能介绍

    ``flowing compile`` / :mod:`flowing.compiler` 构建期异常的归属层，
    只承载归属、不附加行为（与 ``ConfigError`` 等中间层同约定）。

    .. rubric:: 调用关系（审计）

    - 被调：``无``
    - 实例化方：本子类由 ``flowing.compiler`` 编译管线 raise

    .. seealso:: :class:`ArtifactModifiedError`
    """


class ArtifactModifiedError(CompileError):
    """编译产物被外部修改——``py_hash`` 不匹配，报错中止、不静默覆盖。

    .. rubric:: 功能介绍

    显式编译的防覆盖闸：产物同目录 ``.flowing.meta.yaml`` 中记录的
    ``py_hash``（AST 口径，格式化改动不触发）与现有同目录 ``.py``
    的实际 AST hash 不一致 → 该产物被人手工改过，
    编译器**报错中止**而非覆盖（spec-draft 03 §9.2；P3-10 裁决改
    同目录 meta + AST 口径）。

    .. rubric:: 行为规约

    - 期待行为：消息含冲突文件路径；已产出的其他文件**不回滚**
      （编译幂等，重跑可继续）。
    - 测试案例：前置：手工改动某产物 ``.py`` → 操作：重新编译 →
      期望：抛本异常，该文件未被覆盖。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.interfaces.cli.cmd_compile`` 捕获映射为
      ``EXIT_RUNTIME_ERROR``（时机：hash 冲突中止）
    - 实例化方：``flowing.compiler.compile_fya_file`` 第 3 步 meta
      校验（时机：产物 hash 不匹配）
    """

    def __init__(self, path: "Path") -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``无``（仅平行字段赋值）
        - 被调：``flowing.compiler.compile_fya_file`` meta 校验路径
        """
        self.path = path
        super().__init__(f"编译产物被外部修改，拒绝覆盖：{path}")


# ---------------------------------------------------------------------------
# 持久化类（X6 / X7 澄清新增；persistence 为内部模块，不专设中间层，直挂根）
# ---------------------------------------------------------------------------


class FormatVersionError(FlowingError):
    """jsonl 持久化文件的格式版本不受支持（X6 澄清新增的具名类型）。

    .. rubric:: 功能介绍

    ``RecordStore.replay`` 判读首行 ``{"type": "meta", "format_version"}``
    时抛出：文件版本高于当前框架支持的 :data:`flowing.persistence.FORMAT_VERSION`
    （文件比框架新，静默读是数据风险）。无版本首行的存量
    文件按版本 0 处理，不抛本异常（v0 与 v1 行格式相同，见 X5 澄清）。

    .. rubric:: 设计动机

    版本冲突是部署/降级事故（用旧框架读新文件），必须 fail fast 且类型可
    精确 ``except``；与 ``FormatError``（声明式 ``.fya`` 格式）分层——
    jsonl 持久化文件不属声明式资源，故直挂根。

    .. rubric:: 行为规约

    - 五要素：字段 ``path`` / ``found``（文件声明的版本）/ ``supported``
      （框架当前支持版本）；抛出时机为 ``replay`` 开头版本判读；调用方不
      catch（部署错误）；不可重试；属机制。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（框架内无捕获点，fail fast）
    - 实例化方：``flowing.persistence.FileRecordStore.replay``（时机：
      首行版本判读发现不受支持版本）
    """

    path: Path
    """版本不受支持的持久化文件路径。"""
    found: int
    """文件首行声明的格式版本号。"""
    supported: int
    """框架当前支持的格式版本号（``FORMAT_VERSION``）。"""

    def __init__(self, path: Path, found: int, supported: int) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``Exception.__init__``（传递面向人读的消息）
        - 被调：``FileRecordStore.replay`` 版本判读的 raise
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(
            f"Unsupported format version {found} in {path} "
            f"(this framework supports up to {supported})"
        )
        self.path = path
        self.found = found
        self.supported = supported


class CorruptionError(FlowingError):
    """jsonl 持久化文件中间行损坏——报警不容忍（X7 澄清新增的具名类型）。

    .. rubric:: 功能介绍

    ``RecordStore.replay`` 按行解析时发现**中间行**（非撕裂末行）JSON 损坏
    即抛出。撕裂末行（崩溃半截写产物，无换行结尾）是合法容忍路径，截断
    丢弃不抛本异常；中间行损坏意味着已提交数据受损，属事故而非正常窗口。

    .. rubric:: 设计动机

    「中间行损坏报警不容忍」是持久化层一贯约定（见
    :mod:`flowing.persistence` 模块 docstring）；具名类型使调用方 /
    工具层能精确区分「版本问题」（:class:`FormatVersionError`）与
    「数据损坏」（本类）。

    .. rubric:: 行为规约

    - 五要素：字段 ``path`` / ``lineno``（1 基行号）；抛出时机为
      ``replay`` 逐行解析；调用方不 catch（数据事故，需人工介入）；
      不可重试；属机制。

    .. rubric:: 调用关系（审计）

    - 被调：``无``（框架内无捕获点，fail fast）
    - 实例化方：``flowing.persistence.FileRecordStore.replay``（时机：
      中间行 JSON 解析失败，抛出前 ``logging.error`` 记录 path:lineno）
    """

    path: Path
    """损坏行所在的持久化文件路径。"""
    lineno: int
    """损坏行的 1 基行号。"""

    def __init__(self, path: Path, lineno: int) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``Exception.__init__``（传递面向人读的消息）
        - 被调：``FileRecordStore.replay`` 逐行解析的 raise
        """
        # 消息为自然语言关键提示（X14 澄清：英文短语），结构化字段为权威
        super().__init__(f"Corrupted record line at {path}:{lineno}")
        self.path = path
        self.lineno = lineno


# ---------------------------------------------------------------------------
# 信号类
# ---------------------------------------------------------------------------


class Intercepted(Exception):
    """钩子 handler 的有意硬阻断信号。**刻意不继承 ``FlowingError``。**

    .. rubric:: 功能介绍

    handler 三种合法出口之一（返回 value / 设 ``shortcut`` 短路 /
    ``raise Intercepted``）。dispatch 对本信号 catch 后**原样重抛**：
    链停止、后续 handler 不执行、对应 ``after_`` 钩子不触发（整个操作
    标记无效）；不当作错误处理（INFO 级日志，非 ERROR）。

    .. rubric:: 设计动机

    框架只区分「有意的阻止」（审批拒绝 / 安全阻断 / 权限检查——正常业务流）
    与「意外错误」（普通异常，直接上抛）。``Intercepted`` 承载前者；刻意
    不挂在 ``FlowingError`` 下，避免泛化的 ``except FlowingError`` 误吞
    阻断信号。与 ``shortcut`` 的边界：shortcut 是协商式替代（``after_``
    照常触发），``Intercepted`` 是硬阻断（``after_`` 不触发）。

    .. rubric:: 三个使用场景

    1. **安全扫描（``before_turn``）**：Guardrail 扫描用户消息发现威胁，
       阻断整个逻辑 Turn 的启动。
    2. **工具审批（``before_tool_call``）**：``await approval.request(...)``
       在 handler 内暂停等待用户；用户拒绝时抛出——审批策略在 handler 内
       实现，不需要框架级 interrupt 机制。
    3. **权限检查（``before_tool_call``）**：RBAC 判定当前身份无权调用
       该工具，阻断执行。

    扩展声明的钩子点同理可用（如 SkillPlugin 的 ``before_skill_load``
    阻止 Skill 加载；``before_cancel`` 中抛出可阻止取消——「支付已提交」
    类关键事务保护）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.errors import Intercepted

        async def request_approval(agent, tool_call):
            entry = agent._tool_entries[tool_call.name]      # 仅按别名查找
            tool = agent.runtime.tool_registry.get(entry.name_ori)
            if not getattr(tool, "requires_approval", False):
                return tool_call
            response = await approval.request(tool_call)
            if response.action == "deny":
                raise Intercepted(
                    "用户拒绝",
                    payload={"tool": tool_call.name, "denied_by": approval.current_user},
                )
            return response.modified_tool_call

        async def setup(self):
            self.hooks.before_tool_call(request_approval, by="approval", tags=["security"])

    对应的 ``.tool.fya``（``requires_approval`` 为非保留字段，框架不解析，
    直接成为 tool 对象属性，由上面的 handler 读取）::

        type: script
        callable: ./ops.py::delete_file
        requires_approval: true

    .. rubric:: 行为规约

    - 五要素：字段 ``reason`` / ``payload``；抛出时机为任意钩子 handler 内
      （含 ``await`` 暂停后的判定分支）；调用方：dispatch 层 catch 后重抛，
      ``tool_call()`` 路径将其转换为 ``ToolResult.blocked(...)``（LLM 可见
      TOOL 消息 ``content=[TextBlock(reason)]``、``tool_status="blocked"``）；
      与重试无关（正常业务流）；
      机制（信号语义与 dispatch 规则）属核心，**什么该被阻断**的策略不内置。
    - 非行为：不触发任何错误钩子；不进入 ``on_provider_error`` 决策树；
      ``payload`` 内容框架不解释（给审计 / 日志消费）。
    - 边缘情况：在「无对应工具调用」的钩子点（如 ``before_turn``）抛出时，
      由该操作的发起路径决定如何呈现，框架不保证统一的 LLM 可见形式。
    - 测试案例：前置：两个 ``before_tool_call`` handler，前者抛
      ``Intercepted``；操作：发起工具调用；期望：后者未执行、
      ``after_tool_call`` 未触发、LLM 收到 blocked 结果且 ``reason`` 一致。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.hooks.HookList.dispatch``（时机：每次
      dispatch——catch 后原样重抛，链停止、后续 handler 不执行，
      hooks.pyi:813）；``flowing.agent.Agent.tool_call`` 路径（时机：
      转换为 ``ToolResult.blocked(...)``，LLM 可见 blocked 结果，
      agent.pyi:2342，tool.pyi:303-323）
    - 实例化方：钩子 handler（审批 / 安全 / 权限策略代码——属用户与
      扩展代码；框架内仅示例性出现：plugins/comm.pyi:533/860/863、
      plugins/skills.pyi:575）

    .. seealso::

        :class:`flowing.hooks.HookList`
            dispatch 算法（改写链 / shortcut / Intercepted 重抛）。
        :class:`flowing.tool.ToolResult`
            ``ToolResult.blocked(...)`` 的载体。
    """

    reason: str
    """阻断原因（面向人读；``tool_call()`` 路径下进入 LLM 可见的 blocked 结果）。
    
    .. seealso:: :class:`flowing.errors.Intercepted`
    """
    payload: Any
    """结构化附加信息（如 ``{"tool": ..., "denied_by": ...}``），供审计 / 日志
    消费；框架不解释其内容。默认 ``None``。
    
    .. seealso:: :class:`flowing.errors.Intercepted`
    """

    def __init__(self, reason: str, payload: Any = None) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：``Exception.__init__``（时机：每次构造，传递面向人读的
          ``reason``——``tool_call()`` 路径下进入 LLM 可见的 blocked
          结果）
        - 被调：钩子 handler 内的 raise（时机：handler 判定硬阻断时——
          属用户 / 扩展代码；框架内仅示例性出现，plugins/comm.pyi:533/860）
        """
        super().__init__(reason)
        self.reason = reason
        self.payload = payload
