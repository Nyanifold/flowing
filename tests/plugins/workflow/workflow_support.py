"""workflow 插件测试的公共助手。

提供：``harness`` 助手转发（make_runtime / FakeProvider 脚本回放）、
``project`` fixture 工厂（拷贝 workflows fixtures 到 tmp_path 并登记 ``@``
上下文——``make_runtime`` 的真 ``Runtime.__init__`` 仍依赖
``_current_project_root`` contextvar；``resolve_workflow`` 的 ``@/`` 解析
基准经 ``project_root`` 参数显式传入）、``EchoTool``（T82/T83/T85 的最小
注册表工具）、``RecorderAgent``（T92 创建管线钩子观测）。

本模块为普通模块而非 conftest：测试文件直接 ``import workflow_support``，
避免顶级模块名 ``conftest`` 遮蔽。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, ClassVar

import pytest

from harness import (
    add_fake_provider,
    make_runtime,
    script_provider,
    SimpleAgent,
    text_response,
    tool_call_response,
)

from flowing.hooks import on
from flowing.runtime import _current_project_root
from flowing.tool import Tool, ToolDefinition

FIXTURES_WORKFLOWS = Path(__file__).parent.parent.parent / "fixtures" / "workflows"


@pytest.fixture
def project(tmp_path):
    """把 ``tests/fixtures/workflows/`` 整树拷到 ``tmp_path`` 并登记
    ``@`` 上下文（contextvar 直连，与 make_runtime 同惯例）；测试结束复位。
    """
    for f in FIXTURES_WORKFLOWS.iterdir():
        if f.is_file():
            shutil.copy(f, tmp_path / f.name)
    token = _current_project_root.set(tmp_path.resolve())
    try:
        yield tmp_path
    finally:
        _current_project_root.reset(token)


class EchoTool(Tool):
    """最小测试工具：回显 ``text`` 参数（``caller`` 不声明——workflow
    路径以 ``caller=None`` 调度）。"""

    definition = ToolDefinition(
        name="echo-tool",
        description="回显 text 参数。",
        params_schema={"text": {"type": "string", "default": ""}},
    )

    async def execute(self, *, text: str = "") -> dict:
        return {"echo": text}


class RecorderAgent(SimpleAgent):
    """创建管线钩子观测 Agent（T92）：@on 标记方法把钩子名记入类属性。"""

    fired: ClassVar[list[str]] = []

    @on("before_create")
    def _rec_before(self, kwargs):
        type(self).fired.append("before_create")
        return kwargs

    @on("after_create")
    def _rec_after(self, _value=None):
        type(self).fired.append("after_create")

    @on("on_subagent_invoke")
    def _rec_bsi(self, value):
        type(self).fired.append("on_subagent_invoke")
        return value

    @on("on_subagent_returns")
    def _rec_asi(self, value):
        type(self).fired.append("on_subagent_returns")
        return value
