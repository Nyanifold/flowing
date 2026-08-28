"""``flowing.composables`` —— 应用层 Composable：纯函数式能力注入。

.. rubric:: 功能介绍

Composable 是三层架构的**应用层**：普通 Python 函数（命名约定
``use_xxx(agent, ...)``），在 Agent 的 ``setup()`` 中被调用，往已有钩子点 /
注册表上挂载 handler 或提供值。初版内置三个：:func:`use_retry`
（:mod:`flowing.composables.retry`，LLM 错误重试）、:func:`use_compact`
（:mod:`flowing.composables.compact`，上下文超阈值自动压缩换链）与
:func:`use_system_reminder`（:mod:`flowing.composables.reminder`，每回合
注入系统提醒——高频更新值的消息通道注入）；
场景 Composable（``use_logging`` / ``use_guardrail`` 等）属应用代码，
框架不预留符号。

.. rubric:: 设计动机

- 「机制 vs 策略」：Composable 承载策略（重试几次、审批什么），框架核心
  只提供钩子点机制；默认策略可被用户同名 Composable 整组覆盖
  （``remove_by_owner`` + 重新注册）。
- **双层启用阶段二**：``use_xxx(self)`` 按实例启用，不调用的 Agent 零
  开销——prompt 无注入、LLM 看不到相关工具、钩子点不存在。
- 纯 Composable → Plugin 的升级路径：``use_xxx()`` 签名行为不变，只加
  Plugin 包装类，调用方零修改。

.. rubric:: 行为规约

- Composable 只做「挂载」：注册钩子 handler、追加 prompt block、绑定
  实例方法；不修改框架核心状态。
- **同步 / async 由内部需求决定**（M-91 范式裁决）：纯注册型
  Composable 写成同步 ``def``（``use_retry`` / ``use_cron`` /
  ``use_comm`` / ``use_skill`` 均如此）；只有内部确需 ``await``
  （读文件、网络请求等）时才用 ``async def``。不为「形态统一」
  强行 async——调用点写不写 ``await`` 应反映真实需求。
- 无排序约束：``setup()`` 中调用顺序决定最终结果，后执行覆盖先执行。

.. seealso:: :mod:`flowing.plugins`（阶段一插件层）、
    :class:`flowing.hooks.HookRegistry`（Composable 的主要挂载面）
"""

from flowing.composables.retry import use_retry
from flowing.composables.compact import use_compact
from flowing.composables.reminder import use_system_reminder

__all__ = ["use_retry", "use_compact", "use_system_reminder"]
