"""parsable.py 测试（T-22 ~ T-43）：五形式、描述符、求值与渲染链。

Agent 绑定求值全部由 tests/fakes.py 的假 Agent / 假 Runtime 替身驱动
（不经真 Agent，由替身驱动）。
"""

import pytest

from flowing.errors import MissingContextError, ReservedAttributeError
from flowing.parsable import (
    EXPRESSION,
    FILE_REF,
    LITERAL,
    PENDING,
    RAW,
    TEMPLATE,
    Parsable,
)

from fakes import FakeAgent, FakeRuntime


@pytest.fixture
def runtime(tmp_path):
    return FakeRuntime(tmp_path, config={"limits": {"turns": 10}})


@pytest.fixture
def agent(runtime, tmp_path):
    return FakeAgent(runtime, source_dir=tmp_path)


# ---------------------------------------------------------------------------
# 形式推断（T-22 / T-23）
# ---------------------------------------------------------------------------

def test_t22_form_inference():
    assert Parsable("你好 {{ user_id }}").type is TEMPLATE
    assert Parsable("{{ config.limits.turns }}").type is EXPRESSION
    assert Parsable("$./prompt.md").type is FILE_REF
    assert Parsable('$"原始 {{ 文本 }}"').type is RAW
    assert Parsable("普通文本").type is LITERAL
    assert Parsable(123).type is LITERAL


def test_t23_edge_forms():
    assert Parsable('$"_"').type is RAW
    assert Parsable("_").type is LITERAL  # 裸字符串：PENDING 语义只在 .fya 解析层
    # 两个并列表达式不是「恰好一个完整表达式」→ TEMPLATE
    assert Parsable("{{ a }} {{ b }}").type is TEMPLATE


# ---------------------------------------------------------------------------
# 描述符与展示面（T-24 / T-25 / T-39 / T-40）
# ---------------------------------------------------------------------------

class _Host:
    p = Parsable("{{ 1 + 1 }}")


def test_t24_class_access_returns_self_unbound():
    assert _Host.p._instance is None
    assert _Host.p is _Host.p  # 类访问返回原对象（身份断言）


def test_t25_instance_access_returns_bound_shallow_copy():
    a = _Host()
    bound = a.p
    assert bound is not a.p          # 每次访问返回新浅拷贝
    assert bound._instance is a
    assert bound.source is _Host.p.source
    assert bound.type is _Host.p.type


def test_t39_str_shows_template_source_without_eval():
    p = Parsable("你好 {{ user_id }}")
    assert str(p) == "你好 {{ user_id }}"  # 不求值、未绑定不抛错（R-2 澄清）


def test_t40_repr_unbound():
    assert repr(Parsable("{{ x }}")) == "Parsable(source='{{ x }}', type=EXPRESSION)"


# ---------------------------------------------------------------------------
# resolve 的上下文分支（T-26 ~ T-29）
# ---------------------------------------------------------------------------

def test_t26_unbound_resolve_raises():
    with pytest.raises(MissingContextError):
        Parsable("{{ x }}").resolve()


def test_t27_unbound_file_ref_with_mapping_raises():
    with pytest.raises(MissingContextError):
        Parsable("$./prompt.md").resolve({"x": 1})


def test_t28_bound_agent_flattened_attr(agent):
    agent.user_id = "u1"
    assert agent.parsable("{{ user_id }}").resolve() == "u1"


def test_t29_expression_returns_native_value(agent):
    result = agent.parsable("{{ config.limits.turns }}").resolve()
    assert result == 10 and isinstance(result, int)  # int(10) 而非 "10"
    # Mapping 显式注入 config 同效
    assert Parsable("{{ config.limits.turns }}").resolve(
        {"config": {"limits": {"turns": 10}}}
    ) == 10


# ---------------------------------------------------------------------------
# 保留名检测（T-30）
# ---------------------------------------------------------------------------

def test_t30_reserved_attribute(agent):
    agent.env = {"HOME": "/x"}  # 实例属性占用保留名
    with pytest.raises(ReservedAttributeError) as exc:
        agent.parsable("{{ user_id }}").resolve()
    assert exc.value.name == "env"


# ---------------------------------------------------------------------------
# FILE_REF 现场读文件、无缓存（T-31）
# ---------------------------------------------------------------------------

def test_t31_file_ref_no_cache(runtime, tmp_path, copy_fixture):
    target = copy_fixture("parsable/refund-rules.md")  # 原件保持 pristine
    a = FakeAgent(runtime, source_dir=tmp_path)
    a.user_id = "u1"
    p = a.parsable("$./refund-rules.md")
    # Jinja2 默认 keep_trailing_newline=False：文件末尾换行被剥离
    assert p.resolve() == "用户 u1 的订单 30 天内可申请退款。"
    target.write_text("已改：{{ user_id }}", encoding="utf-8")
    assert p.resolve() == "已改：u1"  # 第二次反映新内容（无缓存）


# ---------------------------------------------------------------------------
# RAW 边缘（T-32 / T-33）
# ---------------------------------------------------------------------------

def test_t32_raw_greedy_quotes():
    assert Parsable('$"含有 "引号" 的原始文本"').resolve() == '含有 "引号" 的原始文本'


def test_t33_raw_sentinel_lookalikes():
    assert Parsable('$"_"').resolve() == "_"  # 全程不出现 PENDING
    assert Parsable('$"$200.00"').resolve() == "$200.00"  # 不当文件引用


# ---------------------------------------------------------------------------
# TEMPLATE / EXPRESSION 行为（T-34 / T-35）
# ---------------------------------------------------------------------------

def test_t34_template_rendering_and_undefined():
    assert Parsable("Hello {{ name }}, welcome!").resolve({"name": "x"}) == "Hello x, welcome!"
    assert isinstance(Parsable("Hello {{ name }}!").resolve({"name": "x"}), str)
    # 未定义变量按 Jinja2 默认渲染为空
    assert Parsable("Hello {{ missing }}!").resolve({}) == "Hello !"


def test_t35_expression_parsable_result_unwrapped_one_layer(agent):
    agent.user_id = "u1"
    agent.system_prompt = agent.parsable("系统提示：{{ user_id }}")
    result = agent.parsable("{{ self.system_prompt }}").resolve()
    assert result == "系统提示：u1"  # 渲染后文本而非 Parsable 对象
    assert not isinstance(result, Parsable)


# ---------------------------------------------------------------------------
# 嵌套 .resolved 渲染链（T-36，fixtures/parsable 双文件）
# ---------------------------------------------------------------------------

def test_t36_nested_resolved_chain(runtime, fixtures_dir):
    a = FakeAgent(runtime, source_dir=fixtures_dir / "parsable")
    a.user_id = "u1"
    a.greeting = a.parsable("你好 {{ user_id }}")
    a.refund_rules = a.parsable("$./refund-rules.md")
    p = a.parsable("$./system-prompt.md")
    first = p.resolve()
    assert "你好 u1" in first and "用户 u1 的订单 30 天内可申请退款。" in first
    a.user_id = "u2"
    second = p.resolve()
    assert "你好 u2" in second and "用户 u2 的订单" in second  # 无缓存，反映新值
    assert first is not second


# ---------------------------------------------------------------------------
# {% include %} 与 $ 同源于 resolve_path（T-37，fixtures/parsable/includes）
# ---------------------------------------------------------------------------

def test_t37_include_same_origin(runtime, fixtures_dir):
    a = FakeAgent(runtime, source_dir=fixtures_dir / "parsable")
    a.user_id = "u1"
    a.show = True
    tpl = a.parsable(
        '{% include "./includes/header.md" %}正文{% if show %}{% include "./includes/footer.md" %}{% endif %}'
    )
    out = tpl.resolve()
    assert "[头部] 当前用户：u1" in out and "正文" in out and "[尾部] 完" in out
    a.show = False
    assert "[尾部]" not in tpl.resolve()


# ---------------------------------------------------------------------------
# resolved property（T-38）
# ---------------------------------------------------------------------------

def test_t38_unbound_resolved_raises():
    with pytest.raises(MissingContextError):
        Parsable("{{ x }}").resolved


# ---------------------------------------------------------------------------
# Mapping 分支的 env/config 注入（T-41 / T-42）
# ---------------------------------------------------------------------------

def test_t41_mapping_branch_injects_env(monkeypatch):
    monkeypatch.setenv("FLOWING_TEST_VAR", "hello-env")
    assert Parsable("{{ env.FLOWING_TEST_VAR }}").resolve({}) == "hello-env"


def test_t42_mapping_branch_config_rules():
    # 未绑定时不注入 config：config.* 按 Jinja2 默认渲染为空
    assert Parsable("值=[{{ config.limits.turns }}]").resolve({}) == "值=[]"
    # 显式键优先于注入值
    assert Parsable("env=[{{ env }}]").resolve({"env": "显式"}) == "env=[显式]"
    # 已绑定时 Mapping 分支也注入绑定实例的 config
    rt = FakeRuntime("/tmp", config={"limits": {"turns": 7}})
    a = FakeAgent(rt)
    p = a.parsable("t=[{{ config.limits.turns }}]")
    assert p.resolve({}) == "t=[7]"


# ---------------------------------------------------------------------------
# PENDING 哨兵（T-43）
# ---------------------------------------------------------------------------

def test_t43_pending_singleton():
    import flowing.parsable as pm

    assert pm.PENDING is PENDING  # 两处 import 同一对象
    assert repr(PENDING) == "PENDING"
    assert PENDING is not None and PENDING is not "" and PENDING is not []


# ---------------------------------------------------------------------------
# F2 回归（0904）：FILE_REF/include 基准 = 声明文件目录优先，否则
# Agent.source_dir()（方法形态）；公开 bind 绑定通道；skill content 解析期
# 带 SKILL.fya 目录
# ---------------------------------------------------------------------------

def test_f2_fileref_uses_agent_source_dir_method(tmp_path, runtime):
    """绑定 Agent 的 $./ 引用按 Agent.source_dir()（方法）解析到同目录文件。"""
    sub = tmp_path / "proj"
    sub.mkdir()
    (sub / "body.md").write_text("内容 A", encoding="utf-8")
    a = FakeAgent(runtime, source_dir=sub)
    p = Parsable("$./body.md").bind(a)
    assert p.resolve() == "内容 A"              # Agent 分支（context=None → _do_resolve）
    assert p.resolve({}) == "内容 A"            # Mapping 分支


def test_f2_fileref_prefers_declared_source_dir(tmp_path, runtime):
    """显式 source_dir（声明文件目录，如 SKILL.fya 目录）优先于 Agent 目录。"""
    agent_dir = tmp_path / "agents"
    skill_dir = tmp_path / "skills" / "refactor"
    agent_dir.mkdir(parents=True)
    skill_dir.mkdir(parents=True)
    (agent_dir / "notes.md").write_text("AGENT 文件", encoding="utf-8")
    (skill_dir / "notes.md").write_text("SKILL 文件", encoding="utf-8")
    a = FakeAgent(runtime, source_dir=agent_dir)
    p = Parsable("$./notes.md", source_dir=skill_dir).bind(a)
    assert p.resolve() == "SKILL 文件"          # 声明目录优先，不是 agent 目录
    assert p.resolve({}) == "SKILL 文件"


def test_f2_skill_content_resolves_relative_to_skill_file(tmp_path, runtime):
    """SKILL.fya 的 content: $./notes.md 解析到 SKILL.fya 同目录（registry 解析期带目录）。"""
    from flowing.plugins.skills.registry import _parse_skill_file

    skills_dir = tmp_path / "skills"
    sdir = skills_dir / "refactor-notes"
    sdir.mkdir(parents=True)
    (sdir / "SKILL.fya").write_text(
        "name: refactor-notes\n"
        "description: 重构约定。\n"
        "content: $./notes.md\n",
        encoding="utf-8",
    )
    (sdir / "notes.md").write_text("# 配置常量一律收进 src/config.ts。", encoding="utf-8")
    skill = _parse_skill_file("refactor-notes", skills_dir)
    # content 解析期带 SKILL.fya 目录；bind 到任意目录的 agent 后仍读对文件
    a = FakeAgent(runtime, source_dir=tmp_path / "elsewhere")
    rendered = skill.content.bind(a).resolve({})
    assert "src/config.ts" in rendered
