"""comm 插件测试的公共助手。

提供：``harness`` 助手转发（HarnessRuntime / make_runtime / FakeProvider
脚本回放）、``CommAgent``（setup 中 ``use_comm`` 的测试 Agent）、
``make_comm_harness``（装好 CommPlugin 的 HarnessRuntime 工厂）。

本模块为普通模块而非 conftest：测试文件直接 ``import comm_support``，
避免顶级模块名 ``conftest`` 遮蔽。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness import (
    add_fake_provider,
    HarnessRuntime,
    make_runtime,
    script_provider,
    SimpleAgent,
    text_response,
    tool_call_response,
)

from flowing.plugins.comm import CommPlugin, communication_key, use_comm


class CommAgent(SimpleAgent):
    """setup 中启用 ``use_comm(self, name=comm_name)`` 的测试 Agent。

    ``comm_name`` 经 setup kwargs 传入（缺省 None → 端点回退 node_id）。
    """

    async def setup(self, comm_name: str | None = None, **kwargs: Any) -> None:
        await super().setup(**kwargs)
        use_comm(self, name=comm_name)


def make_comm_harness(tmp_path: Path) -> tuple[Any, Any]:
    """HarnessRuntime + 已安装 CommPlugin；返回 ``(runtime, bus)``。"""
    runtime = HarnessRuntime(tmp_path)
    CommPlugin().install(runtime)
    runtime.register_agent_type(CommAgent, name="comm-agent")
    bus = runtime.inject(communication_key)
    return runtime, bus
