"""Tool 声明与绑定族测试：测试清单 25–30。

覆盖 ToolDefinition.clone_with_overrides（稀疏补丁 / specified 移除 /
未知键新增）与 ToolEntry（llm_definition 四步 / resolve 两步 /
_per-Agent 覆写不污染注册表），含私有函数 ``_apply_param_aliases``
（推测点 1 落点）的撞名负例。

假 Runtime / 假 Agent 替身复用 ``tests/fakes.py``（duck-typed），
注册表用真 ``ToolRegistry``（快路径）。
"""

from __future__ import annotations

import pytest

from flowing.errors import FormatError
from flowing.parsable import Parsable
from flowing.tool import (
    Tool,
    ToolDefinition,
    ToolEntry,
    ToolRegistry,
    _apply_param_aliases,
)

from fakes import FakeAgent, FakeRuntime


class _PaymentTool(Tool):
    """注册表基底工具：params = amount / currency / user_id。"""

    definition = ToolDefinition(
        name="make-payment", description="发起支付",
        params_schema={
            "amount": {"type": "number", "default": 0, "description": "金额"},
            "currency": {"type": "string", "default": "CNY"},
            "user_id": {"type": "string"},
        })

    async def execute(self, **kwargs):
        return kwargs


@pytest.fixture
def runtime(tmp_path):
    rt = FakeRuntime(tmp_path)
    rt.tool_registry = ToolRegistry()
    rt.tool_registry.register(_PaymentTool())
    return rt


@pytest.fixture
def agent(runtime):
    return FakeAgent(runtime)


# ---------------------------------------------------------------------------
# ToolDefinition.clone_with_overrides（清单 25、26、27）
# ---------------------------------------------------------------------------


class TestCloneWithOverrides:
    def test_t25_override_params_sparse_merge(self):
        """清单 25：稀疏补丁只替换出现的关键字，原定义不变。"""
        base = ToolDefinition(
            name="t", description="d",
            params_schema={"amount": {"type": "number", "default": 0,
                                      "description": "金额"}})
        new = base.clone_with_overrides(
            override_params={"amount": {"description": "支付金额"}})
        assert new is not base
        assert new.params_schema["amount"] == {
            "type": "number", "default": 0, "description": "支付金额"}
        # 后置条件：原定义不变
        assert base.params_schema["amount"] == {
            "type": "number", "default": 0, "description": "金额"}

    def test_t26_specified_params_removed(self):
        """清单 26：specified 键从 LLM 视图移除；不存在的键静默忽略。"""
        base = ToolDefinition(
            name="t", description="d",
            params_schema={"amount": {"type": "number", "default": 0},
                           "user_id": {"type": "string"}})
        new = base.clone_with_overrides(
            specified_params={"user_id", "nonexistent"})
        assert set(new.params_schema) == {"amount"}
        # required 由 default 有无派生（无双份状态），移除即同步
        assert set(base.params_schema) == {"amount", "user_id"}

    def test_t27_override_unknown_key_is_new_param(self):
        """清单 27：override_params 的未知键视为新增参数。"""
        base = ToolDefinition(
            name="t", description="d",
            params_schema={"summary": {"type": "string", "default": ""}})
        new = base.clone_with_overrides(
            override_params={"score": {"type": "integer"}})
        assert new.params_schema["score"] == {"type": "integer"}
        assert "score" not in base.params_schema


# ---------------------------------------------------------------------------
# ToolEntry.llm_definition / resolve（清单 28、29、30）
# ---------------------------------------------------------------------------


class TestToolEntry:
    def test_t28_llm_definition_hidden_and_alias(self, runtime, agent):
        """清单 28：specified 隐藏 + param_aliases 改名 + 名字为别名。"""
        entry = ToolEntry(
            name_alias="pay", name_ori="make-payment",
            specified={"user_id": Parsable("{{ self.inject('user_id') }}")},
            param_aliases={"sum": "amount"})
        definition = entry.llm_definition(runtime, agent)
        assert definition.name == "pay"
        assert "user_id" not in definition.params_schema
        assert "sum" in definition.params_schema
        assert "amount" not in definition.params_schema
        # 别名只改名不改内容
        assert definition.params_schema["sum"] == {
            "type": "number", "default": 0, "description": "金额"}

    def test_t28b_override_description_resolved_on_site(self, runtime, agent):
        """override_description 是 Parsable：llm_definition 时以调用方
        Agent 为上下文现场求值。"""
        entry = ToolEntry(
            name_alias="pay", name_ori="make-payment",
            override_description=Parsable("支付工具（{{ self.node_id }}）"))
        definition = entry.llm_definition(runtime, agent)
        assert definition.description == "支付工具（agent-fake）"

    def test_t29_resolve_aggregation(self, runtime, agent):
        """清单 29：别名映射回规范名 + specified（固定值/注入表达式）覆盖。"""
        agent.provide("user_id", "alice")
        entry = ToolEntry(
            name_alias="pay", name_ori="make-payment",
            specified={"currency": Parsable("CNY"),
                       "user_id": Parsable("{{ self.inject('user_id') }}")},
            param_aliases={"sum": "amount"})
        schema = runtime.tool_registry.get("make-payment").definition.params_schema
        resolved = entry.resolve(agent, {"sum": 100}, schema)
        assert resolved == {"amount": 100, "currency": "CNY", "user_id": "alice"}

    def test_t29b_specified_overrides_same_named_llm_arg(self, runtime, agent):
        """防御性场景：LLM 幻觉同名参数被 specified 覆盖（优先级链）。"""
        agent.provide("user_id", "alice")
        entry = ToolEntry(
            name_alias="pay", name_ori="make-payment",
            specified={"user_id": Parsable("{{ self.inject('user_id') }}")})
        schema = runtime.tool_registry.get("make-payment").definition.params_schema
        resolved = entry.resolve(agent, {"user_id": "mallory"}, schema)
        assert resolved["user_id"] == "alice"

    def test_t30_per_agent_overrides_isolated(self, runtime, agent):
        """清单 30：同一注册工具、两个 entry 各自覆写 → 两个不同 schema，
        注册表原定义不变。"""
        entry_a = ToolEntry(
            name_alias="pay", name_ori="make-payment",
            override_params={"amount": {"description": "A 视图"}})
        entry_b = ToolEntry(
            name_alias="pay", name_ori="make-payment",
            override_params={"amount": {"description": "B 视图",
                                        "maximum": 50000}})
        definition_a = entry_a.llm_definition(runtime, agent)
        definition_b = entry_b.llm_definition(runtime, agent)
        assert definition_a.params_schema["amount"]["description"] == "A 视图"
        assert definition_a.params_schema["amount"]["type"] == "number"  # 稀疏回填
        assert definition_b.params_schema["amount"]["description"] == "B 视图"
        assert definition_b.params_schema["amount"]["maximum"] == 50000
        assert "maximum" not in definition_a.params_schema["amount"]
        # 注册表原定义不变
        original = runtime.tool_registry.get("make-payment").definition
        assert original.params_schema["amount"] == {
            "type": "number", "default": 0, "description": "金额"}


# ---------------------------------------------------------------------------
# _apply_param_aliases（推测点 1：私有函数，撞名 → FormatError）
# ---------------------------------------------------------------------------


class TestApplyParamAliases:
    def test_alias_clash_with_existing_key(self):
        base = ToolDefinition(
            name="t", description="d",
            params_schema={"amount": {"type": "number"},
                           "sum": {"type": "integer"}})
        with pytest.raises(FormatError):
            _apply_param_aliases(base, {"sum": "amount"})

    def test_alias_of_unknown_canonical_skipped(self):
        base = ToolDefinition(
            name="t", description="d", params_schema={"a": {"type": "string"}})
        result = _apply_param_aliases(base, {"x": "ghost"})
        assert set(result.params_schema) == {"a"}

    def test_empty_aliases_returns_same_object(self):
        base = ToolDefinition(name="t", description="d", params_schema={})
        assert _apply_param_aliases(base, {}) is base
