"""flowing.plugins.cron —— 定时扩展（CronPlugin / CronScheduler / use_cron）。

.. rubric:: 模块定位

Cron 是 Flowing 的**内置扩展**（随 ``flowing`` 包发布但不自动启用），
为 Agent 提供「按 cron 表达式定时触发」的能力。框架核心**不内置调度**——
定时触发是策略性基础设施，由扩展承载；核心只提供它复用的机制：消息入队
（``enqueue_message``）、实例级钩子（``hooks.declare``）、provide-inject、
插件状态持久化（``Agent.register_state`` / ``Agent.state``）。

遵循全局**双层启用**模型：

- 阶段一（Runtime 安装）：``runtime.use(CronPlugin())`` →
  ``CronPlugin.install(runtime)`` 创建 ``CronScheduler`` 并 provide、注册
  ``schedule-cron`` 系列（兼容/仅消息/仅工具三个变体）与
  ``manage-cron`` 全局工具；观测经插件自身的
  只读 API（``jobs()`` 等），框架无快照命名空间挂载机制。
- 阶段二（Agent 启用）：``setup()`` 中 ``use_cron(self)`` 声明
  ``on_cron_trigger`` 钩子点、经 ``self.register_state("cron_jobs",
  [])`` 声明任务状态键（挂载到该 Agent 自己的
  ``state.jsonl``；单袋化裁决后一个 Agent 一袋，插件键带
  ``cron_`` 前缀）、并在 ``after_recover`` 上注册恢复回顾
  handler（``by="cron"``；recover 在新实例重跑 setup、注册表
  随实例重建，天然不撞，无需去重机制）；任务注册按需发生（声明式注册写在 ``after_create`` handler
  里——只在 create 管线触发，经 ``inject(cron_scheduler_key)``
  的 ``schedule(...)``；recover 时任务表数据由 ``_restore`` 从
  ``state.jsonl`` 自动重放，定时器由 ``after_recover`` handler
  重建，无需重新声明；LLM 经 ``schedule-cron``
  系列工具）。

不调用 ``use_cron()`` 的 Agent 零开销：无 ``on_cron_trigger`` 钩子点、
无恢复回顾 handler。

.. rubric:: 触发语义（两层模型：动作 vs 执行）

一次触发涉及两个正交的决策层：

- **第 1 层（Agent 的选择，任务级，落盘）**：``CronJob.action``
  （:class:`CronAction`）声明「做什么」——``kind="message"`` 表示一条
  提示词，``kind="tool_call"`` 表示一次工具别名化调用。纯数据，随任务
  定义写进该 Agent 的 ``cron_jobs`` 状态键持久化。
- **第 2 层（Cron 插件的决定，插件级，不落盘）**：执行器
  （``CronPlugin(executors={...})``）决定「怎么做」——同一条
  ``message`` 动作可以入队（默认）、阻塞直发、或经 comm 插件转 Signal；
  同一条 ``tool_call`` 动作可以默认「调用并把结果推回消息队列」或
  换作其它兑现方式。执行器是代码，每次加载插件时注入，不进
  ``state.jsonl``——任务恢复后自动跟随当前这次加载的执行策略。

一次触发的完整链路（全部在单进程 asyncio 事件循环内）：

1. ``CronScheduler`` 到点，按 ``job.node_id`` 在 ``runtime._nodes`` 中
   定位 Agent 实体。
2. **休眠门控**：实体不存在（= 休眠：未创建或 session 未恢复）→
   本次不交付、**不推进** ``job.last_fired_at``，直接返回——错过的
   触发次数无状态地隐含在「当前时间 − 游标」中，等实体回到
   ``_nodes`` 后的下一次交付合并计入。注意休眠**不等于**销毁：
   任务定义保留，删任务的唯一路径是显式 ``unschedule`` /
   ``unschedule_all``（destroy 不清任务，见「Agent 消失与任务生命
   周期」）。
3. 实体存在 → 由 cron 表达式与 ``last_fired_at`` 算出
   ``coalesced_count``（区间内理想触发次数，≥1），构造
   ``CronFireContext``，dispatch ``agent.hooks.on_cron_trigger``
   （value 为 ``CronTrigger``）：handler 可改写 ``trigger.action``
   （同一语义的调整）；任一 handler 置 ``trigger.shortcut = True``
   则**跳过本次触发**（语义收窄为「取消」，不是替换执行逻辑——
   替换请走第 2 层执行器）。
4. 未被短路时，按（可能被改写的）``action.kind`` 查执行器表并调用
   ``await executor(agent, job, action, ctx)``；执行成功后推进
   ``last_fired_at`` 并写透落盘；``recurring=False`` 的任务随即自删
   落盘。

默认 message 执行器走消息队列（而非直接调用 handler）的原因：队列是
持久化、可暂停、可取消的入口——定时触发与用户输入、peer 消息在
「进入逻辑 Turn 循环」这一点上完全同构。``on_cron_trigger`` 钩子则是
回合外的观察/拦截点；「到点后换成完全不同的动作」不属于它，属于
执行器注入。

合并交付的内容由**渲染模板**产生（``CronPlugin(templates={...})``，
按 kind 分键，风格与 skill catalog 模板一致：Jinja2 模板源字符串、
默认 XML，渲染经 Parsable TEMPLATE 语义；**不再支持 callable
渲染器**——渲染器模板化裁决）——
``coalesced_count > 1`` 时 Agent 看到的不是 N 条消息，而是一条带
``<cron-fire ... coalescedCount="N">`` 元信息的消息。tool_call 动作
在合并交付时**降级为通知**（默认 tool 执行器在 ``coalesced_count >
1`` 时不真执行工具，而是渲染一条「休眠期错过 N 次定时调用」的
EVENT 消息入队，由 Agent 醒来自行处置）——只有 Agent 活跃时的
当次触发才真执行工具。

.. rubric:: 持久化与恢复（Agent.register_state("cron_jobs", [])）

任务定义属**插件状态**，以 ``cron_`` 前缀键声明进该 Agent 的单袋状态
（核心键裸名，插件键带注册名前缀——单袋化最终裁决）。
``use_cron(self)`` 在 ``setup()`` 中经 ``self.register_state("cron_jobs",
[])`` 声明（M-68：写透存储，无 save 回调）：

- **持久化**：``schedule()`` / ``unschedule()`` 对
  ``agent.state.cron_jobs`` 的 set 即写透
  落盘到该 Agent 自己 session 目录的 ``state.jsonl``（行：
  ``{"op":"set","key":"cron_jobs","value":[...]}``）——
  无需也不存在显式 save 调用。**值恒为 ``list[dict]`` 纯数据**
  （P3-12① 裁决：进出唯一通道是 ``CronJob.to_dict()`` /
  ``CronJob.from_dict()``；内存形态与磁盘形态同构，对象本体只存于
  调度器内存表 ``_jobs``）。
- **恢复时机**：Agent session 恢复（``Agent._restore()``）时，核心键
  与全部插件键随 state.jsonl 自动重放进单袋；随后 ``after_recover``
  handler（``by="cron"``）读出任务表，把该 Agent 的任务重建进中央
  调度器并重新武装定时器（``_load_jobs``），再做错过触发的合并回顾
  （``_sweep``）。
- **休眠 Agent 的读取**：中央调度器在 Agent 休眠期间需要其任务表时，
  经内部 persistence 模块**裸重放**该 Agent 目录的 ``state.jsonl``
  （读方自知 ``cron_jobs`` 键名）；休眠时 defaults 与 ``after_recover``
  重建不可用（该 Agent 的 setup 未在当前进程跑过），读到的是裸持久值——
  cron 所需三个量（cron 表达式 / ``created_at`` / ``last_fired_at``）全部
  是持久化数据，无此需求。

**错过提醒：一律合并（单规则）**。``coalesced_count`` = cron 表达式在
``(last_fired_at ?? created_at, now]`` 区间内的理想触发次数；游标只在
成功交付时推进，因此任何原因（Agent 休眠、进程宕机、调度延迟）造成的
错过都自动累积，并在下一次成功交付时合并为**一次**、计数如实告知
（渲染进 ``coalescedCount`` 元信息）。不补发多次、不区分错过原因。
交付时机有两个：Agent 恢复时由 ``after_recover`` 上的回顾 handler
立即 sweep 一次（``CronScheduler._sweep``）；此后到点正常触发。

.. rubric:: 中心表与落盘的相容性

集中内存表（``CronScheduler._jobs``）与各 Agent 分片的 ``state.jsonl``
之间靠三条约定保持一致，不存在对账机制：

1. **单向数据流**：写路径只有「内存表 → 盘」（全部变更经调度器方法
   写透，应用层无只改内存的入口）；读路径只有「盘 → 内存表」，且
   仅发生在 ``_load_jobs``（session 恢复）一个时机。运行期调度器
   从不回读盘。
2. **写序约定**：``schedule`` 先落盘、再登记内存表、最后武装定时器
   （避免幽灵触发）；``_fire`` 的游标推进是「执行成功 → 推进
   ``last_fired_at`` → 写透」（写透失败崩溃 → 恢复后最多重投一次，
   at-least-once 优于丢触发）。
3. **崩溃语义**：``state.jsonl`` 追加式日志最坏撕坏末行，恢复重放时
   坏行被形状校验丢弃——盘可能略旧于崩溃前的内存表，但内存表随进程
   消失，以盘为准重建即自动收敛；``_load_jobs`` 按 ``job_id`` 覆盖
   进表，重复恢复幂等。

未被恢复的 Agent 的任务记录无限期保留在其 session 的 ``state.jsonl``
中（不进入调度器、不占定时器），直到该 session 被恢复（原样生效）
或被应用层显式清理。

.. rubric:: Agent 消失与任务生命周期

核心语义前提：**destroy ≠ 删除**——``Agent.destroy()`` 只把实体从
``_nodes`` 摘除，session 目录与 ``state.jsonl`` 保留，之后可按 session
恢复（实体回到 ``_nodes``）。因此 cron **不在** ``before_destroy`` 挂
清理（这与 comm 的 handle 清理不同：handle 是纯运行期对象必须清，
cron 任务是落盘数据不该清）。完整状态转换表：

- **destroy()** → 实体出 ``_nodes``；任务保留（集中表与落盘都不动）；
  门控视其为休眠，触发空转、错过次数随游标停滞自动累积。
- **session 恢复** → 实体回 ``_nodes``；若期间进程重启过，先经
  ``_load_jobs`` 把任务重建进集中表；随后 ``after_recover`` 触发
  ``_sweep``，对过期任务**立即合并交付一次**（游标推进），之后
  到点正常触发。
- **进程关闭/崩溃** → ``_stop`` 只停定时器；恢复时重放落盘记录；
  停机区间的错过按「一律合并」计入下次交付。
- **应用层删 session 目录** → 任务记录随目录消失（插件无感）。
- **显式清理** → ``unschedule`` / ``unschedule_all`` 是**应用层主动
  调用**的清理入口，不再由框架自动挂接。

- Runtime 关闭：``runtime.shutdown()`` 插件收尾阶段调度器停止全部
  定时器。

.. rubric:: 规模边界

与通信扩展同：单机小工具、单进程事件循环；cron 表达式为五字段分精度
（``分 时 日 月 周``），触发精度为分钟级；任务总量预期与端点规模同阶
（≤ 100 量级）。性能不是设计约束。

.. seealso::

    - :mod:`flowing.plugins.comm` —— 通信扩展（cron 触发结果是 EVENT
      消息，与通信通道正交）。
    - :mod:`flowing.message` —— ``Message`` / ``MessageKind.EVENT``。
    - :mod:`flowing.hooks` —— 钩子声明与 dispatch 语义。
    - :meth:`flowing.agent.Agent.register_state` —— 插件状态声明点
      （setup 中，per-agent）。
"""

from typing import Any, ClassVar

from flowing.agent import Agent
from flowing.plugins import Plugin
from flowing.runtime import Runtime

from .executors import CronExecutor, CronTemplate
from .scheduler import CronScheduler, cron_scheduler_key
from .tools import (
    ManageCronTool,
    ScheduleCronMessageTool,
    ScheduleCronTool,
    ScheduleCronToolCallTool,
)


class CronPlugin(Plugin):
    """定时扩展插件——阶段一入口：创建调度器、注册工具、挂载持久化与观测。

    .. rubric:: 功能介绍

    ``runtime.use(CronPlugin())`` 时框架调用 ``install(runtime)``，依次：

    1. 创建 ``CronScheduler(runtime)`` 并
       ``runtime.provide(cron_scheduler_key, scheduler)``；构造参数
       ``executors`` / ``templates`` 的条目分别覆盖/扩充调度器的默认
       执行器表与渲染模板表（第 2 层：「怎么做」的插件级注入点）。
    2. ``runtime.register_tool(ScheduleCronTool())``、
       ``ScheduleCronMessageTool()``、``ScheduleCronToolCallTool()`` 与
       ``ManageCronTool()``（注册名 ``"schedule-cron"`` /
       ``"schedule-cron-message"`` / ``"schedule-cron-tool-call"`` /
       ``"manage-cron"``）。

    任务持久化不在 install 挂载——状态袋是 per-agent 声明：
    ``use_cron(self)`` 在各 Agent 的 ``setup()`` 中经
    ``self.register_state("cron_jobs", [])`` 完成（写透，任务变化即落
    该 Agent 自己 ``state.jsonl`` 的 ``cron_jobs`` 键；恢复时由
    ``after_recover`` handler 经 ``CronScheduler._load_jobs`` 重建）。

    观测不注册任何东西：``CronScheduler.jobs()`` 只读 API 即观测面
    （M-81：框架无快照命名空间挂载机制）。

    .. rubric:: 设计动机

    遵守插件约定 R1–R4：install 只注册，不查询其他插件、不创建 Agent。
    「cron 状态键由插件经前缀约定声明、核心不感知」是核心不膨胀的关键
    约定——在核心写死 ``cron`` 恢复逻辑是被明确禁止的反模式。

    执行器经构造参数而非 install 后注册，理由：执行策略是「这次进程
    如何兑现动作」的代码配置，与任务定义（落盘数据）生命周期正交——
    恢复出的旧任务自动跟随当前加载的执行器，不存在「旧任务绑定旧
    代码」的问题。

    .. rubric:: 使用示例

    .. code-block:: python

        # @/main.py
        import flowing
        from flowing.plugins.cron import CronPlugin, default_message_executor

        async def blocking_message_executor(agent, job, action, ctx) -> None:
            # 自定义 message 兑现方式：阻塞直发而非入队（仅作示例）
            await agent.query(action.prompt)

        async def main() -> flowing.Runtime:
            runtime = flowing.Runtime()
            runtime.use(CronPlugin(executors={
                "message": blocking_message_executor,
            }))
            await runtime.mount("@/root.fya")
            return runtime

    .. rubric:: 行为规约

    - 期待行为：install 后调度器即可经 inject 消费，工具出现在全局
      注册表（Agent 仍需 ``add_tool`` 才对 LLM 可见）。
    - 非行为：不自动给任何 Agent 注册任务；不声明任何 Agent 钩子点
      （``on_cron_trigger`` 是 ``use_cron`` 的实例级声明）。
    - 边缘情况：``executors`` 中 key 不属于内建 kind 时按自定义 kind
      接受（不报错——无法与拼写错误区分，但拼错的 key 会在
      ``schedule()`` 校验 ``action.kind`` 时以 ``ValueError`` 暴露）；
      重复 ``runtime.use(CronPlugin())`` → 同名 provide key 冲突，
      后注册者报错。
    - 生命周期：``runtime.shutdown()`` 插件收尾阶段调用调度器
      ``_stop()`` 停止全部定时器。

    .. rubric:: 测试案例

    - 前置：新 Runtime；操作：``runtime.use(CronPlugin())``；期望：
      ``runtime.inject(cron_scheduler_key)`` 得调度器实例，
      ``runtime.tool_registry.get("schedule-cron")``、
      ``get("schedule-cron-message")``、``get("schedule-cron-tool-call")``
      与 ``get("manage-cron")`` 均非 None。
    - 前置：``CronPlugin(executors={"message": spy})`` 并注册一条
      message 任务；操作：推进时钟到触发点；期望：``spy`` 被以
      ``(agent, job, action, ctx)`` 调用，默认入队**不发生**（覆盖语义）。
    - 前置：已装插件并 ``schedule`` 一条任务后进程重启；操作：按
      ``agent_id`` 恢复 session；期望：``scheduler.jobs(node_id)`` 重新
      包含该任务，且 ``after_recover`` 触发的 sweep 立即合并交付一次
      （``coalesced_count`` 覆盖停机区间——一律合并规则）。

    .. rubric:: 调用关系（审计）

    - 实例化方：无（框架内无实例化方；应用层 ``runtime.use(
      CronPlugin())`` 构造，公共 API）

    .. seealso:: :class:`CronScheduler`、:class:`CronAction`、
       :func:`use_cron`、:class:`flowing.runtime.Plugin`、
       :meth:`flowing.agent.Agent.register_state`
    """

    name: ClassVar[str] = "cron"
    """注册名（显式声明，无框架默认；推荐格式见 ``flowing.plugins`` 命名约定）。
    """

    dependencies: ClassVar[list[str]] = []   # S-10：与基类 Plugin 的 ClassVar 对齐
    """依赖声明（类属性元数据）。本插件无依赖，为空列表。
    
    .. seealso:: :meth:`flowing.runtime.Runtime._check_dependencies`
    """

    _executors: dict[str, CronExecutor]
    """构造时保存的执行器覆盖/扩充表（S-24 裁决：类面显式声明；
    ``install`` 时透传给 ``CronScheduler.__init__`` 构造注入）。
    内部 API，不属稳定契约。
    """
    _templates: dict[str, CronTemplate]
    """构造时保存的渲染模板覆盖/扩充表（同 ``_executors``）。
    内部 API，不属稳定契约。
    """

    def __init__(self,
                 executors: dict[str, CronExecutor] | None = None,
                 templates: dict[str, CronTemplate] | None = None) -> None:
        """构造插件。

        :param executors: kind → 执行器的覆盖/扩充表。未提到的 kind 用
            默认执行器；自定义 kind 在此注册后方可被 ``schedule()`` 接受。
            执行器签名统一为
            ``async (agent, job, action, ctx) -> None``。
        :param templates: kind → 渲染模板的覆盖/扩充表（Jinja2 模板源
            字符串，含 ``$./file.j2`` FILE_REF 形式；上下文变量表见
            :data:`CronTemplate`）。默认执行器路径按 kind 查表渲染
            交付内容；自定义执行器可绕开模板表。

        .. rubric:: 调用关系（审计）

        - 调用：无（仅保存覆盖/扩充表到 ``self._executors`` /
          ``self._templates``——S-24 裁决：类面显式声明，``install``
          时透传给 ``CronScheduler.__init__`` 构造注入）
        - 被调：无（框架内无调用方；应用层 ``runtime.use(CronPlugin(...))``
          构造）
        """
        self._executors = executors or {}
        self._templates = templates or {}

    def install(self, runtime: Runtime) -> None:
        """创建调度器并完成 provide / 工具注册。

        .. rubric:: 行为规约

        - 期待行为：两步注册全部同步完成（R1：install 只注册）。
        - 非行为：不启动任务（尚无任务可启动）；不读取其他插件状态（R2）；
          不挂载状态持久化——``cron_jobs`` 状态键是 per-agent 声明，由
          ``use_cron(self)`` 在各 Agent 的 ``setup()`` 中经
          ``self.register_state("cron_jobs", [])`` 完成。
        - 后置条件：调度器已 provide、四件工具已注册；此后每个
          ``use_cron`` 的 Agent 各自获得写透落盘与恢复重建能力。

        .. rubric:: 调用关系（审计）

        - 调用：构造 ``CronScheduler`` + ``Runtime.provide``（时机：
          第 1 步）；``Runtime.register_tool`` ×4（时机：第 2 步）——
          全部同步完成（R1）
        - 被调：``flowing.runtime.Runtime.use``（时机：阶段一安装，
          每个插件被调用恰好一次）

        .. seealso:: :class:`CronScheduler`、:class:`ScheduleCronTool`、
           :class:`ManageCronTool`、:func:`use_cron`
        """
        scheduler = CronScheduler(runtime, executors=self._executors,
                                  templates=self._templates)  # S-24：构造注入
        self._scheduler = scheduler   # 自留引用：shutdown() 收尾用（S-05）
        runtime.provide(cron_scheduler_key, scheduler)
        runtime.register_tool(ScheduleCronTool())
        runtime.register_tool(ScheduleCronMessageTool())
        runtime.register_tool(ScheduleCronToolCallTool())
        runtime.register_tool(ManageCronTool())

    async def shutdown(self) -> None:
        """插件收尾：停止全部定时器（S-05 裁决的显式收尾通道）。

        .. rubric:: 调用关系（审计）

        - 调用：``CronScheduler._stop()``（每次优雅关闭）
        - 被调：``flowing.runtime.Runtime.shutdown()``（插件收尾阶段，
          按 install 顺序）
        """
        self._scheduler._stop()

def use_cron(agent: Agent) -> None:
    """为 Agent 实例启用定时能力——阶段二入口（Composable，幂等）。

    .. rubric:: 功能介绍

    在 ``setup()`` 中调用，完成四件事（全部幂等——统一 setup 契约下
    recover 管线会重跑 ``setup()``，本函数必须可重入）：

    1. ``agent.inject(cron_scheduler_key)`` 校验调度器可用（未安装
       ``CronPlugin`` → ``MissingProvideError``）。
    2. ``agent.hooks.declare("on_cron_trigger", by="cron",
       match_on="source")`` 声明实例级钩子点——handler 按
       ``CronTrigger.job.source`` 做 fnmatch 过滤注册；同名同 by 的
       重复声明按 hooks 总则幂等返回已有 HookList。
    3. ``agent.register_state("cron_jobs", [])`` 声明
       该 Agent 的任务状态键（写透到该 Agent 自己的
       ``state.jsonl``；恢复重放后由第 4 步的 ``after_recover``
       handler 经 ``_load_jobs`` 重建进中央调度器）。同键重复声明
       报错（单袋化裁决：``register_state`` 不支持重复；recover 在
       新实例上重跑 setup，注册表随实例重建，天然不撞）。
    4. 在 ``after_recover`` 上注册恢复回顾 handler（``by="cron"``；
       recover 在新实例重跑 setup、注册表随实例重建，天然不撞）：实体回到 ``_nodes`` 且状态恢复完成后，先
       ``scheduler._load_jobs(agent.node_id, agent.state.cron_jobs)``
       重建任务与定时器，再 ``scheduler._sweep(agent.node_id)`` 对该
       Agent 的过期任务立即做一次合并交付。

    任务注册**按需**发生，不在 ``use_cron`` 内。声明式注册写在
    ``after_create`` handler 里（``after_create`` 只在 create 管线
    触发一次——此刻 ``_nodes`` 已注册、state 写闸门已解开，
    ``schedule(...)`` 可用）；recover 时任务表数据由 ``_restore`` 从
    ``state.jsonl`` 自动重放进单袋，``after_recover`` handler 再经
    ``_load_jobs`` 重建进中央调度器，
    **无需也不应重新注册**（重注册会撞 ``schedule`` 的 job_id
    冲突）。LLM 路径经 ``agent.add_tool("schedule-cron")``
    后由模型调用。

    .. rubric:: 设计动机

    声明与恢复回顾是「启用」的最小集合（声明独占、注册开放：钩子点归
    ``use_cron`` 声明，handler 归应用层注册）；任务集合是应用层策略，
    Composable 不预设。**不挂 ``before_destroy`` 清理**——destroy ≠
    删除（session 存续、可恢复），任务作为落盘数据应随 session 存续；
    这与 comm 的 handle 清理不同构（handle 是纯运行期对象必须清）。
    清理只有显式路径：``unschedule`` / ``unschedule_all``。
    **声明-漂移检测**：更改代码中的 cron 定义后，盘上旧定义在
    recover 时原样回来（无声明比对）——更新任务需显式
    ``unschedule`` + 重注册，或换 ``job_id``。

    .. rubric:: 使用示例

    Python 子类形式：

    .. code-block:: python

        class CompanionAgent(Agent):
            async def setup(self, data_dir: str):
                use_cron(self)
                self.add_tool("schedule-cron")
                self.add_tool("manage-cron")

                @self.hooks.after_create
                def _(agent):
                    # after_create 只在 create 管线触发：此刻 _nodes 已
                    # 注册、state 写闸门已解开，schedule 可用；recover
                    # 时任务由 _restore 自动重建，不会走到这里
                    scheduler = agent.inject(cron_scheduler_key)
                    scheduler.schedule(
                        agent.node_id, "3 2 * * *",
                        job_id="daily-consolidation",
                        source="daily_consolidation",
                        action=CronAction(kind="message", prompt="请执行每日沉淀"),
                    )

                @self.hooks.on_cron_trigger["daily_*"]
                def _(self, trigger: CronTrigger):
                    if trigger.fire.coalesced_count > 5:
                        trigger.shortcut = True   # 积压过多则跳过本次
                    return trigger

    ``.fya`` 声明式形式（``$script`` 块，与上例等价）：

    .. code-block:: text

        ---
        name: companion-agent
        system_prompt: 你是陪伴助手。
        ---

        --- $script
        from flowing.plugins.cron import (
            use_cron, cron_scheduler_key, CronAction, CronTrigger,
        )

        async def setup(self, data_dir: str):
            use_cron(self)
            self.add_tool("schedule-cron")

            @self.hooks.after_create
            def _(agent):
                # after_create 只在 create 管线触发；recover 时任务由
                # _restore 自动重建，不会走到这里
                scheduler = agent.inject(cron_scheduler_key)
                scheduler.schedule(
                    agent.node_id, "3 2 * * *",
                    job_id="daily-consolidation",
                    source="daily_consolidation",
                    action=CronAction(kind="message", prompt="请执行每日沉淀"),
                )

            @self.hooks.on_cron_trigger["daily_*"]
            def _(self, trigger: CronTrigger):
                if trigger.fire.coalesced_count > 5:
                    trigger.shortcut = True
                return trigger
        ---

    .. rubric:: 行为规约

    - 期待行为：调用后该 Agent 的定时触发按「休眠门控 → dispatch
      ``on_cron_trigger`` → 查执行器表执行 → 推进游标」链路执行；默认
      message 执行器入队 ``kind=MessageKind.EVENT``、``source=job.source``、
      文本由渲染模板表产出（默认 ``<cron-fire ... coalescedCount="N">``
      XML）。
    - 非行为：不注册任何任务（按需注册）；不修改 prompt；不创建
      端点（cron 不经通信总线）；不在 ``destroy()`` 时清理任务。
    - 边缘情况：recover 重跑 setup → 天然幂等（新实例、新注册表，
      无需去重机制）；同一实例手写重复调用 ``use_cron`` 属编程错误
      ——hooks 总则「同一 handler 可重复注册、不去重」，回顾 handler
      会重复挂载，后果自负；Agent 休眠/销毁时已触发
      未消费的 EVENT 消息按队列的通用恢复规则处理（``state.jsonl``
      框架核心键中的待消费消息集），调度器侧任务**保留**。
    - 前置条件：``CronPlugin`` 已经 ``runtime.use()`` 安装；只能在
      ``setup()``（或其后）调用。
    - 后置条件：``on_cron_trigger`` 钩子点存在；``after_recover``
      上有 ``by="cron"`` 的回顾 handler。
    - 零开销不变量：未调用的 Agent 无 ``on_cron_trigger`` 钩子点、
      无回顾 handler。

    .. rubric:: 测试案例

    - 前置：已装 ``CronPlugin``，Agent 调 ``use_cron`` 并在
      ``after_create`` handler 中
      ``schedule(node_id, "* * * * *", job_id="t", source="tick",
      action=CronAction(kind="message", prompt="x"))``；操作：推进
      时钟到下一分钟；期望：``on_cron_trigger["tick"]`` handler 被调用，
      随后队列出现 ``kind=EVENT, source="tick"`` 的消息。
    - 前置：同上但 handler 置 ``trigger.shortcut = True``；期望：无
      EVENT 消息入队、游标不推进（下一分钟 ``coalesced_count=2``
      合并交付）。
    - 前置：Agent 有任务且 ``last_fired_at`` 为一小时前（每分钟任务）；
      操作：``await runtime.recover_agent(agent_id)``；期望：
      ``after_recover`` 后队列中立即出现一条 ``coalescedCount=60`` 的
      EVENT 消息，``last_fired_at`` 推进到现在。
    - 前置：Agent 有任务；操作：``await agent.destroy()``；期望：
      ``scheduler.jobs(node_id)`` **不变**（任务保留，destroy ≠ 删除）。
    - 前置：未装 ``CronPlugin``；操作：``setup()`` 中 ``use_cron(self)``；
      期望：抛 ``MissingProvideError``。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.agent.Agent.inject``（时机：第 1 步校验调度器
      可用）；``flowing.hooks.HookRegistry.declare``（时机：第 2 步
      声明 ``on_cron_trigger``）；``flowing.agent.Agent.register_state``
      （时机：第 3 步声明 ``cron_jobs`` 状态键）；``HookList.__call__``
      注册 ``after_recover`` handler（时机：第 4 步，``by="cron"``）
    - 被调：无（框架内无调用方；应用层在各 Agent 的 ``setup()`` 中
      调用的 Composable 公共 API）

    .. seealso:: :class:`CronPlugin`、:class:`CronScheduler`、
       :class:`CronTrigger`、:meth:`CronScheduler.schedule`、
       :meth:`flowing.hooks.HookRegistry.declare`、
       :meth:`flowing.agent.Agent.enqueue_message`
    """
    scheduler: CronScheduler = agent.inject(cron_scheduler_key)
    agent.hooks.declare("on_cron_trigger", by="cron", match_on="source")
    agent.register_state("cron_jobs", [])
    # 单袋化最终裁决：register_state 不再有 load 参数——派生运行时结构
    # （定时器）的重建统一走 after_recover 钩子
    async def _rebuild_on_recover(a: Agent, _value: Any = None) -> None:
        # HookList.dispatch 恒以 (agent, value) 两参调用（无 value 钩子点
        # 传 None）；先重建任务与定时器（同步），再合并回顾过期触发
        # （C-15：_sweep 为 async def；钩子 handler sync/async 透明混用 M-37）
        scheduler._load_jobs(a.node_id, a.state.get("cron_jobs", []))
        await scheduler._sweep(a.node_id)

    agent.hooks.after_recover(_rebuild_on_recover, by="cron")
