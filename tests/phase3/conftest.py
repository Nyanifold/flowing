"""阶段 3 测试的公共 harness：复用阶段 2 的 ``HarnessRuntime`` 迷你管线。

阶段 3 的部分测试（subagents catalog / ``_assemble_context`` 集成面）需要
**真 Agent** 而非 ``tests/fakes.py`` 的 duck-typed 替身；阶段 2 的
``HarnessRuntime`` + ``create_agent`` 迷你管线正是这层契约的既有实现，
经 importlib 按路径载入复用（``phase2/`` 不是包，不能直接 import）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PHASE2_CONFTEST = Path(__file__).parent.parent / "phase2" / "conftest.py"
_spec = importlib.util.spec_from_file_location("phase2_conftest", _PHASE2_CONFTEST)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

HarnessRuntime = _mod.HarnessRuntime
SimpleAgent = _mod.SimpleAgent
text_response = _mod.text_response
script_provider = _mod.script_provider


@pytest.fixture
async def runtime(tmp_path):
    """HarnessRuntime 实例（收尾销毁全部存活 Agent，防跨测试泄漏）。"""
    rt = HarnessRuntime(tmp_path)
    rt.register_agent_type("test-agent", SimpleAgent)
    yield rt
    for node_id, node in list(rt._nodes.items()):
        if node is rt:
            continue
        try:
            await node.destroy()
        except Exception:
            pass
