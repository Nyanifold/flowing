"""``flowing.builtins`` —— 出厂内置工具与标准子智能体（``builtin::`` 命名空间）。

.. rubric:: 功能介绍

本包是框架随包发布的「出厂自带」能力集合，全部注册在 ``builtin::``
命名空间下（注册行为见 :func:`register_builtins`，由 ``Runtime`` 构造
期自动执行，随每个 ``Runtime`` 实例天生在场）：

- :mod:`flowing.builtins.tools` —— 内置工具全集：核心内置
  ``subagent-invoke`` （子智能体唤起入口）与出厂内置的 ``finish``
  （子 Agent 可选交卷），加六个标准文件 / shell 工具 ``read`` /
  ``write`` / ``bash`` / ``edit`` / ``grep`` / ``glob``；
- :mod:`flowing.builtins.agents` —— 标准子智能体 ``ExploreAgent``
  （只读代码库探索）。

.. rubric:: 全局约定（跨符号、影响使用的约定）

注册不等于可见。条目进入注册表只代表「框架认识它」，不代表 LLM 能看到
它：Agent 的工具目录（``Context.tools``）只包含该 Agent 在 ``.fya`` 的
``tools:`` 或 ``add_tool`` 中显式声明的条目；可写文件、执行命令的危险
工具（``write`` / ``bash`` / ``edit`` 等）必须由使用者显式声明才会被
LLM 看到——框架不会因为注册就把它们暴露给 LLM，这是安全边界，不是疏忽。
标准子智能体同理：``ExploreAgent`` 需亲代 Agent 在 ``subagents:`` 或
``add_agent`` 中显式声明才会进入其 catalog。

``builtin::`` 是裸名查找的兜底层：裸名引用先查 ``default::`` 再查
``builtin::``——插件 / 应用可在 ``default::`` 注册同名条目覆盖内置行为，
被覆盖的条目仍可用 ``builtin::xxx`` 全限定名显式引用。

.. rubric:: 使用示例

``.fya`` 中显式声明本 Agent 可用的工具（只读工具直接声明；危险工具同样
必须显式声明才会对 LLM 可见）::

    tools:
      - read
      - grep
      - glob
      - bash

.. seealso::

    - :mod:`flowing.builtins.tools` —— 内置工具全集。
    - :mod:`flowing.builtins.agents` —— 标准子智能体。
    - :mod:`flowing.tool` —— ``builtin::`` / ``default::`` 命名空间与
      裸名查找规则。
"""

from flowing.builtins.agents import ExploreAgent
from flowing.builtins.tools import (
    BashTool,
    EditTool,
    FinishTool,
    GlobTool,
    GrepTool,
    ReadTool,
    SubagentInvokeTool,
    WriteTool,
)

__all__ = [
    "BashTool",
    "EditTool",
    "ExploreAgent",
    "FinishTool",
    "GlobTool",
    "GrepTool",
    "ReadTool",
    "SubagentInvokeTool",
    "WriteTool",
    "register_builtins",
]


def register_builtins(runtime) -> None:
    """把全部出厂内置件注册进 ``builtin::`` 命名空间。

    .. rubric:: 功能介绍

    ``Runtime.__init__`` 的唯一调用点：八个内置工具经
    ``runtime.register_tool(..., namespace="builtin")`` 注册，
    ``ExploreAgent`` 经 ``runtime.register_agent_type("explore-agent",
    ExploreAgent, namespace="builtin")`` 注册。

    .. rubric:: 行为要点

    - 只写注册表：不触碰任何 Agent 实例，也不产生 LLM 可见性——可见性
      只能由 Agent 级显式声明产生（见包 docstring 的「注册不等于可见」）。
    - 预期在 ``Runtime`` 构造期恰好调用一次；同一 ``Runtime`` 重复调用
      会因同名工具条目已注册而抛
      :class:`flowing.errors.ToolNameConflictError`。

    .. seealso:: :mod:`flowing.builtins.tools`、
        :mod:`flowing.builtins.agents`、
        :meth:`flowing.runtime.Runtime.register_tool`
    """
    for tool in (SubagentInvokeTool(), FinishTool(), ReadTool(),
                 WriteTool(), BashTool(), EditTool(), GrepTool(),
                 GlobTool()):
        runtime.register_tool(tool, namespace="builtin")
    runtime.register_agent_type("explore-agent", ExploreAgent,
                                namespace="builtin")
