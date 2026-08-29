"""阶段 4 logging 批次测试的公共助手（非 conftest——避免顶级模块名
遮蔽，见阶段 3 易踩坑笔记）。

提供：phase2 conftest 的 importlib 载入（make_runtime / HarnessRuntime /
FakeProvider 脚本回放助手）、``LogAgent``（setup 中 ``use_logging`` 的
测试 Agent）、``make_logging_runtime``（真 Runtime + 已安装
LoggingPlugin 的工厂——install 依赖 ``runtime.get_plugin`` 探测，
HarnessRuntime/FakeRuntime 无此面，故本批次统一走真 Runtime）、
``read_log``（logging.jsonl 读取）。
"""

from __future__ import annotations

import importlib.util
import json
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

from flowing._unstable.logging import (
    LoggingPlugin,
    logging_plugin_key,
    use_logging,
)
from flowing.providers import FakeProvider


class LogAgent(SimpleAgent):
    """setup 中启用 ``use_logging(self)`` 的测试 Agent。"""

    async def setup(self, **kwargs: Any) -> None:
        await super().setup(**kwargs)
        use_logging(self)


def make_logging_runtime(
    project_root: Path,
    *,
    level: str = "INFO",
    max_value_repr: int = 500,
    pre_plugins: tuple = (),
    agent_cls: type = LogAgent,
) -> tuple[Any, FakeProvider]:
    """真 Runtime + 已安装 LoggingPlugin；返回 ``(runtime, provider)``。

    ``pre_plugins`` 先于 LoggingPlugin 安装（探测在 logging 的 install
    时一次性完成，顺序即语义）。``project_root`` 不存在时先建目录
    （make_runtime 要写 models.yaml）。
    """
    project_root = Path(project_root)
    project_root.mkdir(parents=True, exist_ok=True)
    runtime = make_runtime(project_root)
    runtime.use(*pre_plugins, LoggingPlugin(level=level,
                                            max_value_repr=max_value_repr))
    runtime.register_agent_type("log-agent", agent_cls)
    provider = add_fake_provider(runtime)
    return runtime, provider


def read_log(agent: Any) -> list[dict]:
    """读取该 Agent session 目录的 logging.jsonl（不存在返回空列表）。"""
    path = agent._session_dir / "logging.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def hooks_of(rows: list[dict]) -> list[str]:
    """提取行序列的 hook 字段（保序）。"""
    return [row["hook"] for row in rows]
