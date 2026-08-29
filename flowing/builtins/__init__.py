"""框架自带的内置工具与标准子智能体（``builtin::`` 命名空间）。

.. rubric:: 功能介绍

「出厂自带」能力的唯一入口（P3-06 裁决）：

- :mod:`flowing.builtins.tools` —— 内置工具全集：核心内置
  ``SubagentInvokeTool``（子智能体唤起唯一工具入口，核心特性
  S-01/S-02）与 ``FinishTool``（子 Agent 可选交卷），加六个标准
  文件 / shell 工具 ``read`` / ``write`` / ``bash`` / ``edit`` /
  ``grep`` / ``glob``；
- :mod:`flowing.builtins.agents` —— 标准子智能体 ``ExploreAgent``
  （只读探索）。

**核心特性 ≠ 静默附加**（用户裁决）：``subagent-invoke`` 是核心特性
——它随 Runtime 构造期注册、永远在注册表在场；但它**不能被静默
声明给任何智能体**——对 LLM 可见必须经 Agent 级显式声明
（``.fya`` ``tools:`` 或 ``add_tool``，S-19：一切工具以用户声明为
准）。其余标准件同理：**注册 ≠ 可见**，Agent 的工具目录只含它显式
声明的条目，危险面（Bash/Write/Edit 可写）是声明方自己的决定。

.. rubric:: 设计动机

- **可被覆盖**：``builtin::`` 是裸名视图的兜底层，插件 / 应用可在
  ``default::`` 注册同名工具覆盖之（命名空间裁决）。
- **机制归 tool.py，本体归这里**：基类 / 注册表 / 绑定层等机制在
  :mod:`flowing.tool`；具体工具本体（含核心内置）集中本包，
  一目了然。
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
    """把全部内置件注册进 ``builtin::`` 命名空间。

    .. rubric:: 功能介绍

    ``Runtime.__init__`` 的唯一调用点：核心内置 ``subagent-invoke`` /
    ``finish`` 与六个标准工具经
    ``runtime.register_tool(..., namespace="builtin")``，
    ``ExploreAgent`` 经 ``runtime.register_agent_type("explore-agent",
    ExploreAgent, namespace="builtin")``。

    .. rubric:: 行为规约

    - 幂等性不要求（Runtime 构造期恰好一次）。
    - 只写注册表：不触碰任何 Agent 实例，不产生 LLM 可见性
      （可见性 = Agent 级显式声明，见包 docstring）。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.runtime.Runtime.register_tool`` /
      ``register_agent_type``（时机：逐件注册）
    - 被调：``flowing.runtime.Runtime.__init__``（时机：插件
      install 之前——核心内置经此在场）

    .. seealso:: :mod:`flowing.builtins.tools`、
        :mod:`flowing.builtins.agents`
    """
    # R-02 占位（L4 真身阶段 3 替换）：本期只注册最小 finish 工具
    # （agent.py 的 finish 置位转移测试 T71 需要）；SubagentInvokeTool 的
    # execute 本体、六个标准文件/shell 工具与 ExploreAgent 的注册在
    # 阶段 3/4 补齐。
    runtime.register_tool(FinishTool(), namespace="builtin")
