"""``flowing._unstable.logging`` —— 钩子链路日志插件（LoggingPlugin / use_logging）。

.. rubric:: 功能介绍

实验性、自用 debug 辅助（见 :mod:`flowing._unstable` 的稳定性承诺：
接口与落盘格式均不冻结）。功能范围是确定的：把 Agent 钩子链路的触发
事实按等级写入每 Agent 一个的 ``logging.jsonl``；默认日志策略未定
（记录哪些字段、摘要如何截断、是否滚动都是开放项，本文件只固定最小
骨架）。

完全在框架核心之外，只复用 ``runtime.use()`` 安装、``agent.hooks``
实例级钩子、``Runtime.get_plugin()`` 插件探测等公开或半公开机制，
核心不感知本插件的存在。

.. rubric:: 注册面清单

- 启用方式（双层启用，与内置扩展同构）：

  - 阶段一（Runtime 安装）：``runtime.use(LoggingPlugin(level="INFO"))``
    —— ``install()`` 经 ``runtime.provide(logging_plugin_key, self)``
    提供自身，并在安装期一次性探测四个内置扩展是否已装（运行期不
    重复探测）。重复安装同名插件按框架既有规则报错（一个 Runtime
    同时只装一个同名插件，见 :meth:`flowing.runtime.Runtime.use`）。
  - 阶段二（Agent 启用）：``setup()`` 中 ``use_logging(self)`` —— 经
    ``agent.inject(logging_plugin_key)`` 取回插件实例并按当前等级挂
    handler。未安装本插件时调用抛
    :class:`flowing.errors.MissingProvideError` （不静默跳过）。未调用
    ``use_logging()`` 的 Agent 零开销、零输出。

- 注册的资源：provide key ``logging_plugin_key`` （字符串
  ``"logging:plugin"``），provide 到 Runtime 根，消费方式为
  ``agent.inject(logging_plugin_key)`` 沿亲代链上溯查找；未安装时抛
  :class:`flowing.errors.MissingProvideError`。

- 声明的钩子点：无——本插件不声明任何新钩子点，只向既有钩子点挂
  观察 handler。

- 挂载的钩子：按等级向各钩子点挂纯观察 handler，统一 ``by="logging"``
  （整组可经 ``remove_by_owner("logging")`` 移除）：

  - INFO 关键节点：``use_logging`` 立即挂钩（21 点清单见「等级语义」）；
  - DEBUG 全集与扩展钩子点：延迟到内部 ``after_create`` /
    ``after_recover`` handler 中挂（此时其它 ``use_*()`` 已声明完各自
    钩子点，枚举才能覆盖全集；recover 由 ``after_recover`` 兜底重挂，
    幂等去重）。

- 作用域与副作用：只影响启用它的 Agent 实例——落盘到该 Agent 的
  session 目录；不写 ``tree.jsonl`` / ``state.jsonl``，不参与恢复；不写
  Runtime 级全局日志。写盘失败静默降级（见「落盘」），绝不打断业务
  管线。

.. rubric:: 等级语义（全局单档，非标准 logging 级别体系）

等级是插件实例级全局开关（``LoggingPlugin.level``，运行期可改，下一
次钩子触发即生效——已挂的 handler 每次触发时读当前等级，不缓存）：

- ``"OFF"``：什么都不输出。
- ``"INFO"`` （默认）：关键节点各记一行——生命周期三对（``before_create`` /
  ``after_create`` / ``before_recover`` / ``after_recover`` /
  ``before_destroy`` / ``after_destroy``）、逻辑 turn 边界（``before_turn`` /
  ``after_turn``）、消息出入队（``after_enqueue`` / ``after_dequeue``）、
  LLM 调用边界（``before_provider_gen`` / ``after_provider_gen`` /
  ``on_provider_error``）、工具调用边界（``before_tool_call`` /
  ``after_tool_call``）、子 Agent（``before_subagent_invoke`` /
  ``after_subagent_invoke``）、取消与 fork（``before_cancel`` /
  ``after_cancel`` / ``before_fork`` / ``after_fork``）——共 21 点。
  value 只记摘要（类型名 + 标识字段，如工具名 / 消息 id）。
- ``"DEBUG"``：在 INFO 基础上，该 Agent 实例上存在的全部钩子点都
  输出（枚举 ``agent.hooks`` 的已声明点），value 记 ``repr`` 截断
  （默认 500 字符，构造参数可调）；流式增量 ``on_provider_delta``
  在 DEBUG 下同样逐条输出（高频，自用场景自负）。

.. rubric:: 插件探测

``install()`` 时经 ``runtime.get_plugin()`` 检测 ``"skill"`` /
``"comm"`` / ``"cron"`` / ``"workflow"`` 四个内置扩展是否已安装，
结果记入 :attr:`LoggingPlugin.detected`。语义只是「白名单放行」：
被探测到的扩展的钩子点（``before_skill_load`` / ``after_skill_load`` /
``on_signal`` / ``on_event`` / ``on_cron_trigger``；workflow 无实例级
钩子点）在该 Agent 已声明的前提下纳入 DEBUG 全集与 INFO 关键节点表；
未安装的扩展对应点名不登记、不访问（避免对未声明的点挂钩抛
:class:`flowing.errors.UnknownHookPointError`）。探测只回答「能不能
理」，不替 Agent 声明钩子点。

.. rubric:: 落盘

每个启用 Agent 一个文件：``<该 Agent session 目录>/logging.jsonl``
（session 目录 = ``tree.jsonl`` / ``state.jsonl`` 所在目录）。
append-only、一行一个 JSON object：:

    {"ts": "2026-08-18T06:47:56.565Z", "agent_id": "...", "level": "INFO",
     "hook": "before_tool_call", "value": "ToolCall(name='search', id='t1')",
     "handler_tag": null}

- 写入失败（磁盘满 / 目录被删）静默降级：打印一次 stderr 警告后，
  该 Agent 后续不再尝试写盘——日志插件绝不打断业务管线。
- 不做滚动 / 压缩 / 清理（策略未定，自用阶段手工处理）。

.. seealso:: :mod:`flowing._unstable`、:class:`LoggingPlugin`、:func:`use_logging`
"""

import json
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Literal

from flowing.agent import Agent
from flowing.errors import FlowingError
from flowing.plugins import Plugin
from flowing.runtime import Runtime

logging_plugin_key: str = "logging:plugin"
"""provide key：``use_logging`` 经 ``inject`` 取回
:class:`LoggingPlugin` 实例的键，字符串值即 ``"logging:plugin"``。
"""

LogLevel = Literal["OFF", "INFO", "DEBUG"]
"""全局等级字面量：``"OFF"`` / ``"INFO"`` / ``"DEBUG"``。
语义见模块 docstring「等级语义」。
"""

_LEVELS: tuple[str, ...] = ("OFF", "INFO", "DEBUG")
"""合法等级集合（``LoggingPlugin.level`` setter 的校验依据）。"""

_INFO_HOOK_POINTS: tuple[str, ...] = (
    # 生命周期三对（before_create/before_recover 在 setup 前 dispatch，
    # 挂载发生于 setup 内，创建/恢复管线上实际观察不到，挂载仅为对称完整）
    "before_create", "after_create",
    "before_recover", "after_recover",
    "before_destroy", "after_destroy",
    # 逻辑 turn 边界
    "before_turn", "after_turn",
    # 消息出入队
    "after_enqueue", "after_dequeue",
    # LLM 调用边界
    "before_provider_gen", "after_provider_gen", "on_provider_error",
    # 工具调用边界
    "before_tool_call", "after_tool_call",
    # 子 Agent
    "before_subagent_invoke", "after_subagent_invoke",
    # 取消与 fork
    "before_cancel", "after_cancel",
    "before_fork", "after_fork",
)
"""INFO 级关键节点清单（21 点，``use_logging`` 立即挂钩；模块 docstring
「等级语义」的落地名表）。"""

_EXTENSION_HOOK_POINTS: dict[str, tuple[str, ...]] = {
    "skill": ("before_skill_load", "after_skill_load"),
    "comm": ("on_signal", "on_event"),
    "cron": ("on_cron_trigger",),
    # workflow 无实例级钩子点声明（install 仅注册 run-workflow 工具，
    # Workflow 自持独立 HookRegistry，不经 Agent.hooks 枚举抵达）
    "workflow": (),
}
"""插件探测白名单：已安装内置扩展名 → 其声明的扩展钩子点。``detected``
命中且该 Agent 已 ``declare`` 时，``_attach_debug`` 才挂钩。"""


def _utc_ts() -> str:
    """ISO 8601 UTC 毫秒精度时间戳（``Z`` 后缀，与落盘样例行同形态）。"""
    return (datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))


class LoggingPlugin(Plugin):
    """钩子链路日志插件：等级开关 + 插件探测 + 落盘器。

    ``runtime.use(LoggingPlugin(level=...))`` 安装（阶段一）。持有全局
    等级与探测结果；``use_logging()`` 挂的 handler 每次触发时回读本
    实例的当前等级。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.use(LoggingPlugin(level="DEBUG"))
        # Agent 侧：setup() 中 use_logging(self)
        # 运行期调级：
        runtime.get_plugin("logging").level = "INFO"

    .. rubric:: 行为要点

    - ``install()`` 只注册（provide 自身 + 记录探测结果），不挂钩——
      钩子是 per-agent 的，属 :func:`use_logging` 的职责。
    - ``level`` 运行期可写（property setter 校验），写后下一次钩子
      触发即生效；非法值抛 :class:`flowing.errors.FlowingError`。
    - 重复安装（再次 ``runtime.use(LoggingPlugin())``）→ 同名插件
      冲突，按框架既有规则报错（一个 Runtime 同时只装一个同名插件）。

    .. seealso:: :func:`use_logging`、模块 docstring「注册面清单」。
    """

    name: ClassVar[str] = "logging"
    """注册名（显式声明；推荐格式见 ``flowing.plugins`` 命名约定）。
    """

    namespace: ClassVar[str] = "logging"
    """provide key 的命名空间前缀（与 :data:`logging_plugin_key` 的
    ``"logging:"`` 前缀一致）。
    """

    dependencies: ClassVar[list[str]] = []   # 与基类 Plugin 的 ClassVar 对齐
    """依赖声明（类属性元数据）。本插件无依赖，为空列表。
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

        :param level: 初始全局等级，默认 ``"INFO"`` （经 setter 校验，
            非法值抛 :class:`flowing.errors.FlowingError`）。
        :param max_value_repr: DEBUG 级 value 摘要的 repr 截断长度。
        """
        self.level = level   # 走 setter：初始等级同样过校验
        self.max_value_repr = max_value_repr
        # self.detected 由 install() 探测后写入（时序见模块 docstring）

    @property
    def level(self) -> LogLevel:
        """全局等级。运行期可改；handler 每次触发时回读，不缓存。

        写入非法值（非 ``"OFF"`` / ``"INFO"`` / ``"DEBUG"``）抛
        :class:`flowing.errors.FlowingError`。
        """
        return self._level

    @level.setter
    def level(self, value: LogLevel) -> None:
        if value not in _LEVELS:
            raise FlowingError(
                f"invalid log level: {value!r} (valid values: {', '.join(_LEVELS)})")
        self._level = value

    def install(self, runtime: Runtime) -> None:
        """provide 自身（``logging_plugin_key``）并一次性探测四个内置扩展。

        只注册，不做任何 per-agent 挂钩——钩子是 per-agent 的，属
        :func:`use_logging` 的职责。探测须显式 ``strict=False``
        （默认形态下未安装的扩展会抛 ``KeyError``）。

        :param runtime: 当前安装的 Runtime。
        """
        runtime.provide(logging_plugin_key, self)
        # 探测 skill/comm/cron/workflow 四扩展（有意探测 → 显式 strict=False），
        # 结果记入 self.detected（时序见模块 docstring）；未安装的点名不登记
        self.detected = frozenset(
            name for name in _EXTENSION_HOOK_POINTS
            if runtime.get_plugin(name, strict=False) is not None)


def use_logging(agent: Agent) -> None:
    """阶段二启用：在 ``setup()`` 中调用，按插件当前等级为本 Agent 挂钩。

    同步函数——``inject`` 查表与钩子注册均为同步操作，无 ``await``
    需求。

    .. rubric:: 功能介绍

    经 ``agent.inject(logging_plugin_key)`` 取回 :class:`LoggingPlugin`
    实例；未安装本插件时抛 :class:`flowing.errors.MissingProvideError`
    （不静默跳过）。

    .. rubric:: 挂钩策略

    - INFO 关键节点（清单见模块 docstring「等级语义」）：立即挂到各
      钩子点，``by="logging"``；handler 内早退判断当前等级。
    - DEBUG 全集与扩展钩子点：延迟到一个内部 ``after_create``
      handler（``by="logging"``）中执行——此刻其它 ``use_*()`` 已
      声明完各自钩子点，枚举该实例已声明的全部钩子点才能覆盖全集；
      同时消除「``use_logging`` 必须在其它 ``use_*`` 之后调用」的
      顺序约束。recover 管线同理由 ``after_recover`` 内部 handler
      兜底重挂（幂等：已挂过的点名不重复挂钩）。
    - 所有 handler 为纯观察：不修改 value（原样透传返回）、不
      ``raise Intercepted``；自身异常捕获后置为 stderr 警告（不打断
      管线）。

    .. rubric:: 行为要点

    - ``after_provider_gen`` 的 value（``ProviderResponse``）的
      ``usage`` 概要以 :attr:`flowing.message.Message.usage` 为准，
      不在摘要中复述；``on_provider_error`` 记异常类型名。
    - ``before_enqueue`` 被 ``Intercepted`` 拦截的结果无从观察属
      已知限制（拦截发生在 dispatch 内，观察点只见到「没触发
      ``after_enqueue``」）。
    - 不声明任何新钩子点；不写 state（无恢复义务）。

    :param agent: 启用日志的 Agent（``setup()`` 中的 ``self``）。

    .. seealso:: :class:`LoggingPlugin`、模块 docstring「落盘」。
    """
    # 经 inject 沿链上溯取回插件实例；未安装本插件时抛
    # flowing.errors.MissingProvideError（不静默跳过）
    plugin: LoggingPlugin = agent.inject(logging_plugin_key)
    # session 目录取 Agent 管线预绑的 _session_dir（tree.jsonl /
    # state.jsonl 所在目录），不在本插件重写路径拼接
    log_path = agent._session_dir / "logging.jsonl"
    # per-agent 闭包状态：写盘降级标记 + 已挂点名集（use_logging 随
    # setup 在每实例上跑一次，天然按实例隔离；恢复换新实例后
    # setup 在新实例上执行，闭包随之重建）
    state: dict[str, bool] = {"write_failed": False}
    attached: set[str] = set()

    def _summarize(value: Any) -> Any:
        # INFO 摘要：类型名 + 标识字段（如 tool 名 / 消息 id）；
        # 无 value 钩子点记 None；on_provider_error 记异常类型名
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        parts: list[str] = []
        for field_name in ("id", "name", "kind", "type", "topic", "source", "reason"):
            field_value = getattr(value, field_name, None)
            if field_value is None:
                continue
            if isinstance(field_value, Enum):
                field_value = field_value.value
            parts.append(f"{field_name}={field_value!r}")
        error = getattr(value, "error", None)
        if isinstance(error, BaseException):
            parts.append(f"error={type(error).__name__}")
        if not parts:
            return type(value).__name__
        return f"{type(value).__name__}({', '.join(parts)})"

    def _make_observer(hook_name: str):
        # 按点名构造观察 handler（落盘行的 "hook" 字段需要点名，
        # dispatch 不透传钩子点名，故逐点绑定）

        async def _observe(host: Agent, value: Any = None) -> Any:
            # 纯观察 handler：每次触发回读 plugin.level 当前值（不缓存），
            # "OFF" 早退；INFO 记摘要，DEBUG 记 repr 截断 max_value_repr；
            # 追加 logging.jsonl 一行；写盘失败静默降级（一次性 stderr
            # 警告后不再尝试）；自身异常置 stderr 警告，不打断管线
            try:
                level = plugin.level
                if level == "OFF" or state["write_failed"]:
                    return value
                rendered = (repr(value)[: plugin.max_value_repr]
                            if level == "DEBUG" else _summarize(value))
                line = json.dumps(
                    {"ts": _utc_ts(), "agent_id": host.node_id,
                     "level": level, "hook": hook_name,
                     "value": rendered, "handler_tag": None},
                    ensure_ascii=False, default=repr)
                try:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except OSError as exc:
                    # 写盘失败（磁盘满 / 目录被删）静默降级：一次性
                    # stderr 警告后该 Agent 后续不再尝试写盘
                    state["write_failed"] = True
                    print(f"[flowing._unstable.logging] agent {host.node_id} "
                          f"failed to write log, will not retry {log_path}: {exc!r}",
                          file=sys.stderr)
            except Exception as exc:
                # handler 自身异常：stderr 警告，不传播（日志插件绝不能
                # 打断业务管线）
                print(f"[flowing._unstable.logging] agent {host.node_id} "
                      f"observer handler raised (hook={hook_name}, swallowed): {exc!r}",
                      file=sys.stderr)
            return value

        return _observe

    def _attach(hook_name: str) -> None:
        if hook_name in attached:
            return   # 幂等去重：已挂过的点名不重复挂钩
        attached.add(hook_name)
        agent.hooks._hook_points[hook_name](
            _make_observer(hook_name), by="logging")

    # INFO 关键节点立即挂钩（by="logging"，清单见模块 docstring「等级语义」；
    # handler 内早退判断当前等级）
    for _name in _INFO_HOOK_POINTS:
        _attach(_name)

    async def _attach_debug(host: Agent, _value: Any = None) -> None:
        # DEBUG 全集与插件钩子点的延迟挂钩：挂载面按当前等级分流
        # （只影响「挂不挂」；等级判定统一收敛在 handler 触发时回读，
        # 与早退逻辑叠加不冲突）
        level = plugin.level
        if level == "OFF":
            return
        if level == "DEBUG":
            # 枚举 agent.hooks._hook_points（内部 API），对该实例已存在
            # 的全部钩子点挂观察 handler（含 on_provider_delta 逐条输出；
            # 幂等去重由 _attach 承担）
            for name in agent.hooks._hook_points:
                _attach(name)
            return
        # INFO：只挂 detected 白名单放行的扩展钩子点，且以该 Agent
        # 已 declare 为前提（枚举自然跳过未声明的点，不触发
        # UnknownHookPointError）
        for ext_name in plugin.detected:
            for name in _EXTENSION_HOOK_POINTS[ext_name]:
                if name in agent.hooks._hook_points:
                    _attach(name)

    # 延迟到 after_create / after_recover：此刻其它 use_*() 已 declare 完
    # 各自钩子点，枚举才能覆盖全集；recover 管线由 after_recover 兜底重挂
    agent.hooks.after_create(_attach_debug, by="logging")
    agent.hooks.after_recover(_attach_debug, by="logging")
