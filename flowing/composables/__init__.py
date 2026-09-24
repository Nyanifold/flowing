"""flowing.composables —— 应用层 Composable：纯函数式能力注入。

.. rubric:: 功能介绍

Composable 是三层架构中的应用层：普通 Python 函数（命名约定
``use_xxx(agent, ...)``），在 Agent 的 ``setup()`` 中被调用，为这一个
Agent 实例注册钩子 handler 或绑定实例属性。内置五个：
:func:`use_retry` （:mod:`flowing.composables.retry`，LLM 调用失败退避
重试）、:func:`use_compact` 与 :func:`use_auto_compact`
（:mod:`flowing.composables.compact`，上下文占用超阈值自动压缩——
前者请求后全量换链，后者请求前头尾保留，二选一）、
:func:`use_system_reminder`
（:mod:`flowing.composables.reminder`，每回合注入系统提醒）与
:func:`use_prompt_until`
（:mod:`flowing.composables.prompt_until`，回合收尾断言不成立时
steer 导向续跑）。场景类
Composable（``use_logging`` / ``use_guardrail`` 等）属应用代码，框架
不预留符号。

Composable 只做“挂载”：注册钩子 handler、绑定实例属性；不修改框架
核心状态。本包不提供插件（阶段一）能力：不做全局注册、不经过
``runtime.install()``、自身也没有 ``install()``——调用即生效，作用域严格
限于传入的那一个 Agent 实例。

.. rubric:: 使用示例

典型组合（手写 Agent 子类的 ``setup()`` 中按需启用）：

.. code-block:: python

    from flowing import Agent
    from flowing.composables import (
        use_compact, use_prompt_until, use_retry, use_system_reminder,
    )

    class MyAgent(Agent):
        async def setup(self) -> None:
            use_retry(self, max_retries=3)        # LLM 调用失败：退避重试
            use_compact(self, threshold=0.8)      # 上下文超阈值：自动压缩换链
            use_system_reminder(self, contents=[
                lambda agent: f"当前节点：{agent.node_id}",
            ])                                    # 每回合注入一条系统提醒
            use_prompt_until(self, self._done, "任务未完成，请继续。")
            #                                      # 回合收尾断言不成立时导向续跑

.. rubric:: 行为要点

- 启用方式：双层启用的阶段二——``use_xxx(self)`` 在 ``setup()`` 中按
  实例启用（恢复时 ``setup()`` 在新实例上执行，钩子注册表随实例重建，
  天然不叠加）；未调用 ``use_xxx`` 的 Agent 不持有任何相关 handler 与
  状态——“没启用”是“代码路径从没存在过”，不是“被跳过”，零开销。
- 同步 / async 形态：由内部是否确需 ``await`` 决定——纯注册型
  Composable 写成同步 ``def`` （本包五个均为同步），调用点不需要
  ``await``；需要真实等待（退避 sleep、副线查询）的 handler 才是异步
  函数。
- 无排序约束：``setup()`` 中调用顺序决定最终结果，后注册的 handler
  排在链尾执行。
- 本包五个 Composable 均不做幂等去重：重复调用按注册语义各自叠加一组
  handler（允许以不同参数多次启用）；整组替换用
  ``remove_by_owner()`` （参数取各子模块注册面清单中的 ``by`` 值）
  移除默认 handler 后自注册。
- 注册的资源、声明的钩子点与挂载的钩子：见各子模块 docstring 的
  “注册面清单”。

.. seealso::

    :mod:`flowing.plugins` —— 阶段一插件层（跨 Agent / 进程级能力）。
    :class:`flowing.hooks.HookRegistry` —— Composable 的主要挂载面。
"""

from flowing.composables.retry import use_retry
from flowing.composables.compact import use_auto_compact, use_compact
from flowing.composables.reminder import use_system_reminder
from flowing.composables.prompt_until import use_prompt_until

__all__ = [
    "use_retry",
    "use_compact",
    "use_auto_compact",
    "use_system_reminder",
    "use_prompt_until",
]
