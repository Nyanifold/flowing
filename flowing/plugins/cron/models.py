"""``flowing.plugins.cron.models`` —— 定时扩展的数据对象。

本模块定义定时扩展的四个公开数据对象：:class:`CronFireContext` （一次
交付的只读触发上下文）、:class:`CronAction` （触发时「做什么」的动作
描述）、:class:`CronJob` （一条定时任务的定义，持久化单元）与
:class:`CronTrigger` （``on_cron_trigger`` 钩子点的 value）。

四个对象的关系：:class:`CronJob` 是任务定义，含 :class:`CronAction`；
调度器每次到点触发时构造 :class:`CronFireContext` （本次交付的事实）与
:class:`CronTrigger` （本次触发的可改写视图，含动作拷贝与触发上下文），
把后者交给 ``on_cron_trigger`` 钩子 dispatch。

.. seealso:: :mod:`flowing.plugins.cron` （扩展的启用方式与整体契约）、
    :mod:`flowing.plugins.cron.cron` （插件与 ``use_cron``）、
    :mod:`flowing.plugins.cron.scheduler` （调度器）
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CronFireContext:
    """一次交付的只读触发上下文——执行器收到的第 4 个参数。

    .. rubric:: 功能介绍

    承载「这次交付是什么性质」的事实：理想触发时刻、实际交付时刻、
    合并计数、上次成功交付时刻。由调度器在每次交付（到点触发或恢复
    回顾的合并交付）时构造；钩子 handler 与执行器只读它，不修改。

    与 :class:`CronTrigger` 分工不同：触发上下文是不可变事实，
    :class:`CronTrigger` 是可改写的本次触发视图。

    .. rubric:: 行为要点

    - ``scheduled_at`` 是区间 ``(last_fired_at ?? created_at, fired_at]``
      内最后一个理想触发时刻，恒 ``<= fired_at``。
    - ``coalesced_count`` 是该区间内的理想触发次数（含本次）。定时器
      只在理想触发点之后触发，因此正常调度路径下恒 ``>= 1``；
      ``== 1`` 表示「无错过的准时触发」（区间内只有当前一个理想点）。
      仅在测试场景（手动驱动调度且时钟尚未越过理想点）时计数可为 0。
    - ``last_fired_at=None`` 表示任务从未成功交付（合并计数基数退化
      为 ``job.created_at``）。

    .. seealso:: :class:`CronTrigger`、:class:`CronJob`
    """

    scheduled_at: datetime
    """本次交付对应的理想触发时刻（区间内最后一个理想点，naive UTC）。
    """
    fired_at: datetime
    """实际交付时刻（naive UTC）。
    """
    coalesced_count: int
    """合并计数：``(last_fired_at ?? created_at, fired_at]`` 区间内的
    理想触发次数（含本次）。正常调度路径下 ``>= 1``。
    """
    last_fired_at: datetime | None
    """上次成功交付时刻；``None`` 表示从未交付。
    """

@dataclass
class CronAction:
    """一次触发的动作描述——「到点做什么」（随任务落盘）。

    .. rubric:: 功能介绍

    ``kind="message"`` 表示给 Agent 发一条提示词；``kind="tool_call"``
    表示以给定参数调用一个工具。「怎么做」（入队、直发或其它兑现方式）
    由插件级执行器决定，不在本对象的语义内。动作是纯数据（kind 加
    参数），因此可以随 :class:`CronJob` 一起 JSON 化落盘、崩溃恢复后
    原样重建。

    .. rubric:: 使用示例

    .. code-block:: python

        CronAction(kind="message", prompt="请执行每日沉淀")
        CronAction(kind="tool_call", tool="check-email",
                   args={"folder": "INBOX"})

    .. rubric:: 行为要点

    - 构造即校验（``__post_init__``）：``kind="message"`` 要求
      ``prompt`` 非空；``kind="tool_call"`` 要求 ``tool`` 非空，违反抛
      ``ValueError``。
    - ``kind`` 为其它字符串（自定义 kind）时形状校验放行；能否被调度
      取决于插件是否注册了同名执行器——见
      :meth:`CronScheduler.schedule`。
    - 入库的动作一定通过形状校验；字段全部可 JSON 化。

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
    """``kind="tool_call"`` 的参数，原样传给
    :meth:`flowing.agent.Agent.tool_call`。
    """

    def __post_init__(self) -> None:
        """构造校验：``message`` 需非空 ``prompt``，``tool_call`` 需非空
        ``tool``；违反抛 ``ValueError``。自定义 kind 不在此校验。

        :raises ValueError: ``kind="message"`` 且 ``prompt`` 为空，或
            ``kind="tool_call"`` 且 ``tool`` 为空时。
        """
        if self.kind == "message" and not self.prompt:
            raise ValueError("kind='message' 要求 prompt 非空")
        if self.kind == "tool_call" and not self.tool:
            raise ValueError("kind='tool_call' 要求 tool 非空")
        # 自定义 kind 放行（归属检查在 CronScheduler.schedule）

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 可化纯 dict（落盘形态）。

        .. rubric:: 行为要点

        - 字段平铺为 ``{"kind", "prompt", "tool", "args"}`` 四键；
          ``args`` 拷贝一份，返回的 dict 与对象本身互不影响。
        - 空默认字段（``""`` / ``{}``）也照常写出——落盘结构自描述，
          读端不需要补默认值。

        .. seealso:: :meth:`from_dict`
        """
        return {"kind": self.kind, "prompt": self.prompt,
                "tool": self.tool, "args": dict(self.args)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronAction":
        """从落盘 dict 重建。

        .. rubric:: 行为要点

        - 构造即触发 ``__post_init__`` 形状校验——损坏的持久化数据在
          此 fail-fast（``ValueError``，不静默接受）。
        - 缺 ``prompt`` / ``tool`` / ``args`` 键时按默认值（``""`` /
          ``{}``）重建。

        .. seealso:: :meth:`to_dict`
        """
        return cls(kind=data["kind"], prompt=data.get("prompt", ""),
                   tool=data.get("tool", ""), args=dict(data.get("args", {})))

@dataclass
class CronJob:
    """一条定时任务的定义（持久化单元）。

    .. rubric:: 功能介绍

    描述「哪个 Agent、按什么 cron 表达式、以什么动作与来源触发」。
    是 ``cron_jobs`` 状态键（``Agent.state.register("cron_jobs", [])``
    声明、写透存储、恢复重放）的序列化单元——状态袋里恒存
    ``list[dict]`` 纯数据，对象本体只存在于调度器的内存任务表中。
    进出状态袋的唯一通道是 :meth:`to_dict` /
    :meth:`from_dict`。

    任务定义是纯数据（不含回调、不含定时器句柄）——定时器是运行期
    对象，可随时由「定义加当前时间」重建。崩溃恢复只是重新武装定义，
    不需要执行态迁移。

    .. rubric:: 行为要点

    - ``id`` 在 Runtime 内全局唯一；``node_id`` 指向任务所属 Agent。
    - ``cron`` 为合法五字段表达式（合法性在 :meth:`CronScheduler.schedule`
      时校验，入库的任务一定可解析）。
    - ``last_fired_at`` 只在成功交付时推进——休眠 / 宕机期它停住，
      错过次数由此自动累积（见模块 docstring 的「一律合并」规则）。
    - ``action.args`` 可为空 dict；``source`` 缺省由调度器填
      ``f"cron:{id}"``；``last_fired_at=None`` 表示从未交付（合并计数
      基数退化为 ``created_at``）。

    .. seealso:: :meth:`CronScheduler.schedule`、:class:`CronTrigger`、
        :class:`CronFireContext`
    """

    id: str
    """任务 ID。``schedule()`` 未显式指定时由调度器生成（UUID 字符串）；
    显式指定便于幂等重建与按名取消。
    """
    node_id: str
    """任务所属 Agent 的节点 ID。触发时据此在 ``runtime._nodes`` 定位
    Agent；定位不到表示休眠（门控拦截，任务保留——清理只能经
    ``unschedule`` / ``unschedule_all`` 显式发生）。
    """
    cron: str
    """五字段 cron 表达式（``分 时 日 月 周``，分精度）。
    """
    action: CronAction
    """触发时「做什么」（第 1 层）。可 JSON 化，随任务落盘与恢复；
    「怎么做」由插件级执行器决定。经 ``on_cron_trigger`` 可被改写的是
    :class:`CronTrigger` 的 ``action`` 拷贝，本字段始终是落盘原始定义。
    """
    source: str
    """EVENT 消息的 ``source`` 字段值；也是 ``on_cron_trigger`` 钩子的
    ``match_on`` 匹配字段（fnmatch）。缺省 ``f"cron:{id}"``——应用层
    应显式指定语义化来源（如 ``"daily_consolidation"``）以便过滤。
    """
    created_at: datetime
    """创建时间，naive UTC ``datetime``；也是从未交付任务的合并计数
    基数。必填（无默认值）——合并计数语义依赖一个真实时刻，由
    ``schedule()`` / ``from_dict()`` 显式给出。
    """
    recurring: bool = True
    """``False`` 表示一次性任务：第一次成功交付后自删（写透落盘）。
    到点未交付（休眠 / 被 shortcut 跳过）不算成功交付，不自删。
    """
    last_fired_at: datetime | None = None
    """上次成功交付时刻（naive UTC），随任务写透落盘。合并计数游标：
    ``coalesced_count`` 是 cron 表达式在 ``(last_fired_at ?? created_at,
    now]`` 内的理想触发次数。
    """

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 可化纯 dict——进出 ``cron_jobs`` 状态键的写通道。

        .. rubric:: 行为要点

        - 全部字段平铺；``action`` 委托 :meth:`CronAction.to_dict`；
          ``created_at`` / ``last_fired_at`` 转 ISO 8601 字符串
          （``None`` 保持 ``None``）。
        - 空默认字段照常写出（落盘结构自描述）。

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
        """从落盘 dict 重建——恢复重放的读通道。

        .. rubric:: 行为要点

        - ``action`` 委托 :meth:`CronAction.from_dict` （形状损坏 →
          ``ValueError`` fail-fast）；时间字段从 ISO 8601 解析回 naive
          UTC ``datetime``。
        - 缺 ``recurring`` / ``last_fired_at`` 键按默认值（``True`` /
          ``None``）重建，缺 ``source`` 按 ``f"cron:{id}"`` 回填——
          向前兼容旧版落盘数据。

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

    调度器在到点投递 EVENT 消息之前 dispatch 本对象，handler 签名统一
    为 ``(agent, trigger) -> trigger``。与核心钩子体系的 value 约定
    对齐：可改写（改 ``action`` 属同一语义的调整）、可短路（置
    ``shortcut=True`` 跳过本次触发）、``raise Intercepted`` 硬阻断
    （同样跳过本次并中止后续 handler）。

    触发拦截是机制，「什么该拦截」是应用层策略。置 shortcut 只是取消
    本次触发，不取消任务本身——下一次到点仍会触发；「到点改做别的
    事」请走插件级执行器注入（见 :class:`CronPlugin`）。

    .. rubric:: 使用示例

    .. code-block:: python

        @self.hooks.on_cron_trigger["daily_*"]
        def _(self, trigger: CronTrigger):
            if trigger.fire.coalesced_count > 5:
                trigger.shortcut = True   # 积压过多则跳过本次
            return trigger

    .. rubric:: 行为要点

    - dispatch 返回的（可能被改写的）trigger 决定本次执行的 ``action``
      内容；``shortcut=True`` 时不执行动作（不入队、不调用执行器）。
    - ``shortcut`` 初值 ``None``，以「非 ``None`` 即短路」门控：任一
      handler 置 ``True`` 即跳过本次触发，后续 handler 不再执行（链
      停止）。
    - handler ``raise Intercepted`` 硬阻断：同样跳过本次触发、不推进
      游标，语义同 shortcut。
    - 无 handler 注册时 dispatch 原样返回，正常执行。

    .. seealso:: :class:`CronJob`、:class:`CronAction`、:func:`use_cron`、
        :meth:`flowing.hooks.HookList.dispatch`
    """

    job: CronJob
    """触发来源任务（只读语义：handler 不应修改 ``job`` 字段本身）。
    """
    source: str
    """任务来源标识（初值取自 ``job.source``）——``on_cron_trigger``
    钩子点的 ``match_on="source"`` 过滤字段。handler 可改写以改变
    后续 pattern 匹配（与 value 可改写语义一致）。
    """
    action: CronAction
    """本次生效的动作（初值取自 ``job.action`` 的拷贝）。handler 可
    改写；执行器消费的是本字段，而非 ``job.action``。
    """
    fire: CronFireContext
    """本次交付的只读触发上下文（合并计数、理想 / 实际时刻）。handler
    可据 ``fire.coalesced_count`` 区分准时触发与合并交付；不应修改
    （事实不属于钩子可写面——可写的是 ``action`` 与 ``shortcut``）。
    """
    shortcut: bool | None = None
    """跳过标记（初值 ``None``）。任一 handler 置 ``True`` 即跳过本次
    触发（不执行任何执行器），后续 handler 不再执行（链停止）。语义
    仅限「取消」：替换执行逻辑请用 ``CronPlugin(executors=...)`` 注入。
    """
