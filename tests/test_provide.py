"""阶段 1 provide.py 测试（T-64 ~ T-68）：ProvideNode 协议与 inject_from 上溯。

全部经 tests/fakes.py 的假节点 / 假 Runtime 驱动（本期不接真 Runtime）。
"""

import pytest

from flowing.errors import MissingProvideError
from flowing.provide import ProvideNode, inject_from

from fakes import FakeAgent, FakeRuntime, FakeWorkflow


def test_t64_protocol_isinstance():
    rt = FakeRuntime("/tmp")
    assert isinstance(rt, ProvideNode)
    assert isinstance(FakeAgent(rt), ProvideNode)
    assert isinstance(FakeWorkflow(rt), ProvideNode)


def test_t65_hit_runtime_layer():
    rt = FakeRuntime("/tmp")
    agent = FakeAgent(rt)
    rt.provide("locale", "zh")
    assert inject_from(rt, agent, "locale") == "zh"


def test_t66_nearest_wins_and_override_visible():
    rt = FakeRuntime("/tmp")
    agent = FakeAgent(rt)
    rt.provide("k", "root")
    agent.provide("k", "child")
    assert inject_from(rt, agent, "k") == "child"  # 就近命中
    agent.provide("k", "child-v2")  # 同 key 重复 provide = 覆盖更新（S-37）
    assert inject_from(rt, agent, "k") == "child-v2"  # 不缓存，即刻可见


def test_t67_missing_key_raises():
    rt = FakeRuntime("/tmp")
    agent = FakeAgent(rt)
    with pytest.raises(MissingProvideError) as exc:
        inject_from(rt, agent, "nope")
    assert exc.value.key == "nope"


def test_t68_broken_chain_treated_as_missing():
    rt = FakeRuntime("/tmp")
    rt.provide("locale", "zh")
    agent = FakeAgent(rt)
    mid = FakeAgent(rt, node_id="agent-mid")
    leaf = FakeAgent(rt, node_id="agent-leaf", parent_id="agent-mid")
    # 中间父已销毁：get_node 抛 KeyError → 链断裂按未命中处理
    del rt._nodes["agent-mid"]
    with pytest.raises(MissingProvideError):
        inject_from(rt, leaf, "locale")
    # 断裂链上本节点命中仍然有效
    leaf.provide("locale", "leaf")
    assert inject_from(rt, leaf, "locale") == "leaf"
