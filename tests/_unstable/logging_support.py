"""``_unstable.logging`` 测试的公共助手。

提供：``harness`` 助手转发（make_runtime / HarnessRuntime / FakeProvider
脚本回放）、``LogAgent``（setup 中 ``use_logging`` 的测试 Agent）、
``make_logging_runtime``（真 Runtime + 已安装 LoggingPlugin 的工厂——
install 依赖 ``runtime.get_plugin`` 探测，HarnessRuntime / FakeRuntime
无此面，故本组测试统一走真 Runtime）、``read_log``（logging.jsonl 读取）、
``hooks_of``（日志行 hook 字段序列）。

本模块为普通模块而非 conftest：测试文件直接 ``import logging_support``，
避免顶级模块名 ``conftest`` 遮蔽。
"""

from __future__ import annotations

import json
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
    runtime.install(*pre_plugins, LoggingPlugin(level=level,
                                            max_value_repr=max_value_repr))
    runtime.register_agent_type(agent_cls, name="log-agent")
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
