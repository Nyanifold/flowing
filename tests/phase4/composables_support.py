"""阶段 4 composables 批次测试的公共助手（非 conftest——避免顶级模块名
遮蔽，见阶段 3 易踩坑笔记）。

提供：phase2 conftest 的 importlib 载入（HarnessRuntime / make_runtime /
FakeProvider 脚本回放助手）、``RetryAgent`` / ``CompactAgent`` /
``ReminderAgent``（setup 中调用对应 Composable，类属性透传参数）、
``make_composables_harness``（注册 test-agent 的 HarnessRuntime +
FakeProvider 工厂）、``sleep_spy``（替换 ``asyncio.sleep`` 的记录桩——
延迟公式断言不经真实等待）。
"""

from __future__ import annotations

import asyncio
import importlib.util
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
tool_call_response = _mod.tool_call_response

from flowing.composables import (
    use_compact,
    use_prompt_until,
    use_retry,
    use_system_reminder,
)
from flowing.providers import FakeProvider


class RetryAgent(SimpleAgent):
    """setup 中 ``use_retry``（类属性 ``retry_kwargs`` 透传参数）。"""

    retry_kwargs: ClassVar[dict[str, Any]] = {}

    async def setup(self, **kwargs: Any) -> None:
        await super().setup(**kwargs)
        use_retry(self, **type(self).retry_kwargs)


class CompactAgent(SimpleAgent):
    """setup 中 ``use_compact``（类属性 ``compact_kwargs`` 透传参数）。"""

    compact_kwargs: ClassVar[dict[str, Any]] = {}

    async def setup(self, **kwargs: Any) -> None:
        await super().setup(**kwargs)
        use_compact(self, **type(self).compact_kwargs)


class ReminderAgent(SimpleAgent):
    """setup 中 ``use_system_reminder``（类属性 ``reminder_args`` /
    ``reminder_kwargs`` 透传）。"""

    reminder_args: ClassVar[list] = []
    reminder_kwargs: ClassVar[dict[str, Any]] = {}

    async def setup(self, **kwargs: Any) -> None:
        await super().setup(**kwargs)
        use_system_reminder(self, *type(self).reminder_args,
                            **type(self).reminder_kwargs)


class PromptUntilAgent(SimpleAgent):
    """setup 中 ``use_prompt_until``（类属性 ``prompt_until_args`` 透传）。"""

    prompt_until_args: ClassVar[list] = []

    async def setup(self, **kwargs: Any) -> None:
        await super().setup(**kwargs)
        use_prompt_until(self, *type(self).prompt_until_args)


def make_composables_harness(
    tmp_path: Path,
    *,
    agent_cls: type = RetryAgent,
) -> tuple[HarnessRuntime, FakeProvider]:
    """HarnessRuntime + FakeProvider + 注册 ``"test-agent"`` 类型。

    返回 ``(runtime, provider)``；provider 经 ``script_provider`` 绑定
    脚本回放。
    """
    runtime = HarnessRuntime(tmp_path)
    runtime.register_agent_type("test-agent", agent_cls)
    provider = add_fake_provider(runtime)
    return runtime, provider


class sleep_spy:
    """``asyncio.sleep`` 的记录桩（上下文管理器形态）。

    进入时替换全局 ``asyncio.sleep`` 为「记录时长、立即返回」的协程
    函数；退出还原。延迟公式断言全部经 ``recorded`` 读取，不做真实
    等待（composables 测试基座约定）。
    """

    def __init__(self) -> None:
        self.recorded: list[float] = []
        self._original = None

    async def _fake_sleep(self, delay: float) -> None:
        self.recorded.append(delay)

    def __enter__(self) -> "sleep_spy":
        self._original = asyncio.sleep
        asyncio.sleep = self._fake_sleep
        return self

    def __exit__(self, *exc: Any) -> None:
        asyncio.sleep = self._original
