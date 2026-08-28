"""flowing._unstable.logging —— 钩子链路日志插件（LoggingPlugin / use_logging）。

.. rubric:: 模块定位

**实验性、自用 debug 辅助**（见 :mod:`flowing._unstable` 的稳定性承诺：
接口与落盘格式均不冻结）。功能范围是确定的：把 Agent 钩子链路的触发
事实按等级写入每 Agent 一个的 ``logging.jsonl``；**默认日志策略未定**
——记录哪些字段、摘要如何截断、是否做滚动，都是「先用起来再调」的
开放项，本文件只固定最小骨架。

与框架核心的关系：完全在核心之外，只复用公开/半公开机制——
``runtime.use()`` 安装、``agent.hooks`` 实例级钩子、
``Runtime.get_plugin()`` 插件探测。核心不感知本插件的存在。

.. rubric:: 双层启用（与 cron/comm 同构）

- 阶段一（Runtime 安装）：``runtime.use(LoggingPlugin(level="INFO"))``
  → ``install()`` 经 ``runtime.provide(logging_plugin_key, self)`` 提供
  自身，并**记录**此刻已安装的插件清单（skills/comm/cron/workflow
  探测在此时一次性完成，运行期不重复探测——R3：安装期后注册状态
  不变，探测结果天然稳定）。
- 阶段二（Agent 启用）：``setup()`` 中 ``use_logging(self)`` 经
  ``inject`` 取回插件实例，按当前等级挂 handler。未调用
  ``use_logging()`` 的 Agent 零开销、零输出。

.. rubric:: 等级语义（全局单档，非标准 logging 级别体系）

等级是**插件实例级**的全局开关（``LoggingPlugin.level``，运行期可改，
下一次钩子触发即生效——已挂的 handler 每次触发时读当前等级，不缓存）：

- ``"OFF"``：什么都不输出（handler 早退）。
- ``"INFO"``（默认）：**关键节点**——生命周期（setup/recover/destroy
  三对 6 条）、逻辑 turn 边界（``before_turn`` / ``after_turn``）、
  消息出入队（``after_enqueue`` / ``after_dequeue``）、LLM 调用边界
  （``before_provider_gen`` / ``after_provider_gen`` / ``on_provider_error``）、工具调用
  边界（``before_tool_call`` / ``after_tool_call``）、子 Agent
  （``before_subagent_invoke`` / ``after_subagent_invoke``）、取消与 fork。
  每条一行，value 只记摘要（类型名 + 标识字段，如 tool 名 / 消息 id）。
- ``"DEBUG"``：在 INFO 基础上，**该 Agent 实例上存在的全部钩子点**
  都输出（枚举 ``agent.hooks._hook_points``——内部 API，是本模块
  待在 ``_unstable`` 的原因之一），value 记 ``repr`` 截断（默认 500
  字符，构造参数可调）。流式增量 ``on_provider_delta`` 在 DEBUG 下
  同样逐条输出（高频，自用场景自负）。

.. rubric:: 插件探测

``install()`` 时经 ``runtime.get_plugin()`` 检测
``"skill"`` / ``"comm"`` / ``"cron"`` / ``"workflow"`` 四个内置扩展
是否已安装，结果记入 :attr:`LoggingPlugin.detected`。**语义只是
「白名单放行」**：被探测到的扩展的钩子点（``before_skill_load`` /
``after_skill_load`` / ``on_signal`` / ``on_event`` / ``on_cron_trigger``
/ workflow 声明的点）在该 Agent 已 ``declare`` 的前提下纳入 DEBUG
全集与 INFO 关键节点表（其中 ``on_cron_trigger`` / ``on_signal`` 属
INFO 级关键节点——这些 INFO 级扩展钩子点与 DEBUG 全集一样，延迟到
内部 ``after_create`` / ``after_recover`` handler 按白名单挂钩，
不在 INFO 立即挂钩清单内）；未安装的扩展对应点名不登记、不访问（避免对未
声明的点挂钩抛 :class:`flowing.errors.UnknownHookPointError`）。
某 Agent 未启用对应 ``use_*()`` 时该点不存在于其实例 hooks，枚举
自然跳过——探测只回答「能不能理」，不替 Agent 声明。

.. rubric:: 落盘

每个启用 Agent 一个文件：``<该 Agent session 目录>/logging.jsonl``
（session 目录 = ``tree.jsonl`` / ``state.jsonl`` 所在目录，由
``Runtime`` 持久化根 + ``agent_id`` 决定；本插件经内部路径解析取得，
同属 ``_unstable`` 待稳定面）。**append-only、一行一个 JSON object**::

    {"ts": "2026-08-18T06:47:56.565Z", "agent_id": "...", "level": "INFO",
     "hook": "before_tool_call", "value": "ToolCall(name='search', id='t1')",
     "handler_tag": null}

- 写入失败（磁盘满 / 目录被删）**静默降级**：打印一次 stderr 警告后
  该 Agent 后续不再尝试写盘——日志插件绝不能打断业务管线。
- 非行为：不做滚动 / 压缩 / 清理（策略未定，自用阶段手工处理）；
  不写入 ``tree.jsonl`` / ``state.jsonl``，不参与恢复；不写 Runtime
  级全局日志（Runtime 事件暂无需求，需要时加 ``runtime.log`` 再说）。
"""

from typing import Any, ClassVar, Literal, TextIO

from flowing.agent import Agent
from flowing.plugins import Plugin
from flowing.provide import ProvideNode
from flowing.runtime import Runtime

logging_plugin_key: str
"""use_logging 经 ``inject`` 取回 LoggingPlugin 实例的 provide key。
字符串值即 ``"logging:plugin"``。
"""

LogLevel = Literal["OFF", "INFO", "DEBUG"]
"""全局等级字面量。语义见模块 docstring「等级语义」。
"""


class LoggingPlugin(Plugin):
    """钩子链路日志插件：等级开关 + 插件探测 + 落盘器。

    .. rubric:: 功能介绍

    阶段一载体。持有全局等级与探测结果，``use_logging()`` 挂的
    handler 每次触发时回读本实例的当前等级。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.use(LoggingPlugin(level="DEBUG"))
        # Agent 侧：setup() 中 use_logging(self)
        # 运行期调级：
        runtime.get_plugin("logging").level = "INFO"

    .. rubric:: 行为规约

    - 不变量：``install()`` 遵守 R1（只注册：provide 自身 + 记录探测
      结果，不挂钩——钩子是 per-agent 的，属 ``use_logging`` 职责）。
    - ``level`` 运行期可写，写后下一次钩子触发即生效；非法值抛
      :class:`flowing.errors.FlowingError`。
    - 边缘情况：重复安装（``runtime.use(LoggingPlugin())`` 两次）→
      provide 同名 key 冲突，按框架既有规则报错。

    .. rubric:: 调用关系（审计）

    - 被调：无（框架内无直接调用方；实例经 ``Runtime.use()`` 消费）
    - 实例化方：无（框架内不实例化；用户代码
      ``runtime.use(LoggingPlugin(...))`` 构造传入）

    .. seealso:: :func:`use_logging`（阶段二）、模块 docstring 等级语义。
    """

    name: ClassVar[str] = "logging"
    namespace: ClassVar[str]  # = "logging"

    level: LogLevel
    """全局等级。运行期可改；handler 每次触发时回读，不缓存。
    """

    detected: frozenset[str]
    """install 时探测到的已安装内置扩展名集合（``"skill"`` 等的子集）。
    只读观测面，供 ``use_logging`` 与调试者查询。
    """

    max_value_repr: int
    """DEBUG 级 value ``repr`` 的截断长度（默认 500）。
    """

    def __init__(self, level: LogLevel = "INFO", *, max_value_repr: int = 500) -> None:
        """构造插件实例。

        :param level: 初始全局等级，默认 ``"INFO"``。
        :param max_value_repr: DEBUG 级 value 摘要的 repr 截断长度。

        .. rubric:: 调用关系（审计）

        - 调用：无（纯赋值构造，docstring 未见其它调用规约）
        - 被调：无（框架内无调用方；用户代码
          ``runtime.use(LoggingPlugin(...))`` 构造时调用）
        """
        self.level = level
        self.max_value_repr = max_value_repr
        # self.detected 由 install() 探测后写入（时序见模块 docstring）

    def install(self, runtime: Runtime) -> None:
        """provide 自身（``logging_plugin_key``）并一次性探测四个内置扩展。

        R1：只注册；不做任何 per-agent 挂钩。

        :param runtime: 当前安装的 Runtime。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.Runtime.provide()``（时机：阶段一
          install 内，provide 自身一次）；
          ``flowing.runtime.Runtime.get_plugin(..., strict=False)``
          （时机：install 内一次性探测 skills/comm/cron/workflow 四个
          内置扩展——探测须显式 ``strict=False``，默认形态未安装会抛
          ``KeyError``）
        - 被调：``flowing.runtime.Runtime.use()``（时机：阶段一每次
          安装本插件，恰好一次）
        """
        runtime.provide(logging_plugin_key, self)
        # 探测 skills/comm/cron/workflow 四扩展（有意探测 → 显式 strict=False），
        # 结果记入 self.detected（时序见模块 docstring）：
        for _name in ("skill", "comm", "cron", "workflow"):
            runtime.get_plugin(_name, strict=False)


def use_logging(agent: Agent) -> None:
    """阶段二启用：在 ``setup()`` 中调用，按插件当前等级为本 Agent 挂钩。

    同步函数——全身无 await 需求（inject 查表 + 钩子注册均为同步）；
    遵循「Composable 没有异步需求就不写 async」的范式裁决（M-91 追问）。

    .. rubric:: 功能介绍

    经 ``agent.inject(logging_plugin_key)`` 取回 :class:`LoggingPlugin`
    实例；未安装本插件时抛
    :class:`flowing.errors.MissingProvideError`（与未装 cron 调
    ``use_cron`` 同口径，不静默跳过）。

    .. rubric:: 挂钩策略

    - **INFO 关键节点**：立即挂到各核心钩子点（清单见模块 docstring），
      ``by="logging"``；handler 内早退判断当前等级。
    - **DEBUG 全集与插件钩子点**：延迟到一个内部 ``after_create``
      handler（``by="logging"``）中执行——此刻其它 ``use_*()`` 已
      ``declare`` 完各自钩子点，枚举 ``agent.hooks._hook_points``
      才能覆盖全集；同时消除「``use_logging`` 必须在其它 ``use_*``
      之后调用」的顺序约束。recover 管线同理由 ``after_recover``
      内部 handler 兜底重挂（幂等：重复挂同名 handler 去重）。
    - 所有 handler 为纯观察：不修改 value、不 ``raise Intercepted``、
      不 return；自身异常捕获后置为 stderr 警告（不打断管线）。

    .. rubric:: 行为规约

    - 边缘情况：``after_provider_gen`` 的 value（``ProviderResponse``）不携带
      ``usage``（单源化裁决），usage 概要以
      :attr:`flowing.message.Message.usage` 为准、不在摘要中复述；
      ``on_provider_error`` 记异常类型名；``before_enqueue`` 的
      ``Intercepted`` 拦截结果无从观察属已知限制（拦截发生在
      dispatch 内，观察点只见到「没触发 after_enqueue」）。
    - 非行为：不声明任何新钩子点；不写 state（无恢复义务）。

    :param agent: 启用日志的 Agent（``setup()`` 中的 ``self``）。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.agent.Agent.inject()``（时机：阶段二启用时经
      ``logging_plugin_key`` 取回插件实例）；
      ``flowing.hooks.HookList.__call__()``（时机：INFO 关键节点
      立即挂钩；DEBUG 全集与插件钩子点延迟到内部 ``after_create`` /
      ``after_recover`` handler 中挂钩，幂等去重）
    - 被调：无（框架内无调用方；应用层在 Agent ``setup()`` 中调用，
      属用户代码）

    .. seealso:: :class:`LoggingPlugin`、模块 docstring「落盘」。
    """
    # 经 inject 沿链上溯取回插件实例；未安装本插件时抛
    # flowing.errors.MissingProvideError（不静默跳过）
    plugin: LoggingPlugin = agent.inject(logging_plugin_key)
    # 落盘路径解析（session 目录 = tree.jsonl / state.jsonl 所在目录）属
    # 内部路径解析，规约未具名符号，按契约不虚构函数名（见模块 docstring「落盘」）

    async def _observe(agent: Agent, value: Any) -> None:
        # 纯观察 handler：每次触发回读 plugin.level 当前值（不缓存），
        # "OFF" 早退；INFO 记摘要（类型名 + 标识字段），DEBUG 记 repr
        # 截断 max_value_repr；追加 logging.jsonl 一行；写盘失败静默降级
        # （一次性 stderr 警告后不再尝试）；自身异常置 stderr 警告，不打断管线
        ...

    # INFO 关键节点立即挂钩（by="logging"，清单见模块 docstring「等级语义」；
    # handler 内早退判断当前等级）
    agent.hooks.before_create(_observe, by="logging")
    agent.hooks.after_create(_observe, by="logging")
    agent.hooks.before_recover(_observe, by="logging")
    agent.hooks.after_recover(_observe, by="logging")
    agent.hooks.before_destroy(_observe, by="logging")
    agent.hooks.after_destroy(_observe, by="logging")
    agent.hooks.before_turn(_observe, by="logging")
    agent.hooks.after_turn(_observe, by="logging")
    agent.hooks.after_enqueue(_observe, by="logging")
    agent.hooks.after_dequeue(_observe, by="logging")
    agent.hooks.before_provider_gen(_observe, by="logging")
    agent.hooks.after_provider_gen(_observe, by="logging")
    agent.hooks.on_provider_error(_observe, by="logging")
    agent.hooks.before_tool_call(_observe, by="logging")
    agent.hooks.after_tool_call(_observe, by="logging")
    agent.hooks.before_subagent_invoke(_observe, by="logging")
    agent.hooks.after_subagent_invoke(_observe, by="logging")
    agent.hooks.before_cancel(_observe, by="logging")
    agent.hooks.after_cancel(_observe, by="logging")
    agent.hooks.before_fork(_observe, by="logging")
    agent.hooks.after_fork(_observe, by="logging")

    async def _attach_debug(agent: Agent) -> None:
        # DEBUG 全集与插件钩子点的延迟挂钩：枚举 agent.hooks._hook_points
        # （内部 API），对该实例已存在的全部钩子点挂 _observe（幂等去重，
        # 重复挂同名 handler 去重）；detected 白名单放行的扩展钩子点
        # （before_skill_load / after_skill_load / on_signal / on_event /
        # on_cron_trigger / workflow 声明的点）在该 Agent 已 declare 的
        # 前提下纳入；on_provider_delta 在 DEBUG 下逐条输出
        ...

    # 延迟到 after_create / after_recover：此刻其它 use_*() 已 declare 完
    # 各自钩子点，枚举才能覆盖全集；recover 管线由 after_recover 兜底重挂
    agent.hooks.after_create(_attach_debug, by="logging")
    agent.hooks.after_recover(_attach_debug, by="logging")
