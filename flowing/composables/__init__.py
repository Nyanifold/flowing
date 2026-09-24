"""flowing.composables —— 应用层 Composable：函数式能力注入。

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

``use_xxx(agent, ...)`` 是应用层入口约定，不是行为白名单。函数体可按需
注册钩子 handler、挂载 Agent 属性、通过 ``agent.state.register()`` 登记
状态，也可执行其他应用层逻辑；Composable 的返回值与具体行为不受本包
限制。只在当前运行期使用的临时状态可放在闭包变量或 Agent 属性中：把
状态放在该次调用创建的闭包中，可避免与 Agent 属性名冲突，但外部不能
通过 Agent 直接访问；Agent 属性可供外部读取和管理，但需自行避免属性名
冲突。需要持久化的状态可通过 Flowing 内置的 ``agent.state.register()``
登记，也可由应用自行维护持久化与恢复；
闭包变量和普通 Agent 属性本身不提供持久化能力。
内置 Composable 通常用于为传入的 Agent 实例装配策略。本包不提供插件
（阶段一）能力：
不做全局注册、不经过 ``runtime.install()``、自身也没有 ``install()``——
调用即执行，影响范围由函数实现决定。

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

- 启用方式：双层启用的阶段二——``use_xxx(self, ...)`` 在 ``setup()`` 中按
  需执行。恢复时，新实例会再次执行 ``setup()``；临时状态与钩子按函数和
  注册 API 的语义重新装配，持久化状态由所选的持久化渠道恢复。
-  未调用的 Composable 不会执行，因此不会由它添加 handler、属性或状态。
- 同步 / async 形态：由函数本身是否需要 ``await`` 决定。本包五个
  ``use_xxx`` 当前均为同步 ``def``；这只是当前实现形态，不限制自写
  Composable。需要真实等待（退避 sleep、副线查询）的 handler 可以是异步
  函数。
- 调用顺序：``setup()`` 中 ``use_xxx`` 的调用顺序决定最终结果，后注册的 handler
  排在链尾执行。
- 本包五个 Composable 均不做幂等去重：同一个 ``use_xxx`` 可以用不同参数
  多次调用，重复调用按注册语义各自叠加一组 handler。自写 Composable 的
  多次调用如何组合由其实现决定；挂载 handler 时若需整组替换，可用
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
