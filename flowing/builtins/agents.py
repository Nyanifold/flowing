"""``flowing.builtins.agents`` —— 标准子智能体（``builtin::`` 命名空间）。

.. rubric:: 功能介绍

本模块提供框架随包发布的标准子 Agent 类型：:class:`ExploreAgent`
（注册名 ``explore-agent``，只读代码库探索），注册在 ``builtin::``
命名空间下。

注册不等于可唤起：与工具同一「声明为准」原则——父 Agent 须在 ``.fya``
的 ``subagents:`` 声明或 ``add_agent`` 显式绑定后，该类型才会出现在其
catalog（LLM 可见）中。

.. rubric:: 使用示例

.. code-block:: yaml

    # 父 Agent 的 .fya
    subagents:
      - explore-agent

.. seealso:: :mod:`flowing.builtins.tools`、
    :class:`flowing.agent.Agent`
"""

from flowing.agent import Agent

__all__ = ["ExploreAgent"]


class ExploreAgent(Agent):
    """``explore-agent`` —— 只读代码库探索子智能体。

    .. rubric:: 功能介绍

    最常见的委派形态：主 Agent 把「读懂这块代码」的活派出去，拿回一段
    结论文本，自己继续干活。工具集固定为只读的 ``read`` / ``grep`` /
    ``glob``——结构上不可能修改文件或执行命令，委派方无需审批策略即可
    放心使用。

    「只读」不靠提示词自律，靠工具集构成保证：不给 ``write`` /
    ``edit`` / ``bash`` 就没有可写面。这是「能力即声明的条目集」机制的
    标准示范。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # 父 Agent 的 .fya
        subagents:
          - explore-agent

    .. rubric:: 行为要点

    - 工具集固定为 ``read`` / ``grep`` / ``glob`` （全部只读）；子类想
      加可写工具须显式覆写 :meth:`setup`——此时它不再是「只读探索」，
      安全语义自负。
    - 交卷：默认以 plain 文本回复作为结果回传父 Agent（不绑
      ``finish``——不调用 ``finish`` 是子 Agent 的最常用法，见
      :class:`flowing.builtins.FinishTool`）。
    - ``setup()`` 不接受任何初始化参数；子 Agent 条目无需（也不应）为
      它声明 ``args:`` 覆写。

    .. seealso:: :mod:`flowing.builtins.tools`、
        :class:`flowing.builtins.SubagentInvokeTool`
    """

    system_prompt = (
        "你是只读代码探索助手。用 read / grep / glob 调查代码库，"
        "直接以简明结论文本回复（调用了什么、发现了什么、关键位置）。"
        "你不能修改任何文件或执行命令——工具集只读，这是设计而非限制。"
    )

    async def setup(self) -> None:
        """把只读工具集（``read`` / ``grep`` / ``glob``）绑定到本实例。

        创建 / 恢复管线各自在一个新建实例上运行一次本方法（实例级单次
        契约见 :meth:`flowing.agent.Agent.setup`）；每次运行都从空条目
        表开始，各实例装配互不干扰。
        """
        for name in ("read", "grep", "glob"):
            self.add_tool(name)
