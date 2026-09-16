"""cron 插件测试的公共助手。

提供：``harness`` 助手转发（HarnessRuntime / SimpleAgent / make_runtime /
add_fake_provider / script_provider / text_response）、``CronAgent``（setup
中 ``use_cron`` + on_enqueue 观测槽 + 声明式注册）、``FakeClock``（替换
``flowing.plugins.cron.jobs._now`` 的测试时钟——``_now`` 是任务运行时唯一
时间读取通道）、``make_cron_harness``（可选装好 CronPlugin 的
HarnessRuntime 工厂）。

本模块为普通模块而非 conftest：测试文件直接 ``import cron_support``，
避免顶级模块名 ``conftest`` 遮蔽。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from harness import (
    add_fake_provider,
    HarnessRuntime,
    make_runtime,
    script_provider,
    SimpleAgent,
    text_response,
)

# 注意：包属性 ``flowing.plugins.cron.jobs`` 被同名函数遮蔽，取子模块须经
# importlib 通道（sys.modules）
import importlib as _il

JOBS = _il.import_module("flowing.plugins.cron.jobs")

from flowing.message import Message  # noqa: E402
from flowing.plugins.cron import (  # noqa: E402
    CronPlugin,
    schedule,
    unschedule,
    use_cron,
)

FIXTURES_PERSISTENCE = Path(__file__).parent.parent.parent / "fixtures" / "persistence"


class CronAgent(SimpleAgent):
    """setup 中启用 ``use_cron`` 的测试 Agent。

    - 类属性 ``captured``：``on_enqueue`` 观测槽（recover 换新实例后
      setup 重跑、handler 重挂，槽在类上故跨实例连续捕获）。
    - 类属性 ``declarative_jobs``：非空时在 setup 里注册 ``after_create``
      handler 做声明式任务注册（after_create 只在 create 管线触发，
      recover 不会重注册）；spec 去掉 ``cron`` 键后余项透传给
      ``schedule(agent, cron, **spec)``。
    """

    captured: ClassVar[list[Message]] = []
    declarative_jobs: ClassVar[list[dict[str, Any]] | None] = None

    async def setup(self, **kwargs: Any) -> None:
        await super().setup(**kwargs)
        use_cron(self)
        self.hooks.on_enqueue(
            lambda a, msg: (type(self).captured.append(msg), msg)[1], by="test")
        specs = type(self).declarative_jobs
        if specs:

            async def _register(agent, _value=None):
                for spec in specs:
                    spec = dict(spec)
                    cron = spec.pop("cron")
                    schedule(agent, cron, **spec)

            self.hooks.after_create(_register, by="test")


class FakeClock:
    """替换 ``jobs._now`` 的测试时钟。

    基准时刻 + 真实流逝自动推进；``set()`` 手动跳变（直接驱动
    ``agent._cron._fire`` 的确定性路径用）。
    """

    def __init__(self, t: datetime) -> None:
        self.t = t
        self._real0 = time.monotonic()
        JOBS._now = self.now   # 模块级 _now 是唯一时间读取通道

    def now(self) -> datetime:
        return self.t + timedelta(seconds=time.monotonic() - self._real0)

    def set(self, t: datetime) -> None:
        """手动跳变到指定时刻（真实流逝从跳变点重新起算）。"""
        self.t = t
        self._real0 = time.monotonic()


def local_at(year: int, month: int, day: int,
            hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """系统本地 naive 时刻构造（与运行时 ``_now`` 口径一致）。"""
    return datetime(year, month, day, hour, minute, second)


def make_cron_harness(
    tmp_path: Path,
    *,
    install_plugin: bool = True,
    agent_cls: type = CronAgent,
) -> HarnessRuntime:
    """HarnessRuntime（可选已装 CronPlugin）；返回 ``runtime``。

    v2 无中央调度器：任务经模块 API（``schedule`` 等）注册，运行器在
    ``agent._cron`` 槽位上。
    """
    runtime = HarnessRuntime(tmp_path)
    if install_plugin:
        CronPlugin().install(runtime)
    runtime.register_agent_type(agent_cls, name="cron-agent")
    return runtime
