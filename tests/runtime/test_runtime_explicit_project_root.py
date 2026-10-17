"""Runtime 显式 ``project_root`` 构造参数测试（嵌入场景）。

覆盖：无 ``@`` 上下文显式构造成功、``@/`` 锚实例根（含 None 回落对照）、
``persist_dir="@/rel"`` 解析与建目录、str 入参归一、多实例隔离、
显式模式 mount 含 ``@/`` 工具引用的 fya 根节点端到端。回归锚点：
T95（裸 ``Runtime()`` 抛 ``RuntimeError``）见 ``test_runtime.py``。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from flowing.runtime import Runtime, _current_project_root

from harness import add_fake_provider, make_runtime


def _write_minimal_project(proj: Path) -> None:
    """最小项目：models 两文件 + ``@/`` 工具引用的 fya Agent。"""
    (proj / "agents" / "x").mkdir(parents=True)
    (proj / "tools").mkdir()
    (proj / "models.yaml").write_text(
        "fake:\n  provider: fake\n  model: fake-model\n"
        "  context_window: 100000\n  max_output_tokens: 4096\n",
        encoding="utf-8")
    (proj / "model-tags.yaml").write_text(
        "tags:\n  default: fake\n", encoding="utf-8")
    (proj / "tools" / "mytool.py").write_text(textwrap.dedent('''
        from flowing.tool import flowing_tool

        @flowing_tool
        async def mytool(text: str = "") -> str:
            """Echo the text."""
            return text
        '''), encoding="utf-8")
    (proj / "agents" / "x" / "agent.fya").write_text(textwrap.dedent('''
        name: x
        description: explicit project_root end-to-end agent
        model_tag: default
        tools:
          - "@/tools/mytool.py"
        ---
        $system_prompt:
        You are a test assistant.
        '''), encoding="utf-8")


async def test_explicit_project_root_without_at_context(tmp_path):
    """显式 ``project_root``、无 ``@`` 上下文时 ``Runtime()`` 构造成功。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    assert _current_project_root.get() is None   # 前置：@ 上下文完全未登记
    runtime = Runtime(project_root=proj)
    assert runtime.project_root == proj.resolve()
    # 显式构造不登记 @ 上下文（不污染后续回落路径断言）
    assert _current_project_root.get() is None


async def test_resolve_path_at_anchors_instance_root(tmp_path):
    """显式模式下 ``resolve_path("@/x")`` 锚实例根；None 回落（launch 路径）行为不变。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    explicit = Runtime(project_root=proj)
    assert explicit.resolve_path("@/x") == proj.resolve() / "x"

    fallback = make_runtime(tmp_path / "other")   # None 回落：经 contextvar（harness 内部登记）
    assert fallback.resolve_path("@/x") == (tmp_path / "other").resolve() / "x"


async def test_persist_dir_at_prefix_explicit_mode(tmp_path):
    """``persist_dir="@/rel"`` 在显式模式下解析到实例根下并建目录。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    runtime = Runtime(project_root=proj, persist_dir="@/sessions")
    assert runtime.persist_dir == proj.resolve() / "sessions"
    assert runtime.persist_dir.is_dir()


async def test_str_project_root_normalized(tmp_path):
    """str 入参归一：``Runtime(project_root="...")`` 得到 ``.resolve()`` 后的绝对 Path。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    runtime = Runtime(project_root=str(proj))
    assert runtime.project_root == proj.resolve()
    assert isinstance(runtime.project_root, Path)


async def test_two_explicit_runtimes_isolated(tmp_path):
    """多实例隔离：两个显式不同 project_root 的 Runtime 并存，``@/`` 解析互不串扰。"""
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    rt_a = Runtime(project_root=proj_a)
    rt_b = Runtime(project_root=proj_b)
    assert rt_a.resolve_path("@/x") == proj_a.resolve() / "x"
    assert rt_b.resolve_path("@/x") == proj_b.resolve() / "x"
    assert rt_a.project_root != rt_b.project_root


async def test_mount_fya_with_at_tool_explicit_mode(tmp_path):
    """端到端：显式模式 mount 一个 ``@/`` 路径的 .fya 根节点成功（工具绑定命中）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_minimal_project(proj)

    assert _current_project_root.get() is None   # 前置：不经 launch、无 @ 上下文
    runtime = Runtime(project_root=proj, persist_dir="@/.flowing")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    add_fake_provider(runtime)

    agent = await runtime.mount("@/agents/x", agent_id="agent-main")
    assert "mytool" in [e.name_alias for e in agent._tool_entries.values()]
    await runtime.shutdown()


async def test_explicit_project_root_still_raises_without_both():
    """回归：``project_root`` 与 ``@`` 上下文两者皆无时仍抛 RuntimeError（T95 语义不变）。"""
    assert _current_project_root.get() is None
    with pytest.raises(RuntimeError):
        Runtime()
