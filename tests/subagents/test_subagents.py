"""子智能体条目与解析测试：测试清单 52–56。

覆盖 ``SubagentEntry.catalog_view``（specified 排除 / 覆写 / 别名 /
description 覆写与子类回退）、``_assemble_context`` 的 visible 过滤与
S-19 无隐式附加、同别名撞名 ``EntryNameConflictError``、glob 显式优先
跳过（``_expand_glob_entries``）、``resolve`` 别名映射 + specified
（固定值 / 注入表达式）覆盖。

真 Agent 侧由 harness 的 ``HarnessRuntime`` 迷你管线驱动；
纯 resolve 路径用 ``fakes.py`` 的 FakeAgent（provide 链真实上溯）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from flowing.agent import Agent
from flowing.errors import EntryNameConflictError
from flowing.parser import normalize_entries, parse_fya
from flowing.parsable import Parsable
from flowing.subagents import SubagentEntry, _expand_glob_entries
from flowing.runtime import AGENT_NAMING

from fakes import FakeAgent, FakeRuntime

# 复用 harness 的 HarnessRuntime 迷你管线（真 Agent 侧接口面）。
from harness import (
    HarnessRuntime,
    SimpleAgent,
)


class _PaymentArgs(BaseModel):
    amount: float
    currency: str = "CNY"
    user_id: str = ""


class PaymentAgent(Agent):
    """目录 fixture 中 ``payment`` 子 Agent 类型的替身类（注册表命中）。"""

    description = Parsable("发起支付。")
    args_model = _PaymentArgs
    system_prompt = Parsable("你是支付处理助手。")

    async def setup(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture
async def runtime(tmp_path):
    """HarnessRuntime 实例（收尾销毁全部存活 Agent，防跨测试泄漏）。"""
    rt = HarnessRuntime(tmp_path)
    rt.register_agent_type(SimpleAgent, name="test-agent")
    yield rt
    for node_id, node in list(rt._nodes.items()):
        if node is rt:
            continue
        try:
            await node.destroy()
        except Exception:
            pass


@pytest.fixture
async def parent(runtime):
    """已注册 ``payment`` 子类型、并已启动的亲代 Agent。"""
    runtime.register_agent_type(PaymentAgent, name="payment")
    runtime.provide("user_id", "alice")   # provide 链（inject 上溯终点）
    return await runtime.create_agent("test-agent")


def _order_agent_subagent_ref(fixtures_dir):
    """从目录形态 fixture 解析出 ``- payment as pay`` 的 EntryRef。"""
    text = (fixtures_dir / "fya" / "agents" / "order-agent" / "agent.fya").read_text(
        encoding="utf-8")
    doc = parse_fya(text, entry_fields={"tools", "subagents"})
    return doc.fields["subagents"][0]


# ---------------------------------------------------------------------------
# 清单 52：catalog_view 的 specified 排除（固定值 + 注入表达式）
# ---------------------------------------------------------------------------


async def test_catalog_view_excludes_specified(parent, fixtures_dir):
    entry = parent.add_agent(_order_agent_subagent_ref(fixtures_dir))
    view = entry.catalog_view(parent)
    assert view["name"] == "pay"
    assert view["description"] == "发起支付。"   # 无覆写 → 子类原 description
    params_xml = view["params_xml"]
    assert "currency" not in params_xml   # specified 固定值不出现
    assert "user_id" not in params_xml    # specified 注入表达式不出现
    assert 'name="amount"' in params_xml and 'type="number"' in params_xml
    assert 'required="true"' in params_xml
    assert params_xml.startswith("<params>") and params_xml.endswith("</params>")


async def test_catalog_view_override_and_alias(parent):
    """override_params / param_aliases / override_description 全件生效。"""
    entry = parent.add_agent(
        "payment", alias="pay",
        body={"description": "定制支付描述",
              "args": {"amount as sum": {"description": "支付金额（元）"},
                       "currency": "USD"}})
    view = entry.catalog_view(parent)
    assert view["description"] == "定制支付描述"
    assert 'name="sum"' in view["params_xml"]   # 规范名 amount 已改名
    assert 'name="amount"' not in view["params_xml"]
    assert "支付金额（元）" in view["params_xml"]
    assert "currency" not in view["params_xml"]   # 裸值 → specified → 隐藏


async def test_catalog_view_empty_params_xml(parent):
    """全部参数被 specified 隐藏 → params_xml 为空串。"""
    entry = SubagentEntry(
        name_alias="pay", name_ori="payment",
        specified={"amount": Parsable(1), "currency": Parsable("CNY"),
                   "user_id": Parsable("u")})
    assert entry.catalog_view(parent)["params_xml"] == ""


async def test_catalog_template_renders_views(parent):
    """DEFAULT_SUBAGENT_CATALOG_TEMPLATE 消费 catalog_view 产物。"""
    parent.add_agent("payment", alias="pay")
    catalog = parent._render_subagent_catalog()
    assert catalog.startswith("<available_subagents>")
    assert "<subagent><name>pay</name><description>发起支付。</description>" in catalog


# ---------------------------------------------------------------------------
# 清单 53：visible=False → catalog 无条目；Context.tools 无 subagent-invoke
# ---------------------------------------------------------------------------


async def test_disabled_entry_excluded_from_catalog(parent):
    entry = parent.add_agent("payment", alias="pay")
    entry.visible = False
    context = parent._assemble_context()
    catalog_segments = [s for s in context.system_prompt
                        if s.name == "subagent-catalog"]
    assert catalog_segments == []   # 无 visible 条目 → 整块不注入
    assert context.tools == []   # S-19：无显式声明 → subagent-invoke 不在可见面


async def test_visible_entry_rendered_in_catalog(parent):
    parent.add_agent("payment", alias="pay")
    context = parent._assemble_context()
    catalog = [s for s in context.system_prompt if s.name == "subagent-catalog"]
    assert len(catalog) == 1
    assert "<name>pay</name>" in catalog[0].content
    assert catalog[0].cache == "dynamic"


# ---------------------------------------------------------------------------
# 清单 54：双 payment 推断撞名 → 装配层 EntryNameConflictError
# ---------------------------------------------------------------------------


async def test_duplicate_inferred_alias_conflict(parent):
    refs = normalize_entries(["./a/payment", "./b/payment"], naming=AGENT_NAMING)
    assert [r.alias for r in refs] == ["payment", "payment"]   # 推断撞名（本层不报）
    # 装配层：两处目标分别注册（替身注册表以 raw 为键，代替文件链命中）
    parent.runtime.register_agent_type(PaymentAgent, name="./a/payment")
    parent.runtime.register_agent_type(PaymentAgent, name="./b/payment")
    parent.add_agent(refs[0])
    with pytest.raises(EntryNameConflictError):
        parent.add_agent(refs[1])


# ---------------------------------------------------------------------------
# 清单 55：显式声明 + glob 展开命中同一资源 → glob 条目跳过
# ---------------------------------------------------------------------------


def test_glob_hit_same_resource_skipped(tmp_path):
    (tmp_path / "a" / "payment").mkdir(parents=True)
    (tmp_path / "a" / "extra").mkdir(parents=True)
    # glob 条目不具别名推断条件，装配层在 normalize_entries 之前分流展开
    expanded = _expand_glob_entries(
        ["./a/payment", "./a/*/"], naming=AGENT_NAMING, source_dir=tmp_path)
    # 显式条目原序透传；glob 命中 ./a/payment 与显式条目同资源 → 跳过，
    # 不报错；./a/extra 正常展开（别名经目录名推断）
    assert expanded[0].raw == "./a/payment"
    assert expanded[0].alias == "payment"
    assert [r.alias for r in expanded[1:]] == ["extra"]
    assert expanded[1].raw == str((tmp_path / "a" / "extra").resolve())
    assert expanded[1].body == {}


def test_glob_body_inherited_by_expanded_entries(tmp_path):
    """glob 条目带覆写映射时，每个展开产物继承同一 body。"""
    (tmp_path / "a" / "payment").mkdir(parents=True)
    expanded = _expand_glob_entries(
        [{"./a/*/": {"visible": True}}], naming=AGENT_NAMING,
        source_dir=tmp_path)
    assert [r.alias for r in expanded] == ["payment"]
    assert expanded[0].body == {"visible": True}


# ---------------------------------------------------------------------------
# 清单 56：resolve 别名映射 + specified（固定值 / 注入表达式）覆盖
# ---------------------------------------------------------------------------


def test_resolve_alias_and_specified():
    rt = FakeRuntime(__import__("pathlib").Path.cwd())
    parent = FakeAgent(rt)
    rt.provide("user_id", "alice")
    entry = SubagentEntry(
        name_alias="pay", name_ori="payment",
        param_aliases={"sum": "amount"},
        specified={"currency": Parsable("CNY"),
                   "user_id": Parsable("{{ self.inject('user_id') }}")})
    resolved = entry.resolve(parent, {"sum": 100})
    assert resolved == {"amount": 100, "currency": "CNY", "user_id": "alice"}


def test_resolve_specified_overrides_llm_key():
    """specified 优先级最高：覆盖 LLM 传入的同名键。"""
    rt = FakeRuntime(__import__("pathlib").Path.cwd())
    parent = FakeAgent(rt)
    entry = SubagentEntry(
        name_alias="pay", name_ori="payment",
        specified={"currency": Parsable("CNY")})
    assert entry.resolve(parent, {"currency": "USD"}) == {"currency": "CNY"}
