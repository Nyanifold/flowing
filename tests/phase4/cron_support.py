"""阶段 4 cron 批次测试的公共助手（非 conftest——避免顶级模块名遮蔽，
见阶段 3 易踩坑笔记）。

提供：phase2 conftest 的 importlib 载入（HarnessRuntime / make_runtime /
FakeProvider 脚本回放助手）、``CronAgent``（setup 中 ``use_cron`` +
after_enqueue 观测槽 + 声明式注册）、``FakeClock``（替换
``CronScheduler._now`` 的测试时钟——``_now`` 是调度器唯一时间读取通道）、
``make_cron_harness``（装好 CronPlugin 的 HarnessRuntime 工厂）。
"""

from __future__ import annotations

import importlib.util
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, ClassVar

_PHASE2_CONFTEST = Path(__file__).parent.parent / "phase2" / "conftest.py"
_spec = importlib.util.spec_from_file_location("phase2_conftest", _PHASE2_CONFTEST)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

HarnessRuntime = _mod.HarnessRuntime
SimpleAgent = _mod.SimpleAgent
make_runtime = _mod.make_runtime
add_fake_provider = _mod.add_fake_provider
script_provider = _mod.script_provider
text_response = _mod.text_response

from flowing.message import Message
from flowing.plugins.cron import CronPlugin, cron_scheduler_key, use_cron
from flowing.plugins.cron.scheduler import CronScheduler

FIXTURES_PERSISTENCE = Path(__file__).parent.parent / "fixtures" / "persistence"


class CronAgent(SimpleAgent):
    """setup 中启用 ``use_cron`` 的测试 Agent。

    - 类属性 ``captured``：``after_enqueue`` 观测槽（recover 换新实例后
      setup 重跑、handler 重挂，槽在类上故跨实例连续捕获）。
    - 类属性 ``declarative_jobs``：非空时在 setup 里注册
      ``after_create`` handler 做声明式任务注册（after_create 只在
      create 管线触发，recover 不会重注册）。
    """

    captured: ClassVar[list[Message]] = []
    declarative_jobs: ClassVar[list[dict[str, Any]] | None] = None

    async def setup(self, **kwargs: Any) -> None:
        await super().setup(**kwargs)
        use_cron(self)
        self.hooks.after_enqueue(
            lambda a, m: (type(self).captured.append(m), m)[1], by="test")
        jobs = type(self).declarative_jobs
        if jobs:

            async def _register(agent, _value=None):
                scheduler = agent.inject(cron_scheduler_key)
                for spec in jobs:
                    spec = dict(spec)
                    cron = spec.pop("cron")
                    scheduler.schedule(agent.node_id, cron, **spec)

            self.hooks.after_create(_register, by="test")


class FakeClock:
    """替换 ``scheduler._now`` 的测试时钟。

    基准时刻 + 真实流逝自动推进（真实 ``call_later`` 武装路径用——计算
    出的延迟以真实秒计）；``set()`` 手动跳变（直接驱动 ``_fire`` /
    ``_sweep`` 的确定性路径用）。
    """

    def __init__(self, scheduler: CronScheduler, t: datetime) -> None:
        self.t = t
        self._real0 = time.monotonic()
        scheduler._now = self.now  # 实例属性遮蔽方法（_now 是唯一时间读取通道）

    def now(self) -> datetime:
        return self.t + timedelta(seconds=time.monotonic() - self._real0)

    def set(self, t: datetime) -> None:
        """手动跳变到指定时刻（真实流逝从跳变点重新起算）。"""
        self.t = t
        self._real0 = time.monotonic()


def utcnow() -> datetime:
    """naive UTC 当前时刻（与框架时间约定一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def next_minute(after: datetime | None = None) -> datetime:
    """after（缺省真实 now）之后的下一个分钟边界（理想触发点）。"""
    base = after or utcnow()
    return base.replace(second=0, microsecond=0) + timedelta(minutes=1)


def make_cron_harness(
    tmp_path: Path,
    *,
    executors: dict | None = None,
    templates: dict | None = None,
    agent_cls: type = CronAgent,
) -> tuple[Any, CronScheduler]:
    """HarnessRuntime + 已安装 CronPlugin；返回 ``(runtime, scheduler)``。"""
    runtime = HarnessRuntime(tmp_path)
    CronPlugin(executors=executors, templates=templates).install(runtime)
    runtime.register_agent_type("cron-agent", agent_cls)
    scheduler = runtime.inject(cron_scheduler_key)
    return runtime, scheduler
