"""阶段 1 context.py 测试（T-69 ~ T-82）：PromptBlock 族、Context 容器、用量估计。

本期无真 Agent：逐块求值经 tests/fakes.py 假 Agent 手工模拟组装
（真组装 _assemble_context 属阶段 2）。
"""

from flowing.context import (
    Context,
    ContextUsageEstimate,
    PromptBlock,
    PromptBlockList,
    PromptSegment,
)
from flowing.parsable import Parsable

from fakes import FakeAgent, FakeRuntime


def _blocks() -> PromptBlockList:
    return PromptBlockList()


def test_t69_append_factory_defaults():
    blocks = _blocks()
    block = blocks.append("x", Parsable("hi"))
    assert len(blocks._items) == 1
    assert block.enabled is True
    assert block.cache == "dynamic"  # append 默认值
    assert block.tags == []
    assert block.by == ""  # M-30：省略 by 记为空串
    assert blocks[0] is block  # 返回块对象本身


def test_t70_iter_skips_disabled_keeps_order():
    blocks = _blocks()
    a = blocks.append("a", Parsable("a"))
    b = blocks.append("b", Parsable("b"))
    c = blocks.append("c", Parsable("c"))
    b.enabled = False
    assert [x.name for x in blocks] == ["a", "c"]


def test_t71_getitem_includes_disabled():
    blocks = _blocks()
    a = blocks.append("a", Parsable("a"))
    b = blocks.append("b", Parsable("b"))
    b.enabled = False
    assert blocks[1] is b  # 下标语义不过滤 enabled
    assert blocks[-1] is b  # 含负下标


def test_t72_disable_by_tag():
    blocks = _blocks()
    g1 = blocks.append("g1", Parsable("1"), tags=["g"])
    g2 = blocks.append("g2", Parsable("2"), tags=["g"])
    other = blocks.append("o", Parsable("3"))
    assert blocks.disable_by_tag("g") == 2
    assert g1.enabled is False and g2.enabled is False and other.enabled is True
    assert blocks._items == [g1, g2, other]  # 位置不变
    assert blocks.disable_by_tag("g") == 2  # 幂等：匹配计数（C-11/T-72 口径）


def test_t73_enable_by_tag_restores_order():
    blocks = _blocks()
    a = blocks.append("a", Parsable("a"), tags=["g"])
    b = blocks.append("b", Parsable("b"))
    c = blocks.append("c", Parsable("c"), tags=["g"])
    blocks.disable_by_tag("g")
    assert blocks.enable_by_tag("g") == 2
    assert [x.name for x in blocks] == ["a", "b", "c"]  # 与最初注册顺序一致


def test_t74_disable_by_owner():
    blocks = _blocks()
    s1 = blocks.append("s1", Parsable("1"), by="skill")
    s2 = blocks.append("s2", Parsable("2"), by="skill")
    anon = blocks.append("n", Parsable("3"))  # by 省略 → ""
    other = blocks.append("o", Parsable("4"), by="other")
    assert blocks.disable_by_owner("skill") == 2
    assert s1.enabled is False and s2.enabled is False
    assert anon.enabled is True and other.enabled is True
    assert blocks.disable_by_owner("") == 1  # owner="" 命中省略 by 的块（C-11）
    assert anon.enabled is False


def test_t75_remove_by_tag():
    blocks = _blocks()
    first = blocks.append("first", Parsable("0"))
    g1 = blocks.append("g1", Parsable("1"), tags=["g"])
    g2 = blocks.append("g2", Parsable("2"), tags=["g"])
    last = blocks.append("last", Parsable("3"))
    assert blocks.remove_by_tag("g") == 2
    assert blocks._items == [first, last]  # 物理删除，后续元素前移


def test_t76_remove_by_owner():
    blocks = _blocks()
    a = blocks.append("a", Parsable("1"), by="x")
    c = blocks.append("c", Parsable("2"), by="comm")
    b = blocks.append("b", Parsable("3"), by="y")
    assert blocks.remove_by_owner("comm") == 1
    assert blocks._items == [a, b]  # 其余块顺序不变


def test_t77_no_match_and_idempotent():
    blocks = _blocks()
    blocks.append("a", Parsable("1"), tags=["g"], by="o")
    assert blocks.disable_by_tag("zzz") == 0
    assert blocks.enable_by_tag("zzz") == 0
    assert blocks.remove_by_tag("zzz") == 0
    assert blocks.disable_by_owner("zzz") == 0
    assert blocks.enable_by_owner("zzz") == 0
    assert blocks.remove_by_owner("zzz") == 0
    assert len(blocks._items) == 1  # 无匹配不报错、无操作


def test_t78_same_name_blocks_coexist():
    blocks = _blocks()
    blocks.append("dup", Parsable("1"))
    blocks.append("dup", Parsable("2"))
    assert [b.name for b in blocks] == ["dup", "dup"]  # name 非唯一键


def test_t79_per_block_resolution(tmp_path):
    rt = FakeRuntime(tmp_path)
    agent = FakeAgent(rt, source_dir=tmp_path)
    agent.x = 1
    block = PromptBlock(name="b", content=Parsable("值：{{ x }}"), cache="dynamic", tags=[])
    seg = PromptSegment(content=block.content.resolve(agent), cache=block.cache, name=block.name)
    assert seg.content == "值：1"
    agent.x = 2
    seg2 = PromptSegment(content=block.content.resolve(agent), cache=block.cache, name=block.name)
    assert seg2.content == "值：2"  # 反映最新实例状态


def test_t80_two_resolutions_distinct_objects(tmp_path):
    rt = FakeRuntime(tmp_path)
    agent = FakeAgent(rt)
    agent.x = 1
    p = Parsable("值：{{ x }}")
    s1 = PromptSegment(content=p.resolve(agent), cache="dynamic", name="b")
    agent.x = 2
    s2 = PromptSegment(content=p.resolve(agent), cache="dynamic", name="b")
    assert s1 is not s2 and s1.content == "值：1" and s2.content == "值：2"


def test_t81_usage_estimate_fields():
    est = ContextUsageEstimate(tokens=150, measured=100, estimated=50,
                               anchor_message_id="m1", context_window=1000)
    assert est.tokens == (est.measured or 0) + est.estimated
    # 路径为空语形可构造
    empty = ContextUsageEstimate(tokens=0, measured=None, estimated=0,
                                 anchor_message_id=None, context_window=None)
    assert empty.measured is None and empty.anchor_message_id is None


def test_t82_usage_ratio():
    est = ContextUsageEstimate(tokens=150, measured=None, estimated=150,
                               anchor_message_id=None, context_window=100)
    assert est.usage_ratio == 1.5  # 不 clamp
    no_window = ContextUsageEstimate(tokens=10, measured=None, estimated=10,
                                     anchor_message_id=None, context_window=None)
    assert no_window.usage_ratio is None


def test_w25_context_container():
    ctx = Context(system_prompt=[PromptSegment(content="s", cache="dynamic", name="n")],
                  tools=[], messages=[])
    assert len(ctx.system_prompt) == 1 and ctx.tools == [] and ctx.messages == []
