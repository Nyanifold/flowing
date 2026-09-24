"""``flowing.plugins.cron.models`` —— 定时扩展的数据对象。

本模块定义两个公开数据对象：:class:`CronJob`（一条定时任务，逐智能体
携带的持久化单元）与 :class:`CronFireContext`（``on_cron_trigger``
钩子点的 value——一次即将交付的触发视图）。

.. seealso:: :mod:`flowing.plugins.cron`（扩展的整体契约）、
    :mod:`flowing.plugins.cron.jobs`（任务运行时与模块 API）、
    :mod:`flowing.plugins.cron.cron`（插件与 ``use_cron``）
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class CronJob:
    """一条定时任务——“cron 表达式 + 推送内容”的持久化单元。

    .. rubric:: 功能介绍

    描述“什么时候、给 Agent 推一条什么消息”：``cron`` 是触发节奏，
    ``content`` 是到点推送的消息文本（可含 ``{{current_time}}``
    占位符），``source`` 是供 ``on_cron_trigger`` 钩子 pattern 分组过滤
    的语义标签。任务由 Agent 逐智能体携带，以 ``cron_jobs`` 状态键
    （``list[dict]``）写透落盘；对象本体只在读侧按需重建，运行期不留
    数据镜像。

    .. rubric:: 行为要点

    - 纯数据容器：无构造校验、无 ``__post_init__``——不变式（content
      非空、cron 可解析、job_id 不冲突、占位符格式合法）由注册 API
      :func:`flowing.plugins.cron.schedule` 在注册边界维护。
    - ``source`` 不进消息文本，只服务钩子过滤与 EVENT 消息来源标识；
      空串表示“未设标签”（对非 ``"*"`` 的钩子 pattern 不命中）。
    - ``recurring=False`` 表示一次性任务：第一次成功交付后自移除；
      到点未交付（被跳过）不算成功交付，不移除。
    - 时间字段为系统本地 naive ``datetime``（不做时区归一化）；
      ``last_fired_at=None`` 表示从未交付（合并计数基数退化为
      ``created_at``）。

    .. seealso:: :class:`CronFireContext`、:mod:`flowing.plugins.cron.jobs`
    """

    id: str
    """任务 ID（该 Agent 作用域内唯一；注册时缺省生成 8 位随机 hex）。
    """
    cron: str
    """五字段 cron 表达式（``分 时 日 月 周``，分精度），按系统本地
    时刻解释。
    """
    content: str
    """到点推送的消息文本；可含 ``{{current_time}}`` 占位符。
    """
    created_at: datetime
    """创建时间（系统本地 naive）；从未交付任务的合并计数基数。
    """
    source: str = ""
    """语义标签：``on_cron_trigger`` 钩子的 ``match_on="source"`` 过滤
    字段（fnmatch），也是 EVENT 消息来源（空串时消息 source 落
    ``"cron"``）。不进消息文本。
    """
    recurring: bool = True
    """``False`` = 一次性任务：第一次成功交付后自移除（写透落盘）。
    """
    last_fired_at: datetime | None = None
    """上次成功交付时刻（系统本地 naive）。合并计数游标：只在成功交付
    时推进；``None`` 表示从未交付。
    """

    def to_dict(self) -> dict:
        """序列化为 JSON 可化纯 dict——进出 ``cron_jobs`` 状态键的写通道。

        字段平铺；时间为 ISO 8601 字符串（``None`` 保持 ``None``）；
        ``source`` 恒写出（空串也写，落盘结构自描述）。
        """
        return {
            "id": self.id,
            "cron": self.cron,
            "content": self.content,
            "source": self.source,
            "recurring": self.recurring,
            "created_at": self.created_at.isoformat(),
            "last_fired_at": (self.last_fired_at.isoformat()
                              if self.last_fired_at else None),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CronJob":
        """从落盘 dict 重建（纯字段映射，不做领域校验——盘即数据源）。"""
        return cls(
            id=data["id"],
            cron=data["cron"],
            content=data["content"],
            source=data.get("source", ""),
            recurring=data.get("recurring", True),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_fired_at=(datetime.fromisoformat(data["last_fired_at"])
                           if data.get("last_fired_at") else None),
        )


@dataclass
class CronFireContext:
    """``on_cron_trigger`` 钩子点的 value——一次即将交付的触发视图。

    .. rubric:: 功能介绍

    任务到点（或恢复补发）时，交付例程先构造本对象并
    ``agent.hooks.on_cron_trigger.dispatch(agent, ctx)``，随后才组装并
    入队消息。handler 可改写 ``ctx.content``（仅本次生效，不落盘）或置
    ``ctx.shortcut = True`` 跳过本次推送；``raise Intercepted`` 硬阻断
    语义同 shortcut（游标不推进，miss 继续累积）。

    .. rubric:: 行为要点

    - ``match_on="source"``：钩子按 value 顶层 ``source`` 字段做
      fnmatch 过滤（pattern 注册形态）。
    - ``coalesced_count`` 为 ``(last_fired_at ?? created_at, fired_at]``
      区间内理想触发次数：``== 1`` 为到点裸发；``>= 2`` 为错过多次的
      合并补发（文案走唯一补发模板）。
    - ``content`` 初值为任务原文（未做 ``{{current_time}}`` 替换）；
      替换发生在组装阶段，handler 改写后的文本同样会被替换。

    .. seealso:: :class:`CronJob`、:func:`flowing.plugins.cron.use_cron`
    """

    id: str
    """任务 ID（= ``job.id``）。
    """
    source: str
    """任务语义标签（= ``job.source``）——钩子 pattern 过滤字段。
    """
    cron: str
    """任务 cron 表达式（只读）。
    """
    content: str
    """本次将推送的文本（初值 ``job.content`` 原文；handler 可改写，
    仅本次生效，不落盘）。
    """
    recurring: bool
    """任务是否周期（只读）。
    """
    scheduled_at: datetime
    """区间内最后一个理想触发点（本次交付对应的理想时刻）。
    """
    fired_at: datetime
    """实际交付时刻（系统本地 naive）。
    """
    coalesced_count: int
    """区间内理想触发次数（含本次），``>= 1``。
    """
    last_fired_at: datetime | None
    """上次成功交付时刻（交付前游标）；``None`` = 从未交付。
    """
    shortcut: bool | None = None
    """跳过标记（初值 ``None``）。任一 handler 置 ``True`` 即跳过本次
    推送（不推进游标，miss 继续累积）。
    """
