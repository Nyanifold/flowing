"""阶段 4 comm 批次测试的公共助手（非 conftest——避免顶级模块名遮蔽，
见阶段 3 易踩坑笔记）。

提供：phase2 conftest 的 importlib 载入（HarnessRuntime / make_runtime /
FakeProvider 脚本回放助手）、``CommAgent``（setup 中 ``use_comm`` 的测试
Agent）、``make_comm_harness``（装好 CommPlugin 的 HarnessRuntime 工厂）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

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
    runtime.register_agent_type("comm-agent", CommAgent)
    bus = runtime.inject(communication_key)
    return runtime, bus
