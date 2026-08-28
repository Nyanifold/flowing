"""flowing._unstable —— 实验性命名空间：可导入，但**不冻结**。

.. rubric:: 定位与语义承诺

本包收容「功能已确定、策略 / 接口形态未确定」的自用设施。与
:mod:`flowing.plugins` 的区别在稳定性承诺：

- ``flowing.plugins.*``：内置扩展，契约随框架版本冻结（签名、语义、
  持久化格式按正常版本纪律演进）。
- ``flowing._unstable.*``：**允许在任何版本中改签名、改名、改语义、
  整体删除或迁出**，不视为 breaking change，不发迁移通告。下划线
  前缀同时挡住自动导入与「稳定 API」的心理预期。

.. rubric:: 毕业规则

某设施的策略定稿后，整体迁往正式包（如 ``flowing.plugins``）并在
本包留一个 re-export 过渡（仅当下一个 minor 版本）；迁入正式包后
按正常稳定性纪律管理。

.. rubric:: 使用约定

- 仅自用 / 内部 debug 场景；下游发布物（插件、workflow、技能包）
  **禁止**依赖本包。
- 本包模块的持久化产物（如日志文件）不构成恢复依赖——框架任何
  恢复路径不得读取 ``_unstable`` 设施写出的文件。

当前内容：:mod:`flowing._unstable.logging`（钩子链路日志插件）。
"""

__all__: list[str]
