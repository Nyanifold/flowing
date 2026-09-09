"""lists 模块测试（简报测试清单 L1–L6）。"""

from dataclasses import dataclass, field

from flowing.lists import ManagedList, Togglable


@dataclass
class Item:
    """满足 Togglable 结构契约的测试元素。"""

    name: str
    by: str | None = None
    tags: list[str] = field(default_factory=list)
    enabled: bool = True


def make_container() -> tuple[ManagedList[Item], Item, Item, Item]:
    a = Item("a", by="x", tags=["t"])
    b = Item("b", by="y", tags=["t"])
    c = Item("c", by="x")
    container: ManagedList[Item] = ManagedList()
    for item in (a, b, c):
        container.append(item)
    return container, a, b, c


def test_togglable_protocol_structure():
    """W5：Togglable 为结构契约 Protocol（非 runtime_checkable），三属性就位即可被容器管理。"""
    item = Item("i", by="o", tags=["t"])
    assert (item.enabled, item.by, item.tags) == (True, "o", ["t"])


def test_l1_disable_by_tag():
    """L1：disable_by_tag 留位、计数、__iter__ 跳 disabled。"""
    container, a, b, c = make_container()
    assert container.disable_by_tag("t") == 2
    assert list(container) == [c]
    assert len(container._items) == 3


def test_l2_enable_by_owner():
    """L2：enable_by_owner 恢复且保持原注册顺序。"""
    container, a, b, c = make_container()
    container.disable_by_tag("t")
    assert container.enable_by_owner("y") == 1
    assert list(container) == [b, c]


def test_l3_remove_by_owner():
    """L3：remove_by_owner 物理删除。"""
    container, a, b, c = make_container()
    container.disable_by_tag("t")
    container.enable_by_owner("y")
    assert container.remove_by_owner("x") == 2
    assert container._items == [b]


def test_l4_append_returns_item_and_no_dedup():
    """L4：append 返回元素本身、落尾、不去重。"""
    container: ManagedList[Item] = ManagedList()
    item = Item("x")
    assert container.append(item) is item
    assert container._items[-1] is item
    container.append(item)
    assert len(container._items) == 2


def test_l5_idempotence_and_none_owner():
    """L5：幂等 + disable_by_owner(None) 命中 by=None 条目。"""
    container, a, b, c = make_container()
    assert container.disable_by_tag("t") == 2
    assert container.disable_by_tag("t") == 0  # 已 disabled → 幂等
    assert container.enable_by_owner("y") == 1  # b 恢复
    assert container.enable_by_owner("y") == 0  # 已 enabled → 幂等
    d = Item("d")  # by=None
    container.append(d)
    assert container.disable_by_owner(None) == 1
    assert d.enabled is False


def test_l6_remove_by_tag_is_permanent():
    """L6：remove_by_tag 物理删除不可恢复；无命中返回 0 且容器不变。"""
    container, a, b, c = make_container()
    assert container.remove_by_tag("t") == 2
    assert container.enable_by_tag("t") == 0  # 已物理删除，不可恢复
    assert list(container) == [c]
    before = list(container._items)
    assert container.remove_by_tag("nonexistent") == 0
    assert container._items == before


def test_l7_insert_arbitrary_position():
    """L7：insert 任意位置插入、返回元素本身、迭代反映新顺序。"""
    container, a, b, c = make_container()
    d = Item("d")
    assert container.insert(1, d) is d
    assert container._items == [a, d, b, c]  # 顺移一位
    assert [i.name for i in container] == ["a", "d", "b", "c"]


def test_l8_insert_index_semantics():
    """L8：insert 下标语义同 list.insert（负下标倒数、越界向两端收敛），不去重。"""
    container: ManagedList[Item] = ManagedList()
    x = Item("x")
    container.insert(99, x)  # 空容器越界 → 追加
    assert container._items == [x]
    y = Item("y")
    container.insert(-99, y)  # 过小负下标 → 最前
    assert container._items == [y, x]
    container.insert(5, x)  # 不去重：同一对象可再次插入
    assert container._items == [y, x, x]


def test_l9_insert_preserves_disabled_state_and_order():
    """L9：insert 不影响既有条目的启用状态；停用条目仍被迭代跳过。"""
    container, a, b, c = make_container()
    container.disable_by_tag("t")  # a、b 停用
    d = Item("d", tags=["t"])
    container.insert(1, d)
    assert d.enabled is True  # 新插入条目默认启用
    assert a.enabled is False and b.enabled is False  # 既有状态不变
    assert [i.name for i in container] == ["d", "c"]
