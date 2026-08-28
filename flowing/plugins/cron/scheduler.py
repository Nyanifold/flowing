"""调度器：``cron_scheduler_key`` 注入键、``CronScheduler`` 与内部解析函数
``_next_ideal_fire``。
"""

from datetime import datetime
from typing import Any

from flowing.params import InjectionKey
from flowing.runtime import Runtime

from .executors import (
    CronExecutor,
    CronTemplate,
    DEFAULT_MESSAGE_TEMPLATE,
    DEFAULT_TOOL_NOTICE_TEMPLATE,
    default_message_executor,
    default_tool_executor,
)
from .models import CronAction, CronFireContext, CronJob, CronTrigger


cron_scheduler_key: InjectionKey["CronScheduler"] = InjectionKey("cron_scheduler")
"""功能/动机：全局 ``CronScheduler`` 实例在 provide 链上的注入键。
``CronPlugin.install()`` 以该键 provide 调度器；``use_cron()`` 与应用层
经 ``agent.inject(cron_scheduler_key)`` 消费。

行为边界：键名为 ``"cron_scheduler"``；未安装 ``CronPlugin`` 时 inject
该键抛 ``MissingProvideError``。

.. seealso:: :class:`flowing.params.InjectionKey`、:class:`CronScheduler`
"""

def _next_ideal_fire(cron: str, after: datetime) -> datetime:
    """``after`` 之后 cron 表达式的下一个理想触发点（naive UTC）。

    内部 API，不属稳定契约。**五字段表达式解析与理想点计算的唯一
    具名符号**（P3-12④ 裁决）：``schedule()`` 的合法性校验、
    ``_fire`` / ``_sweep`` 的区间理想点计数、收据 ``next_fire``
    都经本函数。非法表达式 → ``ValueError``。计算引擎（croniter 或
    等价实现）属实现细节，规约只承诺语义。

    .. rubric:: 调用关系（审计）

    - 调用：无（纯计算；引擎为外部库）
    - 被调：``CronScheduler.schedule``（时机：注册时校验 + 收据计算）、
      ``CronScheduler.next_fire``（时机：收据/查询）、``_fire`` /
      ``_sweep``（时机：合并计数，区间内逐理想点迭代）
    """

class CronScheduler:
    """定时调度器——任务注册表 + 分钟级触发循环（Runtime 级服务）。

    .. rubric:: 功能介绍

    由 ``CronPlugin.install()`` 创建并以 ``cron_scheduler_key`` provide。
    持有全部 ``CronJob`` 定义，在事件循环上按 cron 表达式到点触发；
    触发路径为「休眠门控 → dispatch ``on_cron_trigger`` → 查执行器表
    执行 → 推进 ``last_fired_at``」（详见模块 docstring 触发链路）。

    .. rubric:: 设计动机

    调度器是 Runtime 级单例服务（而非每 Agent 一个），因为触发循环是
    进程级资源；任务按 ``node_id`` 归属使 Agent 生命周期管理（销毁清理、
    按 session 恢复）可以精确切片。调度器只认识「任务定义 + node_id」，
    不认识 cron 之外任何业务语义（机制 vs 策略）。

    .. rubric:: 使用示例

    .. code-block:: python

        scheduler = agent.inject(cron_scheduler_key)
        scheduler.schedule(
            agent.node_id,
            "3 2 * * *",                       # 每天 02:03
            job_id="daily-consolidation",
            source="daily_consolidation",
            action=CronAction(kind="message", prompt="请执行每日沉淀"),
        )

    .. rubric:: 行为规约

    - 期待行为：``schedule()`` 登记任务、写透持久化（
      ``agent.state.cron_jobs`` set）
      并武装定时器；声明式注册经 ``after_create`` handler 调
      ``schedule()``（只在 create 管线触发，天然只跑一次）；
      到点触发按模块 docstring 的触发链路执行；``unschedule()`` 移除任务、
      取消定时器并写透持久化。
    - 非行为：不提供秒级精度；不提供任务
      并发控制（同一任务前一触发的 EVENT 消息仍在队列时，后一次触发
      照常交付——去重/合并是应用层策略）。错过触发**一律合并**进下次
      成功交付（见模块 docstring），不存在「不补偿」分支。
    - 边缘情况：目标 Agent 不存在于 ``runtime._nodes`` → 本次触发被
      门控拦下（**不交付、不推进游标、任务保留**——休眠语义；清理
      只能经 ``unschedule*`` 显式发生）；cron 表达式非法 →
      ``schedule()`` 抛 ``ValueError``。
    - 前置条件：实例只能经 ``CronPlugin.install()`` 创建。
    - 不变量：任意时刻一个 ``job_id`` 至多一个任务；``jobs()`` 返回
      只读快照（修改返回值不影响调度器）。

    .. rubric:: 调用关系（审计）

    - 实例化方：``flowing.plugins.cron.CronPlugin.install``（时机：
      阶段一 ``runtime.use(CronPlugin())`` 安装，每次安装恰好一次）

    .. seealso:: :class:`CronPlugin`、:class:`CronJob`、:func:`use_cron`
    """

    runtime: Runtime
    """Runtime 引用（定位 Agent、休眠门控查 ``_nodes``；任务写透经
    目标 Agent 的 ``agent.state.cron_jobs`` 状态键）。
    内部 API，不属稳定契约。
    """
    _jobs: dict[str, CronJob]
    """任务注册表（job_id → 定义）。内部 API，不属稳定契约；
    经 :meth:`jobs` 获取只读视图。
    """
    _executors: dict[str, CronExecutor]
    """执行器表（kind → 执行器）。初值为 ``{"message":
    default_message_executor, "tool_call": default_tool_executor}``，
    构造时被 ``CronPlugin(executors=...)`` 传入的条目覆盖/扩充
    （S-24 裁决：构造注入）。内部 API，不属稳定契约。
    """
    _templates: dict[str, CronTemplate]
    """渲染模板表（kind → Jinja2 模板源）。初值为 ``{"message":
    DEFAULT_MESSAGE_TEMPLATE, "tool_call":
    DEFAULT_TOOL_NOTICE_TEMPLATE}``，构造时被
    ``CronPlugin(templates=...)`` 传入的条目覆盖/扩充。默认执行器
    路径查表使用；自定义执行器可绕开。内部 API，不属稳定契约。
    """

    def __init__(self, runtime: Runtime,
                 executors: dict[str, CronExecutor] | None = None,
                 templates: dict[str, CronTemplate] | None = None) -> None:
        """构造调度器。

        :param executors: kind → 执行器的覆盖/扩充表（S-24 裁决：构造
            注入——默认表打底，传入条目覆盖同名 kind、追加自定义 kind）。
            未提到的 kind 用默认执行器。
        :param templates: kind → 渲染模板的覆盖/扩充表，合并语义同上。
            值为 Jinja2 模板源字符串（含 ``$./file.j2`` FILE_REF 形式），
            上下文变量表见 :data:`CronTemplate`。

        .. rubric:: 行为规约

        - 前置条件：由 ``CronPlugin.install()`` 调用；应用层不应自行
          实例化（实例须进入 provide 链且接上持久化回调才有完整语义）。
        - 非行为：构造不启动任何触发——没有任务时调度循环是空转安全的。
        - 默认执行器的注册形态（P3-12② 裁决）：构造期**闭包**，捕获
          ``self._templates``——闭包体内以查到的模板为 ``template``
          参数委托模块级默认实现；执行器契约签名
          ``async (agent, job, action, ctx) -> None`` 不变，自定义
          执行器无感知。

        .. rubric:: 调用关系（审计）

        - 调用：无（仅初始化任务表与合并执行器/模板表；默认执行器
          以闭包形式注册，体内委托模块级
          :func:`default_message_executor` / :func:`default_tool_executor`）
        - 被调：``flowing.plugins.cron.CronPlugin.install``（时机：
          阶段一安装，每次 ``runtime.use(CronPlugin())`` 一次）

        .. seealso:: :class:`CronPlugin`、:data:`CronTemplate`
        """
        self.runtime = runtime
        self._jobs = {}
        self._templates = {"message": DEFAULT_MESSAGE_TEMPLATE,
                           "tool_call": DEFAULT_TOOL_NOTICE_TEMPLATE,
                           **(templates or {})}   # 同上
        # 默认执行器 = 构造期闭包（P3-12②）：捕获 _templates，按 kind 查表
        # 取得模板后委托模块级默认实现；执行器契约签名不变
        async def _default_message_exec(agent, job, action, ctx):
            await default_message_executor(agent, job, action, ctx,
                                           template=self._templates["message"])

        async def _default_tool_exec(agent, job, action, ctx):
            await default_tool_executor(agent, job, action, ctx,
                                        template=self._templates["tool_call"])
        self._executors = {"message": _default_message_exec,
                           "tool_call": _default_tool_exec,
                           **(executors or {})}    # 传入条目覆盖/扩充（S-24）

    def schedule(
        self,
        node_id: str,
        cron: str,
        *,
        action: CronAction,
        job_id: str | None = None,
        source: str | None = None,
        recurring: bool = True,
    ) -> str:
        """注册定时任务并立即持久化。

        .. rubric:: 功能介绍

        创建 ``CronJob`` 登记进任务表、经目标 Agent 的
        ``cron_jobs`` 状态键写透落盘到该 Agent 的 ``state.jsonl``、
        武装定时器，返回
        ``job_id``。

        .. rubric:: 使用示例

        .. code-block:: python

            job_id = scheduler.schedule(
                self.node_id, "*/5 * * * *",
                action=CronAction(kind="message", prompt="生成 5 分钟摘要"),
                source="recap",
            )

        .. rubric:: 行为规约

        - 期待行为：返回最终生效的 ``job_id``（显式传入则原样返回）。
        - 边缘情况：``job_id`` 与现存任务重复 → 抛 ``ValueError``（注册
          不幂等，与端点注册同立场：冲突应暴露而非覆盖）；``cron`` 非法
          → ``ValueError``；``source=None`` → 填 ``f"cron:{job_id}"``；
          ``action.kind`` 在执行器表中无对应执行器（含未注册执行器的
          自定义 kind）→ ``ValueError``——拼错的 kind 与「忘了注册
          执行器」都在调度时暴露，而不是留到触发时静默失败；
          **目标 Agent 休眠（不在活体表）→ ``KeyError``**（S-38 裁决：
          禁止向休眠 Agent 注册——框架内部生产者（LLM 工具组 /
          ``after_create`` 声明式注册）都以调用方自己为目标，休眠注册
          只可能来自应用层
          误传 ``node_id``；此时无 ``state.jsonl`` 写通道（休眠时
          defaults/load 不可用），与其静默半持久化（重启即丢）不如
          直接报错。沿用 ``Runtime.get_node`` 默认语义，不特判）。
        - 后置条件：任务在下一个匹配分钟触发；状态已落盘。

        :raises ValueError: —— cron 表达式非法、``job_id`` 冲突或
            ``action.kind`` 无注册执行器时。
        :raises KeyError: —— 目标 Agent 休眠（不在活体表）时（S-38 裁决，
            沿用 ``Runtime.get_node`` 默认语义）。

        .. rubric:: 测试案例

        - 前置：空调度器；操作：``schedule("agent-1", "bad expr",
          action=CronAction(kind="message", prompt="x"))``；
          期望：抛 ``ValueError`` 且任务表仍为空。
        - 前置：已有 ``job_id="a"``；操作：再以 ``job_id="a"`` 注册；
          期望：抛 ``ValueError``，原任务不变。
        - 前置：默认调度器；操作：以 ``kind="workflow"`` 的 action
          注册；期望：抛 ``ValueError``（无该 kind 的执行器）。

        .. rubric:: 调用关系（审计）

        - 调用：构造 ``CronJob``（时机：每次注册）；经目标 Agent 的
          ``flowing.agent.Agent.state`` 视图写透落盘（时机：先落盘、
          再登记内存表、最后武装定时器）
        - 被调：``flowing.plugins.cron.ScheduleCronTool.execute`` /
          ``ScheduleCronMessageTool.execute`` /
          ``ScheduleCronToolCallTool.execute``（时机：LLM 每次调用
          schedule-cron 系列工具）；应用层经
          ``inject(cron_scheduler_key)`` 的代码路径（公共 API）——
          声明式注册的推荐形态是 ``after_create`` handler 内调用
          （见 :func:`use_cron` 使用示例）

        .. seealso:: :meth:`unschedule`、:class:`CronJob`、
           :class:`CronAction`
        """
        if job_id is None:
            job_id = "..."  # 缺省时由调度器生成 UUID 字符串（生成器未见具名符号）
        if job_id in self._jobs:
            raise ValueError(f"job_id 冲突: {job_id}")
        # cron 表达式合法性校验（P3-12④：唯一具名解析符号；非法 → ValueError）
        _next_ideal_fire(cron, datetime.utcnow())
        if action.kind not in self._executors:
            raise ValueError(f"action.kind 无注册执行器: {action.kind}")
        if source is None:
            source = f"cron:{job_id}"
        job = CronJob(
            id=job_id, node_id=node_id, cron=cron, action=action,
            source=source, created_at=datetime.utcnow(), recurring=recurring,
        )
        agent = self.runtime.get_node(node_id)   # S-38：目标休眠 → KeyError 直接上抛（不做半持久化注册）
        # 先落盘：经目标 Agent 的 cron_jobs 状态键写透（set 即写透）
        jobs_data = agent.state.get("cron_jobs", [])
        jobs_data.append(job.to_dict())  # P3-12①：state 恒存 list[dict]，对象本体只在调度器内存表
        agent.state.cron_jobs = jobs_data
        self._jobs[job_id] = job          # 再登记内存表
        # 最后武装定时器（避免幽灵触发）：定时器容器未见具名符号
        return job_id

    def next_fire(self, job_id: str) -> str:
        """任务的下一个理想触发点（ISO 8601 字符串，naive UTC）。

        .. rubric:: 功能介绍

        收据与观测用的派生量（P3-12④ 裁决）：当前时刻之后 ``job.cron``
        的下一个理想触发点，经 :func:`_next_ideal_fire` 计算。调度器
        不存储该值——它可由「cron 表达式 + 当前时间」无状态地推出。

        .. rubric:: 行为规约

        - 边缘情况：``job_id`` 不存在 → ``KeyError``（与查表语义一致）。
        - 非行为：不是「实际交付时刻」承诺——休眠门控/合并语义下实际
          交付可能更晚（见模块 docstring 触发链路）。

        .. rubric:: 调用关系（审计）

        - 调用：:func:`_next_ideal_fire`（时机：每次调用现场计算）
        - 被调：``ScheduleCronTool`` / ``ScheduleCronMessageTool`` /
          ``ScheduleCronToolCallTool.execute``（时机：每次注册成功后
          包装收据）；应用层观测（公共 API）
        """
        job = self._jobs[job_id]
        return _next_ideal_fire(job.cron, datetime.utcnow()).isoformat()

    def unschedule(self, job_id: str) -> bool:
        """移除任务并持久化。

        .. rubric:: 行为规约

        - 期待行为：存在则移除、取消定时器、写透落盘，返回 ``True``。
        - 边缘情况：不存在 → 返回 ``False``（幂等友好，清理路径可重入）。

        .. rubric:: 调用关系（审计）

        - 调用：经目标 Agent 的 ``flowing.agent.Agent.state`` 视图写透
          落盘（时机：移除时）
        - 被调：``flowing.plugins.cron.CronScheduler._fire`` 第 7 步
          （时机：一次性任务成功交付后自删）；
          ``flowing.plugins.cron.ManageCronTool.execute``（时机：LLM
          ``action="cancel"``）；应用层显式清理路径（公共 API）

        .. seealso:: :meth:`schedule`、:meth:`unschedule_all`
        """
        job = self._jobs.pop(job_id, None)
        if job is None:
            return False  # 不存在 → 幂等友好
        # 取消定时器：定时器容器未见具名符号
        try:
            agent = self.runtime.get_node(job.node_id)   # 公共查表口；目标休眠 → KeyError
        except KeyError:
            agent = None
        if agent is not None:
            # 写透落盘：从该 Agent 的 cron_jobs 状态键任务表中删除
            jobs_data = [j for j in agent.state.get("cron_jobs", [])
                         if j.get("id") != job_id]  # P3-12①：元素恒为 dict，按 id 比对
            agent.state.cron_jobs = jobs_data
        return True

    def unschedule_all(self, node_id: str) -> None:
        """移除某 Agent 的全部任务（应用层显式清理入口）。

        .. rubric:: 行为规约

        - 期待行为：该 ``node_id`` 下任务全部移除并写透落盘。
        - 边缘情况：无任务 → 空操作（幂等）。
        - 非行为：不影响其他 Agent 的任务；**不被框架自动调用**——
          ``destroy()`` 不触发清理（destroy ≠ 删除，任务随 session
          存续，见模块 docstring「Agent 消失与任务生命周期」）。

        .. rubric:: 调用关系（审计）

        - 调用：无（时机：未见规约——逐条移除是否复用 ``unschedule``
          未写明）
        - 被调：无（框架内无调用方；应用层显式清理入口，docstring
          明示不被框架自动调用）

        .. seealso:: :meth:`unschedule`、:func:`use_cron`
        """
        for job in self.jobs(node_id):
            pass  # 逐条移除：是否复用 unschedule() 未见规约，不猜
            # （设计意图同 unschedule 语义：移除 + 取消定时器 + 写透落盘）

    def jobs(self, node_id: str | None = None) -> list[CronJob]:
        """查询任务（只读快照）。

        .. rubric:: 功能介绍

        插件状态的主动查询入口（M-81 后这是**唯一**观测路径：框架无
        快照命名空间挂载机制，插件状态不进 ``RuntimeSnapshot``）。

        .. rubric:: 行为规约

        - ``node_id=None`` 返回全部任务；否则只返回该 Agent 的任务。
        - 返回值为拷贝列表，元素本身按只读约定使用（改写元素不影响
          调度器内部状态）。

        .. rubric:: 调用关系（审计）

        - 调用：无（只读快照，纯查表）
        - 被调：``flowing.plugins.cron.ManageCronTool.execute``（时机：
          LLM ``action="list"``）；应用层观测入口（公共 API，M-81
          后唯一观测路径）

        .. seealso:: :meth:`flowing.runtime.Runtime.get_plugin`
        """
        if node_id is None:
            return list(self._jobs.values())  # 拷贝列表
        return [job for job in self._jobs.values() if job.node_id == node_id]

    async def _fire(self, job_id: str) -> None:
        """到点触发：dispatch ``on_cron_trigger`` → 查执行器表执行。

        内部 API，不属稳定契约。``async def``（C-15 裁决：时序契约第 4/6
        步要求 await async dispatch 与执行器，同步签名与契约互斥）；
        定时器回调处 ``asyncio.create_task`` 包装属实现细节（fire-and-forget
        惯例见 spec-draft 09 §1169）。时序契约（实现必须遵守）：

        1. 查任务表，任务不存在则直接返回。
        2. **休眠门控**：按 ``job.node_id`` 在 ``runtime._nodes`` 定位
           Agent；不存在 = 休眠 → 不交付、不推进 ``last_fired_at``，
           直接返回（任务保留；错过次数随游标停滞自动累积）。
        3. 计算 ``coalesced_count``：cron 表达式在 ``(job.last_fired_at
           ?? job.created_at, now]`` 内的理想触发次数；构造
           ``CronFireContext``（``scheduled_at`` 取区间最后一个理想点）。
        4. 构造 ``CronTrigger``（``action`` 为 ``job.action`` 的拷贝，
           ``fire`` 为该 ctx）并
           ``agent.hooks.on_cron_trigger.dispatch(agent, trigger)``；
           目标 Agent 未启用 ``use_cron``（无该钩子点）时跳过 dispatch。
        5. ``trigger.shortcut`` 为真则结束（跳过本次触发，**不推进
           游标**——跳过不算成功交付，次数继续累积）。
        6. 按 ``trigger.action.kind`` 查 ``_executors`` 并
           ``await executor(agent, job, trigger.action, ctx)``；表中无该
           kind（恢复出的旧任务撞上未注册对应执行器的插件配置）→
           ``warnings.warn`` 并跳过本次触发——定时链路无人 await，异常
           不应逃逸进事件循环回调。
        7. 执行成功 → 推进 ``job.last_fired_at = ctx.fired_at`` 并写透
           落盘；``job.recurring`` 为 ``False`` → 随即 ``unschedule``
           自删（写透落盘）。**游标推进到实际交付时刻（now）而非区间
           最后一个理想点**（用户裁决）——当前明确不引入 jitter
           （M-88），两方案无可观测差别；注意若未来引入 jitter /
           提前触发，now 方案会把交付抖动混入调度状态（区间边界
           不再对齐 cron 刻度），届时需重估。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.hooks.HookList.dispatch``（时机：第 4 步，
          每次未被门控的触发）；执行器表 ``_executors[kind]``（时机：
          第 6 步）；``CronScheduler.unschedule``（时机：第 7 步，
          一次性任务自删）；``warnings.warn``（时机：第 6 步 kind
          无注册执行器）
        - 被调：定时器回调（时机：cron 表达式到点，分钟级触发循环；
          定时器由 ``schedule`` / ``_load_jobs`` 武装）

        .. seealso:: :class:`CronTrigger`、:class:`CronFireContext`、
           :func:`default_message_executor`、:meth:`_sweep`
        """
        job = self._jobs.get(job_id)
        if job is None:
            return  # 第 1 步：任务不存在直接返回
        try:
            agent = self.runtime.get_node(job.node_id)   # 公共查表口
        except KeyError:
            return  # 第 2 步 休眠门控：不交付、不推进 last_fired_at
        now = datetime.utcnow()
        # 第 3 步：coalesced_count = cron 表达式在 (job.last_fired_at ??
        # job.created_at, now] 内的理想触发次数（经 _next_ideal_fire 逐
        # 理想点迭代计数——唯一具名解析符号，P3-12④）；scheduled_at 取
        # 区间最后一个理想点
        coalesced_count = 1  # 占位（真实值由 _next_ideal_fire 迭代算出）
        ctx = CronFireContext(
            scheduled_at=now, fired_at=now,
            coalesced_count=coalesced_count, last_fired_at=job.last_fired_at,
        )
        # 第 4 步：构造 CronTrigger（action 为 job.action 的拷贝——拷贝
        # 方式未见具名符号）并 dispatch；目标 Agent 未 use_cron（无
        # on_cron_trigger 钩子点）时跳过 dispatch
        trigger = CronTrigger(job=job, source=job.source, action=job.action, fire=ctx)
        # shortcut 初值 None（C-14 裁决后构造体不再显式传 shortcut=False）
        # trigger = await agent.hooks.on_cron_trigger.dispatch(agent, trigger)
        # （C-15 后本方法为 async def，dispatch 可正常 await）
        if trigger.shortcut:
            return  # 第 5 步：跳过本次触发，不推进游标
        executor = self._executors.get(trigger.action.kind)
        if executor is None:
            # 第 6 步：恢复出的旧任务撞上未注册对应执行器的插件配置
            import warnings
            warnings.warn(f"cron job {job_id}: kind "
                          f"{trigger.action.kind!r} 无注册执行器，跳过本次触发")
            return
        # await executor(agent, job, trigger.action, ctx)
        # （C-15 后本方法为 async def，执行器可正常 await）
        job.last_fired_at = ctx.fired_at  # 第 7 步：执行成功 → 推进游标
        jobs_data = [job.to_dict() if j.get("id") == job_id else j
                     for j in agent.state.get("cron_jobs", [])]
        agent.state.cron_jobs = jobs_data
        # ↑ 游标推进的写透落盘：按 id 定点替换该任务的 dict（P3-12①）
        if not job.recurring:
            self.unschedule(job_id)  # 一次性任务随即自删（写透落盘）

    async def _sweep(self, node_id: str) -> None:
        """恢复回顾：Agent 实体回到 ``_nodes`` 时，立即合并交付其过期任务。

        内部 API，不属稳定契约。``async def``（C-15 裁决：交付链路与
        :meth:`_fire` 相同，须能 await dispatch 与执行器；同步签名与
        时序契约互斥）。由 ``use_cron`` 注册的 ``after_recover``
        handler（``by="cron"``）调用。对该 ``node_id`` 的每条任务：

        1. 算 ``count`` = cron 表达式在 ``(last_fired_at ?? created_at,
           now]`` 内的理想触发次数；``0`` → 跳过（无过期）。
        2. ``>= 1`` → 立即走与 :meth:`_fire` 第 3–7 步完全相同的交付
           链路（dispatch 钩子 → 执行器 → 推进游标 → 一次性自删），
           ``CronFireContext.scheduled_at`` 取区间内最后一个理想点。

        与「等下一个触发点惰性交付」的分工：sweep 让恢复后**立即**得到
        一次合并回顾；sweep 之后游标已推进，后续到点触发按
        ``coalesced_count=1`` 正常走。新建管线的 ``after_create`` 不挂
        sweep——新建时不存在过期任务（声明式注册就在 ``after_create``
        handler 里经 ``schedule`` 发生，那一刻起游标才开始）。

        .. rubric:: 调用关系（审计）

        - 调用：与 ``_fire`` 第 3–7 步完全相同的交付链路（时机：
          每条过期任务一次合并交付）
        - 被调：``flowing.plugins.cron.use_cron`` 注册的
          ``after_recover`` handler（``by="cron"``，时机：Agent
          每次 session 恢复、状态恢复完成后）

        .. seealso:: :func:`use_cron`、:meth:`_fire`
        """
        for job in self.jobs(node_id):
            # 第 1 步：count = cron 表达式在 (job.last_fired_at ??
            # job.created_at, now] 内的理想触发次数（经 _next_ideal_fire
            # 逐理想点迭代计数——唯一具名解析符号，P3-12④）
            count = 0  # 占位（真实值由 _next_ideal_fire 迭代算出）
            if count >= 1:
                # 第 2 步：走与 _fire 第 3–7 步完全相同的交付链路
                # （是否复用 _fire 未见规约，此处按复用写；C-15 后
                # _fire 为 async def，正常 await）
                await self._fire(job.id)

    def _load_jobs(self, node_id: str, jobs: list[dict[str, Any]]) -> None:
        """恢复入口：把某 Agent 的持久化任务重建进调度器并重新武装。

        内部 API，不属稳定契约。由 ``use_cron`` 注册的 ``after_recover``
        handler 在 session 恢复（``Agent._restore()`` 重放完成、写闸门
        解锁）后调用——单袋化最终裁决取消了 ``register_state`` 的
        ``load`` 参数，派生运行时结构（定时器）的重建统一走
        ``after_recover`` 钩子。只重建定义与定时器；错过触发的合并交付
        由同一 handler 随后的 :meth:`_sweep` 完成，本方法不直接交付。

        .. rubric:: 调用关系（审计）

        - 调用：无（重建定义与重新武装定时器；逐条是否复用
          ``schedule`` 未见规约）
        - 被调：``use_cron`` 注册的 ``after_recover`` handler（时机：
          ``Agent._restore()`` 重放完成后、``_sweep`` 之前）

        .. seealso:: :func:`use_cron`、:meth:`_sweep`
        """
        for data in jobs:
            # 恢复重放的唯一读通道：CronJob.from_dict（P3-12①；形状损坏
            # → ValueError fail-fast 自 CronAction.from_dict）
            job = CronJob.from_dict(data)
            self._jobs[job.id] = job  # 按 job_id 覆盖进表，重复恢复幂等
            # 重新武装定时器：定时器容器未见具名符号
        # 本方法不直接交付——错过合并由随后的 after_recover → _sweep 完成

    def _stop(self) -> None:
        """停止全部定时器（Runtime shutdown 插件收尾阶段调用）。

        内部 API，不属稳定契约。任务定义不删除（已落盘的定义随
        ``state.jsonl`` 存续，下次启动经 ``_load_jobs`` 恢复）。

        .. rubric:: 调用关系（审计）

        - 调用：无（停止全部定时器，不删任务定义）
        - 被调：``CronPlugin.shutdown()``（时机：每次优雅关闭，
          ``Runtime.shutdown()`` 插件收尾阶段按 install 顺序逐个 await）

        .. seealso:: :meth:`flowing.runtime.Runtime.shutdown`
        """
        ...
