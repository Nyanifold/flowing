"""阶段 5 回归：launch 返回后（repl /new、serve POST /agents 等会话期入口）
的 ``create_agent`` / fya 装配对 ``@/`` 引用的解析。

历史缺陷：``@/`` 曾锚 ``_current_project_root`` ContextVar（launch 返回即
reset），launch 后的惰性解析（fya 的 tools/skills @/ 条目、run-workflow 的
@/ 路径）全部报错。修复后锚点为 Runtime 固化的 ``project_root``。
"""

from __future__ import annotations

import textwrap

import pytest

import flowing
from flowing.plugins.skills import SkillPlugin


def _write_project(proj) -> None:
    """最小夹具项目：fya Agent 带 ``@/`` 工具与 ``@/`` skills glob 引用。"""
    (proj / "agents" / "x").mkdir(parents=True)
    (proj / "tools").mkdir()
    (proj / "skills" / "one").mkdir(parents=True)
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
            """回显文本。"""
            return text
        '''), encoding="utf-8")
    (proj / "skills" / "one" / "SKILL.md").write_text(
        "---\ndescription: 测试技能\n---\n技能正文。\n", encoding="utf-8")
    (proj / "agents" / "x" / "agent.fya").write_text(textwrap.dedent('''
        name: x
        description: at-ref 引用回归用 Agent
        model_tag: default
        tools:
          - "@/tools/mytool.py"
        skills:
          - "@/skills/*"
        ---
        $system_prompt:
        你是测试助手。
        '''), encoding="utf-8")
    (proj / "main.py").write_text(textwrap.dedent('''
        from flowing import Runtime
        from flowing.plugins.skills import SkillPlugin
        from flowing.providers import FakeProvider, ProviderResponse
        from flowing.message import Message, MessageKind, TextBlock


        async def main():
            runtime = Runtime(persist_dir="@/.flowing")
            runtime.install(SkillPlugin())
            runtime.set_models("@/models.yaml")
            runtime.set_model_tags("@/model-tags.yaml")
            provider = FakeProvider()

            async def _gen(context, model):
                return ProviderResponse(
                    message=Message(kind=MessageKind.PROVIDER,
                                    content=[TextBlock(text="ok")]),
                    finish=True, provider_data={})

            provider.generate_fn = _gen
            runtime.provider_registry._instances["fake"] = provider
            await runtime.mount("@/agents/x", agent_id="root")
            return runtime
        '''), encoding="utf-8")


async def test_create_agent_with_at_refs_after_launch(tmp_path):
    """launch 返回后（ContextVar 已 reset）：create_agent 装配含 ``@/`` 工具 /
    skills 引用的 fya 正常完成——回归 repl ``/new`` 场景的报错。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    _write_project(proj)

    runtime = await flowing.launch(str(proj))
    try:
        from flowing.runtime import _current_project_root
        assert _current_project_root.get() is None   # 前置：launch 上下文已复位
        agent = await runtime.create_agent("@/agents/x")   # /new 同路径
        assert agent.node_id.startswith("agent-")
        assert "mytool" in [e.name_alias for e in agent._tool_entries.values()]
        await agent.destroy()
    finally:
        await runtime.shutdown()
