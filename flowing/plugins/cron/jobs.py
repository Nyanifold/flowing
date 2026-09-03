"""``flowing.plugins.cron.jobs`` —— 任务运行时与模块 API。

本模块承载定时扩展的逐智能体运行时：croniter 解析与合并计数、
``{{current_time}}`` 机械替换、唯一补发模板、每 Agent 的定时器句柄容器，
以及到点/恢复补发共用的统一交付例程。公开的模块 API（
:func:`schedule` / :func:`unschedule` / :func:`jobs`）以 ``agent`` 为
首个参数，操作该 Agent 的状态总表（``cron_jobs`` 键）。

.. rubric:: 持久化模型（委托总表）

任务的唯一真相是 ``agent.state.cron_jobs``（``use_cron`` 登记、缺省即
写，此后键恒存在）。本模块**不留数据镜像**：每次变更 = 「读总表 →
改 → 二次赋值（写透）」；交付例程含 await（钩子 dispatch、消息入队），
其写回步骤在 await 后**重新读当前总表、只按 id 替换/移除自己那条**，
并发到点的多个任务互不覆盖游标。

.. rubric:: 时间与占位符

- 全模块唯一时间入口 :func:`_now` = ``datetime.now()``（系统本地 naive，
  不做时区归一化）；cron 表达式按本地墙钟解释。
- 消息文本占位符：``{{current_time}}``（默认格式）与
  ``{{current_time:<strftime>}}``；组装时对 content 先做一次机械正则
  替换，再决定裸发或套补发模板。非法格式在注册期（:func:`schedule`）
  以探针渲染报 ``ValueError``，不在触发期爆雷。

.. seealso:: :mod:`flowing.plugins.cron`（扩展整体契约）、
    :mod:`flowing.plugins.cron.models`（数据对象）、
    :mod:`flowing.plugins.cron.cron`（插件与 ``use_cron``）
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from croniter import croniter

from flowing.agent import Agent
from flowing.errors import Intercepted
from flowing.message import Message, MessageKind, MessagePriority, TextBlock

from .models import CronFireContext, CronJob

_logger = logging.getLogger(__name__)

MISSED_NOTICE = "Scheduled job {cron} missed {count} trigger(s) (last successful delivery: {last_fired_at}); delivering once now:\n{content}"
"""唯一补发模板（模块常量，无注入面）：错过多次（``coalesced_count >= 2``）
时渲染的完整消息，自含内容。

允许的 token（渲染器按固定顺序 ``str.replace``，不用 ``str.format``——
content 内可含任意花括号与 ``{{current_time}}`` 占位符，不受转义影响）：

- ``{count}`` —— 错过次数（十进制）
- ``{cron}`` —— 任务 cron 表达式原文
- ``{last_fired_at}`` —— 上次成功交付时刻（本地 ``%Y-%m-%d %H:%M``；
  从未交付渲染「从未交付」）
- ``{content}`` —— 本次推送内容（已做 ``{{current_time}}`` 替换）

模板不提单一应触发时刻：多次错过没有「某一个本应触发时刻」。
"""

_CURRENT_TIME_PATTERN = re.compile(r"\{\{ *current_time(?::([^}]*))? *\}\}")
"""``{{current_time}}`` 占位符正则：可选冒号后 strftime 格式串。"""

_DEFAULT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
"""``{{current_time}}`` 缺省格式（系统本地墙钟）。"""

_NOTICE_TIME_FORMAT = "%Y-%m-%d %H:%M"
"""补发模板 ``{last_fired_at}`` 的展示格式（系统本地墙钟）。"""

_STRFTIME_DIRECTIVES = frozenset(
    "aAwdbBmYHIpMSfzZjUWcxXGuV%")   # %a/%A/... 标准指令集 + %% 转义
"""``{{current_time}}`` 格式串允许的指令集（Python strftime 标准指令 +
``%%``）。白名单校验的原因：glibc 的 ``strftime`` 对未知指令（如
``%Q``）不报错、原样输出——只有白名单能可靠区分「合法格式」与
「笔误」。
"""

_COALESCE_GUARD = 100_000
"""合并计数逐点迭代防护上限：区间理想点超过该值时换解析式计数。"""


# ───────────────────────── 时间与解析（内部） ─────────────────────────

def _now() -> datetime:
    """当前时刻（系统本地 naive）——全模块唯一时间入口。

    定时链路的测试经替换本函数注入测试时钟（不做真实等待）。
    """
    return datetime.now()


def _next_ideal_fire(cron: str, after: datetime) -> datetime:
    """``after`` 之后 cron 表达式的下一个理想触发点（本地 naive）。

    五字段约束（分 时 日 月 周）：多/少字段在此 fail-fast；croniter 的
    非法表达式天然以 ``ValueError`` 暴露。
    """
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError(
            f"cron expression must have five fields (minute hour day month weekday): {cron!r}")
    return croniter(cron, after).get_next(datetime)


def _count_ideal_fires(
    cron: str, start: datetime, end: datetime
) -> tuple[int, datetime | None]:
    """``(start, end]`` 区间内 cron 理想触发次数与最后一个理想点。

    ``count == 0`` 时 ``last`` 为 ``None``；超过
    :data:`_COALESCE_GUARD` 时换解析式计数（同引擎展开结果，口径一致）。
    """
    if end <= start:
        return 0, None
    it = croniter(cron, start)
    count = 0
    last: datetime | None = None
    point = it.get_next(datetime)
    while point <= end:
        count += 1
        last = point
        if count > _COALESCE_GUARD:
            return _count_ideal_fires_analytical(cron, start, end)
        point = it.get_next(datetime)
    return count, last


def _count_ideal_fires_analytical(
    cron: str, start: datetime, end: datetime
) -> tuple[int, datetime | None]:
    """大区间合并计数的解析式实现（:func:`_count_ideal_fires` 的防护回退）。

    以 ``croniter.expand`` 展开集合按天迭代；``expand`` 产出含非整数项
    （L/W/# 等特殊语法）时回退为无防护逐点迭代（正确性优先）。
    """
    (minutes, hours, dom, months, dow), _ = croniter.expand(cron)
    fields = (minutes, hours, dom, months, dow)
    if any(any(not isinstance(v, int) and v != "*" for v in f) for f in fields):
        it = croniter(cron, start)
        count = 0
        last = None
        point = it.get_next(datetime)
        while point <= end:
            count += 1
            last = point
            point = it.get_next(datetime)
        return count, last
    all_days = list(range(1, 32))
    minutes = list(range(60)) if minutes == ["*"] else minutes
    hours = list(range(24)) if hours == ["*"] else hours
    dom = all_days if dom == ["*"] else dom
    months = list(range(1, 13)) if months == ["*"] else months
    dow = list(range(7)) if dow == ["*"] else dow
    dom_restricted = dom != all_days
    dow_restricted = dow != list(range(7))
    per_day = len(hours) * len(minutes)

    count = 0
    last: datetime | None = None
    day = start.date()
    end_date = end.date()
    while day <= end_date:
        cron_dow = (day.weekday() + 1) % 7   # cron 周字段 0=周日
        dom_match = day.day in dom
        dow_match = cron_dow in dow
        if dom_restricted and dow_restricted:
            day_match = dom_match or dow_match   # Vixie cron：双受限取或
        elif dom_restricted:
            day_match = dom_match
        elif dow_restricted:
            day_match = dow_match
        else:
            day_match = True
        if day_match and day.month in months:
            if start.date() < day < end_date:
                count += per_day
                last = datetime(day.year, day.month, day.day,
                                max(hours), max(minutes))
            else:
                for h in hours:
                    for m in minutes:
                        point = datetime(day.year, day.month, day.day, h, m)
                        if start < point <= end:
                            count += 1
                            last = point
        day += timedelta(days=1)
    return count, last


# ───────────────────────── 占位符替换（内部） ─────────────────────────

def substitute_current_time(text: str, now: datetime) -> str:
    """把文本内全部 ``{{current_time}}`` 占位符机械替换为 ``now`` 的格式化值。

    纯正则替换、无 Parsable/Jinja。格式串非法时 ``strftime`` 抛
    ``ValueError``（注册期探针已挡第一道，触发期不再出现）。
    """
    def _sub(match: re.Match) -> str:
        fmt = match.group(1)
        return now.strftime(fmt) if fmt else now.strftime(_DEFAULT_TIME_FORMAT)
    return _CURRENT_TIME_PATTERN.sub(_sub, text)


def validate_placeholders(content: str) -> None:
    """注册期探针：content 内全部占位符格式做指令集白名单校验。

    格式含白名单外指令（或孤立 ``%``）→ ``ValueError``（配置错误在注册
    边界暴露；``strftime`` 本身对未知指令不报错，故须显式校验）。
    """
    for match in _CURRENT_TIME_PATTERN.finditer(content):
        fmt = match.group(1)
        if not fmt:
            continue
        i = 0
        while i < len(fmt):
            if fmt[i] != "%":
                i += 1
                continue
            if (i + 1 >= len(fmt)
                    or fmt[i + 1] not in _STRFTIME_DIRECTIVES):
                raise ValueError(
                    f"{{{{current_time}}}} placeholder format is invalid: {fmt!r}")
            i += 2


def _render_notice(cron: str, count: int,
                   last_fired_at: datetime | None, content: str) -> str:
    """渲染唯一补发模板（token 顺序替换：count → cron → last_fired_at → content）。"""
    last_txt = (last_fired_at.strftime(_NOTICE_TIME_FORMAT)
                if last_fired_at is not None else "never delivered")
    return (MISSED_NOTICE
            .replace("{count}", str(count))
            .replace("{cron}", cron)
            .replace("{last_fired_at}", last_txt)
            .replace("{content}", content))


# ───────────────────────── 逐智能体运行时（内部） ─────────────────────────

class _CronRuntime:
    """单 Agent 的任务运行时：定时器句柄容器 + 交付例程（内部 API）。

    由 ``use_cron`` 建立并挂在 ``agent._cron`` 槽位上；本对象**不持有
    任务数据镜像**——任务数据一律读 ``agent.state.cron_jobs`` 总表
    （委托总表，见模块 docstring）。
    """

    def __init__(self, agent: Agent) -> None:
        self._agent = agent
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._fire_tasks: set[asyncio.Task] = set()

    # -- 总表访问 ------------------------------------------------------

    def _find(self, job_id: str) -> dict[str, Any] | None:
        """按 id 读总表当前记录（每次现读，不在内部缓存）。"""
        for data in self._agent.state.cron_jobs:
            if data.get("id") == job_id:
                return data
        return None

    # -- 注册 API 实现 ------------------------------------------------

    def schedule(self, cron: str, content: str, *, job_id: str | None = None,
                 source: str = "", recurring: bool = True) -> str:
        """注册一条任务：校验 → 追加总表（二次赋值写透）→ 武装定时器。

        全部校验在注册边界对调用方报 ``ValueError``（content 非空、
        cron 五字段可解析、占位符格式合法、job_id 不冲突）。「读 → 改 →
        赋值」区间无 await，原子完成。
        """
        agent = self._agent
        if not content:
            raise ValueError("content must not be empty")
        _next_ideal_fire(cron, _now())      # 五字段 + 可解析校验
        validate_placeholders(content)
        if job_id is None:
            job_id = str(uuid4())
        if any(d.get("id") == job_id for d in agent.state.cron_jobs):
            raise ValueError(f"job_id conflict: {job_id}")
        record = CronJob(
            id=job_id, cron=cron, content=content, source=source or "",
            recurring=bool(recurring), created_at=_now(),
        ).to_dict()
        agent.state.cron_jobs = [*agent.state.cron_jobs, record]
        self._arm(job_id)
        return job_id

    def unschedule(self, job_id: str) -> bool:
        """移除任务：取消定时器 → 从总表剔除（写透）；不存在返回 False。"""
        self._disarm(job_id)
        jobs_data = self._agent.state.cron_jobs
        existed = any(d.get("id") == job_id for d in jobs_data)
        if existed:
            self._agent.state.cron_jobs = [
                d for d in jobs_data if d.get("id") != job_id]
        return existed

    def jobs(self) -> list[CronJob]:
        """总表只读快照（重建为对象，改动不影响总表）。"""
        return [CronJob.from_dict(d) for d in self._agent.state.cron_jobs]

    # -- 定时器 --------------------------------------------------------

    def _arm(self, job_id: str) -> None:
        """按下一理想点武装一次性定时器（写序约定的最后一步）。"""
        self._disarm(job_id)
        data = self._find(job_id)
        if data is None:
            return
        now = _now()
        delay = max(0.0, (_next_ideal_fire(data["cron"], now) - now)
                    .total_seconds())
        loop = asyncio.get_running_loop()
        self._timers[job_id] = loop.call_later(delay, self._on_timer, job_id)

    def _disarm(self, job_id: str) -> None:
        """取消某任务的定时器（无该定时器为空操作）。"""
        handle = self._timers.pop(job_id, None)
        if handle is not None:
            handle.cancel()

    def _on_timer(self, job_id: str) -> None:
        """定时器回调：fire-and-forget 触发后按下一理想点重新武装。

        重新武装与 ``_fire`` 执行解耦：一次性任务在 ``_fire`` 成功交付后
        自移除（连带 ``_disarm`` 本次重武装的句柄）；跳过/异常不影响
        下一次到点的武装。
        """
        self._timers.pop(job_id, None)
        if self._find(job_id) is None:
            return   # 任务已被移除（与 unschedule 竞速）：不重武装
        task = asyncio.create_task(self._fire(job_id))
        self._fire_tasks.add(task)
        task.add_done_callback(self._fire_tasks.discard)
        self._arm(job_id)

    def cancel_all(self) -> None:
        """取消本 Agent 全部定时器（``before_destroy`` 调用；不改总表）。"""
        for job_id in list(self._timers):
            self._disarm(job_id)

    # -- 交付 ----------------------------------------------------------

    async def _fire(self, job_id: str) -> None:
        """统一交付例程：到点触发与恢复补发共用。

        时序：读总表本记录 → 合并计数 → dispatch ``on_cron_trigger`` →
        组装文本 → 入队 EVENT → 写回（重读总表，只动本记录）→ 一次性
        自移除。skip（``shortcut``/``Intercepted``）不推进游标。
        """
        agent = self._agent
        data = self._find(job_id)
        if data is None:
            return
        now = _now()
        cursor = (datetime.fromisoformat(data["last_fired_at"])
                  if data.get("last_fired_at") else
                  datetime.fromisoformat(data["created_at"]))
        count, last_ideal = _count_ideal_fires(data["cron"], cursor, now)
        if count == 0:
            return   # 未到点（恢复补发路径的空转分支）
        ctx = CronFireContext(
            id=job_id,
            source=data.get("source", ""),
            cron=data["cron"],
            content=data["content"],
            recurring=data.get("recurring", True),
            scheduled_at=last_ideal if last_ideal is not None else now,
            fired_at=now,
            coalesced_count=count,
            last_fired_at=(cursor if data.get("last_fired_at") else None),
        )
        try:
            await agent.hooks.on_cron_trigger.dispatch(agent, ctx)
        except Intercepted:
            return   # 硬阻断：跳过本次，游标不推进
        except Exception:
            _logger.exception("cron job %s: on_cron_trigger dispatch failed; skipping this fire",
                              job_id)
            return
        if ctx.shortcut:
            return   # 跳过本次推送，游标不推进（miss 继续累积）
        content_final = substitute_current_time(ctx.content, now)
        if count >= 2:
            text = _render_notice(ctx.cron, count, ctx.last_fired_at,
                                  content_final)
        else:
            text = content_final
        try:
            await agent.enqueue_message(Message(
                kind=MessageKind.EVENT,
                source=(ctx.source or "cron"),
                content=[TextBlock(text=text)],
                priority=MessagePriority.STEER,
            ))
        except Exception:
            # 入队异常不逃逸进事件循环（fire-and-forget 无人 await）：
            # 游标不推进，任务保留，miss 累积到下一次交付
            _logger.exception("cron job %s: message enqueue failed; skipping this fire", job_id)
            return
        # 写回：重读当前总表，只替换/移除本记录（防并发交错覆盖）
        new_jobs = []
        for d in self._agent.state.cron_jobs:
            if d.get("id") != job_id:
                new_jobs.append(d)
            elif not ctx.recurring:
                pass   # 一次性任务：成功交付即自移除
            else:
                new_jobs.append({**d, "last_fired_at": now.isoformat()})
        self._agent.state.cron_jobs = new_jobs
        if not ctx.recurring:
            self._disarm(job_id)   # 取消 _on_timer 已重武装的句柄

    async def rebuild_after_recover(self) -> None:
        """恢复重建：补发过期任务 → 为剩余任务武装下一理想点。

        由 ``use_cron`` 挂的 ``after_recover`` handler 调用：先逐条走
        交付例程（过期即合并补发，一次性已交付者自移除），再对总表剩余
        任务全部武装（一次性未到期任务同样需要武装）。
        """
        for data in list(self._agent.state.cron_jobs):
            await self._fire(data["id"])
        for data in list(self._agent.state.cron_jobs):
            self._arm(data["id"])


# ───────────────────────── 模块 API ─────────────────────────

def _ensure(agent: Agent) -> _CronRuntime:
    """取 Agent 的任务运行时；未 ``use_cron`` → ``ValueError``。"""
    runtime = getattr(agent, "_cron", None)
    if not isinstance(runtime, _CronRuntime):
        raise ValueError("agent has not called use_cron (no cron runtime attached)")
    return runtime


def schedule(agent: Agent, cron: str, content: str, *,
             job_id: str | None = None, source: str = "",
             recurring: bool = True) -> str:
    """为 Agent 注册一条定时任务，返回 ``job_id``（详见
    :meth:`_CronRuntime.schedule`——校验与落盘语义都在该处）。

    :raises ValueError: agent 未 ``use_cron``、content 为空、cron 非法、
        占位符格式非法或 job_id 冲突时。
    """
    return _ensure(agent).schedule(
        cron, content, job_id=job_id, source=source, recurring=recurring)


def unschedule(agent: Agent, job_id: str) -> bool:
    """取消 Agent 的一条任务（不存在返回 ``False``）。

    :raises ValueError: agent 未 ``use_cron`` 时。
    """
    return _ensure(agent).unschedule(job_id)


def jobs(agent: Agent) -> list[CronJob]:
    """Agent 的任务只读快照（``list[CronJob]``）。

    :raises ValueError: agent 未 ``use_cron`` 时。
    """
    return _ensure(agent).jobs()
