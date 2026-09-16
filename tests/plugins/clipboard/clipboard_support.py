"""clipboard 插件测试的公共助手。

提供：``harness`` 助手转发（HarnessRuntime / make_runtime / FakeProvider
脚本回放）、``ClipboardAgent``（setup 中 ``use_clipboard``）、
``make_clipboard_harness``（装好 ClipboardPlugin 的 HarnessRuntime 工厂）、
``write_lines``（tmp_path 文本文件生成）。

本模块为普通模块而非 conftest：测试文件直接 ``import clipboard_support``，
避免顶级模块名 ``conftest`` 遮蔽。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from harness import (
    add_fake_provider,
    HarnessRuntime,
    make_runtime,
    script_provider,
    SimpleAgent,
    text_response,
    tool_call_response,
)

from flowing.plugins.clipboard import ClipboardPlugin, use_clipboard


class ClipboardAgent(SimpleAgent):
    """setup 中启用 ``use_clipboard`` 的测试 Agent。

    类属性 ``clipboard_kwargs``：透传给 ``use_clipboard`` 的阈值参数
    （recover 换新实例后 setup 重跑，类属性天然跨实例一致）。
    """

    clipboard_kwargs: ClassVar[dict[str, Any]] = {}

    async def setup(self, **kwargs: Any) -> None:
        await super().setup(**kwargs)
        use_clipboard(self, **type(self).clipboard_kwargs)


def make_clipboard_harness(
    tmp_path: Path,
    *,
    agent_cls: type = ClipboardAgent,
) -> HarnessRuntime:
    """HarnessRuntime + 已安装 ClipboardPlugin 与 clipboard-agent 类型。"""
    runtime = HarnessRuntime(tmp_path)
    ClipboardPlugin().install(runtime)
    runtime.register_agent_type(agent_cls, name="clipboard-agent")
    return runtime


def write_lines(path: Path, n: int, *, prefix: str = "line") -> Path:
    """生成 n 行文本文件（``line1\\n`` …… ``lineN\\n``），返回路径。"""
    path.write_text("".join(f"{prefix}{i}\n" for i in range(1, n + 1)),
                    encoding="utf-8")
    return path
