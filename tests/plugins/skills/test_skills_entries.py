"""skills 条目装配与 catalog 渲染测试：测试清单 T06、T16–T23、T33。

真 Agent 侧由 ``harness`` 的 ``HarnessRuntime`` 迷你管线驱动（经
``skills_support`` 间接载入）；SkillPlugin 经 ``make_skill_runtime``
直接 install。注册表命中的技能用 ``runtime.register_skill`` 编程式注册
（裸名落 ``default::``），文件派生技能用 fixtures 副本（tmp_path）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flowing.errors import FormatError
from flowing.plugins.skills import Skill, use_skill
from flowing.plugins.skills.registry import skill_registry_key

from skills_support import make_skill_runtime


def _catalog_text(agent) -> str:
    """取 skills 动态块的当次渲染文本。"""
    block = next(b for b in agent.prompt_blocks if b.name == "skills")
    return block.content.resolve(agent)


# ---------------------------------------------------------------------------
# T06：use_skill 声明 [a, b]（一 disabled）→ 两者均已解析入缓存（M-98）
# ---------------------------------------------------------------------------


async def test_t06_all_declared_entries_parsed_eagerly(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    agent = await runtime.create_agent(
        "skill-host", start_loop=False,
        skills=["sum", {"with-args as wa": {"enabled": False}}])
    use_skill(agent)
    registry = agent.inject(skill_registry_key)
    keys = set(registry._skills)
    assert any(k.endswith("::sum") for k in keys)          # enabled 条目
    assert any(k.endswith("::with-args") for k in keys)    # disabled 条目同样已读入


# ---------------------------------------------------------------------------
# T16：as 别名——name_ori 为规范名，catalog 暴露别名
# ---------------------------------------------------------------------------


async def test_t16_alias_entry(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    runtime.register_skill(Skill(name="summarize", description="摘要技能。",
                                 content="SUM_BODY"))
    agent = await runtime.create_agent(
        "skill-host", start_loop=False, skills=["summarize as sum"])
    use_skill(agent)
    entry = agent._skill_entries["sum"]
    assert entry.name_ori == "summarize"   # 注册表命中（default::）保持裸名
    assert "<name>sum</name>" in _catalog_text(agent)


# ---------------------------------------------------------------------------
# T17：enabled=False 不进 catalog，但编程式 skill_load 不受影响
# ---------------------------------------------------------------------------


async def test_t17_disabled_entry_invisible_but_loadable(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    runtime.register_skill(Skill(name="audit", description="审计技能。",
                                 content="AUDIT_BODY"))
    agent = await runtime.create_agent(
        "skill-host", start_loop=False,
        skills=[{"audit": {"enabled": False}}, "sum"])
    use_skill(agent)
    catalog = _catalog_text(agent)
    assert "<name>audit</name>" not in catalog
    assert "<name>sum</name>" in catalog
    result = await agent.skill_load("audit")   # 编程式入口不做 enabled 检查
    assert "AUDIT_BODY" in result.content


# ---------------------------------------------------------------------------
# T18：无默认值参数未被 specified 覆盖 → skill_add 声明期 FormatError
# ---------------------------------------------------------------------------


async def test_t18_uncovered_param_fails_fast(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    runtime.register_skill(Skill(
        name="i18n", description="x", content="c",
        args_schema={"lang": {"type": "string"}}))   # 无默认值
    agent = await runtime.create_agent(
        "skill-host", start_loop=False, skills=["i18n"])
    with pytest.raises(FormatError, match="lang"):
        use_skill(agent)
    # 补上覆盖则正常
    agent2 = await runtime.create_agent(
        "skill-host", start_loop=False,
        skills=[{"i18n": {"args": {"lang": "zh"}}}])
    use_skill(agent2)
    assert "i18n" in agent2._skill_entries


# ---------------------------------------------------------------------------
# T19：specified 固定值 + 注入表达式（加载时沿 provide 链求值）
# ---------------------------------------------------------------------------


async def test_t19_specified_and_injection_expression(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    runtime.provide("work_directory", "/tmp/w")
    agent = await runtime.create_agent(
        "skill-host", start_loop=False,
        skills=[{"with-args as sum": {"args": {
            "max_length": 500,
            "work_directory": "{{ self.inject('work_directory') }}"}}}])
    use_skill(agent)
    result = await agent.skill_load("sum")
    assert "长度上限 500" in result.content         # specified 覆盖 Skill 默认（1000）
    assert "工作目录 /tmp/w" in result.content       # 注入表达式取 provide 链值
    assert agent._on_load_args == {"max_length": 500, "work_directory": "/tmp/w"}


# ---------------------------------------------------------------------------
# T20：override_description 覆写 catalog 描述；未覆写条目渲染本体描述
# ---------------------------------------------------------------------------


async def test_t20_override_description(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    runtime.register_skill(Skill(name="s1", description="本体描述一", content="c1"))
    runtime.register_skill(Skill(name="s2", description="本体描述二", content="c2"))
    agent = await runtime.create_agent(
        "skill-host", start_loop=False,
        skills=[{"s1": {"description": "覆写文本"}}, "s2"])
    use_skill(agent)
    catalog = _catalog_text(agent)
    assert "覆写文本" in catalog
    assert "本体描述一" not in catalog
    assert "本体描述二" in catalog   # 未覆写条目渲染 Skill.description 的 resolve 结果


# ---------------------------------------------------------------------------
# T21：resolve 只含 enabled 条目且本次渲染无文件 IO
# ---------------------------------------------------------------------------


async def test_t21_resolve_enabled_only_and_no_io(tmp_path, monkeypatch):
    runtime = make_skill_runtime(tmp_path)
    agent = await runtime.create_agent(
        "skill-host", start_loop=False,
        skills=["sum", {"with-args as wa": {"enabled": False}}])
    use_skill(agent)   # 声明期已把定义文件全部读入注册表
    # 渲染期任何文件访问都是违约（读取不惰性、渲染惰性）
    monkeypatch.setattr(Path, "read_text",
                        lambda *a, **k: pytest.fail("resolve 触发文件读取"))
    monkeypatch.setattr(Path, "exists",
                        lambda *a, **k: pytest.fail("resolve 触发文件探测"))
    catalog = _catalog_text(agent)
    assert "<name>sum</name>" in catalog
    assert "<name>wa</name>" not in catalog


# ---------------------------------------------------------------------------
# T22：运行期 enabled 置 False → 再次 resolve 不含该条目（渲染无缓存）
# ---------------------------------------------------------------------------


async def test_t22_runtime_enabled_toggle_reflected(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    agent = await runtime.create_agent(
        "skill-host", start_loop=False, skills=["sum", "with-args as wa"])
    use_skill(agent)
    assert "<name>wa</name>" in _catalog_text(agent)
    agent._skill_entries["wa"].enabled = False
    assert "<name>wa</name>" not in _catalog_text(agent)


# ---------------------------------------------------------------------------
# T23：无 enabled 条目 → resolve 返回 ""，整块不注入（内容为空段）
# ---------------------------------------------------------------------------


async def test_t23_no_enabled_entries_renders_empty(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    runtime.register_skill(Skill(name="audit", description="d", content="c"))
    agent = await runtime.create_agent(
        "skill-host", start_loop=False,
        skills=[{"audit": {"enabled": False}}])
    use_skill(agent)
    assert _catalog_text(agent) == ""
    context = agent._assemble_context()
    segment = next(s for s in context.system_prompt if s.name == "skills")
    assert segment.content == ""   # catalog 不出现（空段）；skill-load 工具条目不受影响


# ---------------------------------------------------------------------------
# T33：模板三级解析——use_skill 参数 > SkillPlugin 构造参数 > 内置默认
# ---------------------------------------------------------------------------


async def test_t33_catalog_template_three_level_resolution(tmp_path):
    custom_agent_level = "AGENT_LEVEL[{% for e, s in entries %}{{ e.name_alias }}{% endfor %}]"
    custom_plugin_level = "PLUGIN_LEVEL[{% for e, s in entries %}{{ e.name_alias }}{% endfor %}]"

    # Agent 级参数最高
    runtime = make_skill_runtime(tmp_path, catalog_template=custom_plugin_level)
    agent = await runtime.create_agent("skill-host", start_loop=False, skills=["sum"])
    use_skill(agent, catalog_template=custom_agent_level)
    assert _catalog_text(agent).startswith("AGENT_LEVEL[")

    # 无 Agent 级参数 → SkillPlugin 构造参数（Runtime 级）
    agent2 = await runtime.create_agent("skill-host", start_loop=False, skills=["sum"])
    use_skill(agent2)
    assert _catalog_text(agent2).startswith("PLUGIN_LEVEL[")

    # 两级都缺省 → 内置 DEFAULT_CATALOG_TEMPLATE
    from flowing.plugins.skills import SkillPlugin

    from skills_support import HarnessRuntime, SkillHostAgent, copy_skills_fixtures

    root3 = tmp_path / "third"
    root3.mkdir()
    copy_skills_fixtures(root3)
    rt3 = HarnessRuntime(root3)
    SkillPlugin().install(rt3)   # 无 catalog_template 构造参数
    rt3.register_agent_type(SkillHostAgent, name="skill-host")
    agent3 = await rt3.create_agent("skill-host", start_loop=False, skills=["sum"])
    use_skill(agent3)
    catalog3 = _catalog_text(agent3)
    assert catalog3.startswith("<available_skills>")
    assert "<name>sum</name>" in catalog3
