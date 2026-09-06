"""``flowing.plugins.cron`` —— 插件主模块（CronPlugin / use_cron）。

.. rubric:: 功能介绍

定时扩展的启用模型是双层解耦的：

- ``runtime.install(CronPlugin())`` 只做一件事：向全局注册表注册两件 LLM
  工具（``schedule-cron`` / ``manage-cron``）。不 provide 任何注入键、
  不创建任何 Runtime 级服务——任务随 Agent 走，不存在中央调度器；
- ``use_cron(agent)`` 在 Agent 的 ``setup()`` 中调用，为该实例登记
  ``cron_jobs`` 状态键、声明 ``on_cron_trigger`` 钩子点、建立任务运行时
  并挂生命周期 handler。**不依赖** ``CronPlugin`` 是否安装：不装插件时
  Agent 仍可编程使用模块 API（``schedule`` / ``unschedule`` / ``jobs``），
  只是 LLM 没有工具入口。

任务注册不在 ``use_cron`` 内：声明式注册写在应用层的 ``after_create``
handler 里（只跑一次），LLM 路径经 ``add_tool("schedule-cron")`` 暴露。

.. rubric:: 注册面清单

- 状态键：``cron_jobs``（``agent.state.register("cron_jobs", [])``——
  缺省即写，此后键恒存在，写透到该 Agent 的 ``state.jsonl``）。
- 钩子点：``on_cron_trigger``（``by="cron"``，``match_on="source"``，
  由 ``use_cron`` 声明、由交付例程在使用处 dispatch——谁声明谁
  dispatch）。
- 挂载的钩子：``after_recover``（by="cron"：恢复补发 + 重建定时器）、
  ``before_destroy``（by="cron"：取消全部定时器句柄）。
- 未启用的 Agent（未调 ``use_cron``）：无钩子点、无运行时槽、无定时器、
  ``state.jsonl`` 无 cron 键；对其调模块 API 抛 ``ValueError``。

.. rubric:: 使用示例

.. code-block:: python

    from flowing.plugins.cron import use_cron, schedule, CronFireContext

    async def setup(self, data_dir: str):
        use_cron(self)                      # 登记键 + 声明钩子点 + 运行时
        self.add_tool("schedule-cron")
        self.add_tool("manage-cron")

        @self.hooks.after_create
        def _(agent, _value=None):
            schedule(agent, "3 2 * * *",
                     "请执行每日沉淀，现在是 {{current_time:%Y-%m-%d %H:%M}}",
                     source="daily")

        @self.hooks.on_cron_trigger["daily-*"]
        def _(agent, fire: CronFireContext):
            if 某条件:
                fire.shortcut = True        # 跳过本次推送（游标不推进）
            return fire

.. seealso:: :mod:`flowing.plugins.cron.jobs`（运行时与模块 API）、
    :mod:`flowing.plugins.cron.models`（数据对象）、
    :mod:`flowing.plugins.cron.tools`（LLM 工具）
"""

from typing import Any, ClassVar

from flowing.agent import Agent
from flowing.plugins import Plugin
from flowing.runtime import Runtime

from .jobs import _CronRuntime
from .tools import ManageCronTool, ScheduleCronTool


class CronPlugin(Plugin):
    """定时扩展插件——只注册两件 LLM 工具，无 Runtime 级服务。

    ``runtime.install(CronPlugin())`` 时框架调用 ``install(runtime)``：
    ``runtime.register_tool`` 注册 ``schedule-cron`` / ``manage-cron``。
    不 provide、不创建调度器、不挂状态持久化（``cron_jobs`` 键是
    ``use_cron`` 的 per-Agent 声明）。

    .. rubric:: 行为要点

    - 安装与否不影响 ``use_cron`` 与模块 API 的可用性——工具只是 LLM
      入口（Agent 仍需 ``add_tool`` 才对 LLM 暴露）。
    - 无收尾资源：定时器全部在 Agent 侧（随 destroy 取消），
      ``shutdown`` 无事可做。
    """

    name: ClassVar[str] = "cron"
    """插件注册名（``runtime.get_plugin("cron")`` 查询用）。"""

    dependencies: ClassVar[list[str]] = []
    """无依赖声明。"""

    def install(self, runtime: Runtime) -> None:
        """注册两件 LLM 工具（install 只注册，不读取任何持久状态）。"""
        runtime.register_tool(ScheduleCronTool())
        runtime.register_tool(ManageCronTool())


def use_cron(agent: Agent) -> None:
    """为 Agent 实例启用定时能力（``setup()`` 中调用，每实例恰好一次）。

    .. rubric:: 功能介绍

    完成四件事：

    1. ``agent.state.register("cron_jobs", [])``——登记任务总表状态键
       （缺省即写、幂等），此后 ``agent.state.cron_jobs`` 恒存在；
    2. ``agent.hooks.declare("on_cron_trigger", by="cron",
       match_on="source")``——声明实例级钩子点（handler 按 value 顶层
       ``source`` 字段 fnmatch 过滤注册）；
    3. 建立任务运行时并挂在内部槽位 ``agent._cron``（只持定时器句柄，
       不持任务数据镜像——数据以总表为唯一真相）；
    4. 挂 ``after_recover``（by="cron"：读总表补发过期 + 重建定时器）
       与 ``before_destroy``（by="cron"：取消全部定时器句柄；任务记录
       留总表随 session 存续，miss 由下次恢复补发）。

    .. rubric:: 行为要点

    - **不设防**：每实例恰好执行一次；同一实例重复调用属编程错误
      （handler 重复挂载），后果自负，不提供幂等保证。
    - 不注册任何任务（按需注册）；不依赖 ``CronPlugin`` 安装；不为
      Agent 声明任何其他资源。
    - 恢复路径：``setup()`` 在新实例上执行，本函数以新实例为起点展开
      ——声明与挂载不跨实例叠加。
    - 零开销不变量：未调用的 Agent 无钩子点、无运行时槽、无定时器。

    :param agent: 目标 Agent 实例。

    .. seealso:: :class:`CronPlugin`、:mod:`flowing.plugins.cron.jobs`、
        :meth:`CronJob`、:meth:`CronFireContext`
    """
    agent.state.register("cron_jobs", [])
    agent.hooks.declare("on_cron_trigger", by="cron", match_on="source")
    runtime = _CronRuntime(agent)
    agent._cron = runtime   # 内部槽位：任务运行时（定时器句柄容器）

    async def _rebuild(a: Agent, _value: Any = None) -> None:
        # 恢复时 setup 在新实例执行、运行时槽随实例重建；先补发过期
        # （合并交付，一次性已交付者自移除），再为剩余任务武装
        rt = getattr(a, "_cron", None)
        if rt is None:
            return
        await rt.rebuild_after_recover()

    def _cleanup(a: Agent, _value: Any = None) -> None:
        # destroy 时持久化后端已关闭（不可写 state）；只取消定时器句柄，
        # 任务记录留总表（destroy ≠ 删除，miss 由恢复补发）
        rt = getattr(a, "_cron", None)
        if rt is not None:
            rt.cancel_all()

    agent.hooks.after_recover(_rebuild, by="cron")
    agent.hooks.before_destroy(_cleanup, by="cron")
