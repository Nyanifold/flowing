"""flowing.lists —— 一批带来源与标签的可开关条目的分组管理容器（``ManagedList`` / ``Togglable``）。

.. rubric:: 功能介绍

本模块提供「一批带来源与标签的可开关条目」的统一管理：:class:`ManagedList`
负责按标签（tag）或来源（owner）批量停用、恢复或删除条目，迭代时自动
跳过已停用条目；:class:`Togglable` 是条目需要满足的最小结构契约。适合
「按组临时停用一批能力、稍后整组恢复」的场景；框架内
:class:`flowing.hooks.HookList` 与 :class:`flowing.context.PromptBlockList`
都基于本模块实现。

本模块只提供容器管理：不规定条目除 :class:`Togglable` 三个属性之外的
任何字段，也不关心条目的业务含义。

.. rubric:: 使用示例

.. code-block:: python

    from flowing.lists import ManagedList

    class Item:                                # 满足 Togglable 即可
        def __init__(self, by=None, tags=None, enabled=True):
            self.by = by
            self.tags = tags or []
            self.enabled = enabled

    entries = ManagedList()
    entries.append(Item(by="rule-a", tags=["guardrail"]))
    entries.append(Item(by="rule-b", tags=["guardrail"]))
    entries.disable_by_tag("guardrail")        # 整组停用，条目保留
    list(entries)                              # []：停用条目不参与迭代
    entries.enable_by_tag("guardrail")         # 整组恢复

.. rubric:: 行为要点

- 停用只把条目的 ``enabled`` 字段置为 ``False``：条目保留在容器中、
  注册顺序不变、可随时恢复；删除才是把条目彻底移除、不可恢复。
- 容器内条目的相对顺序等于注册顺序，停用与恢复不改变顺序。
- 按来源的匹配是精确相等（``item.by == owner``，``None`` 与 ``None``
  也相等）；按标签的匹配是「``tags`` 列表中任一元素与给定标签相等
  即命中」。

.. seealso:: :class:`Togglable`、:class:`ManagedList`、
    :class:`flowing.hooks.HookList`、:class:`flowing.context.PromptBlockList`
"""

from collections.abc import Callable, Iterator
from typing import Any, Generic, Protocol, TypeAlias, TypeVar

__all__ = ["ManagedList", "Togglable"]

T = TypeVar("T")

class Togglable(Protocol):
    """``ManagedList`` 的元素契约：条目具备 ``enabled`` / ``by`` / ``tags`` 三个属性即可被容器管理。

    .. rubric:: 功能介绍

    :class:`ManagedList` 只感知条目的这三个属性，其余字段一概不关心。
    本协议是结构契约：条目类无需显式继承本协议，具备三个属性即满足；
    协议未加 ``@runtime_checkable``，不要用 ``isinstance`` 检查条目
    是否满足契约（会抛 ``TypeError``）。

    .. rubric:: 行为要点

    - ``enabled``：启用标记。容器对它的修改是原地赋值（停用置
      ``False``、恢复置 ``True``），条目本身不移动。
    - ``by``：来源或所有者标识。允许 ``None``；按来源的批量操作以
      精确相等匹配，``None`` 与 ``None`` 也相等。
    - ``tags``：标签列表。按标签的批量操作以「列表中任一元素与给定
      标签相等」匹配。
    - 注意对照：``flowing.tool.ToolEntry`` 与
      ``flowing.plugins.skills.SkillEntry`` 只有 ``enabled`` 一个
      同名字段（Agent 级绑定层条目，存于按别名索引的 dict，不进
      ``ManagedList``）——它们**不满足**本协议，不享有按 by/tags
      的整组管理。

    .. seealso:: :class:`ManagedList`、:class:`flowing.hooks.HookEntry`、
        :class:`flowing.context.PromptBlock`
    """

    enabled: bool
    """启用标记。``True`` 表示启用：条目参与迭代，进而参与钩子分发、
    上下文组装等消费路径。``False`` 表示停用：条目被迭代跳过，但仍
    保留在容器中、注册顺序不变，调用对应的 ``enable_*`` 方法可恢复。
    """
    by: str | None
    """来源或所有者标识，供 ``disable_by_owner`` / ``enable_by_owner`` /
    ``remove_by_owner`` 按来源批量操作时精确匹配。允许 ``None``：按
    ``item.by == owner`` 判断相等时 ``None`` 与 ``None`` 也相等，因此
    ``disable_by_owner(None)`` 一类调用能命中注册时未提供来源的条目。
    """
    tags: list[str]
    """标签列表（``list[str]``，可为空、可含多个），供
    ``disable_by_tag`` / ``enable_by_tag`` / ``remove_by_tag`` 按标签
    批量操作时匹配。匹配规则：列表中任一元素与给定标签相等即命中。
    """


Tg = TypeVar("Tg", bound=Togglable)


class ManagedList(Generic[Tg]):
    """按标签或来源批量管理一组可开关条目的容器。

    .. rubric:: 功能介绍

    容器维护一批条目的顺序，提供整组操作：``append`` 追加条目、
    ``insert`` 在任意位置插入条目；
    ``disable_by_tag`` / ``enable_by_tag`` / ``remove_by_tag`` 按标签
    批量操作；``disable_by_owner`` / ``enable_by_owner`` /
    ``remove_by_owner`` 按来源批量操作；``__iter__`` 按列表顺序产出
    当前启用的条目。框架内 :class:`flowing.hooks.HookList` 与
    :class:`flowing.context.PromptBlockList` 以它为基础；使用者也可以
    直接使用本类管理自己的条目组。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.lists import ManagedList

        class Item:                            # 满足 Togglable 即可
            def __init__(self, by=None, tags=None, enabled=True):
                self.by = by
                self.tags = tags or []
                self.enabled = enabled

        entries = ManagedList()
        entries.append(Item(by="guard-a", tags=["guardrail"]))
        entries.append(Item(by="guard-b", tags=["guardrail"]))
        entries.disable_by_tag("guardrail")    # 返回 2：两条全部停用
        entries.disable_by_tag("guardrail")    # 返回 0：已停用不重复计数
        entries.enable_by_tag("guardrail")     # 返回 2：两条全部恢复
        entries.remove_by_owner("guard-a")     # 返回 1：彻底移除

    .. rubric:: 行为要点

    - 批量操作只作用于当前容器实例，不影响其它容器：每个容器各自
      维护自己的条目与状态。
    - 停用（``disable_*``）只把条目的 ``enabled`` 字段置为 ``False``：
      条目仍保留在容器中、注册顺序不变，之后调用对应的 ``enable_*``
      可恢复。恢复同样只改字段，不移动条目。
    - 删除（``remove_*``）把匹配条目从容器中彻底移除，之后无法恢复。
    - 标签匹配：条目的 ``tags`` 列表中任一元素与给定标签相等即命中。
    - 来源匹配：``item.by == owner`` 精确相等；``None`` 与 ``None``
      也相等，因此 ``remove_by_owner(None)`` 能命中注册时未提供来源
      的条目。
    - 停用与恢复只对状态实际发生变化的条目计数：已处于目标状态的
      匹配条目不产生任何变化、不计入返回值，重复调用第二次返回 0。
    - 删除对全部匹配条目计数，与条目当前是否停用无关；没有匹配条目
      时返回 0 且容器内容不变。
    - 容器内条目的相对顺序由 ``append`` / ``insert`` 共同决定（纯
      ``append`` 时等于注册顺序），``disable_*`` / ``enable_*`` 不
      改变顺序。

    .. seealso:: :class:`Togglable`、:class:`flowing.hooks.HookList`、
        :class:`flowing.context.PromptBlockList`
    """

    _items: list[Tg]
    """内部存储，保存全部条目（含已停用条目）。以下划线开头，不属
    稳定契约；读取活跃条目请用迭代（自动跳过停用条目）。
    """

    def __init__(self) -> None:
        """创建一个空容器。"""
        self._items = []

    def append(self, item: Tg) -> Tg:
        """把一个条目追加到容器末尾，并返回该条目本身。

        .. rubric:: 功能介绍

        追加后条目参与后续的批量操作与迭代。容器内条目的迭代顺序即
        注册顺序——框架内钩子分发、上下文组装等消费路径都按这个顺序
        处理条目。

        :return: 被追加的条目本身，便于链式使用。

        .. rubric:: 行为要点

        - 不做去重：同一个条目对象可以多次追加，每次追加都会在容器
          中产生一个位置，迭代与批量操作各处理一次。
        - 本方法对子类形态不做约束：子类可把 append 特化为「构造并
          追加」的工厂式签名（如 :class:`flowing.context.PromptBlockList`
          的 ``append(name, content, ...)``）；此时基类的元素式
          ``append(item)`` 在该子类上不可用，属有意为之。

        .. seealso:: :class:`flowing.hooks.HookList`、
            :class:`flowing.context.PromptBlockList`
        """
        self._items.append(item)
        return item  # 返回追加的条目本身（不变量：新条目始终是容器最后一个元素）

    def insert(self, index: int, item: Tg) -> Tg:
        """把一个条目插入到容器的任意位置，并返回该条目本身。

        .. rubric:: 功能介绍

        :meth:`append` 的任意位置版本：条目进入 ``index`` 指定的位置，
        其后既有条目顺移一位。下标语义同 ``list.insert``：负下标从尾部
        倒数，越界下标向两端收敛（过小插到最前、过大追加到尾部）。
        插入后条目参与后续的批量操作与迭代。

        .. rubric:: 使用示例

        .. code-block:: python

            entries.insert(0, Item(by="core"))       # 插到最前
            entries.insert(1, Item(by="md-prompt"))  # 插到第二位

        :param index: 插入位置（可为负，语义同 ``list.insert``）。
        :return: 被插入的条目本身，便于链式使用。

        .. rubric:: 行为要点

        - 插入后容器的条目顺序由 ``append`` 与 ``insert`` 共同决定——
          不再恒等于注册顺序；迭代与消费路径按列表新顺序处理条目。
        - 与 :meth:`append` 一样不做去重：同一个条目对象可多次插入。
        - 本方法对子类形态不做约束：子类可把 insert 特化为「构造并
          插入」的工厂式签名（如 :class:`flowing.context.PromptBlockList`
          的 ``insert(index, name, content, ...)``）；此时基类的元素式
          ``insert(index, item)`` 在该子类上不可用，属有意为之。

        .. seealso:: :meth:`append`
        """
        self._items.insert(index, item)  # list.insert 语义：负下标倒数、越界向两端收敛
        return item  # 返回插入的条目本身

    def disable_by_tag(self, tag: str) -> int:
        """把 ``tags`` 列表中含有 ``tag`` 的全部条目从启用改为停用。

        :param tag: 要匹配的标签；条目的 ``tags`` 列表中任一元素与它
            相等即命中。
        :return: 本次实际从启用变为停用的条目数；没有匹配条目时返回 0。

        .. rubric:: 行为要点

        停用只修改条目的 ``enabled`` 字段（置为 ``False``）：条目仍保留
        在容器中、注册顺序不变，之后调用 ``enable_by_tag(tag)`` 可恢复。
        已处于停用状态的条目不受影响、也不会被本次调用计数：重复调用
        同一个 ``tag`` 不改变任何状态，第二次调用返回 0。

        .. seealso:: :meth:`enable_by_tag`、:meth:`remove_by_tag`
        """
        count = 0
        for item in self._items:  # 遍历全部条目（含已停用者），只处理当前启用的匹配条目
            if item.enabled and tag in item.tags:  # 已停用者跳过，保证「本次新停用」的计数口径
                item.enabled = False  # 只改 enabled 字段：条目保留原位、顺序不变，之后可恢复
                count += 1
        return count

    def enable_by_tag(self, tag: str) -> int:
        """把 ``tags`` 列表中含有 ``tag`` 的全部停用条目恢复为启用。

        :param tag: 要匹配的标签；条目的 ``tags`` 列表中任一元素与它
            相等即命中。
        :return: 本次实际从停用变为启用的条目数；没有匹配条目时返回 0。

        .. rubric:: 行为要点

        恢复只修改条目的 ``enabled`` 字段（置为 ``True``），不移动条目，
        迭代顺序保持注册顺序。已处于启用状态的条目不受影响、也不会被
        本次调用计数：重复调用同一个 ``tag`` 第二次返回 0。

        .. seealso:: :meth:`disable_by_tag`、:meth:`remove_by_tag`
        """
        count = 0
        for item in self._items:
            if not item.enabled and tag in item.tags:  # 已启用者跳过，保证「本次新恢复」的计数口径
                item.enabled = True  # 恢复且保持原注册顺序
                count += 1
        return count

    def remove_by_tag(self, tag: str) -> int:
        """把 ``tags`` 列表中含有 ``tag`` 的全部条目从容器中彻底移除。

        :param tag: 要匹配的标签；条目的 ``tags`` 列表中任一元素与它
            相等即命中。
        :return: 被移除的条目数；没有匹配条目时返回 0。

        .. rubric:: 行为要点

        移除是彻底的：条目不再保留在容器中，之后无法经
        ``enable_by_tag(tag)`` 恢复。移除对全部匹配条目生效，与条目
        当前是否停用无关；没有匹配条目时容器内容不变。

        .. seealso:: :meth:`disable_by_tag`
        """
        kept: list[Tg] = []
        removed = 0
        for item in self._items:
            if tag in item.tags:  # 匹配即移除（不区分启用 / 停用状态）
                removed += 1
            else:
                kept.append(item)
        self._items = kept
        return removed

    def disable_by_owner(self, owner: str | None) -> int:
        """把 ``by`` 与 ``owner`` 相等的全部条目从启用改为停用。

        :param owner: 要匹配的来源标识；按 ``item.by == owner`` 精确相等
            判断，``None`` 与 ``None`` 也相等。
        :return: 本次实际从启用变为停用的条目数；没有匹配条目时返回 0。

        .. rubric:: 行为要点

        停用只修改条目的 ``enabled`` 字段（置为 ``False``）：条目仍保留
        在容器中、注册顺序不变，之后调用 ``enable_by_owner(owner)`` 可
        恢复。已处于停用状态的条目不受影响、也不会被本次调用计数：
        重复调用同一个 ``owner`` 第二次返回 0。

        .. seealso:: :meth:`enable_by_owner`、:meth:`remove_by_owner`
        """
        count = 0
        for item in self._items:
            if item.enabled and item.by == owner:  # 按来源精确相等匹配（含 None 与 None），已停用者跳过
                item.enabled = False  # 只改 enabled 字段：条目保留原位、顺序不变，之后可恢复
                count += 1
        return count

    def enable_by_owner(self, owner: str | None) -> int:
        """把 ``by`` 与 ``owner`` 相等的全部停用条目恢复为启用。

        :param owner: 要匹配的来源标识；按 ``item.by == owner`` 精确相等
            判断，``None`` 与 ``None`` 也相等。
        :return: 本次实际从停用变为启用的条目数；没有匹配条目时返回 0。

        .. rubric:: 行为要点

        恢复只修改条目的 ``enabled`` 字段（置为 ``True``），不移动条目，
        迭代顺序保持注册顺序。已处于启用状态的条目不受影响、也不会被
        本次调用计数：重复调用同一个 ``owner`` 第二次返回 0。

        .. seealso:: :meth:`disable_by_owner`、:meth:`remove_by_owner`
        """
        count = 0
        for item in self._items:
            if not item.enabled and item.by == owner:  # 已启用者跳过，保证「本次新恢复」的计数口径
                item.enabled = True  # 恢复且保持原注册顺序
                count += 1
        return count

    def remove_by_owner(self, owner: str | None) -> int:
        """把 ``by`` 与 ``owner`` 相等的全部条目从容器中彻底移除。

        :param owner: 要匹配的来源标识；按 ``item.by == owner`` 精确相等
            判断，``None`` 与 ``None`` 也相等。
        :return: 被移除的条目数；没有匹配条目时返回 0。

        .. rubric:: 行为要点

        移除是彻底的：条目不再保留在容器中，之后无法经
        ``enable_by_owner(owner)`` 恢复。移除对全部匹配条目生效，与条目
        当前是否停用无关；没有匹配条目时容器内容不变。

        .. seealso:: :meth:`disable_by_owner`
        """
        kept: list[Tg] = []
        removed = 0
        for item in self._items:
            if item.by == owner:  # 匹配即移除（不区分启用 / 停用状态）
                removed += 1
            else:
                kept.append(item)
        self._items = kept
        return removed

    def __iter__(self) -> Iterator[Tg]:
        """按注册顺序迭代当前启用的条目，自动跳过停用条目。

        :return: 产出 ``enabled`` 为 ``True`` 的条目，顺序与注册顺序一致。

        .. rubric:: 行为要点

        停用条目（``enabled=False``）仍保留在容器中，但不参与迭代。
        消费活跃条目的路径（钩子分发、上下文组装等）都经由本迭代读取；
        直接读取内部存储（``_items``）会绕过停用跳过逻辑，扩展实现请
        经由本迭代读取活跃条目。

        .. seealso:: :class:`Togglable`
        """
        for item in self._items:
            if item.enabled:  # 只产出启用条目——停用条目不参与消费由本迭代统一保障
                yield item
