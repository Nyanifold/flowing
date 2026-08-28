"""标准子智能体（``builtin::`` 命名空间，P3-06 裁决）。

.. rubric:: 功能介绍

框架自带的通用子 Agent 类型。注册 ≠ 可唤起：父 Agent 须在
``.fya`` ``subagents:`` 声明或 ``add_agent`` 显式绑定后才会出现在其
catalog 中（与工具同一「声明为准」原则）。
"""

from flowing.agent import Agent

__all__ = ["ExploreAgent"]


class ExploreAgent(Agent):
    """``explore-agent``——只读代码库探索子智能体。

    .. rubric:: 功能介绍

    最常见的委派形态：主 Agent 把「读懂这块代码」的活派出去，拿回
    一段结论文本，自己继续干活。只读工具集（``read`` / ``grep`` /
    ``glob``）——结构上不可能修改文件或执行命令，委派方无需审批
    策略即可放心使用。

    .. rubric:: 设计动机

    「只读」不靠 prompt 自律，靠**工具集构成**保证：不给 Write /
    Edit / Bash，就没有可写面。这是「能力 = 声明的条目集」机制的
    标准示范（用户裁决：只有声明的才能用）。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # 父 Agent 的 .fya
        subagents:
          - explore-agent

    .. rubric:: 行为规约

    - 工具集固定为 ``read`` / ``grep`` / ``glob``（全部只读）；
      子类想加可写工具须显式覆写 :meth:`setup`——此时它不再是
      「只读探索」，语义自负。
    - 交卷：默认 plain 文本回复（不绑 ``finish``——不调用 finish
      是子 Agent 的最常用法，见 :class:`flowing.tool.FinishTool`）。
    - 无实例化参数（``args_schema`` 为空）。

    .. rubric:: 测试案例

    - 前置：父 Agent 声明 ``explore-agent`` → 操作：LLM 经
      ``subagent-invoke`` 唤起并要求「列出 src 下文件」→ 期望：
      子 Agent 工具目录仅含三个只读工具，结果经 plain 文本回传。

    .. rubric:: 调用关系（审计）

    - 调用：:meth:`flowing.agent.Agent.add_tool`（时机：setup 中
      三次，绑只读工具集）
    - 被调：``Runtime.create_agent`` / ``recover_agent`` 管线（时机：
      每次创建/恢复本类型实例）

    .. seealso:: :mod:`flowing.builtins.tools`、
        :class:`flowing.tool.SubagentInvokeTool`
    """

    system_prompt = (
        "你是只读代码探索助手。用 read / grep / glob 调查代码库，"
        "直接以简明结论文本回复（调用了什么、发现了什么、关键位置）。"
        "你不能修改任何文件或执行命令——工具集只读，这是设计而非限制。"
    )

    async def setup(self) -> None:
        """绑定只读工具集（read / grep / glob）。

        .. rubric:: 调用关系（审计）

        - 调用：``self.add_tool`` 三次（时机：setup 期声明期绑定）
        - 被调：``Runtime.create_agent`` / ``recover_agent`` 管线
          （时机：两管线各跑一次，天然幂等——重复绑定同别名报错，
          重跑在新实例上无冲突）
        """
        for name in ("read", "grep", "glob"):
            self.add_tool(name)
