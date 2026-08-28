"""Cron 数据对象：``CronAction`` / ``CronFireContext`` / ``CronJob`` / ``CronTrigger``。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CronFireContext:
    """一次交付的只读触发上下文（执行器的第 4 个参数；渲染模板时摊平为
    模板变量）。

    .. rubric:: 功能介绍

    承载「这次交付是什么性质」的事实：理想触发时刻、实际交付时刻、
    合并计数、上次成功交付时刻。与 :class:`CronTrigger`（钩子 value，
    可改写、可短路）分工不同——本对象是不可变事实，钩子 handler 与
    执行器都只读它。

    .. rubric:: 设计动机

    「一律合并」规则下，错过计数不需要任何持久化记账结构：
    ``coalesced_count`` 由 cron 表达式在 ``(last_fired_at ?? created_at,
    now]`` 区间内无状态地算出（见模块 docstring 的错过提醒策略）。
    本对象只是把算出的结果打包传递。

    .. rubric:: 行为规约

    - 不变量：``coalesced_count >= 1``；``== 1`` 即「无错过的准时
      触发」（区间内只有当前一个理想点）；``scheduled_at`` 是区间内
      **最后一个**理想触发时刻（``<= fired_at``）。
    - 边缘情况：``last_fired_at=None`` 表示任务从未成功交付（基数为
      ``job.created_at``）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.plugins.cron.CronScheduler._fire``（时机：每次到点
      触发的第 3 步构造）、``flowing.plugins.cron.CronScheduler._sweep``
      （时机：恢复回顾，每条过期任务交付时构造）
    - 实例化方：``flowing.plugins.cron.CronScheduler._fire`` 与
      ``flowing.plugins.cron.CronScheduler._sweep``（每次交付）

    .. seealso:: :class:`CronTrigger`、:class:`CronJob`
    """

    scheduled_at: datetime
    """本次交付对应的理想触发时刻（区间最后一个理想点，naive UTC）。
    """
    fired_at: datetime
    """实际交付时刻（naive UTC）。
    """
    coalesced_count: int
    """合并计数：``(last_fired_at ?? created_at, fired_at]`` 区间内的
    理想触发次数（含本次），``>= 1``。
    """
    last_fired_at: datetime | None
    """上次成功交付时刻；``None`` 表示从未交付。
    """

@dataclass
class CronAction:
    """一次触发的**动作描述**（第 1 层：Agent 的选择，随任务落盘）。

    .. rubric:: 功能介绍

    描述「到点做什么」：``kind="message"`` 表示给 Agent 发一条提示词；
    ``kind="tool_call"`` 表示以别名化参数调用一个工具。「怎么做」由插件级
    执行器决定，不在本对象的语义内。

    .. rubric:: 设计动机

    动作是纯数据（kind + 参数），因此可以随 ``CronJob`` 一起 JSON 化
    落盘、崩溃恢复后原样重建；不可序列化的执行策略被推到插件级执行器
    （每次加载插件时重新注入）。动作自成对象而非平铺进 ``CronJob``，
    使校验集中于 ``__post_init__``、落盘结构自描述、未来新增 kind
    不破坏既有字段。

    .. rubric:: 使用示例

    .. code-block:: python

        CronAction(kind="message", prompt="请执行每日沉淀")
        CronAction(kind="tool_call", tool="check-email",
                   args={"folder": "INBOX"})

    .. rubric:: 行为规约

    - 期待行为：构造即校验（``__post_init__``）——``kind="message"``
      要求 ``prompt`` 非空；``kind="tool_call"`` 要求 ``tool`` 非空。
    - 边缘情况：``kind`` 为其它字符串（自定义 kind）时形状校验放行，
      但是否可调度取决于插件是否注册了同名执行器——见
      :meth:`CronScheduler.schedule`。
    - 不变量：入库的 action 一定通过形状校验；字段全部可 JSON 化。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.plugins.cron.CronScheduler.schedule``（时机：
      注册时形状与执行器归属校验）
    - 实例化方：``flowing.plugins.cron.ScheduleCronTool.execute`` /
      ``ScheduleCronMessageTool.execute`` /
      ``ScheduleCronToolCallTool.execute``（时机：LLM 每次调用
      schedule-cron 系列工具）；应用层代码构造（公共 API）

    .. seealso:: :class:`CronJob`、:class:`CronPlugin`、
       :meth:`CronScheduler.schedule`
    """

    kind: str
    """动作种类。内建 ``"message"`` / ``"tool_call"``；自定义 kind 需
    插件经 ``CronPlugin(executors={...})`` 注册同名执行器。
    """
    prompt: str = ""
    """``kind="message"`` 的提示词。默认执行器将其作为 EVENT 消息文本。
    """
    tool: str = ""
    """``kind="tool_call"`` 的工具注册名（kebab-case）。
    """
    args: dict[str, Any] = field(default_factory=dict)
    """``kind="tool_call"`` 的别名化参数，原样传给
    :meth:`flowing.agent.Agent.tool_call`。
    """

    def __post_init__(self) -> None:
        """形状校验：``message`` 需非空 ``prompt``，``tool_call`` 需非空
        ``tool``；违反抛 ``ValueError``。自定义 kind 不在此校验（归属
        检查在 :meth:`CronScheduler.schedule`）。

        .. rubric:: 调用关系（审计）

        - 调用：无（纯形状校验）
        - 被调：dataclass 构造协议（时机：每次 ``CronAction`` 实例化）
        """
        if self.kind == "message" and not self.prompt:
            raise ValueError("kind='message' 要求 prompt 非空")
        if self.kind == "tool_call" and not self.tool:
            raise ValueError("kind='tool_call' 要求 tool 非空")
        # 自定义 kind 放行（归属检查在 CronScheduler.schedule）

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 可化纯 dict（落盘形态）。

        .. rubric:: 功能介绍

        字段平铺为 ``{"kind", "prompt", "tool", "args"}`` 四键；空默认
        字段（``""`` / ``{}``）**也照常写出**——落盘结构自描述，不依赖
        读端补默认值。

        .. rubric:: 调用关系（审计）

        - 调用：无（纯字段拷贝）
        - 被调：:meth:`CronJob.to_dict`（时机：每次任务写透落盘）

        .. seealso:: :meth:`from_dict`
        """
        return {"kind": self.kind, "prompt": self.prompt,
                "tool": self.tool, "args": dict(self.args)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronAction":
        """从落盘 dict 重建。构造即触发 ``__post_init__`` 形状校验——
        损坏的持久化数据在此 fail-fast（``ValueError``）。

        .. rubric:: 调用关系（审计）

        - 调用：dataclass 构造协议（``__post_init__`` 形状校验）
        - 被调：:meth:`CronJob.from_dict`（时机：恢复重放重建任务）

        .. seealso:: :meth:`to_dict`
        """
        return cls(kind=data["kind"], prompt=data.get("prompt", ""),
                   tool=data.get("tool", ""), args=dict(data.get("args", {})))

@dataclass
class CronJob:
    """一条定时任务的定义（持久化单元）。

    .. rubric:: 功能介绍

    描述「哪个 Agent、按什么 cron 表达式、以什么负载与来源触发」。
    是 ``cron_jobs`` 状态键（``Agent.register_state("cron_jobs", [])``
    声明、写透存储/恢复重放）的序列化单元——**状态袋里恒存
    ``list[dict]`` 纯数据**（P3-12① 裁决：内存形态与磁盘形态同构，
    写透层无需任何领域知识）；对象本体只存在于调度器内存表
    ``CronScheduler._jobs``。进出 state 的唯一通道是
    :meth:`to_dict` / :meth:`from_dict`。

    .. rubric:: 设计动机

    任务定义是纯数据（不含回调、不含 Task 句柄）——定时器是运行期对象，
    可随时由「定义 + 当前时间」重建。这使崩溃恢复只是「重新武装定义」，
    无需任何执行态迁移。

    .. rubric:: 行为规约

    - 不变量：``id`` 在 Runtime 内全局唯一；``node_id`` 指向任务所属
      Agent；``cron`` 为合法五字段表达式（合法性在 ``schedule()`` 时
      校验，入库的 job 一定可解析）；``last_fired_at`` 只在**成功交付**
      时推进——休眠/宕机期它停住，错过次数由此自动累积（见模块
      docstring 的「一律合并」规则）。
    - 边缘情况：``action.args`` 可为空 dict；``source`` 缺省由调度器填
      ``f"cron:{id}"``；``last_fired_at=None`` 表示从未交付（合并计数
      基数退化为 ``created_at``）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.plugins.cron.CronScheduler._fire`` /
      ``_sweep``（时机：每次触发读取）；``CronScheduler.jobs``
      （时机：观测查询）；``CronTrigger.job`` 承载进钩子 dispatch
    - 实例化方：``flowing.plugins.cron.CronScheduler.schedule``
      （时机：每次注册）；``CronScheduler._load_jobs``（时机：
      session 恢复重放后重建）

    .. seealso:: :meth:`CronScheduler.schedule`、:class:`CronTrigger`、
       :class:`CronFireContext`
    """

    id: str
    """任务 ID。``schedule()`` 未显式指定时由调度器生成（UUID 字符串）；
    显式指定便于幂等重建与按名取消。
    """
    node_id: str
    """任务所属 Agent 的节点 ID。触发时据此在 ``runtime._nodes`` 定位
    Agent；定位不到 = 休眠（门控拦截，任务保留——清理只能经
    ``unschedule`` / ``unschedule_all`` 显式发生）。
    """
    cron: str
    """五字段 cron 表达式（``分 时 日 月 周``，分精度）。
    """
    action: CronAction
    """触发时「做什么」（第 1 层）。可 JSON 化，随任务落盘与恢复；
    「怎么做」由插件级执行器决定。经 ``on_cron_trigger`` 可被改写
    的是 ``CronTrigger.action`` 副本，本字段始终是落盘原始定义。
    """
    source: str
    """EVENT 消息的 ``source`` 字段值；也是 ``on_cron_trigger`` 钩子的
    ``match_on`` 匹配字段（fnmatch）。缺省 ``f"cron:{id}"``——应用层
    应显式指定语义化来源（如 ``"daily_consolidation"``）以便过滤。
    """
    created_at: datetime = ...
    """创建时间，naive UTC ``datetime``（与通信信封的时间约定一致）；
    也是从未交付任务的合并计数基数。
    """
    recurring: bool = True
    """``False`` = 一次性任务：第一次成功交付后自删（写透落盘）。
    到点未交付（休眠/被 shortcut 跳过）不算「成功交付」，不自删。
    """
    last_fired_at: datetime | None = None
    """上次**成功交付**时刻（naive UTC），随任务写透落盘。合并计数
    游标：``coalesced_count`` = cron 表达式在 ``(last_fired_at ??
    created_at, now]`` 内的理想触发次数。
    """

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 可化纯 dict——进出 ``cron_jobs`` 状态键的唯一
        写通道（P3-12① 裁决）。

        .. rubric:: 功能介绍

        全部字段平铺；``action`` 委托 :meth:`CronAction.to_dict`；
        ``created_at`` / ``last_fired_at`` 转 ISO 8601 字符串
        （``None`` 保持 ``None``）。空默认字段照常写出。

        .. rubric:: 调用关系（审计）

        - 调用：``CronAction.to_dict``（时机：嵌套字段序列化）
        - 被调：``CronScheduler.schedule`` / 触发后写回（时机：每次
          ``cron_jobs`` 状态键写透）；``manage-cron`` 工具 ``action="list"``
          （时机：向 LLM 输出任务清单）

        .. seealso:: :meth:`from_dict`
        """
        return {
            "id": self.id, "node_id": self.node_id, "cron": self.cron,
            "action": self.action.to_dict(), "source": self.source,
            "created_at": self.created_at.isoformat(),
            "recurring": self.recurring,
            "last_fired_at": self.last_fired_at.isoformat() if self.last_fired_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronJob":
        """从落盘 dict 重建——恢复重放的唯一读通道（P3-12① 裁决）。

        .. rubric:: 功能介绍

        ``action`` 委托 :meth:`CronAction.from_dict`（形状损坏 →
        ``ValueError`` fail-fast）；时间字段从 ISO 8601 解析回 naive UTC
        ``datetime``。

        .. rubric:: 行为规约

        - 边缘情况：缺 ``recurring`` / ``last_fired_at`` 键按默认值
          （``True`` / ``None``）重建——向前兼容旧版落盘数据。
        - 非行为：不校验 ``node_id`` 对应 Agent 是否在场（休眠门控是
          触发期行为，不是重建期行为）。

        .. rubric:: 调用关系（审计）

        - 调用：``CronAction.from_dict``（时机：嵌套字段重建）
        - 被调：``CronScheduler._load_jobs``（时机：``after_recover``
          重建任务表）

        .. seealso:: :meth:`to_dict`
        """
        return cls(
            id=data["id"], node_id=data["node_id"], cron=data["cron"],
            action=CronAction.from_dict(data["action"]),
            source=data.get("source", f"cron:{data['id']}"),
            created_at=datetime.fromisoformat(data["created_at"]),
            recurring=data.get("recurring", True),
            last_fired_at=(datetime.fromisoformat(data["last_fired_at"])
                           if data.get("last_fired_at") else None),
        )

@dataclass
class CronTrigger:
    """``on_cron_trigger`` 钩子的 value——一次即将发生的触发。

    .. rubric:: 功能介绍

    调度器在到点投递 EVENT 消息**之前** dispatch 的本对象。handler 签名
    统一为 ``(agent, trigger) -> trigger``。

    .. rubric:: 设计动机

    与核心钩子体系的 value 约定对齐：可改写（改 ``action``，属同一语义
    的调整）、可短路（置 ``shortcut=True`` **跳过本次触发**——纯取消
    语义，不是替换执行逻辑；「到点改做别的事」请走插件级执行器注入，
    见 :class:`CronPlugin`）、``raise Intercepted`` 硬阻断（同样跳过
    本次并中止后续 handler）。触发拦截是机制；「什么该拦截」是应用层
    策略。``shortcut`` 初值 ``None``、以「非 None 即短路」门控
    （C-14 裁决，与全库统一契约一致；初值 ``False`` 会让首个 handler
    返回即误停链）。

    .. rubric:: 使用示例

    .. code-block:: python

        @self.hooks.on_cron_trigger["daily_*"]
        def _(self, trigger: CronTrigger):
            if self.is_active_within(minutes=10):
                trigger.shortcut = True      # 有活动则跳过本次沉淀
            return trigger

    .. rubric:: 行为规约

    - 期待行为：dispatch 返回的（可能被改写的）trigger 决定本次执行
      的 ``action`` 内容；``shortcut=True`` 时**不执行动作**（不入队、
      不调用执行器）。
    - 非行为：置 shortcut 不取消任务本身——下一次到点仍会触发（一次性
      任务请用 ``schedule(..., recurring=False)``）。
    - 边缘情况：无 handler 注册时 dispatch 原样返回，正常执行。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.hooks.HookList.dispatch``（时机：每次触发第 4
      步，作为 ``on_cron_trigger`` 钩子 value）
    - 实例化方：``flowing.plugins.cron.CronScheduler._fire`` 第 4 步
      （时机：每次未被门控的触发）、``CronScheduler._sweep``（时机：
      恢复回顾交付）

    .. seealso:: :class:`CronJob`、:class:`CronAction`、:func:`use_cron`、
       :meth:`flowing.hooks.HookList.dispatch`
    """

    job: CronJob
    """触发来源任务（只读语义：handler 不应修改 ``job`` 字段本身）。
    """
    source: str
    """任务来源标识（初值取自 ``job.source``，C-16 裁决升格为本对象的
    顶层字段）——``on_cron_trigger`` 钩子点的 ``match_on="source"``
    过滤字段；dispatch 只做顶层 ``getattr``（不下钻 ``job.source``），
    故必须在本对象上实体存在。handler 可改写以改变后续 pattern
    匹配（与 value 可改写语义一致）。
    """
    action: CronAction
    """本次生效的动作（初值取自 ``job.action`` 的拷贝）。handler 可改写；
    执行器消费的是本字段，而非 ``job.action``。
    """
    fire: CronFireContext
    """本次交付的只读触发上下文（合并计数、理想/实际时刻）。handler
    可据 ``fire.coalesced_count`` 区分准时触发与合并交付；
    不应修改（事实不属于钩子可写面——可写的是 ``action`` 与
    ``shortcut``）。
    """
    shortcut: bool | None = None
    """跳过标记（初值 ``None``，C-14 裁决——与核心 dispatch 的「非 None
    即短路」门控对齐；初值 ``False`` 会让首个 handler 返回即误停链）。
    任一 handler 置 ``True`` 即跳过本次触发（不执行任何执行器），
    后续 handler 不再执行（链停止）。语义仅限「取消」：替换执行逻辑
    请用 ``CronPlugin(executors=...)`` 注入。
    """
