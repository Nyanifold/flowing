"""阶段 4 clipboard 批次测试的公共助手（非 conftest——避免顶级模块名遮蔽，
见阶段 3 易踩坑笔记）。

提供：phase2 conftest 的 importlib 载入（HarnessRuntime / make_runtime /
FakeProvider 脚本回放助手）、``ClipboardAgent``（setup 中
``use_clipboard``）、``make_clipboard_harness``（装好 ClipboardPlugin 的
HarnessRuntime 工厂）、``write_lines``（tmp_path 文本文件生成）。
"""

from __future__ import annotations

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
    runtime.register_agent_type("clipboard-agent", agent_cls)
    return runtime


def write_lines(path: Path, n: int, *, prefix: str = "line") -> Path:
    """生成 n 行文本文件（``line1\\n`` …… ``lineN\\n``），返回路径。"""
    path.write_text("".join(f"{prefix}{i}\n" for i in range(1, n + 1)),
                    encoding="utf-8")
    return path
