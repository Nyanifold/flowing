"""阶段 4 workflow 加载器测试（W33）：测试清单 T84–T87。

fixtures 经 ``project`` fixture 拷入 tmp_path 并登记 ``@`` 上下文；
T87 的「未经 launch 裸进程」用例刻意不用该 fixture。
"""

from __future__ import annotations

import pytest

from flowing.errors import FlowingError
from flowing.plugins.workflow import Workflow, resolve_workflow

from workflow_support import (
    EchoTool,
    add_fake_provider,
    make_runtime,
    project,   # noqa: F401（fixture 注册）
)


async def test_t84_class_form_resolution(project):
    """T84：verify_fix.py 含一个 Workflow 子类 → resolve 返回该类。"""
    cls = resolve_workflow("@/verify_fix.py")
    assert issubclass(cls, Workflow)
    assert cls.__name__ == "VerifyFixWorkflow"


async def test_t85_function_form_compiles_and_runs(project, tmp_path):
    """T85：quick.py 只含顶层 async def run（无 self）→ 返回生成的
    Workflow 子类；运行中裸名 create_agent/agent 实际调用
    self.create_agent，tool_call 实际调用 self.tool_call。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.register_tool(EchoTool())
    try:
        cls = resolve_workflow("@/quick.py")
        assert issubclass(cls, Workflow)
        assert cls.__name__ == "Quick"          # 类名由文件名推导（PascalCase）
        wf = cls(None, runtime)
        result = await wf.run(prompt="hi")
        assert result == {"echo": {"echo": "hi"}}   # tool_call 改写生效
        assert len(wf._agents) == 2             # create_agent 与 agent 简写各一
        # 两个子 Agent 均由 self.create_agent 创建（parent 链指回本 workflow）
        assert all(a._parent_id == wf.node_id for a in wf._agents.values())
        await wf.destroy()
    finally:
        await runtime.shutdown()


async def test_t86_missing_ambiguous_absent_and_class_priority(project):
    """T86：路径不存在 → FlowingError（含解析后路径）；两个子类 → 歧义；
    既无子类又无顶层 run → 缺失；类形态与顶层 run 并存 → 类优先。"""
    with pytest.raises(FlowingError, match="ghost"):
        resolve_workflow("@/ghost.py")
    with pytest.raises(FlowingError, match="歧义"):
        resolve_workflow("@/two_classes.py")
    with pytest.raises(FlowingError, match="缺失"):
        resolve_workflow("@/no_run.py")
    cls = resolve_workflow("@/both.py")
    assert cls.__name__ == "BothWorkflow"   # 类形态优先（顶层 run 被忽略）


async def test_t87_kebab_double_candidates_and_bare_process(project, tmp_path):
    """T87：kebab 末段依次尝试 verify-fix.py / verify_fix.py；两候选都
    命中 → FlowingError（歧义）；未经 launch 的裸进程调用 → RuntimeError。"""
    # 单候选命中：flows/verify-fix 落到 verify_fix.py（连字符转下划线）
    (tmp_path / "flows").mkdir()
    (tmp_path / "flows" / "verify_fix.py").write_text(
        (project / "verify_fix.py").read_text(encoding="utf-8"),
        encoding="utf-8")
    cls = resolve_workflow("@/flows/verify-fix")
    assert issubclass(cls, Workflow) and cls.__name__ == "VerifyFixWorkflow"

    # 双候选都命中 → 歧义
    (tmp_path / "flows" / "verify-fix.py").write_text(
        (project / "verify_fix.py").read_text(encoding="utf-8"),
        encoding="utf-8")
    with pytest.raises(FlowingError, match="歧义"):
        resolve_workflow("@/flows/verify-fix")


def test_t87b_bare_process_without_launch_raises():
    """T87 后半：无 launch 登记的 @ 上下文 → RuntimeError。"""
    from flowing.runtime import _current_project_root

    assert _current_project_root.get() is None   # 前置：确无登记
    with pytest.raises(RuntimeError):
        resolve_workflow("@/verify_fix.py")
