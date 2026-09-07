"""``flowing.runtime`` —— Runtime 对象图根、唯一入口 ``launch``、``@`` 上下文与 provide-inject 链终点。

.. rubric:: 功能介绍

本模块是 Flowing 框架的对象图根模块，承载：

- :class:`Runtime`：一个运行中的 flowing 子项目对应一个 Runtime 实例——它持有
  全部 Agent / Workflow 节点、已安装插件、全局工具注册表与
  agent 池，并对外提供扩展注册（``use`` / ``register_tool`` 等）、配置读取
  （``get_config`` 等）、节点管理（``create_agent`` / ``recover_agent`` /
  ``get_agent`` / ``archive_agent``）与观测（``snapshot``）API。
- :func:`launch`：Runtime 的唯一创建入口，同时登记 ``@`` 项目根上下文。
- :func:`resolve`：模块级 ``@/`` 路径解析。
- :class:`flowing.provide.ProvideNode` 协议与 :func:`flowing.provide.inject_from`
  的再导出（定义见 :mod:`flowing.provide`；``__all__`` 同时保留本模块出口）。

``launch(path, **kwargs)`` 是创建 Runtime 的唯一途径：它登记 ``@`` 上下文
（模块级 ``ContextVar``，按 asyncio Task 隔离——一个进程可同时运行多个
Runtime，并发 ``launch`` 互不串扰）、import 子项目入口 ``main.py`` 并
``await main(**kwargs)``。子项目 ``main()`` 在函数体内构造 Runtime（可任意
子类，``@`` 自动绑定）、``use`` 插件、``provide`` 注入、``mount`` 根节点，
并返回它。绕过 ``launch`` 直接 ``Runtime()`` 会因 ``@`` 上下文未登记而抛
``RuntimeError``。

``launch`` 只负责把子项目拉起为 Runtime：不解析配置（配置读取经
:meth:`Runtime.get_config` 与宿主启动层（``flowing.interfaces`` 各入口））、不认识插件（插件经
:meth:`Runtime.install` 显式启用）、不决定新建还是恢复（那是子项目 ``main()``
的策略，如 ``main(resume: str | None = None)``）、不启动任何服务端口（暴露
方式由调用方决定，CLI / HTTP / Web / 测试 / 嵌入五种入口共用本函数）。
子项目入口永远是 Python ``main()`` 函数，没有声明式入口文件。

.. rubric:: 全局约定（跨符号、影响使用的约定）

async 方法约定：会触发钩子或触发异步管线的公开方法都是协程，调用时用
``await``——``launch`` / ``mount`` / ``create_agent`` / ``recover_agent`` /
``get_agent`` / ``archive_agent`` / ``archive_orphans`` / ``shutdown``。
不触发钩子的方法保持同步：``use`` / ``get_plugin`` / ``get_node`` /
``provide`` / ``inject`` / ``get_config`` / ``set_config`` / ``register_*`` /
``set_model_tags`` 等。

``@`` 上下文：``launch`` 登记的项目根在 asyncio 中按 Task 隔离——一个进程
可同时运行多个 Runtime，各自 ``@`` 解析与 ``project_root`` 互不串扰；
``launch`` 返回后复位上下文不影响已 spawn 的子 Task（它们已继承正确值）。
``resolve("@/...")`` 在 ``launch`` 登记后即可用（``main()`` 内构造 Runtime
之前也行）。``@`` 指向 flowing 子项目目录；宿主大工程自己的根是应用层
概念，经 ``provide`` 注入，不进 ``@/``。真正的进程级强隔离（多租户、
A/B 测试）用子进程，不靠多 Runtime。

provide-inject 链：Runtime 是链终点。``Runtime.provide(key, value)`` 注册
的值对全树所有节点可见（上溯终点）；同 key 重复 provide 是覆盖更新，
``inject`` 实时沿链查找、更新即刻可见。敏感信息（API key / 凭证）禁止经
``provide`` 传递——注入值沿链对后代节点可见。通用查找算法与节点协议见
:mod:`flowing.provide`。

配置读取：``get_config`` 的读取顺序是——``set_config`` 运行期覆盖层最
优先，其次为构造期浅合并结果（用户级 ``$FLOWING_CONFIG_HOME/config.yaml``
> 项目级 ``@/config.yaml`` > 框架推荐默认值）。环境变量与命令行参数不
直接进入本链：``FLOWING_*`` 环境变量由各自消费点直读，命令行参数经
``set_config`` 表达。浅合并在 ``Runtime()`` 构造时完成——合并完成后
任何时机均可调用 :meth:`Runtime.get_config`；合并前（典型即模块顶层
import 期）调用抛 :class:`flowing.errors.ConfigNotReadyError`。
``get_config`` 不做命名空间访问控制：任何代码可读任何命名空间，注册
只是「谁负责校验」的声明。

插件启用：``Runtime.install(plugin)`` 是插件启用入口（阶段一），按实参顺序
执行各插件的 ``install(runtime)``；插件在 install 中注册全局能力（工具 /
provide 值 / 配置命名空间 / Agent 类型 / Resource / 全局状态命名空间），
各 Agent 再在 ``setup()`` 中经 ``use_xxx(self)`` 做实例级启用（阶段二）。
依赖校验随 ``install()`` 增量执行：已装插件依赖图成环抛
:class:`flowing.errors.DependencyError` （报错现场即引入环的那次 ``install()``）；
依赖缺失只 ``warnings.warn`` 警告、不抛错（``install()`` 可分批）。未启用的
扩展对 Agent 零开销（不是被 skip）。

创建 / 恢复管线：``create_agent`` 与 ``recover_agent`` 是两个独立方法，
共用同一管线结构。两条管线 dispatch 四个生命周期钩子点——
``before_create`` / ``after_create`` （仅创建管线触发）与 ``before_recover`` /
``after_recover`` （仅恢复管线触发）。恢复管线时序：``_restore()`` （重放
session 目录日志）→ ``before_recover`` → ``setup`` → PENDING 检查 →
``_nodes`` 注册 → ``after_recover`` → 常驻工作循环 Task 启动。两条管线在
各自的一个新建实例上执行一次 ``setup()``——装配语义与执行它的实例
无关。钩子点全集与触发时机见 :mod:`flowing.hooks`。

观测：``Runtime.snapshot()`` 返回一致性只读快照（拉取通道；推送通道由
钩子系统承载）——节点表、agent 池、插件清单等一次性只读视图，全部字段
JSON 可序列化；``_provided`` 的值内容（凭证等敏感值）绝不进入快照。
完整字段契约见 :mod:`flowing.snapshot`。

关闭：``shutdown()`` 是「请求关闭」——递归 destroy 全部节点、插件收尾、
关闭全局状态视图、置位退出事件后返回；``await runtime`` 在事件置位后解除
阻塞，此时全部善后已完成。空 Runtime（未 mount）同样可 await / shutdown。
推荐调用顺序见 :func:`launch` 与各方法 docstring。

.. rubric:: 使用示例

.. code-block:: python

    # @/main.py —— 子项目入口（launch 会 import 并调用它）
    from flowing import Runtime

    async def main(resume: str | None = None) -> Runtime:
        runtime = Runtime()                      # @ 自动绑定到实例
        runtime.install(MyPlugin())              # 阶段一：安装插件
        runtime.provide("workspace_root", "/ws")
        if resume is not None:
            await runtime.recover_agent(resume)  # 恢复既有 agent
        else:
            await runtime.mount("@/root.fya")    # 新建根节点
        return runtime

    # 宿主进程 / CLI 内部：拉起并保持存活
    runtime = await launch("/path/to/flowing-project", resume="agent-xxx")
    await runtime                               # 阻塞至 shutdown()

.. seealso::

    :mod:`flowing.agent` —— ``Agent`` 基类与创建 / 恢复管线的另一半
    （``setup`` / 常驻工作循环）。
    :mod:`flowing.provide` —— provide-inject 链的节点协议与查找算法。
    :mod:`flowing.errors` —— 本模块抛出的异常层次。
    :mod:`flowing.plugins` —— ``Plugin`` 基类与扩展层约定。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import asyncio
import json
import logging
import os
import warnings

from collections.abc import Awaitable, Callable, Generator, MutableMapping
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar, overload

from ruamel.yaml import YAML   # 与 flowing.model 同一 yaml 库选型

from flowing.persistence import FileRecordStore, StateView
from flowing.errors import (
    ConfigNamespaceConflictError,
    ConfigNotReadyError,
    DependencyError,
    FormatError,
    MissingFieldError,
    MissingProvideError,
    NameMismatchError,
    ResourceNameConflictError,
    ResourceNotFoundError,
    UnknownHookPointError,
)
from flowing.params import ConfigKey, InjectionKey
from flowing.paths import NamingRules
from flowing.paths import classify_ref as _classify_ref
from flowing.paths import infer_name as _infer_name
from flowing.paths import kebab_to_snake as _kebab_to_snake
from flowing.paths import path_to_module_name as _path_to_module_name
from flowing.paths import probe_candidates as _probe_candidates
from flowing.paths import resolve_path as _paths_resolve_path
from flowing.paths import to_project_path as _paths_to_project_path
from flowing.provide import ProvideNode, inject_from
from flowing.providers import ProviderRegistry, load_provider_candidates
from flowing.snapshot import AgentInfo, NodeInfo, RuntimeSnapshot
from flowing.builtins import register_builtins
from flowing.tool import Tool, ToolRegistry

if TYPE_CHECKING:
    from flowing.agent import Agent
    from flowing.plugins import Plugin

__all__ = [
    "AGENT_NAMING",
    "ProvideNode",
    "Runtime",
    "inject_from",
    "launch",
    "resolve",
]

T = TypeVar("T")

_logger = logging.getLogger("flowing.runtime")

AGENT_NAMING = NamingRules(
    suffixes=(".agent.fya", ".fya", ".py"),
    generic_names=frozenset({"agent.fya", "AGENT.fya"}),
)
"""Agent 资源的路径形态身份名推断规则表（:class:`flowing.paths.NamingRules`）。

紧邻候选链声明（:meth:`Runtime.get_agent_class`）：命中通用名候选
（``agent.fya`` / ``AGENT.fya``）→ 身份名取目录名；否则文件名去首个
匹配后缀、snake→kebab。供 agent 装配层调
:func:`flowing.parser.parse_fya` 时传入 ``naming=AGENT_NAMING``，以及
name 断言的推断侧。
"""

_FRAMEWORK_CONFIG_DEFAULTS: dict[str, Any] = {
    "agent": {"timeout": 60, "max_turns": 20, "max_depth": 10},
    "runtime": {"log_level": "info"},
}
"""框架推荐默认值层（配置优先级链的最底层；已知 key 清单见
:meth:`Runtime.get_config` 的行为要点）。内部 API，不属稳定契约。
"""

_POOL_META_KEYS: tuple[str, ...] = (
    "agent_type", "parent_agent_id", "created_at", "args",
)
"""身份四键（agent_type / parent_agent_id / created_at / args）：create
管线整写进 agent 自己 session 目录的 ``meta.json`` （JSON 整写、非状态）；
池扫描 / 跨进程恢复经 ``_read_pool_meta`` 读回重建 ``_agent_pool`` 条目。
内部 API，不属稳定契约。
"""

_current_project_root: ContextVar[Path | None] = ContextVar(
    "_current_project_root", default=None)
"""``@`` 项目根上下文的载体（内部 API，不属稳定契约）。

``launch`` 用 ``.set(Path(path).resolve())`` 登记、``.reset(token)`` 复位；
模块级 ``resolve()`` 与 ``Runtime.__init__`` 读同一 contextvar，保证两条
通道同源。asyncio 中 per-Task 隔离，是多 Runtime 并发的时序稳定机制。

行为边界：未登记时取值为 ``None``；用户代码不应直接读写本变量。

.. seealso:: :func:`flowing.runtime.launch`、:func:`flowing.runtime.resolve`
"""


async def launch(
    path: str | Path,
    main_file: str | None = None,
    **kwargs: Any,
) -> Runtime:
    """Runtime 的唯一创建入口：登记 ``@`` 上下文，import 子项目入口并返回其 Runtime。

    .. rubric:: 功能介绍

    框架核心层函数。把一个 flowing 子项目目录拉起为配置完毕的 Runtime：
    子项目的入口 main 文件（缺省 ``<path>/main.py``；``main_file`` 指定替代
    路径，CLI 上以 ``-m`` 传入，支持绝对路径与 ``@`` 相对路径）必须导出
    ``async def main(**kwargs) -> Runtime``。``main()`` 在函数体内构造
    Runtime（可任意子类，``@`` 自动绑定到实例）、``use`` 插件、``provide``
    注入、``mount`` 根节点，并返回它。CLI / HTTP / Web / 测试 / 嵌入五种
    暴露方式共用此入口——调用方决定如何暴露 Runtime，框架不耦合暴露方式。

    新建 vs 恢复是子项目 ``main()`` 的策略（如
    ``main(resume: str | None = None)``）：框架不特殊处理 ``--resume`` 之类
    的参数——它们经本函数的 ``**kwargs`` 原样透传给 ``main(**kwargs)``。

    .. rubric:: 使用示例

    .. code-block:: python

        # @/main.py —— 子项目入口（launch 会 import 并调用它）
        from flowing import Runtime

        async def main(resume: str | None = None) -> Runtime:
            runtime = Runtime()                      # @ 自动绑定到实例
            if resume is not None:
                await runtime.recover_agent(resume)
            else:
                await runtime.mount("@/root.fya")
            return runtime

    .. code-block:: python

        # 宿主进程 / CLI 内部
        runtime = await launch("/path/to/flowing-project", resume="agent-xxx")
        await runtime                                # 阻塞至 shutdown()

    .. rubric:: 行为要点

    - 时序：先登记 ``@`` 上下文（``path`` 解析为绝对路径），再 import 子项目
      main 文件并 ``await mod.main(**kwargs)``，``main()`` 返回后复位上下文。
      ``main()`` 内（构造 Runtime 之前）即可调用 ``flowing.resolve("@/...")``。
    - ``@`` 上下文按 asyncio Task 隔离：一个进程可同时 ``launch`` 多个子项目，
      各自 Runtime 的 ``project_root`` 互不串扰；复位发生在 ``main()`` 返回
      之后，已 spawn 的子 Task 不受影响（它们已继承正确值）。
    - ``**kwargs`` 原样透传给 ``main``，本函数不做参数变换（CLI 的
      ``--key value`` 规则由 ``flowing.interfaces.cli`` 负责）。
    - 返回值是 ``main()`` 返回的 Runtime——此时 Agent 已激活、工作循环
      Task 已就绪；若调用方不做 ``await runtime``，进程/任务随即无事可做。
    - 本函数只负责把子项目拉起为 Runtime：不解析配置、不认识插件、不决定
      「新建 vs 恢复」（那是 ``main()`` 的策略）、不启动任何服务端口。

    :param path: flowing 子项目目录（普通文件系统路径，CLI 层不感知 ``@``）。
    :param main_file: 替代的 main 文件路径（可选；缺省 ``<path>/main.py``）。
    :param kwargs: 透传给子项目 ``main(**kwargs)`` 的任意参数。
    :return: 配置完毕的 ``Runtime``。
    :raises FileNotFoundError: 子项目缺少 main 文件时（import 失败的原生
        异常直接上抛，框架不包装——模块无 ``main`` 属性等其它原生异常同理）。

    .. seealso:: :func:`flowing.runtime.resolve`、:class:`flowing.runtime.Runtime`、
        :meth:`flowing.runtime.Runtime.mount`
    """
    import importlib.util

    token = _current_project_root.set(Path(path).resolve())   # 登记 @ 上下文（main() 内 resolve 与 Runtime() 均依赖）
    try:
        # import 子项目入口 main 文件（缺省 @/main.py，main_file 支持绝对路径与
        # @ 相对路径）；原生 import 异常直接上抛，框架不包装
        main_path = resolve(main_file) if main_file is not None else Path(path).resolve() / "main.py"
        spec = importlib.util.spec_from_file_location("flowing_subproject_main", main_path)
        mod = importlib.util.module_from_spec(spec)   # type: ignore[union-attr]
        spec.loader.exec_module(mod)   # type: ignore[union-attr]
        runtime: Runtime = await mod.main(**kwargs)   # **kwargs 原样透传，不做参数变换
        return runtime
    finally:
        _current_project_root.reset(token)   # main() 返回后复位；已 spawn 的子 Task 已继承正确值

def resolve(path: str) -> Path:
    """模块级 ``@/`` 路径解析：把 ``@/`` 前缀路径解析为当前 Task 项目根下的绝对 ``Path``。

    .. rubric:: 功能介绍

    读 ``launch`` 登记的 ``@`` 项目根上下文，把 ``@/`` 前缀路径解析为绝对
    路径。与 ``Runtime.__init__`` 读同一个上下文——两条通道同源；模块级
    函数形态使 ``main()`` 内加载配置文件等早期代码无需先持有 Runtime。

    .. rubric:: 使用示例

    .. code-block:: python

        import flowing

        async def main() -> Runtime:
            config = load_yaml(flowing.resolve("@/config.yaml"))   # Runtime() 之前也行
            runtime = Runtime()
            ...

    .. rubric:: 行为要点

    - ``path`` 以 ``@/`` 开头：返回 ``project_root / path[2:]``。
    - 非 ``@/`` 路径：不经 ``@`` 上下文处理，按普通 ``Path`` 语义返回
      （相对路径基于当前工作目录）。完整的前缀规则（``./`` / ``../`` /
      裸名）是 :meth:`Runtime.resolve_path` 的职责，本函数只负责 ``@/``。
    - 未经 ``launch`` 登记（上下文为空）时抛 ``RuntimeError``，不留静默回退。

    :param path: 待解析路径字符串。
    :return: 解析后的 ``Path``。
    :raises RuntimeError: 未经 ``flowing.launch`` 登记 ``@`` 上下文时抛出。

    .. seealso:: :func:`flowing.runtime.launch`、
        :meth:`flowing.runtime.Runtime.resolve_path`
    """
    root = _current_project_root.get()
    if root is None:
        raise RuntimeError("@ context not registered via flowing.launch; resolve() unavailable")
    if path.startswith("@/"):
        return root / path[2:]
    return Path(path)   # 非 @/ 路径：普通 Path 语义（完整前缀规则是 Runtime.resolve_path 的职责）


def _check_pending(instance: "Agent", agent_type: str) -> None:
    """创建 / 恢复管线在 ``setup()`` 之后执行的 PENDING 检查器（内部 API）。

    扫实例 ``__dict__`` 与类 MRO 属性中的 ``PENDING`` 哨兵（含
    ``Parsable`` 包裹形态——``Parsable.source is PENDING``），命中即抛
    :class:`flowing.errors.MissingFieldError` （单字段结构（``field`` /
    ``agent_type``），多字段命中时报首个（定义序），其余待修复后下次
    创建再报）；``instance.hooks._pending_on`` 非空（``@on`` 暂记的钩子
    点在 setup 结束前未被 declare）→ 抛
    :class:`flowing.errors.UnknownHookPointError` （消息列出钩子点名与
    方法名）。
    """
    from flowing.parsable import PENDING, Parsable   # 局部 import：模块头依赖图保持单向

    for mapping in (vars(instance), *(vars(c) for c in type(instance).__mro__)):
        for field, value in mapping.items():
            if field.startswith("__"):
                continue
            if isinstance(value, Parsable):
                value = value.source   # Parsable 包裹形态：哨兵在 source 位
            if value is PENDING:
                raise MissingFieldError(field, agent_type)
    # 同帧结算 @on 暂记：目标钩子点未被 declare 即视为未消费
    if instance.hooks._pending_on:
        unclaimed = [f"{hook_name} (method {getattr(bound, '__name__', bound)})"
                     for bound, hook_name, _, _, _ in instance.hooks._pending_on]
        raise UnknownHookPointError(
            f"@on markers found no owning hook point: {'; '.join(unclaimed)}"
            " — hook point name misspelled, or the corresponding plugin/Composable is not enabled in setup()")


# ``ProvideNode`` 协议与 ``inject_from`` 统一上溯算法的定义在
# ``flowing.provide``；此处为 import + 再导出（``__all__`` 保留），
# ``flowing.runtime.ProvideNode`` / ``flowing.runtime.inject_from`` 同样可用。


class Runtime:
    """子项目实例、对象图根与唯一全局容器。

    .. rubric:: 功能介绍

    一个 ``Runtime`` 对应一个运行中的 flowing 子项目：持有全部 Agent /
    Workflow 节点、已安装插件、全局工具
    注册表（``tool_registry``）与 agent 池，并提供扩展 API（``use`` /
    ``register_tool`` / ``provide`` / ``register_config_namespace`` 等）、
    节点管理（``create_agent`` / ``recover_agent`` / ``get_agent`` /
    ``archive_agent``）与观测通道（``snapshot``）。

    实例不直接构造：唯一创建路径是 ``launch(path, **kwargs)`` 内由子项目
    ``main()`` 构造（可任意子类）；绕过 ``launch`` 直接 ``Runtime()`` 会因
    ``@`` 上下文未登记而抛 ``RuntimeError``。构造时固化 ``project_root``、
    注册内置工具与标准子智能体、扫描 provider 候选清单与 agent 池——
    均不实例化任何 Provider / Agent（懒加载原则）。

    Runtime 是 provide-inject 链的终点：``Runtime.provide`` 注册的值对全树
    所有节点可见，``Runtime.inject`` 只查根级存储。

    .. rubric:: 行为要点

    - 创建即注册：Agent 诞生必经 ``_nodes`` 注册（结构保证「不可能创建而
      不注册」）。节点的遗忘分三档：

      - destroy（``Agent.destroy()``）：丢实例、从 ``_nodes`` 摘除，池 key
        与名录保留——可经 ``recover_agent`` / ``get_agent`` 现场恢复；
      - archive（``archive_agent``）：``_nodes`` / 池 / ``core`` 名录一并
        移除整棵子树，session 文件留档——运行时完全遗忘（``get_agent``
        返回 ``None``），重新引入需外部运维把 id 加回名录；
      - 删除：物理删除 session 目录 + 名录项，不可逆——框架不提供删除
        功能，应用层需要删除已有归档文件请自行实现（建议先归档再删除，
        避免活引用指向已消失的目录）。
    - ``shutdown()`` 的返回与 ``await runtime`` 解除阻塞表达「已关闭」的
      观测语义——不提供 ``status`` 字段之类的生命周期状态投影。
    - 一个进程可同时运行多个 Runtime（``@`` 上下文按 asyncio Task 隔离），
      互不共享存储。

    .. seealso:: :class:`flowing.agent.Agent`、
        :meth:`flowing.agent.Agent.register_state`
    """

    _nodes: dict[str, ProvideNode]
    """活体表（node_id → 节点实体；Agent / Workflow 均注册在内）。
    内部 API，不属稳定契约。
    """
    _plugins: dict[str, Any]
    """已安装插件表（插件 name → 实例），``get_plugin`` 的查询源。
    内部 API，不属稳定契约。
    """
    node_id: str
    """固定为 ``"runtime-0"`` （共享 ID 空间的 ``runtime-`` 前缀；单 Runtime 的
    共享 ID 空间内唯一——一个 Runtime 下不可能有多个 Runtime）；本 Runtime 是
    inject 链终点与 ``_nodes`` 中所有节点的最终亲节点，``__init__`` 时自注册为
    ``_nodes`` 首条目。参见 :class:`ProvideNode`。
    """
    runtime: "Runtime"
    """:class:`ProvideNode` 协议成员：链终点的 ``runtime`` 自指——Runtime 的
    ``runtime`` 指向自己，以此表达「本节点就是链终点」（``__init__`` 时
    真实赋值）。
    """
    project_root: Path
    """``@`` 上下文的固化值：``__init__`` 时从 ``_current_project_root`` 读取；
    ``resolve_path("@/...")`` 的解析基准。只读语义，构造后不改。
    """
    tool_registry: ToolRegistry
    """全局工具注册表（规范名 → ``Tool``）；``register_tool`` 的写入目标；
    读取经 Agent 的 ``tool_call`` 按别名查 ``_tool_entries`` （见 ``flowing.tool``）。
    """
    provider_registry: ProviderRegistry
    """Provider 懒实例化表（providers.yaml 条目名 → ``Provider`` 实例）。
    ``Agent.provider_gen()`` 的 provider 懒获取入口；候选清单在构造期扫描
    完成后就位。adapter 类的进程级注册表是
    ``flowing.providers.provider._provider_adapters``，与本表（条目实例）
    分层。
    """
    _provided: dict[str, Any]
    """根级 provide 存储，inject 链终点；敏感信息（API key / 凭证）禁止进入。
    内部 API，不属稳定契约。
    """
    _config_overrides: dict[str, Any]
    """运行期配置覆盖层（``set_config`` 的写入目标）：``get_config`` 读取时
    优先于优先级链合并结果命中——「下游产品运行期复写框架配置」的通道
    （如 ``set_config("agent.timeout", ...)``）。不持久化（进程级，重启即
    失效；持久覆盖请改配置文件）。内部 API，不属稳定契约。
    """
    _config_namespaces: dict[str, Any]
    """配置命名空间注册表（命名空间 → 扩展声明的 schema）；注册只是「谁负责校验」
    的声明，不构成访问控制。内部 API，不属稳定契约。
    """
    _resources: dict[str, Any]
    """Resource 注册存储（name → 任意实例）；生命周期跨 Agent，独立于 provide 链。
    内部 API，不属稳定契约。
    """
    _agent_pool: dict[str, dict[str, Any]]
    """agent 池注册表：``agent_id → {agent_type, parent_agent_id, created_at, args}``；
    池 key 的唯一权威来源是全局 ``core`` 名录（见 ``archive_agent`` /
    ``recover_agent``）；value 是元数据不是实例。内部 API，不属稳定契约。
    """
    _agent_types: dict[str, type[Agent]]
    """子 Agent 类型注册表（``ns::注册名`` → Agent 类；裸名视图 =
    ``default::`` / ``builtin::``，见 ``get_agent_class``）；
    ``register_agent_type`` 的写入目标，``get_agent_class`` 的查找源。
    内部 API，不属稳定契约。
    """
    _states: dict[str, StateView]
    """全局持久化状态命名空间表（命名空间 → ``StateView``）；
    ``register_state`` 写入（创建即 replay）、``states`` property 暴露
    （含 ``core`` + ``default`` 两个框架自登记的内建命名空间）。
    内部 API，不属稳定契约。
    """
    _persist_dir: Path
    """持久化根目录；构造时固化（``Runtime(persist_dir=...)`` 指定，显式
    指定即建目录；缺省 ``<cwd>/.flowing`` 推迟到首个持久化动作才建）。
    构造后不可更改；只读访问经 :attr:`persist_dir`。内部 API，不属稳定
    契约。
    """
    _model_tags_path: Path | None
    """模型标签文件路径（默认 ``~/.flowing/model-tags.yaml``，``FLOWING_MODEL_TAGS``
    环境变量优先；``set_model_tags`` 可编程覆盖）。模型标签是模型解析的
    常规通道，无默认文件时按「未定义标签回退 default、default 也缺报错」
    处理（见 ``flowing.model``）。内部 API，不属稳定契约。
    """
    _models_path: Path | None
    """models.yaml 来源路径（``set_models`` 赋值；构造期解析为
    ``FLOWING_MODELS_PATH`` / ``$FLOWING_CONFIG_HOME/models.yaml`` 的有效
    默认路径——``Agent._resolve_model_tag`` 要求两路径均非 ``None``，
    「未设定 → 默认路径」的现场求值由构造期的就地换算承担）。
    内部 API，不属稳定契约。
    """
    _providers_path: Path | None
    """providers.yaml 来源路径（``set_providers`` 赋值；默认 ``None`` =
    构造期解析的 ``FLOWING_PROVIDERS_PATH`` / 默认路径）。内部 API，不属
    稳定契约。
    """
    _shutdown_event: asyncio.Event
    """退出事件；``__await__`` 等待它，``shutdown()`` 末尾置位。
    内部 API，不属稳定契约。
    """
    _config_ready: bool
    """配置就绪闸：优先级链浅合并在 ``__init__`` 尾部同步完成前为
    ``False``，``get_config`` 未就绪即抛 ``ConfigNotReadyError``。
    内部 API，不属稳定契约。
    """
    _merged_config: dict[str, Any]
    """优先级链浅合并产物（点分扁平 key → 值）；``get_config`` 的第二读源
    （第一是 ``_config_overrides``）。内部 API，不属稳定契约。
    """
    env: MappingProxyType
    """``os.environ`` 只读视图——Parsable 渲染上下文的 ``env`` 入口
    （``{{ env.X }}`` 经 Jinja 的 attr→item 回退命中，env 直接以可点号
    引用对象进渲染上下文）。构造期建立，是 ``os.environ`` 的活视图。
    """

    def __init__(self, *, persist_dir: str | Path | None = None) -> None:
        """构造 Runtime（同步）；``@`` 由 launch 上下文自动绑定到 ``project_root``。

        .. rubric:: 功能介绍

        必须在 ``flowing.launch`` 登记的 ``@`` 上下文内调用（通常由子项目
        ``@/main.py`` 的 ``main()`` 调用）。构造时固化 ``project_root``
        与持久化根（``persist_dir`` 参数）、初始化全部空存储、执行
        Provider 候选清单与 agent 池的扫描（不实例化——懒加载原则，
        重量级初始化推迟到首次使用）。

        .. rubric:: 使用示例

        .. code-block:: python

            class MyRuntime(Runtime): ...          # 可任意子类化

            async def main() -> Runtime:
                runtime = MyRuntime()              # @ 自动绑定
                return runtime

        .. rubric:: 行为要点

        - 前置条件：``_current_project_root`` 已登记（即在 ``launch`` 的
          调用栈内）；违反抛 ``RuntimeError``。
        - 构造期副作用仅限：固化 ``project_root`` 与持久化根（``persist_dir``
          显式指定时即建目录）、初始化空存储、核心内置工具注册
          （``subagent-invoke`` / ``finish``）、扫描 providers.yaml 构建
          Provider 候选清单、扫描持久化目录构建 agent 池注册表。不
          实例化任何 Provider 或 Agent。
        - 一进程可构造多个实例（各自 Task 的 contextvar 隔离），互不
          共享存储。
        - 不启动工作循环（没有 Agent 时没有 Task）、不打开端口。

        :param persist_dir: 持久化根目录（可选）。``None`` → 默认
            ``<cwd>/.flowing``，目录推迟到首个持久化动作才建（零持久化
            场景不落盘）；显式指定 → 构造时固化并立即建目录。路径支持
            ``@/`` 前缀规则（经 :meth:`resolve_path`）。构造后不可更改。
        :raises RuntimeError: 未经 ``flowing.launch`` 登记 ``@`` 上下文
            （绕过唯一入口）时抛出。

        .. seealso:: :func:`flowing.runtime.launch`
        """
        root = _current_project_root.get()
        if root is None:
            raise RuntimeError("must register the @ context via flowing.launch (bypassing the single entry point)")
        self.project_root = root   # 固化 @ 上下文（构造后不改）
        self.node_id = "runtime-0"
        self.runtime = self   # ProvideNode 协议成员：链终点的 runtime 自指
            # （仅注解不赋值不会产生实例属性——isinstance(runtime, ProvideNode)
            # 需要数据成员真实存在）
        self._nodes = {self.node_id: self}   # 自注册为 _nodes 首条目——inject 链终点
            # 可达性的结构保证之一（另一根：根节点 _parent_id 指向本 id）；
            # shutdown 销毁循环须跳过自身（Runtime 无 destroy()）
        self._plugins = {}
        self.tool_registry = ToolRegistry()
        self._agent_types = {}   # 须在 register_builtins 之前初始化（ExploreAgent 注册写本表）
        # 框架自带工具与标准子智能体注册（物理实现全部在
        # flowing.builtins）：核心内置 subagent-invoke / finish + 六个标准
        # 文件/shell 工具 + ExploreAgent，随 Runtime 天生在场，归属
        # builtin:: 命名空间（裸名视图的兜底层，可被 default:: 覆盖）；
        # 对 LLM 的可见性仍由 Agent 级 ``.fya`` ``tools:`` 声明或显式
        # ``add_tool`` 控制（注册 ≠ 可见，框架不静默附加）
        register_builtins(self)
        self._provided = {}
        self._config_overrides = {}
        self._config_namespaces = {}
        self._resources = {}
        self._agent_pool = {}
        self._states = {}
        if persist_dir is not None:
            # 构造时固化：显式指定即建目录（@/ 前缀规则经 resolve_path）
            self._persist_dir = self.resolve_path(str(persist_dir))
            self._persist_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._persist_dir = Path.cwd() / ".flowing"   # 默认持久化根：推迟到首个持久化动作才建（零持久化不落盘）
        config_home = Path(os.environ.get(
            "FLOWING_CONFIG_HOME", os.path.expanduser("~/.flowing")))
        self._model_tags_path = Path(os.environ.get(
            "FLOWING_MODEL_TAGS",
            str(config_home / "model-tags.yaml")))   # 默认启用（模型标签不可不启用）：env 优先、默认 $FLOWING_CONFIG_HOME/model-tags.yaml、set_model_tags 可覆盖
        self._models_path = Path(os.environ.get(
            "FLOWING_MODELS_PATH",
            str(config_home / "models.yaml")))   # 构造期就地换算有效默认路径（Agent._resolve_model_tag 要求非 None）
        self._shutdown_event = asyncio.Event()
        # 扫描 providers.yaml 构建 provider 候选清单（load_provider_candidates；
        # 懒加载原则：不实例化任何 Provider）。路径解析：FLOWING_PROVIDERS_PATH
        # 环境变量优先，否则 $FLOWING_CONFIG_HOME/providers.yaml（默认
        # ~/.flowing/）；set_providers 可在 mount 前编程覆盖
        self._providers_path = Path(os.environ.get(
            "FLOWING_PROVIDERS_PATH",
            str(config_home / "providers.yaml")))
        self.provider_registry = ProviderRegistry(load_provider_candidates(self._providers_path))
        # 配置优先级链三层浅合并（框架默认 < 项目级 @/config.yaml < 用户级
        # $FLOWING_CONFIG_HOME/config.yaml）在 __init__ 尾部同步完成，完成后
        # 置就绪闸；命令行层不进链（由调用方经 set_config 落 _config_overrides
        # 表达），env 层暂无 key 映射规约（FLOWING_* 均为路径/开关类，由各自
        # 消费点直读）
        self._config_ready = False
        self._merged_config = self._merge_config_layers()
        self._config_ready = True
        self.env = MappingProxyType(os.environ)   # os.environ 只读视图（渲染上下文 env 入口）
        # 框架自登记 core + default 两全局命名空间——core 管框架注册表
        # （agents/session_dirs/plugins）、default 管 runtime 自身生命周期
        # 状态 + 小插件键（键前缀隔离）；register_state 创建即 replay（开
        # 空间即恢复），随后池扫描（构造期只读——不在 cwd 建默认目录，
        # 零持久化场景不污染工作目录）
        self.register_state("core")
        self.register_state("default")
        self._scan_agent_pool()

    @property
    def persist_dir(self) -> Path:
        """持久化根目录（只读）：构造时固化的值，构造后不可更改。

        .. rubric:: 行为要点

        - 与 :attr:`project_root` 同为构造固化字段；目录是否已建取决于
          构造参数：显式指定即建，缺省（``None``）推迟到首个持久化动作。
        - 供宿主 / 插件 / 子类读取（如向持久化根写入自己的文件）——
          ``FileRecordStore`` 不自建上级目录，写入前目录须已存在。

        .. seealso:: :meth:`flowing.runtime.Runtime.create_agent`
            （``session_dir`` 参数）、:attr:`_persist_dir`
        """
        return self._persist_dir

    def install(self, *plugins: Plugin) -> None:
        """安装插件（阶段一启用）：按实参顺序执行各插件的 ``install(runtime)``。

        .. rubric:: 功能介绍

        框架核心层方法，双层启用的第一层。插件在 ``install`` 中注册全局能力
        （工具 / provide 值 / 配置命名空间 / Agent 类型 / Resource / 全局
        状态命名空间），随后 Agent 在 ``setup()`` 中经 ``use_xxx(self)`` 做
        实例级启用（阶段二）；未启用的扩展对 Agent 零开销。插件声明式依赖
        （``dependencies``）的校验随本方法增量执行：已装插件依赖图成环抛
        :class:`flowing.errors.DependencyError` （报错现场即引入环的那次
        ``install()``）；依赖缺失只 ``warnings.warn`` 警告、不抛错（「声明了
        依赖但实际用不上」是合法形态，``install()`` 可分批）。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.install(SkillPlugin())
            runtime.install(CommPlugin(), GuardrailPlugin())   # 分批合法

        .. rubric:: 行为要点

        - 推荐在首个 ``mount()`` / ``create_agent()`` / ``recover_agent()``
          之前完成全部 ``install()``。框架不校验调用时机——之后 ``install()`` 不报错，
          但已创建的 Agent 不会获得迟装插件注册的能力（插件注册只在
          ``install`` 发生）。
        - 同名 provide key 重复注册是覆盖更新（由 ``provide`` 的覆盖语义
          承载，后者生效，inject 实时可见）。
        - 每次安装后对当前已装集合做依赖增量校验（成环抛错、缺失警告）；
          不实例化 Provider / Agent——``install`` 里调用
          ``await runtime.create_agent(...)`` 属违规用法，框架不阻止但行为
          不受支持。
        - 重复安装同名插件 → 后安装者报 ``ValueError`` （插件表 key 冲突，
          一个 Runtime 同时只装一个同名插件）。

        :param plugins: 待安装插件实例，按顺序 install。
        :raises flowing.errors.ConfigNamespaceConflictError: 插件注册了已被
            占用的配置命名空间时（经 ``register_config_namespace`` 抛出）。
        :raises flowing.errors.DependencyError: 安装后已装插件依赖图成环时。
        :raises ValueError: 重复安装同名插件时。

        .. seealso:: :meth:`flowing.runtime.Runtime.mount`、
            :class:`flowing.plugins.Plugin`
        """
        for plugin in plugins:   # 按实参顺序执行 install；可分批调用
            if plugin.name in self._plugins:
                raise ValueError(
                    f"plugin installed twice: {plugin.name} — a plugin with the same name is already installed"
                    " (the later installer errors; the spec names no exception type — a programming error, so the builtin ValueError is used)")
            plugin.install(self)
            self._plugins[plugin.name] = plugin
        self._check_dependencies()   # 增量校验已装子图：成环抛 DependencyError；缺失 warnings.warn 不抛
        # install 中 register_state 已创建即 replay（开空间即恢复，D13）；
        # 写透落盘需要持久化根目录——有插件即建（原引导 _materialize 的职责；
        # 无状态插件 install() 也建目录——持久化根，无害）
        self._persist_dir.mkdir(parents=True, exist_ok=True)

    def get_plugin(self, name: str, *, strict: bool = True) -> Any | None:
        """按名查询已安装插件实例；``strict`` 标志兼容「直接用」与「探测」两种写法。

        .. rubric:: 功能介绍

        返回 ``install()`` 安装过的插件实例（查询源：``_plugins``，key 为
        ``plugin.name``）。未安装时的行为由 ``strict`` 决定：

        - ``strict=True`` （默认）：抛 ``KeyError``——直接用写法
          （``runtime.get_plugin("cron").plugin_dir``），未安装时立即以
          清晰异常失败，而非延迟到 ``None.plugin_dir`` 的
          ``AttributeError``；
        - ``strict=False``：返回 ``None``——探测写法
          （``if runtime.get_plugin("skill", strict=False) is not None: ...``）。

        默认取 ``True``：探测是有意主动触发的存在性检查，显式写
        ``strict=False`` 是意图的自我声明；「直接用」是更常见的形态，
        应享受更短的写法与更响亮的失败。

        .. rubric:: 使用示例

        .. code-block:: python

            # 直接用（默认形态）
            plugin_dir = runtime.get_plugin("cron").plugin_dir

            # 探测（显式关闭严格）
            if runtime.get_plugin("skill", strict=False) is not None:
                ...

        .. rubric:: 行为要点

        - 同步、只读查询，无副作用；不实例化插件（实例化只发生在
          ``install()``）、不触发 ``install``、不做依赖解析。
        - 同名插件重复安装已被 ``install()`` 拦截，本方法读到的必然是唯一实例。

        :param name: 插件的 ``name`` 类属性值（如 ``"cron"``）。
        :param strict: 未安装时是否抛 ``KeyError`` （默认 ``True``；传 ``False``
            则返回 ``None``，用于有意探测）。
        :returns: 插件实例，或未安装且 ``strict=False`` 时的 ``None``。
        :raises KeyError: ``strict=True`` （默认）且插件未安装。

        .. seealso:: :meth:`flowing.runtime.Runtime.install`、
            :class:`flowing.plugins.Plugin`、
            :meth:`flowing.runtime.Runtime.snapshot`
        """
        if strict:
            return self._plugins[name]   # 未安装 → KeyError（直接用写法的快速失败）
        return self._plugins.get(name)   # 未安装 → None（探测写法，须显式 strict=False）

    async def mount(
        self,
        node: str,
        *,
        agent_id: str | None = None,
        **kwargs: Any,
    ) -> ProvideNode:
        """挂载根节点（文件 → 根）：创建或恢复 ``parent_id=None`` 的 Agent 根。

        .. rubric:: 功能介绍

        ``node`` 为 Agent 根引用：``.fya`` **文件**（如 ``"@/root.fya"``）
        或含 ``agent.fya`` 的**目录**（如 ``"@/agents/coding-agent"``，目录
        形态与单文件并存，候选探测同 :meth:`create_agent` 路径形态）——
        mount 仅处理 Agent 根；Workflow 根由
        :meth:`flowing.plugins.workflow.WorkflowPlugin.launch` 创建（文件内
        需恰好一个 ``Workflow`` 子类，见 :mod:`flowing.plugins.workflow`）。
        可多次调用（多根并存，如多项目宿主）；不调用也合法（嵌入大程序，
        按需 ``create_agent()``）。

        mount / create_agent / recover_agent 三者分工：

        - ``mount(path)``：输入是 Agent 根引用（``.fya`` 文件或含
          ``agent.fya`` 的目录，负责 引用 → 类 的解析与装配），挂到对象图
          上 Runtime 之下成为根，其余管线与 ``create_agent`` 完全一致
          （内部直接委托）。
        - ``create_agent(agent_type)``：输入是类型名，新建任意节点。
        - ``recover_agent(id)`` / ``get_agent(id)``：输入是已有 id，
          恢复 / 现场恢复，不新建。

        幂等挂载（指定 ``agent_id`` 时）：手动 mount 的根是特殊节点，
        应当指定固定 id——``agent_id`` 已存在于池中则走恢复（委托
        ``recover_agent``，含休眠记录的现场恢复）而非新建；不存在则新建。
        第二次启动再 mount 同一文件同一 id，语义是「同一个根回来了」，
        不是「又创建了一个根」。``agent_id=None`` 则每次新建新根（自动
        生成 id）——多根并存 / 临时根的形态。根节点的类型只由 ``node``
        解析决定；``agent_id`` 只是身份指定，不改变类型。

        ``**kwargs`` 透传给节点初始化（进 ``setup(**kwargs)``），与
        :meth:`create_agent` 的 ``**kwargs`` 同一契约：写入池元数据
        持久化、是节点身份的一部分、应可 JSON 序列化。

        .. rubric:: 使用示例

        .. code-block:: python

            await runtime.mount("@/root.fya")                      # Agent 根（每次新建，单文件形态）
            await runtime.mount("@/agents/coding-agent")           # 目录形态（内含 agent.fya）
            await runtime.mount("@/root.fya",
                                agent_id="agent-main")             # 固定 id：第二次启动恢复同一根
            await runtime.mount("@/root.fya", locale="zh")         # 第二个根（不同 id）

        .. rubric:: 行为要点

        - 内部顺序：``agent_id`` 非空且在池中 → 委托 ``recover_agent``；
          否则 → 创建管线（委托 ``create_agent``）。mount 不承担插件
          依赖校验（校验在 ``install()`` 时增量执行）。
        - 返回后：节点已注册进 ``_nodes``、工作循环已启动、队列为空
          （挂起在 ``await queue.get()``）。返回不代表有活干。
        - 不接受已构造的实例（实例形态统一走 ``create_agent`` 等价管线，
          mount 只做文件 → 节点）；不读取消息、不启动网络服务、不等待
          任何回合结果。

        :param node: Agent 根引用（支持路径前缀规则）：``.fya`` 文件路径，
            或含 ``agent.fya`` 的目录路径（候选探测 ``AGENT.fya`` >
            ``agent.fya`` > ``<name>.agent.fya`` > ``<name>.fya``，与
            :meth:`create_agent` 的路径形态同一候选链）。
        :param agent_id: 可选，Agent 根的固定 ``node_id``。已存在于池中 →
            恢复而非新建（幂等挂载）；不存在 → 以该 id 新建；``None`` →
            每次新建（自动生成 id）。类型由 ``node`` 解析决定，本参数不
            指定类型。
        :param kwargs: 节点初始化参数（身份的一部分，见上文）。
        :return: 创建或恢复的根节点（``Agent``）。
        :raises FileNotFoundError: 路径不存在时。
        :raises ValueError: ``node`` 既不是 ``.fya`` 文件、也不是含
            ``agent.fya`` 的目录时（``.py`` Agent 根经 :meth:`create_agent`；
            Workflow 根经
            :meth:`flowing.plugins.workflow.WorkflowPlugin.launch`）。

        .. seealso:: :meth:`flowing.runtime.Runtime.create_agent`、
            :meth:`flowing.runtime.Runtime.recover_agent`、
            :meth:`flowing.plugins.workflow.WorkflowPlugin.launch`
        """
        resolved = self.resolve_path(node)   # 统一为路径字符串
        if not resolved.exists():
            raise FileNotFoundError(f"mount path does not exist: {resolved}")
        self._ensure_persist_ready()   # 首个 mount 前的持久化就位（persist 目录 + 插件清单 + 兜底引导）
        if resolved.is_file() and resolved.suffix != ".fya":
            # mount 仅接受 Agent 根：.fya **文件**，或含 agent.fya 的**目录**
            # （目录形态与单文件并存，候选探测 AGENT.fya > agent.fya >
            # <name>.agent.fya > <name>.fya，与 create_agent 的路径形态同
            # 链——F4）。其余文件形态（.py 等）经 create_agent；Workflow 根
            # 经 WorkflowPlugin.launch（mount 不再承载 Workflow）
            raise ValueError(
                f"mount only accepts .fya agent roots (file or directory): {node} — "
                ".py agent roots go through create_agent; Workflow roots go through WorkflowPlugin.launch")
        # Agent 根：与子 Agent 走同一条唯一创建入口，仅 parent_id=None 不同；
        # 幂等挂载：agent_id 指定且已在池中 -> 恢复而非新建（手动 mount 的
        # 根是特殊节点，固定 id 使第二次启动「同一个根回来了」）；
        # 类型仍由 node 解析决定
        if agent_id is not None and agent_id in self._agent_pool:
            return await self.recover_agent(agent_id, **kwargs)
        return await self.create_agent(node, parent_id=None, agent_id=agent_id, **kwargs)

    async def create_agent(self, agent_type: str, *, parent_id: str | None = None,
                           agent_id: str | None = None,
                           session_dir: str | Path | None = None,
                           **kwargs: Any) -> Agent:
        """创建新 Agent（纯新建路径）：分配新 ``node_id``，走完整创建管线并注册。

        .. rubric:: 功能介绍

        框架核心层方法，唯一真正执行创建的代码路径。``mount()`` /
        ``Agent.create_subagent()`` / ``Workflow.create_agent()`` 全部委托
        本方法——委托方只提供「自己的 ``node_id`` 作为 ``parent_id``」。
        ``agent_type`` 统一为字符串类型名（非类对象；类对象无法持久化）。

        .. rubric:: 使用示例

        .. code-block:: python

            agent = await runtime.create_agent("order-agent", parent_id=None,
                                               order_id="123")

        .. rubric:: 行为要点

        1. ``get_agent_class(agent_type)`` —— 类型名 → 类（惰性解析；裸名
           先查注册表，路径形态经 ``resolve_path``）。
        2. ``__new__`` 并绑定 ``node_id`` （``agent_id`` 指定时即该值，须
           不在池注册表与活体表中，重复抛 ``ValueError``；缺省
           ``f"{_id_prefix}-{uuid4()}"``）、``runtime`` 与 ``_parent_id``
           （``parent_id=None`` 翻译为 Runtime 的 ``node_id``——「根」由
           「亲节点是 Runtime」表达）。目录存在性检查：该 id 不在池 / 活体表
           但 session 目录已存在 → 抛 ``FileExistsError`` （可能是已归档
           的留档（``archive_agent``）或指定错了 ``session_dir`` /
           ``agent_id``；框架不在此销毁任何内容，由调用方捕获决定——
           改 id / 先删目录 / 运维恢复）。``agent_id`` 未指定（自动生成
           uuid）时撞目录不报错，重新生成随机 id（uuid 碰撞概率为零，
           此为防御性兜底）。
        3. ``instance.__init__()`` —— 同步骨架，建立持久化后端与
           ``_extra``。
        4. 身份四键整写 ``meta.json``：``agent_type`` / ``parent_agent_id`` /
           ``created_at`` / ``args``，JSON 整写、非状态。前置条件全部在
           setup 前已知；``before_create`` 对 kwargs 的改写不落盘——恢复时
           经 ``before_recover`` 重新表达。
        5. ``kwargs = await hooks.before_create.dispatch(instance, kwargs)``
           —— 可改写 kwargs。本钩子仅创建管线触发（recover 不触发），
           是「只应在创建时做」的逻辑落点；handler 只能来自类上 ``@on``
           声明（实例 hooks 在 ``__init__`` 注册，插件 / Composable 的
           挂载通道是 setup 里的 ``use_xxx``，赶不上本钩子）。与
           ``before_tool_call["subagent-invoke"]`` 的分工：后者只覆盖工具
           唤起路径，本钩子覆盖全部创建路径。此刻实例骨架已就位：
           ``_extra`` 可写、hooks 已建、可 ``register_state`` 声明状态键
           （声明只落 defaults 表，不落盘）；state 写透立即可用。
        6. ``await instance.setup(**kwargs)``。
        7. PENDING 检查（固定步骤，非钩子）：``PENDING`` 哨兵未兑现抛
           :class:`flowing.errors.MissingFieldError` （见
           ``flowing.parsable.PENDING``）；``@on`` 暂记未结算（目标钩子点
           在 setup 结束前未被 declare）抛
           :class:`flowing.errors.UnknownHookPointError`，消息列出未消费
           的钩子点名与方法名。
        8. 池注册：全局 ``core`` 名录追加新 ``agent_id`` （写透）+
           ``_agent_pool[node_id]`` （``agent_type`` / ``parent_agent_id`` /
           ``created_at`` / ``args``，args 用 ``before_create`` 改写后的
           最终值）。因此 ``**kwargs`` 应可 JSON 序列化（不可序列化值
           不被持久化，恢复时缺失）。
        9. ``_nodes[node_id] = instance`` —— 创建即注册。
        10. ``await hooks.after_create.dispatch(instance)``。
        11. 常驻工作循环 Task 启动，返回 instance。

        - 后置条件：Agent 已激活、就绪、工作循环运行；队列为空时挂起
          等待。
        - 不加载持久化状态（那是 ``recover_agent`` 管线的
          ``instance._restore()``）；不接受类对象作为 ``agent_type``；
          不带 ``resume`` 之类的双语义参数。

        :param agent_type: 字符串类型名（裸名或路径形态，经 ``get_agent_class``
            惰性解析）。
        :param parent_id: 亲节点 id（``None`` = 根节点，内部翻译为 Runtime 的
            ``node_id``，inject 链直连链终点；API 层的 ``None`` 只是默认值，
            不落进实例字段与池元数据）。
        :param session_dir: 该 agent 的 session 持久化目录（``tree.jsonl`` /
            ``state.jsonl`` 所在目录）。``None`` → 默认 ``persist_dir / node_id``；
            指定为绝对路径原样使用，相对路径以 ``runtime._persist_dir`` 为
            基准解析。存储形式：``persist_dir`` 内相对、根外绝对（与
            :meth:`to_project_path` 的表示约定同构）；存入池元数据
            ``session_dir`` 字段与 ``core`` 名录 ``session_dirs`` 映射，
            恢复 / 池扫描据此定位。属框架机制字段，不进 args。
        :param agent_id: 指定该 agent 的 ``node_id`` （可选）。``None`` → 自动生成
            ``{_id_prefix}-{uuid4()}``；指定值须不在池注册表与活体表中
            （与 :meth:`recover_agent` 的「要求已存在」对称：create 要求
            不存在），重复 → :class:`ValueError`。不校验格式，可以使用
            ``agent-`` 前缀（与自动生成的形态保持一致，便于看 id 知类型），
            建议使用可作目录名的字符（缺省 ``session_dir`` 时即 session
            目录名）。
        :param kwargs: 实例化参数，透传 ``before_create`` → ``setup(**kwargs)``，
            并作为 args 持久化。
        :return: 创建并注册完毕的 Agent。
        :raises KeyError: ``agent_type`` 无法解析（注册表与路径均不命中）时。
        :raises ValueError: ``agent_id`` 指定且已存在于池注册表或活体表时。
        :raises FileExistsError: 指定 id 不在池 / 活体表但 session 目录已
            存在时（归档留档态或指定错误）。
        :raises flowing.errors.MissingFieldError: PENDING 检查失败（延迟定义
            未兑现）时；哨兵语义见 ``flowing.parsable``。
        :raises flowing.errors.UnknownHookPointError: ``@on`` 暂记未结算
            （目标钩子点在 setup 结束前未被 declare）时。

        .. seealso:: :meth:`flowing.runtime.Runtime.recover_agent`、
            :meth:`flowing.agent.Agent.create_subagent`、
            :meth:`flowing.agent.Agent.setup`
        """
        from uuid import uuid4

        self._ensure_persist_ready()   # 入口就位：persist 目录 + core 插件清单
        agent_class = self.get_agent_class(agent_type)   # 类型名 → 类（惰性解析）
        instance: Agent = agent_class.__new__(agent_class)   # __new__ + 绑定
        if agent_id is not None:
            # 指定 agent_id：须不在池注册表与活体表中（创建冲突即报错——与
            # recover_agent 的「要求已存在」对称；_nodes 含 Workflow 节点，一并防撞）
            if agent_id in self._agent_pool or agent_id in self._nodes:
                raise ValueError(
                    f"agent_id already exists: {agent_id} — duplicate creation is not allowed"
                    " (create requires the id to be absent; use recover_agent to restore)")
            node_id = agent_id
            resolved_session = self._resolve_session_dir(session_dir, node_id)
            # 目录存在性检查（create 侧严格）：id 不在池/活体表但目录
            # 已存在 → FileExistsError（可能是 archive 留档或指定错了
            # session_dir / agent_id；runtime 不销毁任何内容，由调用方决定）
            if resolved_session.exists():
                raise FileExistsError(
                    f"session directory already exists: {resolved_session} — possibly an archived "
                    "record (archive_agent) or a wrong session_dir / agent_id; "
                    "change the id, delete the directory, or recover via ops")
        else:
            # 缺省自动生成：撞目录不报错，重新生成随机 id（uuid 碰撞概率为零，
            # 此为防御性兜底；session_dir 显式指定时目录即定点，撞了只能报错）
            while True:
                node_id = f"{agent_class._id_prefix}-{uuid4()}"
                resolved_session = self._resolve_session_dir(session_dir, node_id)
                if not resolved_session.exists():
                    break
                if session_dir is not None:
                    raise FileExistsError(
                        f"session directory already exists: {resolved_session} — the specified "
                        " session_dir already exists; check the path or delete the directory first")
        instance.node_id = node_id
        instance.runtime = self
        # None 翻译为 Runtime 的 node_id：「根」由「亲节点是 Runtime」表达，
        # _parent_id 字段内不出现 None（inject 链终点可达性的结构保证之一）
        instance._parent_id = parent_id if parent_id is not None else self.node_id
        instance._session_dir = resolved_session   # session 目录（Agent.__init__ 的 _open_stores 使用，骨架期即可知）
        # 管线负责建 session 目录（FileRecordStore 惰性打开句柄时不建上级目录；
        # 目录存在性检查已过，此处 mkdir 即「创建即注册」的物理侧）
        resolved_session.mkdir(parents=True, exist_ok=True)
        instance.__init__()   # 同步骨架
        # 身份四键整写 meta.json（JSON 整写、非状态、Runtime 属主）。前置
        # 条件核对：agent_type/parent_agent_id/args 来自调用方、created_at
        # 现取、session_dir 由管线预绑——全部在 setup 前已知，可行；
        # before_create 对 kwargs 的改写不落盘（恢复时经 before_recover
        # 重新表达，两条路径自洽）
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            json.dumps(kwargs)   # args 应可 JSON 序列化；不可序列化值不被持久化（恢复时缺失）
            persisted_args = kwargs
        except TypeError:
            _logger.warning("agent %s: args are not JSON-serializable; persisting meta.json with empty args", node_id)
            persisted_args = {}
        (resolved_session / "meta.json").write_text(json.dumps({
            "agent_type": agent_type,
            "parent_agent_id": instance._parent_id,
            "created_at": created_at,
            "args": dict(persisted_args),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        kwargs = await instance.hooks.before_create.dispatch(instance, kwargs)   # 可改写 kwargs
        await instance.setup(**kwargs)
        # PENDING 检查（固定步骤，非钩子；见 _check_pending——含 @on 暂记结算）
        _check_pending(instance, agent_type)
        # 模型初始解析（agent.py 类属性契约「实例化时由 model_tag 解析填充
        # 初始值」的管线落点）：setup 已直接赋 self.model（ModelConfig 任意
        # 模型通道）或改过 model_tag（__setattr__ 已重解析）则跳过；解析
        # 失败（模型文件缺失 / 标签未定义）即创建失败——fail fast，不留
        # 「创建成功但首次调用才爆雷」的窗口
        if "model" not in instance.__dict__:
            instance.model = instance._resolve_model_tag(instance.model_tag)
        # 池注册——core 名录追加（读-改-写写透；整表替换会丢掉既有名录项）
        core = self.states["core"]
        core["agents"] = [*core.get("agents", []), node_id]
        core["session_dirs"] = {
            **core.get("session_dirs", {}),
            node_id: self._store_session_dir(instance._session_dir),
        }   # 名录平行映射：agent_id → session 目录（根内相对 / 根外绝对；池扫描与恢复据此定位）
        self._agent_pool[node_id] = {
            "agent_type": agent_type,
            "parent_agent_id": instance._parent_id,   # 存翻译后的实际值，recover 直接回绑
            "created_at": created_at,
            "session_dir": self._store_session_dir(instance._session_dir),   # 自定义 session 目录（None 时为默认路径的存储形式）
            "args": dict(kwargs),     # args 是 Agent 身份的一部分；内存条目用 before_create 改写后的最终值（与 meta.json 的原始值对应，恢复时经 before_recover 再改写）
        }
        self._nodes[node_id] = instance   # 创建即注册
        await instance.hooks.after_create.dispatch(instance)
        instance._loop_task = asyncio.create_task(instance._work_loop())   # 常驻工作循环 Task 启动（具名句柄，destroy 的取消落点）
        return instance

    async def recover_agent(self, agent_id: str, **override_args: Any) -> Agent:
        """按已有 ``agent_id`` 恢复 Agent：``node_id = agent_id`` （身份连续、可重现）。

        .. rubric:: 功能介绍

        框架核心层方法。从 agent 池元数据 + 该 agent 自己的 session 目录
        （``tree.jsonl`` / ``core.jsonl`` / ``state.jsonl`` / ``meta.json``）
        重建实例。与 ``create_agent`` 共用管线结构，差异仅两处：``node_id``
        用已有 id；恢复多一步 ``instance._restore()`` （位于
        ``before_recover`` 之前）。恢复路径经 ``setup(**args)`` 触发
        ``before_recover`` / ``after_recover`` 钩子对，不再触发
        ``before_create`` / ``after_create``——两条钩子对完全独立。
        恢复与创建是两条语义不同的路径，故独立成方法，不合并为带
        ``resume`` 参数的单函数。

        .. rubric:: 使用示例

        .. code-block:: python

            # main 决定新建 or 恢复（子项目策略，框架不特殊处理 --resume）
            async def main(resume: str | None = None) -> Runtime:
                runtime = Runtime()
                if resume is not None:
                    agent = await runtime.recover_agent(resume)        # 续接
                else:
                    await runtime.mount("@/root.fya")                  # 新建
                return runtime

            # 覆盖：恢复同一个 agent 结构，但用新参数
            agent = await runtime.recover_agent("agent-xxx", order_id="789")

        .. rubric:: 行为要点

        1. 读池元数据 ``meta = _agent_pool[agent_id]``；不在池中即
           ``KeyError``。
        2. ``get_agent_class(meta["agent_type"])``。
        3. ``args = dict(meta.get("args", {}))``，``override_args`` 覆盖
           —— 默认透传持久化 args，override 覆盖。
        4. ``__new__`` → ``node_id = agent_id`` （已有 id，不是新 UUID）、
           ``runtime``、``_parent_id = meta["parent_agent_id"]`` （meta 存
           的是翻译后的实际值，直接回绑）、``_session_dir`` （
           ``meta["session_dir"]`` 回绑；缺省 ``persist_dir / agent_id``，
           兼容旧数据）。
        5. 亲代链可达性：亲节点在池但不在 ``_nodes`` → 逐级向上
           ``recover_agent(parent_id)`` （到 Runtime 止——inject 上溯依赖
           亲代链完整，亲节点缺位会在子恢复后造成 ``MissingProvideError`` 假
           故障）；亲节点悬空（既不在 ``_nodes`` 也不在池、且非
           ``runtime-0``）→ ``warnings.warn`` 孤儿警告，仍继续恢复本节点
           （运维应跑 ``archive_orphans()`` 清理）。
        6. ``__init__()`` —— 同步骨架，建立持久化后端与 ``_extra``。
        7. ``await instance._restore()`` —— 只有 recover 有这一步；重放
           该 agent session 目录的 ``tree.jsonl`` / ``core.jsonl`` /
           ``state.jsonl`` （重放是读 / 加载，先于 ``before_recover``——
           重放后 setup 中写 state 不再被覆盖）。
        8. ``args = await hooks.before_recover.dispatch(instance, args)``
           （可改写）→ ``await instance.setup(**args)``。
        9. PENDING 检查（固定步骤，非钩子）：同 create 管线——``PENDING``
           哨兵未兑现抛 ``MissingFieldError``；``@on`` 暂记未结算抛
           ``UnknownHookPointError``。
        10. ``_nodes`` 注册。
        11. ``await hooks.after_recover.dispatch(instance)``。
        12. 常驻工作循环 Task 启动，返回 instance。

        - 恢复后不变量：已持久化消息全部在（可继续对话）；``current_head_id``
          指向消息树中最后持久化的消息；队列待消费消息在，恢复后作为新
          逻辑 Turn 处理；进行中的逻辑 Turn 不恢复（未持久化消息丢弃，
          撕裂末行丢弃）。
        - 恢复不递归子 agent（子代经「有 key 无 value → 现场恢复」在
          ``get_agent`` 时惰性重建）；但向上递归亲代链（见管线第 5 步）。
        - 不做「半截 Turn 精确续跑」；不校验 ``override_args`` 与创建时
          args 的一致性。

        :param agent_id: 池注册表中已有的 agent id。
        :param override_args: 覆盖持久化 args 的参数。
        :return: 恢复并注册完毕的 Agent。
        :raises KeyError: ``agent_id`` 不在池注册表中时。

        .. seealso:: :meth:`flowing.runtime.Runtime.create_agent`、
            :meth:`flowing.runtime.Runtime.get_agent`、
            :meth:`flowing.agent.Agent.setup`、
            :meth:`flowing.agent.Agent._restore`
        """
        self._ensure_persist_ready()   # 入口就位：persist 目录 + core 插件清单
        meta = self._agent_pool[agent_id]   # 读池元数据；不在池中即 KeyError
        agent_class = self.get_agent_class(meta["agent_type"])
        args: dict[str, Any] = dict(meta.get("args", {}))
        args.update(override_args)   # override 覆盖持久化 args
        instance: Agent = agent_class.__new__(agent_class)   # node_id 用已有 id（身份连续）
        instance.node_id = agent_id
        instance.runtime = self
        instance._parent_id = meta["parent_agent_id"]   # meta 存的是翻译后实际值（根条目为 Runtime 的 node_id），直接回绑
        # 亲代链可达性：亲节点在池但不在 _nodes → 逐级向上 recover_agent(parent_id)
        # （到 Runtime 止：根条目的亲节点是 runtime-0，在 _nodes 中即终止；inject
        # 上溯依赖亲代链完整，亲节点缺位会在子恢复后造成 MissingProvideError 假
        # 故障）；亲节点悬空（既不在 _nodes 也不在池、且非 runtime-0）→
        # warnings.warn 孤儿警告，仍继续恢复本节点（不抛错，保持可用性；
        # 运维应跑 archive_orphans() 清理）
        recover_parent = instance._parent_id
        if recover_parent != self.node_id and recover_parent not in self._nodes:
            if recover_parent in self._agent_pool:
                await self.recover_agent(recover_parent)
            else:
                warnings.warn(
                    f"recover_agent: parent node {recover_parent} of {agent_id} is dangling"
                    " (not in _nodes nor in the pool) — continuing to restore this node as an orphan; inject "
                    "lookup will end with MissingProvideError at the break; clean up via "
                    "archive_orphans()")
        instance._session_dir = self._load_session_dir(meta.get("session_dir"), agent_id)   # 自定义 session 目录回绑（缺省 persist_dir/agent_id，兼容旧数据）
        if not instance._session_dir.exists():
            # 名录在案但目录缺失：可诊断告警 + 按空 session 容忍（与
            # Agent._restore 的「按空 session 处理并报出可诊断错误」同裁）；
            # mkdir 使 _restore 的压缩 sync 有落点（FileRecordStore 不建上级目录）
            warnings.warn(
                f"recover_agent: session directory of {agent_id} is missing"
                f" ({instance._session_dir}) — restoring with an empty session")
            instance._session_dir.mkdir(parents=True, exist_ok=True)
        instance.__init__()   # 同步骨架
        # _restore 前移到 setup 前：双袋重放先做完，setup 中写 state 不再被
        # 重放覆盖、读 state 可见持久值
        await instance._restore()   # 仅 recover 有；重放 tree.jsonl / core.jsonl / state.jsonl（后端已在 __init__ 建立）
        args = await instance.hooks.before_recover.dispatch(instance, args)   # 可改写 args
        await instance.setup(**args)   # 触发 before/after_recover 钩子对（不触发 before/after_create）
        # PENDING 检查（同 create 管线——见 _check_pending，含 @on 暂记结算）
        _check_pending(instance, meta["agent_type"])
        # 模型初始解析（同 create 管线落点；setup 已直接赋 self.model 则跳过）
        if "model" not in instance.__dict__:
            instance.model = instance._resolve_model_tag(instance.model_tag)
        self._nodes[agent_id] = instance   # 创建即注册
        await instance.hooks.after_recover.dispatch(instance)
        instance._loop_task = asyncio.create_task(instance._work_loop())   # 常驻工作循环 Task 启动（具名句柄，destroy 的取消落点）
        return instance

    def get_node(self, node_id: str, *, strict: bool = True) -> ProvideNode | None:
        """按 ID 从 ``_nodes`` 取节点（共享 ID 空间单点查表）。

        .. rubric:: 功能介绍

        框架核心层方法，``inject_from`` 上溯与销毁子树定位的查表入口。
        节点 ID 带类型前缀（``runtime-`` / ``workflow-`` / ``agent-``），
        看 ID 即知类型，单个查表即可覆盖全部节点。

        .. rubric:: 使用示例

        .. code-block:: python

            parent = runtime.get_node(
                runtime.snapshot(keys={"nodes"}).nodes[agent.node_id].parent_id)

        .. rubric:: 行为要点

        - 只查活实例注册表；已 ``destroy()`` 的节点不在其中
          （其池记录 / session 仍保留，属 ``get_agent`` 的现场恢复语义）。
        - 不触发任何恢复逻辑。
        - ``strict`` （与 :meth:`get_plugin` 同构）：``True`` （默认）未命中
          抛 ``KeyError``——直接用写法（``inject_from`` 上溯依赖此形态）；
          ``False`` 未命中返回 ``None``——探测写法。

        :param node_id: 节点 ID（带前缀）。
        :param strict: 未命中时是否抛 ``KeyError`` （默认 ``True``；传
            ``False`` 返回 ``None``，用于有意探测）。
        :return: 节点实例；``strict=False`` 且未注册时为 ``None``。
        :raises KeyError: ``strict=True`` （默认）且 ``node_id`` 未注册时。

        .. seealso:: :meth:`flowing.runtime.Runtime.get_agent`、
            :func:`flowing.runtime.inject_from`
        """
        if strict:
            return self._nodes[node_id]   # 未注册即 KeyError（直接用写法的快速失败）
        return self._nodes.get(node_id)   # strict=False：探测写法，未注册 -> None

    async def get_agent(self, agent_id: str, *, strict: bool = False) -> Agent | None:
        """按 ``agent_id`` 查 agent 池；池记录存在但实例不存在时现场恢复。

        .. rubric:: 功能介绍

        框架核心层方法，统一语义「有 key 无 value → 现场恢复」的入口：
        池中有活实例直接返回；池记录存在但实例不存在（主 agent 恢复后
        未实例化的子 agent、或已 ``destroy()`` 但 session 保留的子 agent）
        时，内部走 ``recover_agent(agent_id)`` 现场重建后返回。调用方无需
        区分「从未实例化」与「已销毁」——恢复后上下文延续（「同一个子
        agent」有记忆）；恢复代价与树大小无关（主 agent 恢复不递归子
        agent）。

        .. rubric:: 使用示例

        .. code-block:: python

            agent = await runtime.get_agent(child_id)
            result = await agent.query(...)         # 恢复后继续调用

        .. rubric:: 行为要点

        - 现场恢复流程：查池 → 见 key 无 value → 按池元数据
          ``agent_type`` 重建实例并重放该 agent session 目录（消息级树 /
          状态）→ 返回实例（细节见 :meth:`recover_agent`）。
        - 因可能触发异步恢复管线，本方法是协程。
        - 不递归恢复子 agent 的子 agent（逐层惰性）；不做模糊匹配。
        - ``strict`` （与 :meth:`get_plugin` 同构）：``False`` （默认）完全
          不在池中返回 ``None``——探测写法；``True`` 完全不在池中抛
          ``KeyError``——直接用写法。strict 只作用于「完全不在池」；在池
          的活体 / 现场恢复路径不受其影响。

        :param agent_id: 池注册表中的 agent id。
        :param strict: 完全不在池中时的行为（默认 ``False`` 返回 ``None``；
            传 ``True`` 抛 ``KeyError``）。
        :return: 活 Agent 实例（原有的或现场恢复的）；``strict=False`` 且
            完全不在池中时为 ``None``。
        :raises KeyError: ``strict=True`` 且 ``agent_id`` 完全不在池中时。

        .. seealso:: :meth:`flowing.runtime.Runtime.recover_agent`、
            :meth:`flowing.agent.Agent.destroy`
        """
        if agent_id in self._nodes:
            return self._nodes[agent_id]   # 活实例直接返回
        if agent_id in self._agent_pool:
            # 「有 key 无 value → 现场恢复」：按元数据重建实例并重放 session（在 recover_agent 内）
            return await self.recover_agent(agent_id)
        if strict:
            raise KeyError(agent_id)   # strict=True：直接用写法的快速失败
        return None   # 默认探测形态：完全不在池中返回 None（不模糊匹配）

    async def archive_agent(self, node_id: str) -> list[str]:
        """从运行时完全归档一个节点及其整棵子树（递归清除，保留文件）。

        .. rubric:: 功能介绍

        框架核心层方法。把 ``node_id`` 及其全部后代从运行时清除：
        ``_nodes`` （活体表，经 ``destroy()`` 摘除）、``_agent_pool``
        （池注册表，仅 Agent 有条目）、全局 ``core`` 名录
        （``states["core"]["agents"]``，写透）。保留文件：各 session
        目录（``tree.jsonl`` / ``core.jsonl`` / ``state.jsonl`` /
        ``meta.json``）原样留档——归档不等于删除记录。``node_id`` 可为
        任意 ``_nodes`` / 池成员（Agent 或 Workflow——二者都注册
        ``_nodes``、都有 ``_parent_id`` 与 ``destroy()``，本方法不区分
        类型）。

        与 ``destroy()`` 的区别：``destroy()`` 只丢实例、保留池 key 与
        名录（「有 key 无 value → 现场恢复」）；``archive_agent`` 把 key
        与名录一并移除——归档后 ``get_agent`` 返回 ``None`` /
        ``recover_agent`` 找不到，运行时完全遗忘，文件留档供审计 /
        手动恢复（外部运维）。

        .. rubric:: 行为要点

        - 流程：双来源收集子树（池 ``parent_agent_id`` 链——覆盖全部
          Agent 子代，含已 destroy 的池条目；``_nodes`` ``_parent_id``
          链——覆盖在 ``_nodes`` 但不在池的节点，两来源重叠去重）→ 清理
          各被归档节点的亲代侧引用（亲代 Agent 的 ``child_ids`` 条目移除并
          写透）→ 对仍存活的节点 ``await destroy()`` （Agent / Workflow
          各自实现，Workflow 的 destroy 级联其子 Agent）→ 从
          ``_agent_pool`` 与 ``core`` 名录移除（写透；不在池的节点
          pop 幂等）。
        - 返回：被归档的 ``node_id`` 列表（含自身与全部后代）。
        - 归档后不变量：节点不在 ``_nodes`` 与 ``_agent_pool``；``core``
          名录不含这些 id；session 目录与文件保留。
        - 不删除任何 session 目录 / 文件；不递归恢复；不影响
          ``shutdown()`` （归档节点不在 ``_nodes``，销毁循环自然跳过）。
        - 边缘情况：归档根节点（``parent_id == "runtime-0"``）合法；归档
          后 session 目录保留（名录无 id + 目录存在 = 归档留档态，不报错、
          不可自动恢复；之后显式 ``create_agent`` 撞该目录会抛
          ``FileExistsError``）。

        :param node_id: 要归档的节点 id。
        :return: 被归档的 ``node_id`` 列表（含自身与全部后代）。
        :raises KeyError: ``node_id`` 在 ``_nodes`` 与 ``_agent_pool`` 中
            均不命中时（不做模糊匹配）。

        .. seealso:: :meth:`flowing.runtime.Runtime.get_agent`、
            :meth:`flowing.runtime.Runtime.recover_agent`、
            :meth:`flowing.runtime.Runtime.archive_orphans`
        """
        if node_id not in self._nodes and node_id not in self._agent_pool:
            raise KeyError(node_id)   # 双来源均不命中即抛错（不做模糊匹配）
        # 1. 双来源递归收集子树：① 池链（parent_agent_id，覆盖全部 Agent 子代
        #    含已 destroy 的池条目）；② _nodes 链（_parent_id，覆盖在 _nodes
        #    但不在池的节点）。两来源重叠去重。
        to_archive: list[str] = []
        stack = [node_id]
        while stack:
            cur = stack.pop()
            if cur in to_archive:
                continue
            to_archive.append(cur)
            for pid, meta in self._agent_pool.items():
                if pid not in to_archive and meta.get("parent_agent_id") == cur:
                    stack.append(pid)
            for nid, node in self._nodes.items():
                if nid not in to_archive and getattr(node, "_parent_id", None) == cur:
                    stack.append(nid)
        # 2. 清理亲代侧 child_ids（亲节点为活 Agent 时，写透整表）——archive 是显式
        #    遗忘通道，对「child_ids 只增不改」的受控例外；child_ids 核心键
        #    在 core 袋（property 透传），读经 parent.child_ids、写经
        #    _core_state 整表写
        for aid in to_archive:
            meta = self._agent_pool.get(aid)
            parent_id: str | None = (
                meta.get("parent_agent_id") if meta is not None
                else getattr(self._nodes.get(aid), "_parent_id", None))
            parent = self._nodes.get(parent_id) if parent_id else None
            if parent is not None and parent is not self and hasattr(parent, "child_ids"):
                # Workflow 等非 Agent 节点无 child_ids property——跳过
                ids = dict(parent.child_ids)
                for name, cid in list(ids.items()):
                    if cid == aid:
                        del ids[name]
                parent._core_state["child_ids"] = ids
        # 3. destroy 仍存活的节点（多态分派：Agent.destroy / Workflow.destroy——
        #    后者级联归档其子；已归档子跳过，互调幂等）
        for aid in to_archive:
            node = self._nodes.get(aid)
            if node is not None:
                await node.destroy()
        # 4. 池注册表 + core 名录移除（写透持久化；不在池的节点 pop 幂等）
        for aid in to_archive:
            self._agent_pool.pop(aid, None)
        archived = set(to_archive)
        core_agents = list(self.states["core"].get("agents", []))
        self.states["core"]["agents"] = [a for a in core_agents if a not in archived]
        return to_archive

    async def archive_orphans(self) -> list[str]:
        """归档全部 parent 悬空的池条目（孤儿），逐个递归连同各自子树。

        .. rubric:: 功能介绍

        框架核心层方法。枚举 ``_agent_pool`` 中 ``parent_agent_id`` 悬空
        的 Agent 条目（亲节点 id 既不在 ``_nodes`` 也不在 ``_agent_pool``），
        对每个经 :meth:`archive_agent` 递归归档（孤儿自身可能还有子树，
        一并清除）。返回全部被归档的 ``node_id`` 列表。

        孤儿从何而来：亲节点可能不持久化（如 Workflow 运行状态不落盘、
        崩溃后节点消失），而子 Agent 走标准创建管线持久化入池——崩溃重启
        后子条目 ``parent_agent_id`` 悬空。正常关闭路径由亲节点自身的
        ``destroy()`` 级联处理；崩溃路径无法执行级联，由本方法在恢复后
        运维清理。

        .. rubric:: 行为要点

        - 悬空判定：``parent_agent_id`` 非空，且不在 ``self._nodes`` 也
          不在 ``self._agent_pool``。根节点（亲节点为 ``runtime-0``，在
          ``_nodes`` 中）、活 Workflow 子代（亲节点在 ``_nodes``）、亲节点在池的
          条目均不判定为孤儿。
        - 每个孤儿经 :meth:`archive_agent` 递归归档（含其子树）；孤儿之间
          无亲子重叠（子条目因亲节点在池而不入选），归档安全。
        - 无孤儿 → 返回空列表（可随时调用）。
        - 不校验「parent 悬空」是否确由崩溃造成（也可能是亲节点被归档后的
          残留——归档亲节点本就递归含子，正常路径不产生，但本方法不区分来源，
          一律清理）。
        - 与 ``recover_agent`` 的关系：恢复遇孤儿亲节点只 ``warnings.warn``
          警告、继续恢复本节点，不自动归档——归档是显式运维动作，由本
          方法承载。

        :return: 全部被归档的 ``node_id`` 列表；无孤儿时为 ``[]``。

        .. seealso:: :meth:`flowing.runtime.Runtime.archive_agent`、
            :meth:`flowing.plugins.workflow.Workflow.destroy`
        """
        orphans = [
            aid for aid, meta in self._agent_pool.items()
            if meta.get("parent_agent_id")
            and meta["parent_agent_id"] not in self._nodes
            and meta["parent_agent_id"] not in self._agent_pool
        ]
        archived: list[str] = []
        for aid in orphans:
            archived.extend(await self.archive_agent(aid))   # 递归归档含各自子树
        return archived

    def provide(self, key: str | InjectionKey[Any], value: Any) -> None:
        """根级 provide 注册（inject 链终点的供方）。

        .. rubric:: 功能介绍

        ``ProvideNode`` 协议实现。注册的值对全树所有节点可见（上溯终点）。
        应用层概念（宿主工程根、locale、trace adapter 等）经 provide 注入
        而非进 ``@/`` 路径体系。``InjectionKey[T]`` 仅作编译期类型标注；
        注入存储的 key 始终是字符串（``str(key)`` 归一），类型信息不跨
        节点传递。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.provide("locale", "zh")
            runtime.provide("workspace_root", workspace_root)

        .. rubric:: 行为要点

        - 影响 Agent 装配期（setup / 装配钩子）读取的 provide 值应在
          ``mount()`` / ``create_agent()`` 之前注册——装配期 inject 未命中
          即 ``MissingProvideError``；运行期 provide / 覆盖合法（inject
          实时查找即刻可见），是运行时变更的正规通道（如运行时切换
          locale）。
        - 同 key 重复 provide 是覆盖更新（后者生效）；``inject`` 每次实时
          沿链查找、不缓存，更新即刻对后续 inject 可见。防止插件间静默
          覆盖靠 ``InjectionKey`` / key 名的前缀约定（如 ``"comm:..."``），
          机制本身不拦截。
        - value 不做序列化、不进持久化；API key / 凭证等敏感信息禁止经
          provide 传递（注入值沿链对后代节点可见的安全边界）。

        :param key: provide key（字符串，或 ``InjectionKey`` 标注）。
        :param value: 任意对象。

        .. seealso:: :class:`flowing.runtime.ProvideNode`、
            :func:`flowing.runtime.inject_from`、
            :class:`flowing.params.InjectionKey`
        """
        k = str(key)   # InjectionKey[T] 仅编译期类型标注；注入存储的 key 始终是 str
        # 同 key 重复 provide = 覆盖更新（inject 实时查找即刻可见）
        self._provided[k] = value   # 敏感信息（API key / 凭证）禁止进入

    def inject(self, key: str | InjectionKey[T]) -> T:
        """根级 inject：在链终点（本 Runtime 的注入存储）查找，未命中抛错。

        .. rubric:: 功能介绍

        ``ProvideNode`` 协议实现，等价于 ``inject_from(self, self, key)``
        ——Runtime 没有亲节点，查找即终点，只查根级注入存储。

        .. rubric:: 使用示例

        .. code-block:: python

            locale = runtime.inject("locale")

        .. rubric:: 行为要点

        - 只查根级注入存储，无上溯（已是终点）。
        - 不查 ``_config_overrides`` / ``_resources`` （三个存储语义独立：
          provide 跟随节点生命周期，Resource 跨 Agent 树外直引，config
          覆盖层是运行期配置复写）。

        :param key: provide key。
        :return: 命中值。
        :raises flowing.errors.MissingProvideError: 未命中时，携带 ``key``。

        .. seealso:: :func:`flowing.runtime.inject_from`、
            :exc:`flowing.errors.MissingProvideError`
        """
        result: T = inject_from(self, self, key)   # 链终点查找（Runtime 无亲节点）
        return result

    def get_config(self, key: str | ConfigKey[T], default: T | None = None) -> T | None:
        """配置读取：返回运行期覆盖层或构造期浅合并后的最终值（实例方法）。

        .. rubric:: 功能介绍

        框架核心层方法。读取顺序：``set_config`` 运行期覆盖层最优先，其次
        为构造期浅合并结果（用户级 ``$FLOWING_CONFIG_HOME/config.yaml`` >
        项目级 ``@/config.yaml`` > 框架推荐默认值）。环境变量与命令行参数
        不直接进入本链——``FLOWING_*`` 由各自消费点直读，命令行参数经
        ``set_config`` 表达。``ConfigKey[T]`` 约束 ``default`` 的类型与
        泛型参数一致（mypy / pyright 可检查）。读取全部是实例方法，不存在
        「隐式拿到某个 Runtime」的旁路——调用方须持有 Runtime 实例。无
        命名空间访问控制：注册只是「谁负责校验」的声明，未注册命名空间
        静默保留、可自由读取。

        .. rubric:: 使用示例

        .. code-block:: python

            timeout = self.get_config("agent.timeout", default=60)   # Agent.setup 内
            lang = runtime.get_config("i18n.default_lang")           # 未注册命名空间也可读

        .. rubric:: 行为要点

        - 就绪时点：浅合并完成（``Runtime()`` 构造尾部）之前，典型即模块
          顶层 import 期，调用抛
          :class:`flowing.errors.ConfigNotReadyError`；合并完成后任何时机
          可调用（``setup()``、钩子回调、工具 callable、``main()`` 后续
          代码、插件运行期方法等），读取为现场求值（每次读取都重新计算，
          不缓存结果）。
        - 浅合并语义：同名 key 高优先级覆盖；未覆盖的 key 沿用低优先级值；
          列表值整列表覆盖（不合并）。
        - 读取顺序：``set_config`` 运行期覆盖层命中优先，其次为浅合并
          结果。
        - 不校验 key 属于哪个命名空间；不写回配置文件（写入走
          ``set_config``，不持久化）。
        - 已知核心 key 与默认值：``agent.timeout`` （60）、
          ``agent.max_turns`` （20）、``agent.max_depth`` （10）、
          ``runtime.log_level`` （``"info"``）。

        :param key: 配置 key（点分字符串或 ``ConfigKey[T]``）。
        :param default: key 不存在时的默认值；类型须与 ``ConfigKey[T]`` 一致。
        :return: 配置值或 ``default``。
        :raises flowing.errors.ConfigNotReadyError: 配置未就绪（合并完成前
            调用）时。

        .. seealso:: :meth:`flowing.runtime.Runtime.register_config_namespace`、
            :meth:`flowing.runtime.Runtime.set_config`、
            :class:`flowing.params.ConfigKey`
        """
        # 调用时机约束：优先级链合并完成（__init__ 尾部）之前——典型即模块
        # 顶层 import 期——调用抛 ConfigNotReadyError；就绪判定 = _config_ready
        # 闸（__dict__ 读取兼管子类 super().__init__() 之前的过早调用）
        if not self.__dict__.get("_config_ready", False):
            raise ConfigNotReadyError()   # 无字段叶子（固定英文提示消息）
        k = str(key)
        if k in self._config_overrides:
            return self._config_overrides[k]   # set_config 运行期覆盖层优先
        return self._merged_config.get(k, default)   # 浅合并产物（点分扁平 key）；未注册命名空间静默保留、可自由读取；ConfigKey[T] 约束 default 类型

    def set_config(self, key: str, value: Any) -> None:
        """运行期配置覆盖（写入覆盖层，``get_config`` 读取时最优先）。

        功能与动机：下游产品运行期复写框架配置的通道——``main()`` 内读
        产品自己的配置文件，命中则 ``runtime.set_config("agent.timeout",
        ...)`` 覆盖框架配置链的取值。

        .. rubric:: 使用示例

        .. code-block:: python

            product_cfg = load_yaml("config.local.yaml")   # 产品自己的文件
            if "agent.timeout" in product_cfg:
                runtime.set_config("agent.timeout", product_cfg["agent.timeout"])

        .. rubric:: 行为要点

        - 同 key 覆盖写（后写胜出）；不持久化（进程级，重启即失效——持久
          覆盖请改配置文件 / 环境变量）。
        - 即时生效语义以各读取方为准（``get_config`` 现场求值），框架不
          广播变更（无响应式系统）。

        :param key: 配置 key（点分字符串；可覆盖 ``agent.timeout`` /
            ``agent.max_turns`` / ``agent.max_depth`` /
            ``runtime.log_level`` 等已知 key，及扩展注册的命名空间 key）。
        :param value: 覆盖值。

        .. seealso:: :meth:`flowing.runtime.Runtime.get_config`
        """
        self._config_overrides[key] = value   # 同 key 覆盖写（后写胜出；不持久化）

    @property
    def config(self) -> dict[str, Any]:
        """项目配置合并视图（Parsable 渲染上下文的 ``config`` 入口）。

        优先级链浅合并产物的嵌套形态——模板里 ``{{ config.agent.timeout }}``
        逐级取值（Jinja attr→item 回退）；``get_config`` 消费的是点分扁平
        形态。现场反摊平，只读语义——改写返回的 dict 不回写框架。不含
        ``set_config`` 的运行期覆盖层（渲染上下文契约是「配置合并视图」，
        覆盖层只服务 ``get_config``）。
        """
        nested: dict[str, Any] = {}
        for dotted, value in self.__dict__.get("_merged_config", {}).items():
            parts = dotted.split(".")
            cursor = nested
            for part in parts[:-1]:
                nxt = cursor.setdefault(part, {})
                if not isinstance(nxt, dict):
                    break   # 叶子与命名空间撞名：保留先到者（配置组织异味，不报错）
                cursor = nxt
            else:
                cursor[parts[-1]] = value
        return nested

    @overload
    def get_resource(self, name: str) -> Any: ...
    @overload
    def get_resource(self, name: str, type_hint: type[T]) -> T: ...
    def get_resource(self, name: str, type_hint: type[T] | None = None) -> Any:
        """共享 Resource 读取；``type_hint`` 仅 IDE 推断，运行时不做 isinstance 校验。

        .. rubric:: 功能介绍

        框架核心层方法。Resource 是跨任务、跨 Agent 树共享的实例存储
        （知识库、连接池等），生命周期由 Runtime 管理、独立于任何 Agent——
        因此不走 inject 链（provide / inject 的值跟随 Agent 生命周期，
        Resource 在树外直引）。读写通道对称：``register_resource`` /
        ``get_resource`` 都是实例方法。Agent 便捷方法 ``Agent.get_resource()``
        委托 ``self.runtime``；工具 callable 经 ``caller.get_resource(...)``
        统一入口。

        .. rubric:: 使用示例

        .. code-block:: python

            # main.py
            runtime.register_resource("kb", KnowledgeBase("./index/docs"))

            # 工具 callable 内（框架自动注入 caller）
            async def search_kb(query: str, caller: Agent = None) -> list[str]:
                kb = caller.get_resource("kb", KnowledgeBase)
                return await kb.search(query)

        .. rubric:: 行为要点

        - name 未注册 → ``ResourceNotFoundError``。
        - ``type_hint`` 不做运行时校验（纯类型标注通道）。
        - 不惰性创建（Resource 在 ``mount()`` 前由 ``register_resource``
          显式注册完毕）；不经 provide 链查找。

        :param name: Resource 注册名。
        :param type_hint: 期望类型（仅类型检查器与 IDE 使用）。
        :return: 注册实例。
        :raises flowing.errors.ResourceNotFoundError: name 未注册时。

        .. seealso:: :meth:`flowing.runtime.Runtime.register_resource`、
            :meth:`flowing.agent.Agent.get_resource`
        """
        if name not in self._resources:
            raise ResourceNotFoundError(name)
        return self._resources[name]   # type_hint 仅 IDE 推断，运行时不做 isinstance 校验

    def register_tool(self, tool: Tool | str, *, namespace: str | None = None) -> None:
        """注册全局工具（``ns::规范名`` → ``Tool``，写入 ``tool_registry``）。

        .. rubric:: 功能介绍

        插件三类资源注册之一（插件 ``install`` 中的注册通道）。全局注册表
        存「可执行对象 + 默认 LLM 可见声明」，Agent 级差异由 ``ToolEntry``
        绑定层覆写，不改全局注册表。两种入参形态：

        - 已构造的 ``Tool`` 实例——直接注册；
        - 文件路径字符串（``.fya`` / ``.py``，支持 ``@/`` ``./`` 等前缀
          规则）——先经 ``ToolRegistry.get`` 的文件解析链构造出 Tool 再
          注册（插件随身携带的文件形态工具即在 ``install()`` 中经此通道
          解析注册；基准目录显式给出，不经 Agent 的 ``source_dir`` 链）。

        .. rubric:: 行为要点

        - ``ns::name`` 完整键（含命名空间）冲突时后注册者报错；注册只在 ``install`` 发生
          （运行时不增删全局注册状态）；Agent 侧按别名查 ``_tool_entries``
          （见 ``flowing.tool``）。
        - 命名空间：``namespace`` 显式指定时与文件路径无关（覆盖目录
          派生）；缺省时——实例形态落入 ``default::`` （裸名视图优先层——
          往 ``default::`` 注册与核心同名的工具即覆盖原生行为，被覆盖者
          仍可用 ``builtin::name`` 显式引用），文件形态从所在目录派生
          命名空间；自定义命名空间的资源只能以 ``ns::name`` 全限定名
          引用（命名空间规则见 :meth:`get_agent_class`）。

        :param tool: 已构造的 ``Tool`` 实例，或工具定义文件路径字符串
            （``.fya`` / ``.py``）。
        :param namespace: 命名空间；``None`` → 实例形态 ``"default"`` /
            文件形态按目录派生；显式指定时与文件路径无关。

        .. seealso:: :class:`flowing.tool.Tool`、:class:`flowing.tool.ToolEntry`、
            :attr:`flowing.runtime.Runtime.tool_registry`
        """
        if isinstance(tool, str):
            # 文件形态：经 ToolRegistry.get 的文件解析链构造并注册
            # （基准目录显式——插件包目录等，不经 Agent source_dir 链）；
            # get 内部已按目录派生注册命名空间，显式 namespace 时覆盖之
            tool = self.tool_registry.get(tool)   # 文件 → Tool（惰性解析链）
            if namespace is None:
                return   # get 已按目录派生注册，不再二次注册
        self.tool_registry.register(tool, namespace=namespace)   # ns::name 完整键（含命名空间）冲突时后注册者报错（由 ToolRegistry.register 承载）

    def register_agent_type(self, name: str, agent_class: type[Agent], *,
                            namespace: str | None = None) -> None:
        """注册子 Agent 类型（``ns::注册名`` → Agent 类），使任何 Agent 可以引用。

        .. rubric:: 功能介绍

        插件三类资源注册之一（插件 ``install`` 中的注册通道）。注册表
        条目同样惰性——``get_agent_class`` 在 invoke / 创建时才解析类。
        注册表 key 为 ``ns::注册名``；名称格式（kebab / snake / Pascal）
        由框架自动转化。

        .. rubric:: 使用示例

        .. code-block:: python

            class MyPlugin(Plugin):
                def install(self, runtime: Runtime) -> None:
                    runtime.register_agent_type("payment-agent", PaymentAgent,
                                                namespace="myplugin")
                    # 引用方需写 myplugin::payment-agent

        .. rubric:: 行为要点

        - ``ns::name`` 完整键（含命名空间）冲突时后注册者报错；注册只在 ``install`` 发生。
        - 命名空间：缺省落入 ``default::`` （裸名视图优先层，同名即覆盖
          核心内置类型）；建议（非强制）插件用自身注册名作命名空间
          （``myplugin::xxx``）；自定义命名空间的类型只能以全限定名引用
          （命名空间规则见 :meth:`get_agent_class`）。

        :param name: 注册名（身份名推断的锚点）。
        :param agent_class: Agent 子类。
        :param namespace: 命名空间（缺省 ``"default"``）。

        .. seealso:: :meth:`flowing.runtime.Runtime.get_agent_class`、
            :class:`flowing.agent.Agent`
        """
        # ns::name 完整键（含命名空间）冲突时后注册者报错（注册表 = 类注解 _agent_types）
        key = f"{namespace or 'default'}::{name}"
        self._agent_types[key] = agent_class   # 条目惰性：get_agent_class 在 invoke/创建时才解析
        agent_class.registry_key = key   # 回写（与 Tool/Skill.registry_key 同构；文件派生注册点同律）

    def register_config_namespace(self, name: str, schema: Any) -> None:
        """声明扩展的配置命名空间（「谁负责校验」的声明，非访问控制）。

        .. rubric:: 功能介绍

        框架只解析核心命名空间（``agent`` / ``runtime``），其余 key 透传给
        经本方法声明的扩展；扩展对已声明命名空间有完全控制权（校验、默认
        值、类型转换——经 ``schema`` 表达，具体形态由扩展自定）。未注册
        命名空间静默保留，``get_config()`` 可自由读取。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.register_config_namespace("i18n", I18nSchema)

        .. rubric:: 行为要点

        - 多扩展注册同一命名空间 → ``ConfigNamespaceConflictError``；
          注册只在 ``install`` 发生。

        :param name: 配置命名空间名（点分 key 的第一段）。
        :param schema: 扩展自定的校验 / 默认值 / 类型转换声明。

        .. seealso:: :meth:`flowing.runtime.Runtime.get_config`、
            :exc:`flowing.errors.ConfigNamespaceConflictError`
        """
        if name in self._config_namespaces:
            raise ConfigNamespaceConflictError(name)   # 多扩展注册同一命名空间
        self._config_namespaces[name] = schema   # 注册只是「谁负责校验」的声明，非访问控制

    def register_resource(self, name: str, instance: Any) -> None:
        """注册共享 Resource（name → 任意实例，不要求继承基类）。

        .. rubric:: 功能介绍

        与 ``get_resource`` 读写对称；Resource 生命周期跨 Agent（GB 级索引、
        连接池等加载一次全局共享）。与 provide / inject 的区分原则：值跟随
        Agent 生命周期 → provide / inject；跨 Agent 跨任务 → Resource。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.register_resource("kb", KnowledgeBase("./index/product-docs"))
            runtime.register_resource("order_db", DatabasePool(dsn))

        .. rubric:: 行为要点

        - name 在 Runtime 内全局唯一，重复注册 →
          ``ResourceNameConflictError``。
        - 必须在启动根 Agent（``mount()``）前完成。
        - 框架不代管 instance 的析构。

        :param name: Resource 注册名。
        :param instance: 任意实例。

        .. seealso:: :meth:`flowing.runtime.Runtime.get_resource`、
            :exc:`flowing.errors.ResourceNameConflictError`
        """
        if name in self._resources:
            raise ResourceNameConflictError(name)   # name 在 Runtime 内全局唯一
        self._resources[name] = instance   # 须在 mount() 前完成；框架不代管析构


    def snapshot(self, *, keys: set[str] | None = None) -> RuntimeSnapshot:
        """一致性只读快照：Runtime 全局状态的观测入口（拉取通道）。

        .. rubric:: 功能介绍

        返回 :class:`flowing.snapshot.RuntimeSnapshot`——节点表、插件清单、
        agent 池、provider 候选名、配置覆盖层、资源的一次性只读视图。
        供测试断言、repl ``/snapshot``、serve ``GET /snapshot`` 使用。
        插件状态不在快照中（框架不为插件提供快照命名空间挂载机制），经
        :meth:`get_plugin` 拿插件自己的只读 API 观测。

        .. rubric:: 使用示例

        .. code-block:: python

            snap = runtime.snapshot()
            assert snap.agents["agent-xxx"].loaded is True

        .. rubric:: 行为要点

        - 只读：修改返回对象不影响 Runtime；字段为拷贝或 Info 视图。
        - 一致性：单次调用内各字段取同一时刻的读值。
        - ``keys``：``None`` （默认）收集全部切面；指定时只收集指定字段
          （其余为 ``None``）。需要多切面同一时刻一致 → 同一次调用传入
          全部所需 key。
        - 可序列化：全部字段 JSON 可序列化（``GET /snapshot`` 直接序列化
          即消费点）。
        - 不暴露项：``_provided`` 的值内容（凭证等敏感值绝不进入快照）、
          控制信号对象。
        - 完整字段契约见 :mod:`flowing.snapshot` 模块级 docstring。

        :param keys: 要收集的切面名集合（``nodes`` / ``agents`` /
            ``plugins`` / ``providers`` / ``config_overrides`` /
            ``resources``）；``None`` 收集全部。
        :return: ``RuntimeSnapshot`` 只读视图。

        .. seealso:: :meth:`flowing.agent.Agent.snapshot`、
            :class:`flowing.snapshot.RuntimeSnapshot`
        """
        def _want(name: str) -> bool:
            return keys is None or name in keys   # None 收集全部切面；指定时只收集指定字段（其余为 None）

        # nodes：_nodes 的 NodeInfo 只读投影（类型取共享 ID 空间前缀——
        # runtime-/agent-/workflow-；parent_id 对 Runtime 自身为 None——链终点无亲节点）
        nodes: dict[str, NodeInfo] | None = None
        if _want("nodes"):
            nodes = {
                node_id: NodeInfo(
                    type=node_id.split("-", 1)[0],
                    parent_id=getattr(node, "_parent_id", None))
                for node_id, node in self._nodes.items()
            }
        # agents：池注册表的 AgentInfo 投影（含 loaded 标记；created_at 存储形
        # 式为 ISO 字符串——state.jsonl 须 JSON 可序列化——此处还原 datetime）
        agents: dict[str, AgentInfo] | None = None
        if _want("agents"):
            agents = {}
            for agent_id, meta in self._agent_pool.items():
                raw_created = meta.get("created_at") or ""
                try:
                    created = (raw_created if isinstance(raw_created, datetime)
                               else datetime.fromisoformat(str(raw_created)))
                except ValueError:
                    created = datetime.fromtimestamp(0, timezone.utc)   # 缺省/旧数据兜底
                agents[agent_id] = AgentInfo(
                    agent_type=meta.get("agent_type", ""),
                    parent_agent_id=meta.get("parent_agent_id", ""),
                    created_at=created,
                    loaded=agent_id in self._nodes)
        return RuntimeSnapshot(
            nodes=nodes,
            plugins=([p.name for p in self._plugins.values()] if _want("plugins") else None),
            agents=agents,
            providers=(list(self.provider_registry._candidates) if _want("providers") else None),   # 仅候选名——实例化与否不进快照
            config_overrides=(dict(self._config_overrides) if _want("config_overrides") else None),   # 只读副本（set_config 覆盖层）
            resources=(list(self._resources) if _want("resources") else None),
        )   # _provided 的值内容绝不进入快照（凭证边界）

    def set_model_tags(self, path: str | Path) -> None:
        """指定模型标签映射文件的来源（可选初始化步骤，``mount()`` 之前调用）。

        .. rubric:: 功能介绍

        把 ``path`` 登记为模型标签映射文件（``model-tags.yaml``）的来源，
        覆盖默认路径。模型标签是单值映射（标签 → 单个模型条目名，无候选
        列表、无回退链——标签未定义即报错；「换模型」只能动态改
        ``self.model`` 或 ``model_tag``）。标签来源的优先级从高到低：本方法
        登记的路径（代码级）> ``FLOWING_MODEL_TAGS`` 环境变量指向的文件 >
        默认 ``$FLOWING_CONFIG_HOME/model-tags.yaml``——本方法只登记单个
        文件路径，不存在多文件合并。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.set_model_tags("@/model-tags.yaml")

        .. rubric:: 行为要点

        - 仅登记映射来源，解析发生在 ``self.model`` 求值时（现场求值，
          无缓存；文件解析经 :func:`flowing.model.load_model_tags`）。
        - 路径支持 ``@/`` 前缀规则。
        - 标签无映射 → 模型调用时直接报错（无回退链：未定义标签回退
          ``default``，``default`` 也未定义 → 报错，见 ``flowing.model``）。

        :param path: 模型标签映射文件路径。

        .. seealso:: :class:`flowing.model.ModelConfig`、
            :meth:`flowing.runtime.Runtime.resolve_path`
        """
        resolved = self.resolve_path(str(path))   # 路径支持 @/ 前缀规则
        # 仅登记映射来源（存储字段 = 类注解 _model_tags_path）；解析现场求值于
        # self.model（flowing.model.load_model_tags 消费），无缓存；
        # 标签无映射 → 模型调用时直接报错（无回退链，见 flowing.model）
        self._model_tags_path = resolved

    def set_models(self, path: str | Path) -> None:
        """指定 models.yaml 来源路径（可选初始化步骤，``mount()`` 之前调用）。

        .. rubric:: 功能介绍

        与 :meth:`set_model_tags` 对称的 models 侧通道——默认
        ``$FLOWING_CONFIG_HOME/models.yaml`` （``FLOWING_MODELS_PATH`` 环境
        变量重定向），本方法以编程方式覆盖来源。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.set_models("@/models.yaml")

        .. rubric:: 行为要点

        - 仅登记来源，解析发生在 ``self.model`` 求值时
          （:func:`flowing.model.load_models`，现场求值、无缓存）。
        - 路径支持 ``@/`` 前缀规则。

        :param path: models.yaml 文件路径。

        .. seealso:: :meth:`flowing.runtime.Runtime.set_model_tags`、
            :meth:`flowing.runtime.Runtime.resolve_path`
        """
        resolved = self.resolve_path(str(path))   # 路径支持 @/ 前缀规则
        # 仅登记来源（存储字段 = 类注解 _models_path）；models 读取现场求值于
        # self.model 解析链（flowing.model.load_models 消费，优先本路径，
        # 缺省默认/环境变量），无缓存
        self._models_path = resolved

    def set_providers(self, path: str | Path) -> None:
        """指定 providers.yaml 来源路径（可选初始化步骤，``mount()`` 之前调用）。

        .. rubric:: 功能介绍

        与 :meth:`set_model_tags` 对称的 providers 侧通道——默认
        ``$FLOWING_CONFIG_HOME/providers.yaml`` （``FLOWING_PROVIDERS_PATH``
        环境变量重定向），本方法以编程方式覆盖来源并立即重建候选清单。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.set_providers("@/providers.yaml")

        .. rubric:: 行为要点

        - 登记来源并立即重建 ``provider_registry`` （候选清单是构造期产物，
          ``mount()`` 前调用可覆盖；构造期无 provider 实例化，重建不丢
          任何已实例化条目——安全）。
        - 路径支持 ``@/`` 前缀规则；凭证（api_key 等）随文件，安全边界见
          ``flowing.providers`` 包 docstring。

        :param path: providers.yaml 文件路径。

        .. seealso:: :meth:`flowing.runtime.Runtime.set_models`、
            :meth:`flowing.runtime.Runtime.set_model_tags`、
            :func:`flowing.providers.load_provider_candidates`
        """
        resolved = self.resolve_path(str(path))   # 路径支持 @/ 前缀规则
        self._providers_path = resolved   # 登记来源
        # 重建候选清单（ProviderRegistry = 候选清单 + 懒实例化表；构造期无
        # provider 实例化，重建不丢任何已实例化条目——安全）
        self.provider_registry = ProviderRegistry(load_provider_candidates(resolved))

    def register_state(self, namespace: str, backend: str = "file") -> StateView:
        """开启 / 注册一个全局持久化状态命名空间（Runtime / 插件级）。

        .. rubric:: 功能介绍

        状态不挂在任何 agent 名下：写在持久化根目录的
        ``<namespace>.jsonl`` （一空间一文件，``'file'`` 后端细节）。
        承载 Runtime / 插件的全局状态——框架自登记的 ``core`` 全局命名
        空间含已注册 agent id 名录与已安装插件清单；``default`` 是
        Runtime 自身生命周期状态 + 小插件键的默认袋（经 :attr:`state`
        property 访问）；插件的全局登记类状态（如通信扩展的全局路由表）
        走自定义命名空间。加载 / 派生时机完全由插件内部管理：核心只提供
        基础设施——开启、写透、创建即 replay（开空间即恢复，与 Agent 侧
        :meth:`Agent.register_state` 对称）；何时读出持久值派生运行时结构
        是插件自己的事（懒重建、显式初始化方法均可），核心不提供
        ``load`` 恢复回调（Agent 侧的派生重建走 ``after_recover`` 钩子）。

        .. rubric:: 行为要点

        - 幂等：同命名空间重复声明是空操作、返回同一视图。
        - 创建即 replay：声明时即重放持久值进内存（恢复绑定在命名空间
          创建——``install()`` 后引导与 ``_ensure_persist_ready`` 兜底重放
          不再需要）。
        - ``backend`` 当前仅 ``'file'``；其它值 → ``ValueError``。
        - 重放产物是裸持久值（JSON 纯数据）；派生运行时结构的重建时机
          与方式由插件内部管理，异常由插件自行处理。

        :param namespace: 全局命名空间名（建议带插件前缀，如
            ``"comm.routes"``——框架不参与命名）。
        :param backend: 后端（当前仅 ``'file'``）。
        :return: 全局 :class:`flowing.persistence.StateView`。

        .. seealso:: :attr:`states`、:attr:`state`、
            :meth:`flowing.agent.Agent.register_state`
        """
        if backend != "file":
            raise ValueError(f"register_state backend only supports 'file': {backend!r}")
        existing = self._states.get(namespace)
        if existing is not None:
            return existing   # 幂等：同 ns → 同视图
        # 路径基 = _persist_dir（构造固化；开空间即恢复）
        view = StateView(FileRecordStore(
            self._persist_dir / f"{namespace}.jsonl", merge_last_line=True))
        # 创建即 replay（开空间即恢复）
        persisted = view._persisted
        for record in list(view._store.replay()):   # replay 是惰性生成器——显式消费
            op = record.get("op")
            if op == "set":
                persisted[record["key"]] = record["value"]
            elif op == "delete":
                persisted.pop(record["key"], None)
            # 未知行形态（meta 已被 replay 吸收）静默跳过
        if persisted:
            view._maybe_compact(force=True)   # 压缩时点①的 Runtime 侧落点
        self._states[namespace] = view
        return view

    @property
    def states(self) -> Mapping[str, StateView]:
        """全局命名空间注册表：只读 Mapping 视图——``["ns"]`` / ``in`` /
        ``.get()`` 均可用（读写语义继承
        :class:`flowing.persistence.StateView` 的类级契约）。

        .. seealso:: :meth:`register_state`、:attr:`state`
        """
        return MappingProxyType(self._states)

    @property
    def state(self) -> StateView:
        """Runtime 默认袋：``states["default"]``——与 ``agent.state`` 对称
        （runtime 自身生命周期状态 + 小插件键的落点）。"""
        return self._states["default"]

    # ------------------------------------------------------------------
    # 配置合并与持久化引导（内部 API）
    # ------------------------------------------------------------------

    @staticmethod
    def _read_config_file(path: Path) -> dict[str, Any]:
        """读一层配置文件（缺失按空层处理；内部 API）。"""
        if not path.exists():
            return {}
        data = YAML(typ="rt").load(path.read_text(encoding="utf-8"))
        return dict(data) if data else {}

    @staticmethod
    def _flatten_config(data: dict[str, Any], *, _prefix: str = "") -> dict[str, Any]:
        """嵌套 dict 摊平为点分 key（浅合并的落实粒度：叶子字段级覆盖——
        「同名 key 高优先级覆盖、未覆盖 key 沿用低优先级」；列表不递归，
        整列表覆盖。内部 API）。"""
        flat: dict[str, Any] = {}
        for key, value in data.items():
            dotted = f"{_prefix}{key}"
            if isinstance(value, dict):
                flat.update(Runtime._flatten_config(value, _prefix=f"{dotted}."))
            else:
                flat[dotted] = value   # 列表值不递归——整列表覆盖
        return flat

    def _merge_config_layers(self) -> dict[str, Any]:
        """优先级链浅合并（内部 API）。

        低 → 高：框架推荐默认值（``_FRAMEWORK_CONFIG_DEFAULTS``）< 项目级
        （``@/config.yaml``）< 用户级（``$FLOWING_CONFIG_HOME/config.yaml``，
        默认 ``~/.flowing/``）。逐层摊平为点分 key 后 ``dict.update`` 叠加。
        env 层暂无 key 映射规约（``FLOWING_*`` 均为路径 / 开关类，由各自
        消费点直读，不进合并链）；命令行层不进链——由调用方经 ``set_config``
        落覆盖层表达（读取时最优先）。
        """
        config_home = Path(os.environ.get(
            "FLOWING_CONFIG_HOME", os.path.expanduser("~/.flowing")))
        merged: dict[str, Any] = {}
        for layer in (
            _FRAMEWORK_CONFIG_DEFAULTS,
            self._read_config_file(self.project_root / "config.yaml"),
            self._read_config_file(config_home / "config.yaml"),
        ):
            merged.update(self._flatten_config(layer))
        return merged

    def _scan_agent_pool(self) -> None:
        """agent 池扫描（内部 API）：以全局 ``core`` 名录为池 key 唯一
        权威来源，逐个开 session 目录重建 ``{agent_id → 元数据}`` 注册表
        （实例不在扫描阶段创建）。已在池的 id 跳过（幂等）。
        """
        core = self._states.get("core")
        if core is None:
            return   # core 未注册（理论不可能——__init__ 注册即 replay）——防御
        agents = list(core.get("agents", []))
        session_dirs = dict(core.get("session_dirs", {}))
        for agent_id in agents:
            if agent_id in self._agent_pool:
                continue
            session_dir = self._load_session_dir(session_dirs.get(agent_id), agent_id)
            self._agent_pool[agent_id] = self._read_pool_meta(session_dir, agent_id)

    def _read_pool_meta(self, session_dir: Path, agent_id: str) -> dict[str, Any]:
        """从 session 目录的 ``meta.json`` 解析池元数据（扫描专用；内部 API）。

        ``meta.json`` 是身份四键（agent_type / parent_agent_id / created_at
        / args）的 JSON 整写文件（create 管线写入）。``meta.json`` 缺失即
        失败（``FileNotFoundError``），不回退读旧 ``state.jsonl``——历史
        session（无 meta.json）无效。
        """
        path = session_dir / "meta.json"
        if not path.exists():
            raise FileNotFoundError(
                f"pool scan: meta.json for registered {agent_id} is missing ({path})"
                " — identity metadata incompatible (decision 7: the historical session is invalid; no fallback read of the old state.jsonl)")
        meta = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or not isinstance(meta.get("args"), dict):
            raise ValueError(f"pool scan: meta.json of {agent_id} is malformed: {path}")
        meta["session_dir"] = self._store_session_dir(session_dir)   # 目录在 core 名录平行映射，meta.json 不含
        return meta

    def _ensure_persist_ready(self) -> None:
        """首个 mount/create/recover 前的持久化就位（内部 API）。

        建持久化根目录（默认路径推迟到真正的持久化动作才落盘，避免零
        持久化场景在 cwd 留下空 ``.flowing/``）→ core 名录写入已安装
        插件清单（此刻 persist 目录已就位，写透安全）→ 池扫描。
        """
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        core = self._states.get("core")
        if core is not None:
            plugins = sorted(self._plugins)
            if core.get("plugins", []) != plugins:
                core["plugins"] = plugins   # 写透（merge_last_line 防膨胀）
        self._scan_agent_pool()

    def resolve_path(self, path: str, *, source_dir: Path | None = None) -> Path:
        """路径前缀规则的执行器：``@/`` ``./`` ``../`` 绝对路径 → ``Path``。

        .. rubric:: 功能介绍

        框架核心层方法，所有文件路径引用的解析入口。前缀语义：``@/`` →
        ``project_root``；``./`` → ``source_dir``；``../`` →
        ``source_dir.parent``，多级 ``../../`` 逐级向上；绝对路径接受；
        裸名不走本方法（名称查找，仅指向框架内注册表的内置 Agent /
        工具 / 技能）。适用面：``$`` 引用、``{% include %}``、
        ``subagents:`` / ``tools:`` / ``skills:`` 等所有文件路径引用。

        根内相对不变量：解析结果越出 ``project_root`` 合法（绝对路径或
        ``../`` 逃逸均可），但对象（消息 / 快照 / 日志 / 错误的对外文本）
        中的路径表示分两种：根内一律根相对形式（``@/a/b``）；根外保留
        绝对路径（如 ``/etc/x``——跨机共享语义本就只对项目内容成立，
        宿主环境路径如实呈现）。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime.resolve_path("@/tools/search.py")
            runtime.resolve_path("./subagents/*", source_dir=agent_dir)

        .. rubric:: 行为要点

        - ``./`` / ``../`` 前缀且未提供 ``source_dir`` → 报错（调用方应传
          ``agent.source_file`` 所在目录，见 ``flowing.agent``）。
        - 仅用于 flowing 项目资源引用；不涉及 flowing 自身的配置读取——
          框架配置（``providers.yaml`` / ``config.yaml`` / 用户级配置）由
          宿主启动层（``flowing.interfaces`` 各入口）直接读取，其值经
          config / provide 注入系统，路径本身
          不进入任何对象。
        - 输入语法：``@/`` / ``./`` / ``../`` / 绝对路径（``is_absolute``
          判定，POSIX 前导 ``/`` 与 Windows 盘符 / UNC 均算），以及含 ``/``
          或反斜杠字符且不含 ``::`` 的普通相对路径（以 ``source_dir`` 为
          基准）；前缀判定中 ``\\`` 与 ``/`` 等价（``.\\`` / ``..\\`` 视同
          ``./`` / ``../``）；``~`` / ``~/...`` 永远按绝对路径触发——经
          ``os.path.expanduser`` 展开为家目录后按绝对路径规则处理（词法
          细节的唯一来源是 :func:`flowing.paths.resolve_path`）。
        - 边缘情况：``@/`` 不带后续路径段时表示根目录本身（通用规则的自
          然结果，无需特判）；裸 ``@`` 不带斜杠不命中任何形态，按裸名 /
          普通相对路径处理（几乎必为笔误）。
        - 不做存在性检查（解析不等于打开）；不做 glob 展开（展开由调用方）。

        :param path: 带前缀路径字符串。
        :param source_dir: 相对前缀的基准目录。
        :return: 解析后的 ``Path``。
        :raises ValueError: 相对路径（``./`` / ``../`` 或含 ``/`` / 反斜杠
            字符）缺少 ``source_dir`` 时。

        .. seealso:: :meth:`flowing.runtime.Runtime.to_project_path`、
            :func:`flowing.runtime.resolve`
        """
        return _paths_resolve_path(
            path, project_root=self.project_root, source_dir=source_dir)   # 纯函数委托(词法唯一来源在 flowing.paths)

    def to_project_path(self, absolute: Path) -> str:
        """反向表示：绝对路径 → ``@/`` 前缀字符串（用于日志与错误提示）。

        .. rubric:: 功能介绍

        与 ``resolve_path`` 互逆的显示层工具：项目内路径用 ``@/`` 表示更短
        更可移植。

        .. rubric:: 行为要点

        - 根内路径 → ``@/`` 前缀字符串；根外路径原样返回绝对路径字符串
          （不报错——跨机共享语义只对项目内容成立，宿主环境路径如实
          呈现）。
        - 纯字符串运算，不触碰文件系统。

        :param absolute: 绝对路径。
        :return: ``@/`` 前缀字符串或原样绝对路径字符串。

        .. seealso:: :meth:`flowing.runtime.Runtime.resolve_path`
        """
        return _paths_to_project_path(absolute, project_root=self.project_root)   # 纯函数委托

    async def shutdown(self) -> None:
        """优雅关闭：递归 destroy → 插件收尾 → 关闭全局状态视图 → 置位退出事件。

        .. rubric:: 功能介绍

        框架核心层方法。语义是「请求关闭」而非「同步等待全进程退出」——
        发信号后立刻返回，善后流程在本方法内按上述顺序执行完毕。协作式
        关闭：不用 ``os._exit`` / 强制 kill（除非关闭本身卡死，那是应用层
        兜底）。信号处理（SIGINT / SIGTERM）→ 本方法 → ``await runtime``
        处被唤醒 → 进程退出。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime = await flowing.launch(path)
            await runtime                  # 阻塞至 shutdown
            # 另一 Task / 信号处理器中：
            await runtime.shutdown()

        .. rubric:: 行为要点

        - 内部顺序（不变量）：递归 destroy 所有节点（Agent 的工作循环
          Task 被取消，session 记录保留——各 Agent 的 tree / state 后端在
          其 ``destroy()`` 内排空关闭；Workflow 节点级联销毁其子 Agent）
          → 插件收尾（按 ``install()`` 调用顺序逐个 ``await``
          ``plugin.shutdown()``；单插件异常记日志后继续——尽力收尾路径）
          → 关闭全部全局状态视图（排空 + 停写；放在插件收尾之后，插件
          ``shutdown()`` 中仍可写全局状态）→ ``_shutdown_event.set()``。
        - 不变量：插件收尾与全局状态视图关闭均发生在
          ``_shutdown_event.set()`` 之前——``await runtime`` 解除阻塞时
          所有善后已完成。
        - 重复调用安全：收尾流程已完成过（事件已置位）的直接返回；已置位
          的事件重复置位无副作用。
        - 空 Runtime（无 Agent）合法：直接跳到插件收尾。
        - 不删除任何 session 目录（池 key 到显式删目录才移除）；不等待
          ``await runtime`` 的 waiter 实际被调度唤醒。

        .. seealso:: :meth:`flowing.runtime.Runtime.__await__`、
            :meth:`flowing.agent.Agent.destroy`
        """
        # 幂等闸：收尾流程已完成过（事件已置位）的重复调用直接返回——
        # destroy / 插件收尾 / 视图关闭均不重演（重演会对已关闭的
        # RecordStore 再提交压缩请求而报错）
        if self._shutdown_event.is_set():
            return
        for node in list(self._nodes.values()):   # 递归 destroy 所有节点（Agent 的工作循环 Task 被取消，session 记录保留；Workflow 节点级联销毁其子 Agent）
            if node is self:
                continue   # Runtime 自注册在 _nodes 中但无 destroy()——销毁循环跳过自身
            await node.destroy()
        # 插件收尾：按 install 顺序逐个 await plugin.shutdown()；单插件
        # 异常记日志后继续（尽力收尾路径）
        for plugin in self._plugins.values():
            try:
                await plugin.shutdown()
            except Exception:
                _logger.exception(
                    "plugin %s raised during shutdown; continuing with remaining shutdown", getattr(plugin, "name", "?"))
        # 关闭全部全局状态视图（drain 排空 + 停写任务——排空屏障点；在插件
        # 收尾之后，插件 shutdown() 中仍可写全局状态）
        for view in self._states.values():
            if view._persisted:
                view._maybe_compact(force=True)   # 压缩时点②的 Runtime 侧落点（请求随 _close 排空一并执行；空袋跳过——避免为零内容命名空间建出实体文件）
            await view._close()
        self._shutdown_event.set()   # 末尾置位；幂等（重复置位无副作用）；空 Runtime 直接跳到此处

    def __await__(self) -> Generator[Any, None, None]:
        """使 ``await runtime`` 阻塞至 shutdown（进程存活语义）。

        .. rubric:: 功能介绍

        等价于 ``yield from self._shutdown_event.wait().__await__()``。与有无
        Agent 无关——空 Runtime（未 mount）同样有效，只等退出事件。「保持
        进程存活」与「关闭信号」解耦：CLI / 嵌入方统一 ``await runtime``；
        若 ``main()`` 返回后不做此 await，进程立即退出（mount 返回不等于
        有活干）。

        .. rubric:: 使用示例

        .. code-block:: python

            runtime = await flowing.launch(path)
            await runtime                          # 阻塞直到 shutdown()

        .. rubric:: 行为要点

        - 解除阻塞时点：``_shutdown_event.set()`` 之后——此时 destroy /
          插件收尾 / 全局状态视图关闭已全部完成。
        - 不消费消息、不做周期任务（Runtime 自身无事件循环职责）。

        .. seealso:: :meth:`flowing.runtime.Runtime.shutdown`
        """
        yield from self._shutdown_event.wait().__await__()   # 阻塞至 shutdown() 末尾置位；空 Runtime 同样有效

    def get_agent_class(self, agent_type: str, *,
                        source_dir: Path | None = None) -> type[Agent]:
        """类型名字符串 → Agent 类（惰性解析）——Agent 类型的唯一解析公开入口。

        .. rubric:: 功能介绍

        创建 / 恢复管线的第一步；也是插件 / 用户代码取类对象的公开入口。
        与 ``ToolRegistry.get`` / ``SkillRegistry.get`` 同构。形态判别委托
        :func:`flowing.paths.classify_ref` （词法唯一来源），三种引用形态：

        - 限定名（含 ``::``，如 ``myplugin::payment-agent``）：只查注册表
          精确键，不走文件查找链；
        - 裸名（如 ``payment``）：``source_dir`` 提供时先走文件查找链
          （相对 ``source_dir``——文件覆盖注册表）；``source_dir`` 缺省时
          跳过文件链。之后查注册表裸名视图——``default::`` 优先于
          ``builtin::`` （插件覆盖原生行为的通道）。需要文件上下文的调用
          走 :meth:`flowing.agent.Agent.get_agent_class` （自动携带
          ``source_dir``）；
        - 路径形态（``./`` / ``@/`` / glob）经 ``resolve_path`` 定位
          ``.fya`` 或手写 ``.py`` 后编译 / 加载（``@/`` 锚 ``project_root``
          无需 ``source_dir``；``./`` / ``../`` 缺省 ``source_dir`` 报错）。

        路径形态细则：

        - 目录形态候选链 ``AGENT.fya`` > ``agent.fya`` > ``<name>.agent.fya``
          > ``<name>.fya``，探测循环委托 :func:`flowing.paths.probe_candidates`、
          首个存在者生效（目录存在但无任一候选 → ``KeyError``；链上顺序
          只是确定性的消歧规则，不推荐同一链路真的同时存在多个候选文件）；
          同名 ``.fya`` 单文件与文件夹并存时文件夹优先；``.fya`` 与手写
          子类同名并存时 ``.fya`` 优先并告警。
        - 指向手写 ``.py`` 文件时，模块内需恰好一个 Agent 子类（与
          Workflow 定义文件的约定同构）；零个 →
          :class:`flowing.errors.FormatError`；多个 → 用 ``路径::ClassName``
          形态消歧（左段含路径特征——``/`` / 反斜杠 / ``.py`` 结尾——时
          按「文件::类名」解析，绕开「恰好一个子类」限制；与命名空间
          限定名 ``ns::name`` 的区分在 ``flowing.paths.classify_ref`` 词法
          层完成）。
        - 目录候选链只含 ``.fya``——不接管手写类的目录组织（手写类的
          目录组织走标准 Python 包机制 + ``register_agent_type``）。
        - 文件解析产物的命名空间从所在目录派生（``@/`` 下相对、根外绝对，
          文件夹式取上层目录），仅作内部身份标识，引用写法不变。
        - 两级惰性：亲代 Agent 实例化时只记元信息，创建 / invoke 时才加载类。

        .. rubric:: 行为要点

        - 解析失败抛 ``KeyError``。
        - 身份名一律推断（路径文件名 / 目录名、注册名、类名 ``__name__``）；
          ``.fya`` 或手写子类中写了 ``name`` 仅作一致性断言——与推断值
          不符抛 :class:`flowing.errors.NameMismatchError`；``class_name``
          推断规则见 :class:`flowing.agent.Agent`。

        :param agent_type: 类型名字符串（限定名 / 裸名 / 路径形态）。
        :param source_dir: 裸名与 ``./`` / ``../`` 路径形态的基准目录。
        :return: Agent 类。
        :raises KeyError: 注册表与文件链均无法解析时。
        :raises flowing.errors.FormatError: 手写 ``.py`` 模块内 Agent 子类
            数量不为恰好一个、且未用 ``路径::ClassName`` 消歧时。
        :raises flowing.errors.NameMismatchError: 声明 ``name`` 与推断身份
            名不符时。

        .. seealso:: :meth:`flowing.runtime.Runtime.register_agent_type`、
            :meth:`flowing.runtime.Runtime.create_agent`、
            :meth:`flowing.tool.ToolRegistry.get` —— 同构的 Tool 解析入口
        """
        # 精确键短路（先于形态判别）：文件派生限定键的命名空间含路径特征
        # （目录派生，如 "@/order-agent::payment"），过不了 classify_ref 的
        # 限定名判别（左段含 / 判成路径形态）——注册表在场证据优先于词法
        # 分流（与 ToolRegistry.get 同口径）
        if agent_type in self._agent_types:
            return self._agent_types[agent_type]
        # 形态判别委托 classify_ref（词法唯一来源）——注意不能用
        # `"::" in agent_type` 粗判：「文件::类名」（R21 消歧形态）也含 ::，
        # 但属路径形态（左段含路径特征时 classify_ref 判 "path"）
        form = _classify_ref(agent_type)
        if form == "qualified":
            # 限定名（ns::name）：只查注册表精确键，不走文件查找链
            if agent_type in self._agent_types:
                return self._agent_types[agent_type]
            raise KeyError(agent_type)
        if form == "bare":
            # 裸名：source_dir 提供时先走文件查找链（相对 source_dir——文件
            # 覆盖注册表）；缺省时跳过文件链
            if source_dir is not None:
                loaded = self._load_agent_from_name_chain(agent_type, source_dir)
                if loaded is not None:
                    return loaded
            # 注册表裸名视图：default:: 优先于 builtin::（插件覆盖原生行为通道）
            for key in (f"default::{agent_type}", f"builtin::{agent_type}"):
                if key in self._agent_types:
                    return self._agent_types[key]
            raise KeyError(agent_type)
        # 路径形态（./ @/ 绝对路径 / 含分隔符的相对路径 / 文件::类名）：
        # @/ 锚 project_root 无需 source_dir；./ ../ 缺省 source_dir 报错
        # （resolve_path 现有口径）
        path_part, sep, class_name = agent_type.partition("::")
        resolved = self.resolve_path(path_part, source_dir=source_dir)
        return self._load_agent_from_path(
            resolved, class_name if sep else None, ref=agent_type)

    def _load_agent_from_name_chain(
        self, name: str, source_dir: Path
    ) -> "type[Agent] | None":
        """裸名的定向文件查找链（内部 API）。

        相对 ``source_dir`` 探测：目录形态 ``<name>/`` 优先（候选链只含
        ``.fya``：``AGENT.fya > agent.fya > <name>.agent.fya > <name>.fya``，
        首个存在者生效）；目录外依次 ``<name>.fya`` 单文件、
        ``<name_snake>.py`` 手写文件。``.fya`` 命中经
        :meth:`_load_agent_from_fya` 编译装配；``.fya`` 与同名 ``.py`` 并存
        → 告警且 ``.fya`` 优先。全部未命中 → ``None`` （调用方继续查注册表
        裸名视图）。
        """
        directory = source_dir / name
        if directory.is_dir():
            hit = _probe_candidates(
                directory,
                ["AGENT.fya", "agent.fya", f"{name}.agent.fya", f"{name}.fya"])
            if hit is not None:
                return self._load_agent_from_fya(hit, ref=name)
        fya_file = source_dir / f"{name}.fya"
        py_file = source_dir / f"{_kebab_to_snake(name)}.py"
        if fya_file.exists():
            if py_file.exists():
                warnings.warn(
                    f"a same-name .fya and a handwritten .py coexist; the .fya wins: {fya_file} / {py_file}")
            return self._load_agent_from_fya(fya_file, ref=name)
        if py_file.exists():
            return self._load_agent_from_py(py_file, None, ref=name)
        return None

    def _load_agent_from_path(
        self, resolved: Path, class_name: "str | None", *, ref: str
    ) -> "type[Agent]":
        """路径形态的编译 / 加载（内部 API）。

        目录 → 候选链探测（只含 ``.fya``；无任一候选 → ``KeyError``）；
        ``.fya`` 文件 → :meth:`_load_agent_from_fya` 编译装配；
        手写 ``.py`` → :meth:`_load_agent_from_py`；不存在 / 其它后缀 →
        ``KeyError`` （解析失败的统一口径）。
        """
        if resolved.is_dir():
            name = _infer_name(resolved, naming=AGENT_NAMING)   # 目录：basename 即目录名
            hit = _probe_candidates(
                resolved,
                ["AGENT.fya", "agent.fya", f"{name}.agent.fya", f"{name}.fya"])
            if hit is None:
                raise KeyError(ref)   # 目录存在但无任一候选 → 解析失败
            return self._load_agent_from_fya(hit, ref=ref)
        if not resolved.exists():
            raise KeyError(ref)
        if resolved.suffix == ".fya":
            return self._load_agent_from_fya(resolved, class_name, ref=ref)
        if resolved.suffix == ".py":
            return self._load_agent_from_py(resolved, class_name, ref=ref)
        raise KeyError(ref)

    def _load_agent_from_fya(
        self, path: Path, class_name: "str | None" = None, *, ref: str
    ) -> "type[Agent]":
        """``.fya`` 命中的编译装配与派生注册（内部 API）。

        经 :func:`flowing.compiler.compile_fya_class` 现场合成 Agent 子类
        （``name`` 一致性断言在合成层完成）。派生注册与
        :meth:`_load_agent_from_py` 同范式：派生键
        ``to_project_path(dir)::infer_name`` + ``registry_key`` 回写 +
        派生键已在注册表 → 短路复用（不重复合成）。``class_name``
        （``路径::ClassName`` 形态）对 ``.fya`` 仅作一致性断言——单文件
        只合成一个类，不符 → :class:`flowing.errors.FormatError`。
        """
        from flowing.compiler import compile_fya_class   # 局部 import：模块头依赖图保持单向

        name = _infer_name(path, naming=AGENT_NAMING)   # 身份名推断（通用名取目录名）
        derived_key = f"{self.to_project_path(path.parent)}::{name}"   # 目录派生命名空间
        if derived_key in self._agent_types:
            cls = self._agent_types[derived_key]   # 派生键短路复用（文件解析是声明期行为）
            # 短路同样过 class_name 一致性断言——不得静默返回不符的已注册类
            if class_name is not None and cls.__name__ != class_name:
                raise FormatError(
                    f"registered synthesized class of {path} is {cls.__name__}, not {class_name!r}"
                    " (.fya files synthesize exactly one class per file; :: disambiguation is the mechanism for handwritten .py multi-class files)")
            return cls
        cls = compile_fya_class(path, project_root=self.project_root)  # F3：运行时编译自带项目根
        if class_name is not None and cls.__name__ != class_name:
            raise FormatError(
                f"synthesized class of {path} is {cls.__name__}, not {class_name!r}"
                " (.fya files synthesize exactly one class per file; :: disambiguation is the mechanism for handwritten .py multi-class files)")
        cls.registry_key = derived_key   # 回写（与 _load_agent_from_py 同构）
        self._agent_types[derived_key] = cls
        return cls

    def _load_agent_from_py(
        self, path: Path, class_name: "str | None", *, ref: str
    ) -> "type[Agent]":
        """加载手写 ``.py`` 中的 Agent 子类（内部 API）。

        模块内需恰好一个本文件定义的 Agent 子类（``__module__`` 过滤掉
        import 进来的）；零个 → ``FormatError``；多个 → ``FormatError``
        （消息指明用 ``路径::ClassName`` 消歧）；``class_name`` 指定时
        直接按名取（绕开「恰好一个」限制）。命中后注册到派生键（命名空间
        从所在目录派生：``@/`` 下根相对、根外绝对——``to_project_path``
        形式，仅作内部身份标识）并回写 ``cls.registry_key``；派生键已在
        注册表 → 短路复用（不重复加载）。``::ClassName`` 消歧形态的派生键
        含类名（``<目录键>::<身份名>::<类名>``——每类一键，否则同一多类
        文件先注册 ``::A`` 后 ``::B`` 会误短路返回 A）；无 ``class_name``
        时为 ``<目录键>::<身份名>``。类体写了 ``name`` 仅作一致性断言：
        与推断值不符抛 ``NameMismatchError``。
        """
        import importlib.util

        from flowing.agent import Agent   # 局部 import：模块头依赖图保持单向

        name = _infer_name(path, naming=AGENT_NAMING)   # 身份名推断（文件名去后缀、snake→kebab）
        base_key = f"{self.to_project_path(path.parent)}::{name}"   # 目录派生命名空间（内部身份标识）
        # ::ClassName 消歧形态的派生键含类名（每类一键）——同一多类文件
        # 先 ::A 后 ::B 时，B 不得误命中 A 的短路
        derived_key = f"{base_key}::{class_name}" if class_name is not None else base_key
        if derived_key in self._agent_types:
            return self._agent_types[derived_key]   # 派生键短路复用（文件解析是声明期行为）
        spec = importlib.util.spec_from_file_location(
            _path_to_module_name(path, project_root=self.project_root,
                                 prefix="flowing_agent_file_"), path)
        module = importlib.util.module_from_spec(spec)   # type: ignore[union-attr]
        spec.loader.exec_module(module)   # type: ignore[union-attr]
        if class_name is not None:
            cls = getattr(module, class_name, None)
            if not (isinstance(cls, type) and issubclass(cls, Agent)):
                raise FormatError(
                    f"no Agent subclass {class_name} in {path} (file::ClassName disambiguation failed)")
        else:
            candidates = [
                obj for obj in vars(module).values()
                if isinstance(obj, type) and issubclass(obj, Agent)
                and obj is not Agent and obj.__module__ == module.__name__
            ]
            if not candidates:
                raise FormatError(
                    f"no Agent subclass in {path} — handwritten .py files must define exactly one")
            if len(candidates) > 1:
                raise FormatError(
                    f"{path} contains multiple Agent subclasses"
                    f"（{', '.join(c.__name__ for c in candidates)}）——"
                    " — disambiguate with the path::ClassName form")
            cls = candidates[0]
        explicit_name = cls.__dict__.get("name")   # name 非机制字段：写了仅作一致性断言
        if explicit_name is not None and explicit_name != name:
            raise NameMismatchError(
                f"{cls.__name__} in {path} declares name={explicit_name!r}, "
                f"which does not match the inferred identity name {name!r}")
        cls.registry_key = derived_key   # 回写（与 register_agent_type 同构）
        self._agent_types[derived_key] = cls
        return cls

    def _resolve_session_dir(self, session_dir: str | Path | None, node_id: str) -> Path:
        """解析 agent 级 session 目录（内部 API，不属稳定契约）。

        ``None`` → ``persist_dir / node_id`` （默认）；绝对路径原样；
        相对路径以 ``runtime._persist_dir`` 为基准解析（``persist_dir / p``）。
        供 ``create_agent`` 管线绑定 ``instance._session_dir``。
        """
        if session_dir is None:
            return self._persist_dir / node_id
        p = Path(session_dir)
        return p if p.is_absolute() else self._persist_dir / p

    def _store_session_dir(self, path: Path) -> str:
        """session 目录的持久化存储形式（内部 API，不属稳定契约）。

        ``persist_dir`` 内 → 相对形式（``os.path.relpath``，可移植——目录迁移
        后仍有效）；根外 → 绝对路径原样。与 :meth:`to_project_path` 的
        表示约定同构。
        """
        try:
            rel = os.path.relpath(path, self._persist_dir)
        except ValueError:   # 跨盘（Windows）无法 relpath
            return str(path)
        return rel if rel != "." and not rel.startswith("..") else str(path)

    def _load_session_dir(self, stored: str | None, node_id: str) -> Path:
        """从存储形式恢复 session 目录（内部 API，不属稳定契约）。

        空 → ``persist_dir / node_id`` （兼容旧数据）；相对 → ``persist_dir / rel``；
        绝对 → 原样。供 ``recover_agent`` / 池扫描回绑 ``instance._session_dir``。
        """
        if not stored:
            return self._persist_dir / node_id
        p = Path(stored)
        return p if p.is_absolute() else self._persist_dir / p

    # ------------------------------------------------------------------
    # 内部方法（`_` 前缀）：以下签名承载关键时序，但不属于稳定契约。
    # ------------------------------------------------------------------

    def _check_dependencies(self) -> None:
        """插件依赖图增量校验（成环抛错、缺失警告）。内部 API，不属稳定契约。

        每次 ``install()`` 安装后对当前已装集合校验：

        - 成环 → 抛 :class:`flowing.errors.DependencyError` （报错现场即
          引入环的那次 ``install()``；``install()`` 可分批——缺依赖不报错，只有
          真成环才报）；
        - 依赖缺失 → ``warnings.warn`` 警告不抛（「声明了依赖但实际用
          不上」是合法形态；要严格化可用 ``-W error`` 升级）。运行时真
          用到缺失依赖时由 ``MissingProvideError`` （inject 失败）兜底。

        .. seealso:: :meth:`flowing.runtime.Runtime.install`、
            :exc:`flowing.errors.DependencyError`
        """
        for plugin in self._plugins.values():
            for dep in plugin.dependencies:   # 插件只声明 dependencies: list[str]
                if dep not in self._plugins:
                    warnings.warn(f"missing plugin dependency: {plugin.name} depends on the uninstalled {dep}")   # 警告不抛
        # DAG 无环校验（DFS 三色标记；只走已装集合内的边——缺依赖已在上方警告，
        # 不成环）：已装子图成环 → DependencyError（报错现场 = 引入环的那次 install()）
        color = dict.fromkeys(self._plugins, 0)   # 0=未访问 1=在栈 2=完成

        def _visit(name: str, stack: tuple[str, ...]) -> None:
            color[name] = 1
            for dep in getattr(self._plugins[name], "dependencies", []):
                if dep not in self._plugins:
                    continue
                if color[dep] == 1:
                    # 成环：复用 DependencyError 的结构化字段表达——plugin 为
                    # 环闭合点所在插件，missing 列出构成环回边的依赖
                    raise DependencyError(name, [dep])
                if color[dep] == 0:
                    _visit(dep, (*stack, name))
            color[name] = 2

        for name in self._plugins:
            if color[name] == 0:
                _visit(name, ())
