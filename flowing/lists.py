"""flowing.lists —— 分组管理公共容器抽象（``ManagedList`` / ``Togglable``）。

.. rubric:: 功能介绍

本模块承载「一批带来源与标签的可开关条目」的统一管理语义：``append``
追加；``disable_*`` / ``enable_*`` / ``remove_*`` 按 tag 或 owner 批量
操作；``__iter__`` 自动跳过 disabled 元素。框架核心层、零依赖叶子模块。

**两个家族的分界（定稿叙事）**：本抽象服务「实例级有序内容/行为流」
家族——附着在单个 Agent 实例上、顺序有语义（dispatch 顺序 / 拼接顺序）、
运行期增删与分组批量管理是常态——真实使用方是
:class:`flowing.hooks.HookList` 与 :class:`flowing.context.PromptBlockList`。
能力绑定条目（Tool / Subagent / Skill 的 Entry）走另一家族模式：别名键
dict + 全局注册表双层结构、``enabled`` 点开关——Entry 类型的
``enabled`` / ``by`` / ``tags`` 字段语义**呼应** :class:`Togglable`
契约，但容器不经 ``ManagedList`` 管理（无分组批量操作的消费方；
将来出现真实需求时，Agent 级 dict 换 ManagedList 子类是向后兼容的
局部替换）。

.. rubric:: 模块历史

本模块由 ``flowing.hooks`` 抽出（C1 裁决）：``ManagedList`` /
``Togglable`` 是通用容器抽象而非钩子专有，物理位置与依赖方向
因此一致（hooks 与 context 都向本模块 import）。旧引用路径
``flowing.hooks.ManagedList`` 经 re-export 保持有效。
"""

from collections.abc import Callable, Iterator
from typing import Any, Generic, Protocol, TypeAlias, TypeVar

__all__ = ["ManagedList", "Togglable"]

T = TypeVar("T")

class Togglable(Protocol):
    """``ManagedList`` 的元素契约（Protocol）。

    .. rubric:: 功能介绍

    ``ManagedList`` 容器只关心元素的三个属性：``enabled`` / ``by`` / ``tags``，
    其余字段一概不感知。满足本 Protocol 即可被 ``ManagedList`` 管理。

    .. rubric:: 设计动机

    HookList 与 PromptBlockList 曾各自描述一套同构的分组管理 API；提炼为
    Protocol + 泛型容器后两处共享同一实现与同一语义，避免描述漂移。
    Tool / Subagent / Skill 的 Entry 类型有 ``enabled`` 字段、元素语义呼应
    本契约，但其容器是别名键 dict、不经 ``ManagedList`` 管理（两家族分界
    见模块 docstring）。

    .. rubric:: 行为规约

    - ``enabled`` 必须可被容器**原地读写**（disable/enable 直接翻转该字段，
      元素保留原位）。
    - ``by`` 为来源 / 所有者单一标识；允许 ``None``（注册时省略 ``by`` 的条目），
      ``by=None`` 的条目可被 ``xxx_by_owner(None)`` 命中（不推荐依赖此行为）。
    - ``tags`` 定稿为 ``list[str]``（可空、可多个）；匹配按相等判断。
    - 本 Protocol 是结构契约，不要求显式继承。

    .. rubric:: 调用关系（审计）

    - 被调：无（结构契约 Protocol，框架内无调用方；``ManagedList`` 的
      ``TypeVar`` bound 引用本 Protocol）
    - 实例化方：无（Protocol 不要求显式继承，无直接实例化）

    .. seealso::

        :class:`ManagedList`、:class:`HookEntry`、
        :class:`flowing.tool.ToolEntry`、``flowing.plugins.skills.SkillEntry``
    """

    enabled: bool
    """启用标记。``True`` 参与 ``__iter__`` 与 dispatch；``False`` 被跳过但元素
    保留原位，``enable_*`` 可恢复且保持原注册顺序。
    """
    by: str | None
    """来源 / 所有者标识，供 ``disable_by_owner`` / ``enable_by_owner`` /
    ``remove_by_owner`` 精确匹配。
    """
    tags: list[str]
    """任意标签列表，供 ``disable_by_tag`` / ``enable_by_tag`` /
    ``remove_by_tag`` 匹配（任一 tag 命中即算命中）。
    """


Tg = TypeVar("Tg", bound=Togglable)


class ManagedList(Generic[Tg]):
    """分组管理公共容器抽象。

    .. rubric:: 功能介绍

    框架核心层。为「一批带来源与标签的可开关条目」提供统一管理语义：
    ``append`` 追加；``disable_*`` / ``enable_*`` / ``remove_*`` 按 tag 或
    owner 批量操作；``__iter__`` 自动跳过 disabled 元素。
    真实使用方：:class:`HookList` 与
    ``flowing.context.PromptBlockList``（两家族分界见模块 docstring）。

    .. rubric:: 设计动机

    disable 与 remove 必须区分：Live2D 控制器断开时要「一次性停用它注册的
    全部钩子、稍后可恢复」，而不是逐个查找引用删除——disable 翻转标记留位，
    enable 恢复且保持原注册顺序，remove 才是彻底删除。

    .. rubric:: 使用示例

    .. code-block:: python

        # 注册时统一打 l2d 标签
        self.hooks.before_turn(fast_path, by="l2d-fast-path", tags=["l2d"])
        self.hooks.after_turn(animation, by="live2d", tags=["l2d", "animation"])

        def on_live2d_disconnected(self):
            self.hooks.before_turn.disable_by_tag("l2d")
            self.hooks.after_turn.disable_by_tag("l2d")

    .. rubric:: 行为规约

    - **批量操作只作用于当前容器实例**：不存在「跨钩子点的全局
      disable_by_tag」——每个钩子点是独立的 ``ManagedList``，需在注册了
      handler 的每个容器上分别调用。
    - ``disable_*`` 只翻转 ``enabled``，元素保留原位、原顺序；
      ``enable_*`` 恢复且保持原注册顺序；``remove_*`` 彻底删除。
    - 六个批量操作方法返回**受影响元素数**（int）。
    - tag 匹配语义：元素的 ``tags`` 中任一等于给定 tag 即命中；
      owner 匹配语义：``item.by == owner`` 精确相等（含 ``None == None``）。
    - ``__iter__`` 自动跳过 ``enabled=False`` 的元素——遍历活跃元素时无需
      ``if item.enabled`` 守卫。dispatch、上下文组装等所有「消费活跃元素」的
      路径都经由迭代，因此 disabled 元素对运行路径完全不可见。
    - 不变量：元素在容器内的相对顺序等于注册顺序，disable/enable 不改变顺序。

    .. rubric:: 测试案例

    - 前置：容器含 ``[a(by="x",tags=["t"]), b(by="y",tags=["t"]), c(by="x")]``。
      操作：``disable_by_tag("t")``。期望：返回 ``2``；``list(container)``
      只剩 ``c``；``_items`` 长度仍为 3。
    - 前置：同上操作后。操作：``enable_by_owner("y")``。期望：``b`` 恢复；
      迭代顺序为 ``a(disabled 跳过)`` → ``b`` → ``c``，即 ``[b, c]``。
    - 前置：同上。操作：``remove_by_owner("x")``。期望：返回 ``2``；
      ``_items`` 只剩 ``b``。

    .. rubric:: 调用关系（审计）

    - 调用：无
    - 被调：``flowing.hooks.HookList`` 继承（时机：类定义时）；
      ``flowing.context.PromptBlockList`` 继承（时机：类定义时，
      ``class PromptBlockList(ManagedList[PromptBlock])``）；
      ``flowing.__init__`` re-export
    - 实例化方：无直接实例化——经子类 ``HookList`` 由
      ``HookRegistry.__init__`` / ``declare`` 间接实例化（时机：
      每次创建 Agent 实例 / 扩展声明钩子点时）

    .. seealso::

        :class:`Togglable`、:class:`HookList`、
        :class:`flowing.context.PromptBlockList`
    """

    _items: list[Tg]
    """内部存储（含 disabled 元素）。内部 API，不属稳定契约；外部应通过
    ``__iter__``（跳过 disabled）或 ``__getitem__``（HookList 的索引形态）
    访问。
    """

    def append(self, item: Tg) -> Tg:
        """追加一个元素到容器末尾。

        .. rubric:: 功能介绍 / 设计动机

        统一的注册落点：``HookList.__call__``、``PatternRegistrar.__call__``
        等注册入口最终都经本方法落库，保证「注册顺序即执行顺序」。

        .. rubric:: 行为规约

        - 元素追加到末尾；返回被追加的元素本身（便于链式读取 entry）。
        - 不做去重：同一 handler 重复注册即执行多次。
        - 后置条件：``_items[-1] is item``。
        - 子类特化（C-11 裁决）：本类**不对子类 append 形态做约束**——
          子类可改为工厂式签名（如
          ``PromptBlockList.append(name, content, *, cache, by, tags)``，
          spec-draft 06 §18.1 的全部实际示例均为工厂式）；此时父类
          元素式接口在该子类上不可用属有意为之。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.hooks.HookList.__call__`` 与
          ``flowing.hooks.PatternRegistrar.__call__``（时机：每次注册
          handler，统一注册落点——见本方法 docstring「功能介绍」）

        .. seealso::

            :meth:`HookList.__call__`、:meth:`PatternRegistrar.__call__`
        """
        self._items.append(item)
        # -> item（后置条件：``_items[-1] is item``，见行为规约）

    def disable_by_tag(self, tag: str) -> int:
        """将 tags 含 ``tag`` 的全部元素置为 disabled（留位，可恢复）。

        .. rubric:: 行为规约

        只翻转 ``enabled=False``，不删除、不改变顺序；对已是 disabled 的元素
        幂等；返回本次新置为 disabled 的元素数。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（框架内未见调用；类 docstring 示例为用户代码）

        .. seealso::

            :meth:`enable_by_tag`、:meth:`remove_by_tag`
        """
        count = 0
        for item in self._items:  # 遍历含 disabled 的全量底层列表
            if item.enabled and tag in item.tags:  # 条件：tags 任一命中且当前 enabled（幂等）
                item.enabled = False  # 原地翻转标记，留位可恢复、不改变顺序
                count += 1
        return count

    def enable_by_tag(self, tag: str) -> int:
        """恢复 tags 含 ``tag`` 的全部 disabled 元素。

        .. rubric:: 行为规约

        恢复原注册顺序；对已 enabled 的元素幂等；返回本次新恢复的元素数。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（框架内未见调用）

        .. seealso::

            :meth:`disable_by_tag`
        """
        count = 0
        for item in self._items:
            if not item.enabled and tag in item.tags:  # 条件：tags 命中且当前 disabled（幂等）
                item.enabled = True  # 恢复且保持原注册顺序
                count += 1
        return count

    def remove_by_tag(self, tag: str) -> int:
        """彻底删除 tags 含 ``tag`` 的全部元素。

        .. rubric:: 行为规约

        物理删除，不可经 ``enable_*`` 恢复；返回删除的元素数。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（框架内未见调用）

        .. seealso::

            :meth:`disable_by_tag`
        """
        kept: list[Tg] = []
        removed = 0
        for item in self._items:
            if tag in item.tags:  # 条件：tags 任一命中 → 物理删除
                removed += 1
            else:
                kept.append(item)
        self._items = kept
        return removed

    def disable_by_owner(self, owner: str | None) -> int:
        """将 ``by == owner`` 的全部元素置为 disabled。

        .. rubric:: 行为规约

        精确相等匹配；``owner=None`` 命中注册时省略 ``by`` 的条目。
        语义与 :meth:`disable_by_tag` 对称（翻转标记、留位、幂等、返回计数）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（框架内未见调用；HookList 类 docstring 示例为用户代码）

        .. seealso::

            :meth:`disable_by_tag`、:class:`Togglable`
        """
        count = 0
        for item in self._items:
            if item.enabled and item.by == owner:  # 条件：by 精确相等（含 None == None）且当前 enabled
                item.enabled = False  # 原地翻转标记，留位可恢复
                count += 1
        return count

    def enable_by_owner(self, owner: str | None) -> int:
        """恢复 ``by == owner`` 的全部 disabled 元素。语义同 :meth:`enable_by_tag`。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（框架内未见调用）

        .. seealso:: :meth:`enable_by_tag`、:meth:`disable_by_owner`
        """
        count = 0
        for item in self._items:
            if not item.enabled and item.by == owner:  # 条件：by 精确相等且当前 disabled（幂等）
                item.enabled = True  # 恢复且保持原注册顺序
                count += 1
        return count

    def remove_by_owner(self, owner: str | None) -> int:
        """彻底删除 ``by == owner`` 的全部元素。语义同 :meth:`remove_by_tag`。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.composables.retry`` 文档示例以
          ``remove_by_owner("retry")`` 整组替换默认重试策略（时机：
          用户替换策略时；框架内仅文档级引用，见 retry.pyi seealso）

        .. seealso:: :meth:`remove_by_tag`、:meth:`disable_by_owner`
        """
        kept: list[Tg] = []
        removed = 0
        for item in self._items:
            if item.by == owner:  # 条件：by 精确相等 → 物理删除
                removed += 1
            else:
                kept.append(item)
        self._items = kept
        return removed

    def __iter__(self) -> Iterator[Tg]:
        """迭代活跃元素（自动跳过 ``enabled=False``）。

        .. rubric:: 行为规约

        - 按注册顺序产出 ``enabled=True`` 的元素。
        - dispatch、PromptBlock 组装等所有消费路径必须经此迭代，不得直接遍历
          ``_items``——这是「disabled 元素对运行路径不可见」不变量的唯一
          保障点。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.hooks.HookList.dispatch``（时机：每次
          dispatch，迭代活跃条目）；``flowing.context`` 的 PromptBlock
          组装路径（时机：每次上下文组装——见本类行为规约「所有消费
          活跃元素的路径都经由迭代」）

        .. seealso::

            :class:`Togglable`、:meth:`HookList.dispatch`
        """
        for item in self._items:
            if item.enabled:  # 条件：跳过 enabled=False（「disabled 不可见」不变量的唯一保障点）
                yield item
