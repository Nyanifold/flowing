"""``flowing.context`` —— 上下文组装：system prompt 分层注册、求值与组装输出。

.. rubric:: 功能介绍

本模块定义「system prompt 分层注册 → 惰性求值 → Provider adapter 消费」
这条链路上的全部数据结构：

- :class:`PromptBlock`：一段 system prompt 的注册声明。记录惰性内容
  （``content`` 为 :class:`flowing.parsable.Parsable`，注册时不解析）与
  管理元数据（``cache`` / ``tags`` / ``by`` / ``enabled``）。
- :class:`PromptBlockList`：Agent 级有序容器（每个 Agent 实例一个，即
  ``agent.prompt_blocks``），语义继承 :class:`flowing.lists.ManagedList`。
- :class:`PromptSegment`：单个块在一次组装中求值后的产物（已解析字符串
  + cache 标记），是 :class:`Context` 的 ``system_prompt`` 元素类型。
- :class:`Context`：组装输出，``Provider.generate()`` 的唯一输入类型。
- :class:`ContextUsageEstimate`：当前上下文占用的估算快照（锚点实测 +
  本地估算）。

本模块只定义数据结构与同步容器操作：上下文组装不 await 任何东西
（``Parsable.resolve()`` 是同步惰性求值，消息沿 ``parent_id`` 链的收集
是纯内存遍历），全部类与方法都是同步的。组装动作本身由
``flowing.agent.Agent`` 完成，本模块只规约输出的形状。

:class:`Context` 是三个正交字段的组合，不是扁平消息列表——adapter
不得把三个字段合并成单一消息流后再自行拆分：

.. list-table::
   :header-rows: 1

   * - 字段
     - 内容
   * - ``system_prompt``
     - 分层 prompt 段（:class:`PromptSegment` 列表，顺序 = 块注册顺序、
       跳过已停用块），保留 cache 标记供 adapter 做缓存优化
   * - ``tools``
     - 当前启用工具的定义列表（:class:`flowing.tool.ToolDefinition`），
       独立存在、不混进消息
   * - ``messages``
     - 消息级树上从根到 ``current_head_id`` 的上溯路径
       （:class:`flowing.message.Message` 列表，根→head 顺序）

``cache`` 是框架提供给 Provider adapter 的意图标记，取值 ``"static"`` /
``"dynamic"`` / ``"session"``：``static`` 表示字节稳定、适合前缀缓存
（如文件引用块）；``dynamic`` 表示每轮可能变化；``session`` 表示会话
启动渲染一次、会话内不变。框架本地不实现任何缓存策略——每次
``provider_gen`` 前都现场组装、现场求值，``cache`` 只作意图通道，是否
缓存、如何缓存由 adapter / 服务端决定。

.. rubric:: 使用示例

``setup()`` 中注册 prompt 块（动态块 + 文件引用块）：

.. code-block:: python

    from flowing import Parsable

    async def setup(self):
        # 动态块：每次组装现场求值，引用当前实例属性
        self.prompt_blocks.append(
            "session-info",
            Parsable("当前模式：{{ current_mode }}"),
            cache="dynamic",
            by="session",
            tags=["session"],
        )
        # 静态块：引用文件，字节稳定，适合前缀缓存
        self.prompt_blocks.append(
            "mode-plan",
            Parsable("$./prompts/plan-mode.md"),
            cache="static",
            by="mode-switcher",
            tags=["mode-plan"],
        )

``.fya`` 入口：``system_prompt`` 字段不需要注册块——它经
``PromptBlockList[0]`` 惰性引用块自动进入（见 :class:`PromptBlockList`
docstring）。

.. rubric:: 行为要点

- 现场求值、零缓存：每次组装重新求值所有 Parsable、重新收集消息路径、
  重新生成工具定义；框架不缓存组装结果。这保证环境变量、实例属性、
  模式状态永远取最新值（惰性求值是功能正确性前提，不是性能优化）。
- 每次组装产生全新对象与全新列表，两次组装的 ``Context`` 互不共享
  可变状态。
- 半截 turn 不截断：崩溃恢复后，最后一条 ``turn_end=True`` 之后已落盘
  的半截 turn 消息照常进入 ``messages`` （它们是已产生的完整历史）；
  执行状态不恢复、不续跑。
- 成对匹配：同一分支上 PROVIDER 消息内 ``ToolCallBlock.id`` 与后续
  TOOL 消息的 ``tool_call_id`` 字段 1:1 严格成对；恢复时合成的
  ``synthetic=True`` 占位 TOOL 消息封闭孤立 tool_call，adapter 不需要
  （也不应该）自行修补。
- 副线消息不出现：``side_query`` 副线调用的消息不进树、不落盘，不会
  出现在主流程组装的 ``messages`` 中。
- 组装结果可改写：先经 ``before_provider_gen`` 钩子（value 即
  :class:`Context`，handler 可整体改写三个字段）再交给
  ``Provider.generate()``。
- SYSTEM 注入双通道：本模块通道（``prompt_blocks.append(...)`` 注册的块
  在每次组装时进入 ``system_prompt``，不触发新回合，适合每轮自动追加的
  上下文）；消息队列通道（``enqueue_message`` 投递 ``SYSTEM`` 消息进入
  消息历史，触发新回合，适合需要独立处理的通知）。两条通道见
  :class:`flowing.message.Message` 与
  :meth:`flowing.agent.Agent.enqueue_message`。

.. seealso::

    :class:`flowing.parsable.Parsable`
        ``PromptBlock.content`` 的类型；惰性求值与两步渲染规约。
    :class:`flowing.lists.ManagedList`
        ``PromptBlockList`` 遵循的公共容器抽象与元素契约。
    :class:`flowing.message.Message`
        ``Context.messages`` 的元素类型。
    :class:`flowing.tool.ToolDefinition`
        ``Context.tools`` 的元素类型。
    :meth:`flowing.providers.Provider.generate`
        :class:`Context` 的最终消费者。
    :meth:`flowing.agent.Agent._assemble_context`
        组装动作的承担方（内部 API，不属稳定契约）。
"""

from __future__ import annotations  # 注解延迟求值（ToolDefinition 仅注解级引用）

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from flowing.lists import ManagedList
from flowing.message import Message
from flowing.parsable import Parsable

if TYPE_CHECKING:
    # 只做注解级引用（dataclass 字段注解惰性求值，运行期不需要真实类）
    from flowing.tool import ToolDefinition


@dataclass
class PromptBlock:
    """system prompt 分层的注册声明——记录惰性内容与管理元数据，注册时不求值。

    .. rubric:: 功能介绍

    框架核心层数据结构。一个 ``PromptBlock`` 是「一段 system prompt 的注册
    声明」：``content`` 是惰性内容（:class:`flowing.parsable.Parsable`），
    ``cache`` / ``tags`` / ``by`` / ``enabled`` 是管理元数据。注册时不解析
    内容，直到每次组装（``Agent._assemble_context()``）现场求值，产出
    :class:`PromptSegment`。

    system prompt 从「一段完整文本」变为「按注册顺序拼接的分层片段」，使
    框架骨架（``by="core"``）、内置扩展与应用代码能各自注册、各自管理自己
    的片段，互不感知。内容用 ``Parsable`` 承载是因为惰性求值是功能正确性
    的前提：注册时环境变量、配置值、实例属性可能尚不存在，只有使用时才能
    解析。

    .. rubric:: 使用示例

    ``setup()`` 中注册（``.fya`` 的 ``$script`` 或手写子类中相同）：

    .. code-block:: python

        async def setup(self):
            self.prompt_blocks.append(
                "session-info",
                Parsable("当前模式：{{ current_mode }}"),
                cache="dynamic",
                by="session",
                tags=["session"],
            )

    .. rubric:: 行为要点

    - ``content`` 在每次组装时现场求值；两次 ``provider_gen`` 之间实例属性
      的变化会在下一次组装中反映。
    - ``enabled=False`` 的块保留在列表原位，被迭代与组装跳过；重新启用后
      恢复原注册顺序位置。
    - 块的内容应当求值为 ``str`` （字面量 / 文件引用 / 混合模板均如此）；
      使用纯表达式形式求值出非 ``str`` 结果属使用错误，
      :class:`PromptSegment` 的 ``content`` 类型契约是 ``str``。
    - 注册时不求值、不校验内容合法性（文件是否存在、模板变量是否存在都
      在求值时才暴露）。
    - 框架不根据 ``cache`` 做任何本地缓存或跳过求值。
    - 块之间不自动插入分隔符；拼接格式（换行、标题层级）由块内容自带或
      adapter 决定。
    - ``tags=None`` 归一化为空列表；``by`` 省略时为空串 ``""``。
    - 同名块允许共存（``name`` 是标识与排查用标签，不是唯一键）；需要唯一
      定位时用 ``tags`` / ``by`` 分组管理。

    .. seealso::

        :class:`PromptSegment`
            本类的求值产物。
        :class:`PromptBlockList`
            本类的容器（ManagedList 语义）。
        :class:`flowing.parsable.Parsable`
            ``content`` 的类型与求值规则。
    """

    name: str
    """块名：标识与日志 / 排查用标签，供 :class:`PromptSegment` 的 ``name``
    透传给 adapter 与调试输出。

    行为边界：非唯一键，允许重名；不做格式校验。

    .. seealso:: :attr:`PromptSegment.name`
    """

    content: Parsable
    """惰性内容（:class:`flowing.parsable.Parsable`）：注册时不解析，使块可以
    引用注册时尚不存在的实例属性 / 环境变量 / 配置。

    行为边界：组装时由框架自动 ``resolve()``；用户不应在注册前手动求值
    （那会把动态内容固化成静态字符串）。

    .. seealso:: :class:`flowing.parsable.Parsable`
    """

    cache: Literal["static", "dynamic", "session"]
    """缓存意图标记：给 Provider adapter 的意图通道，三值 ``"static"`` /
    ``"dynamic"`` / ``"session"``，语义见模块 docstring。

    行为边界：框架本地不读、不解释、不据此缓存；adapter 可以忽略它。非法
    取值由 adapter 侧报错，框架核心不校验。

    .. seealso:: :attr:`PromptSegment.cache`
    """

    tags: list[str]
    """分组标签：``enable_by_tag`` / ``disable_by_tag`` / ``remove_by_tag`` 的
    操作目标，支持一块多标签。

    行为边界：框架不枚举合法值；标签是自由字符串。

    .. seealso:: :meth:`PromptBlockList.disable_by_tag`
    """

    by: str = ""
    """来源标识：ManagedList「按属主管理」的操作目标。默认空串 ``""`` （不标
    来源）；约定值 ``"core"`` （框架骨架——框架内部 append 时显式传入，非
    默认值）/ ``"skill"`` （SkillPlugin）/ 应用自定义值。

    行为边界：扩展注册块时必须使用自己插件的 ``by`` 值，否则
    ``disable_by_owner`` / ``remove_by_owner`` 无法正确回收；对 ``"core"``
    属主做 remove 属使用错误（会破坏 ``[0]`` 引用块约定）。

    .. seealso:: :meth:`PromptBlockList.remove_by_owner`
    """

    enabled: bool = True
    """启用开关：ManagedList 可逆开关——``disable`` 只翻本字段、元素保留
    原位，``enable`` 恢复且保持注册顺序。

    行为边界：``False`` 时被迭代与组装跳过；直接赋值 ``block.enabled =
    False`` 与 ``disable_by_*`` 等价。

    .. seealso:: :class:`flowing.lists.ManagedList`
    """


@dataclass
class PromptSegment:
    """prompt 块惰性求值的产物（已解析字符串 + cache 标记）。

    .. rubric:: 功能介绍

    框架核心层数据结构。``PromptSegment`` 是 :class:`PromptBlock` 在一次
    组装中求值后的结果，是 ``Context.system_prompt`` 的元素类型，直接面向
    Provider adapter。声明（``PromptBlock``）与产物分离：管理元数据
    （``tags`` / ``by`` / ``enabled``）对 adapter 无意义、被剥离；cache
    意图与来源名保留，因为 adapter 的缓存优化与调试输出需要它们。每次
    组装产生新的 segment 对象，adapter 不可能持有过期的 prompt。

    .. rubric:: 使用示例

    .. code-block:: python

        for seg in context.system_prompt:
            print(seg.name, seg.content)
            if seg.cache == "static":
                ...   # adapter：把该段标记为可缓存前缀

    .. rubric:: 行为要点

    - ``content`` 是当次组装的解析结果；``system_prompt`` 列表顺序即拼接
      顺序（= 块注册顺序，跳过已停用块）。
    - ``cache`` / ``name`` 从来源块原样透传，组装方不改写。
    - 不携带 ``tags`` / ``by`` / ``enabled``——这些是管理元数据，求值后
      失去意义。
    - 不是缓存条目：框架不因 ``cache="static"`` 而复用上一轮的 segment；
      下一轮组装产生全新对象，内容可能不同（若引用源变了，那正是正确
      行为）。
    - 空列表合法（例如全部块被停用；把框架注入的 ``[0]`` 引用块停用会
      导致 system prompt 消失，属使用错误）。
    - 同名块产生同名 segment，adapter 不得把 ``name`` 当唯一键。

    .. seealso::

        :class:`PromptBlock`
            本类的来源声明。
        :class:`Context`
            本类的容器（``system_prompt`` 字段）。
        :meth:`flowing.providers.Provider.generate`
            最终消费者。
    """

    content: str
    """已解析的 prompt 文本：adapter 直接拼接 / 映射的最终内容。

    行为边界：类型契约是 ``str``；来源块求值为非 ``str`` 属使用错误。

    .. seealso:: :attr:`PromptBlock.content`
    """

    cache: Literal["static", "dynamic", "session"]
    """cache 意图标记，从来源块透传：adapter 缓存优化的依据；框架本地不解释。

    行为边界：三值语义见模块 docstring。

    .. seealso:: :attr:`PromptBlock.cache`
    """

    name: str
    """来源块名，原样透传：调试输出与 adapter 日志的可追踪性。

    行为边界：非唯一键。

    .. seealso:: :attr:`PromptBlock.name`
    """


class PromptBlockList(ManagedList[PromptBlock]):
    """prompt 块的有序容器（ManagedList 语义），Agent 实例属性
    ``prompt_blocks`` 的类型。

    .. rubric:: 功能介绍

    框架核心层容器。每个 Agent 实例持有一个 ``PromptBlockList``：
    ``Agent.__init__`` 注入 ``[0]`` 惰性引用块（``by="core"``），之后
    ``setup()``、Composable、内置扩展按注册顺序追加自己的块。容器语义继承
    :class:`flowing.lists.ManagedList`：条目有 ``enabled`` / ``by`` / ``tags``
    三个管理属性；注册顺序即拼接顺序；``disable_*`` 只翻 ``enabled``、条目
    保留原位可恢复；``remove_*`` 物理删除、后续条目前移、不可逆；迭代自动
    跳过已停用条目；分组操作无匹配不报错。

    ``[0]`` 惰性引用块：指向类属性 ``system_prompt`` 的引用块（内容是
    ``Parsable("{{ self.system_prompt }}")``，不是内容副本）。``system_prompt``
    是 system prompt 内容的唯一数据源：``setup()`` 中改它，下次组装自动
    反映，引用块无需感知变化。``[0]`` 由框架注入（``by="core"``），应用与
    扩展不得移除、替换或在它之前插入元素；``remove_by_owner("core")`` 会
    破坏本约定，属使用错误。

    .. rubric:: 使用示例

    模式切换：切换 system prompt 的激活 section。

    .. code-block:: python

        from flowing import Parsable

        async def setup(self):
            self.prompt_blocks.append(
                "mode-plan", Parsable("$./prompts/plan-mode.md"),
                cache="static", by="mode-switcher", tags=["mode-plan"])
            self.prompt_blocks.append(
                "mode-review", Parsable("$./prompts/review-mode.md"),
                cache="static", by="mode-switcher", tags=["mode-review"])

        def switch_mode(self, mode: str):
            self.prompt_blocks.disable_by_owner("mode-switcher")
            self.prompt_blocks.enable_by_tag(f"mode-{mode}")

    ``.fya`` 入口：``system_prompt`` 字段自动成为 ``[0]`` 引用块的引用源，
    无需手工注册；额外的块在 ``$script`` 的 ``setup()`` 中用同款代码注册。

    .. rubric:: 行为要点

    - 注册顺序即拼接顺序：组装按列表顺序遍历（跳过已停用块），产出的
      ``PromptSegment`` 序列与之严格同序。
    - ``__iter__`` 产出的是块对象本身（非求值产物），求值只发生在组装时。
    - 不用于每 Turn 的内容增删（反模式）：频繁 ``append`` + ``remove_by_tag``
      破坏注册顺序稳定性。动态内容放进 ``PromptBlock.content`` 的模板引用
      里——Parsable 每次组装现场求值，``{{ current_mode }}`` 这类低频变量
      引用自然拿到最新值，不需要增删 block。高频更新值（如当前时间）不应
      进 prompt 块——prompt 变动会破坏 provider 侧前缀缓存；这类信息应走
      消息通道（``before_turn`` 附加式注入，见
      ``flowing.agent.TurnContext.pending_messages``；现成实现见
      :func:`flowing.composables.reminder.use_system_reminder`）。
    - 分组操作（``*_by_tag`` / ``*_by_owner``）无匹配元素时不报错、无操作；
      对已处于目标状态的元素重复操作不产生任何变化。
    - ``remove_*`` 物理删除后后续元素前移——依赖固定下标（除 ``[0]`` 约定
      外）是脆弱的，应用按 ``tags`` / ``by`` 管理。
    - 框架核心永不移除 ``[0]`` 块；应用对 ``by="core"`` 做
      ``remove_by_owner`` 属使用错误。

    .. seealso::

        :class:`flowing.lists.ManagedList`
            公共容器抽象与完整元素契约。
        :class:`PromptBlock`
            元素类型。
        :meth:`flowing.agent.Agent.setup`
            注册块的推荐时机。
    """

    def append(
        self,
        name: str,
        content: Parsable,
        *,
        cache: Literal["static", "dynamic", "session"] = "dynamic",
        by: str = "",
        tags: list[str] | None = None,
    ) -> PromptBlock:
        """构造并追加一个 prompt 块（工厂式签名），返回构造出的块对象。

        .. rubric:: 功能介绍

        以零散参数构造 :class:`PromptBlock` 并追加到列表尾部（注册顺序末尾）。
        这是对父类 ``ManagedList.append`` 的工厂式特化：父类的元素式
        ``append(item)`` 接口在本类不可用（父类 docstring 已声明不对子类 append
        形态做约束）。

        .. rubric:: 使用示例

        .. code-block:: python

            block = self.prompt_blocks.append(
                "session-info",
                Parsable("当前模式：{{ current_mode }}"),
                cache="dynamic",
                by="session",
                tags=["session"],
            )
            block.enabled   # True

        :param name: 块名（非唯一键，见 :class:`PromptBlock`）。
        :param content: 惰性内容（:class:`flowing.parsable.Parsable`）。
        :param cache: 缓存意图标记，三值之一，默认 ``"dynamic"``。
        :param by: 来源标识，默认空串 ``""``。
        :param tags: 分组标签列表，默认 ``None`` （归一化为空列表）。
        :return: 构造出的块对象（已挂入列表）。

        .. rubric:: 行为要点

        - 追加到尾部；``tags=None`` 归一化为 ``[]``；新块 ``enabled=True``。
        - 返回的块已挂入列表，对其字段的后续修改（如 ``enabled``）直接生效。
        - 不求值 ``content``；不检查 ``name`` 唯一性；不检查 ``cache`` 取值
          合法性（非法值由 adapter 侧暴露）。
        - 推荐在 ``setup()`` （或 Composable）中调用：框架对 ``[0]`` 块的注入
          发生在 ``__init__``，早于任何 ``setup()`` 追加，用户 append 的块永远
          在 ``[0]`` 之后。

        .. seealso::

            :class:`PromptBlock`
                构造产物。
            :meth:`disable_by_tag`
                按标签管理已注册块。
        """
        block = PromptBlock(
            name=name, content=content, cache=cache, by=by,
            tags=tags or [],  # tags=None 归一化为 []
        )
        super().append(block)  # 追加到注册顺序末尾（ManagedList.append）
        return block  # 返回构造产物本身（见行为要点）

    def disable_by_tag(self, tag: str) -> int:
        """把 ``tags`` 列表中含有 ``tag`` 的全部块从启用改为停用（可逆，条目
        保留原位）。

        .. rubric:: 功能介绍

        标签是「一组块共同的功能身份」（如 ``"mode-plan"``），启停一组块不应
        依赖各自的位置或名字。

        .. rubric:: 使用示例

        .. code-block:: python

            self.prompt_blocks.disable_by_tag("mode-review")

        :param tag: 要匹配的标签；块的 ``tags`` 列表中任一元素与它相等即命中。
        :return: 命中的块数。

        .. rubric:: 行为要点

        - 只翻 ``enabled`` 字段（置为 ``False``）：块保留原位、原注册顺序不变；
          之后调用 :meth:`enable_by_tag` 可恢复。
        - 已处于停用状态的匹配块不产生任何变化，但仍计入返回值：重复调用同一
          个 ``tag`` 返回相同数值（本类的计数口径是「命中数」，与父类
          ``ManagedList`` 的「本次实际变化数」口径不同）。
        - 无匹配块时不报错、返回 0。
        - 不删除元素、不求值、不触发任何钩子。

        .. seealso::

            :meth:`enable_by_tag`
                逆操作。
            :class:`flowing.lists.ManagedList`
                分组语义来源（计数口径不同，见 :return:）。
        """
        # 匹配数口径（见 docstring 注记）：已 disabled 的匹配块仍计入
        matched = [b for b in self._items if tag in b.tags]
        for b in matched:
            b.enabled = False  # 只翻 enabled，元素保留原位、顺序不变
        return len(matched)

    def enable_by_tag(self, tag: str) -> int:
        """把 ``tags`` 列表中含有 ``tag`` 的全部块从停用改为启用（恢复原注册顺序
        位置）。

        :param tag: 要匹配的标签；块的 ``tags`` 列表中任一元素与它相等即命中。
        :return: 命中的块数（计数口径同 :meth:`disable_by_tag`）。

        .. rubric:: 行为要点

        - 只翻 ``enabled`` 字段（置为 ``True``）：块从未离开列表，恢复后天然
          处于原注册位。
        - 已处于启用状态的匹配块不产生任何变化，但仍计入返回值。
        - 无匹配块时不报错、返回 0。
        - 不改变任何块的内容与顺序。

        .. seealso::

            :meth:`disable_by_tag`
                逆操作。
        """
        # 匹配数口径（同 disable_by_tag 的注记）
        matched = [b for b in self._items if tag in b.tags]
        for b in matched:
            b.enabled = True
        return len(matched)

    def enable_by_owner(self, owner: str) -> int:
        """把 ``block.by == owner`` 的全部块从停用改为启用（恢复原注册顺序位置）。

        :param owner: 要匹配的来源标识；块的 ``by`` 字段与它相等即命中
            （``owner=""`` 命中省略 ``by`` 的块）。
        :return: 命中的块数（计数口径同 :meth:`disable_by_tag`）。

        .. rubric:: 行为要点

        - 只翻 ``enabled``、恢复原注册位。
        - 已处于启用状态的匹配块不产生任何变化，但仍计入返回值。
        - 无匹配块时不报错、返回 0。

        .. seealso::

            :meth:`disable_by_owner`
                逆操作。
        """
        # 匹配数口径（同 disable_by_tag 的注记）；owner="" 命中省略 by 的块
        matched = [b for b in self._items if b.by == owner]
        for b in matched:
            b.enabled = True
        return len(matched)

    def remove_by_tag(self, tag: str) -> int:
        """从列表中物理删除所有 ``tags`` 列表中含有 ``tag`` 的块（不可逆）。

        .. rubric:: 功能介绍

        与 disable 系列互补：disable 是「暂时不用」（可逆、保序），remove 是
        「永久退场」（如扩展卸载、模式机制整体移除）。

        .. rubric:: 使用示例

        .. code-block:: python

            self.prompt_blocks.remove_by_tag("deprecated-feature")

        :param tag: 要匹配的标签；块的 ``tags`` 列表中任一元素与它相等即命中。
        :return: 被删除的块数；无匹配块时返回 0。

        .. rubric:: 行为要点

        - 物理删除，后续元素前移；删除发生在两次组装之间时，下一次组装即生效
          （正在进行的 ``provider_gen`` 不受影响——``Context`` 已组装完毕）。
        - 不级联删除其他 tag 重叠的块之外的任何东西。
        - 不推荐用于每 Turn 的内容清洗（反模式，见 :class:`PromptBlockList`
          类 docstring）。

        .. seealso::

            :meth:`remove_by_owner`
                按属主回收。
            :meth:`disable_by_tag`
                可逆的替代方案。
        """
        return super().remove_by_tag(tag)  # 物理删除语义由 ManagedList.remove_by_tag 承载；返回删除数

    def disable_by_owner(self, owner: str) -> int:
        """把 ``block.by == owner`` 的全部块从启用改为停用（可逆，条目保留原位）。

        .. rubric:: 功能介绍

        属主是「谁注册的」——扩展或 Composable 需要整体停用自己注册的全部块
        时，不应枚举各自的 tag。这也是扩展必须如实填写 ``by`` 的原因。

        :param owner: 要匹配的来源标识；块的 ``by`` 字段与它相等即命中
            （``owner=""`` 命中省略 ``by`` 的块）。
        :return: 命中的块数（计数口径同 :meth:`disable_by_tag`）。

        .. rubric:: 行为要点

        - 只翻 ``enabled``、保留原位、保序；之后调用
          :meth:`enable_by_owner` 可恢复。
        - 已处于停用状态的匹配块不产生任何变化，但仍计入返回值。
        - 无匹配块时不报错、返回 0。
        - 对 ``owner="core"`` 调用属使用错误（会停用 ``[0]`` 引用块，使
          system prompt 消失）。

        .. seealso::

            :meth:`remove_by_owner`
                不可逆版本。
            :attr:`PromptBlock.by`
                属主字段约定。
        """
        # 匹配数口径（同 disable_by_tag 的注记）
        matched = [b for b in self._items if b.by == owner]
        for b in matched:
            b.enabled = False
        return len(matched)

    def remove_by_owner(self, owner: str) -> int:
        """从列表中物理删除所有 ``block.by == owner`` 的块（不可逆）——扩展卸载时
        回收自己注册的全部块的规范通道。

        .. rubric:: 功能介绍

        「谁注册、谁回收」：属主维度比 tag 更贴近扩展的生命周期边界（一个
        扩展可能注册多个不同 tag 的块）。

        .. rubric:: 使用示例

        .. code-block:: python

            # 扩展卸载（插件收尾逻辑中）
            agent.prompt_blocks.remove_by_owner("my-plugin")

        :param owner: 要匹配的来源标识；块的 ``by`` 字段与它相等即命中
            （``owner=""`` 命中省略 ``by`` 的块）。
        :return: 被删除的块数；无匹配块时返回 0。

        .. rubric:: 行为要点

        - 物理删除、后续元素前移。
        - 框架核心永不以 ``"core"`` 为参数调用本方法；应用如此调用属使用错误
          （删除 ``[0]`` 引用块后 system prompt 不再进入上下文，框架不兜底）。

        .. seealso::

            :meth:`disable_by_owner`
                可逆版本。
            :attr:`PromptBlock.by`
                属主字段约定。
        """
        return super().remove_by_owner(owner)  # 物理删除语义由 ManagedList.remove_by_owner 承载；返回删除数

    def __getitem__(self, index: int) -> PromptBlock:
        """按下标访问块（含负数下标的常规序列语义）——``[0]`` 惰性引用块约定的
        承载接口。

        :param index: 下标（可为负）。
        :return: 列表中对应位置的块。

        .. rubric:: 行为要点

        - ``[0]`` 恒为框架注入的 ``system_prompt`` 惰性引用块（``by="core"``）：
          框架注入发生在 ``__init__``、早于一切用户注册，位置由构造顺序结构性
          保证，不需要按名字查找。
        - 下标作用于底层列表，包含已停用块——与 ``__iter__`` 的过滤语义正交。
        - 不支持按名字 / 标签索引（那是 ``*_by_tag`` / ``*_by_owner`` 的职责）。

        .. seealso::

            :class:`PromptBlockList`
                类 docstring 的不变量。
        """
        block = self._items[index]  # 下标作用于底层列表，含 disabled（与 __iter__ 正交）
        return block  # 返回对应位置的块（见行为要点）

    def __iter__(self) -> Iterator[PromptBlock]:
        """迭代已启用的块，顺序 = 注册顺序。

        :return: 产出 ``enabled=True`` 的块对象本身（不求值）。

        .. rubric:: 行为要点

        - 自动跳过已停用块；过滤后保持注册顺序的相对序。组装方
          （``Agent._assemble_context``）依赖本方法遍历。
        - 不是快照：迭代过程中其他代码增删块属使用错误（单进程 asyncio 模型
          下，同步迭代不会被协程切换打断）。

        .. seealso::

            :meth:`__getitem__`
                不过滤 enabled 的访问通道。
            :class:`flowing.lists.ManagedList`
                迭代语义来源。
        """
        it = super().__iter__()  # 跳过 enabled=False、注册顺序（ManagedList.__iter__）
        return it  # -> Iterator[PromptBlock]（产出块对象本身，不求值）


@dataclass
class Context:
    """上下文组装的输出——``Provider.generate()`` 的唯一输入类型。

    .. rubric:: 功能介绍

    由 ``flowing.agent.Agent._assemble_context()`` 在每次 ``provider_gen``
    前现场组装（遍历 ``prompt_blocks`` 跳过已停用块逐块求值、沿
    ``current_head_id`` 的 ``parent_id`` 链收集消息路径、取启用工具的
    ``llm_definition()`` 产物），经 ``before_provider_gen`` 钩子（value 即
    本类，可整体改写）后交给 Provider adapter。三个正交字段：
    ``system_prompt`` （分层 prompt 段，保留 cache 标记）、``tools`` （当前
    启用的工具定义）、``messages`` （根→head 消息路径）。三字段结构让
    adapter 的映射是机械的逐字段转换（kind→role、cache→cache_control、
    ToolDefinition→function 声明），无需从扁平列表猜测结构。

    .. rubric:: 使用示例

    ``before_provider_gen`` 钩子整体改写（应用层）：

    .. code-block:: python

        from flowing import Agent, on
        from flowing.context import PromptSegment

        class MyAgent(Agent):
            @on('before_provider_gen')
            def _attach_trace(self, context):
                context.system_prompt.append(
                    PromptSegment(content=f"请求 ID：{self.node_id}",
                                  cache="dynamic", name="trace"))
                return context

    .. rubric:: 行为要点

    - ``messages`` 为根→head 顺序：从 ``current_head_id`` 沿
      ``Message.parent_id`` 上溯到根后反转；活跃逻辑 turn 执行中，本 turn
      已 append 到树的消息自然在路径上。无活跃 turn（崩溃恢复后、新 turn
      开始前）行为相同。
    - 半截 turn 不截断：最后一条 ``turn_end=True`` 之后已落盘的半截 turn
      消息照常进入 ``messages``；孤立 tool_call 由 ``synthetic=True`` 占位
      TOOL 消息封闭（``tool_call_id`` 等于孤立调用 id、
      ``tool_status="error"``），tool_call / tool_result 严格成对。
    - ``tools`` 只含 ``enabled=True`` 的工具条目经 ``llm_definition()`` 的
      产物，与消息流完全分离。无隐式附加：``subagent-invoke`` /
      ``finish`` 等内置工具同样需用户显式声明（``tools:`` /
      ``add_tool``）才出现。
    - 副线消息不出现：主流程组装的 ``messages`` 中不含 ``side_query`` 副线
      调用的消息（它们不进树、不落盘）。
    - 框架不对本类做 token 计数、窗口裁剪、长度校验——超长由 Provider
      adapter 抛 :class:`flowing.errors.ContextLengthError` 兜底（该异常
      不经过 ``on_provider_error``，直接上抛）。
    - 框架不为本类做 role 映射：kind→API role 是 adapter 的职责。
    - 改写 ``messages`` 不推荐：``before_provider_gen`` 钩子对 ``messages``
      的直接改动不会进入消息树、不会落盘，下一次组装即丢失。内容增删应
      走持久化路径：回合开头的注入用 ``before_turn`` 向
      ``TurnContext.pending_messages`` 附加（随批次挂树落盘）；回合中途的
      追加 / 擦除用 ``MessageChain`` 手术（``insert`` / ``remove``）。
    - 每次组装产生全新对象与全新列表；两次组装互不共享可变状态。
    - 实例不保证线程安全（单进程 asyncio 模型，无此需求）。

    边缘情况：

    - 全新 Agent（无任何消息）：``messages == []``、``system_prompt`` 至少
      含 ``[0]`` 引用块的 segment、``tools`` 可为空列表——三个字段的空列表
      均合法，adapter 不得假设非空。
    - fork 切换 ``current_head_id`` 后，下一次组装自动反映新分支路径——
      fork 是纯上下文操作，不需要额外同步。

    .. seealso::

        :class:`PromptSegment`
            ``system_prompt`` 的元素类型。
        :class:`flowing.tool.ToolDefinition`
            ``tools`` 的元素类型。
        :class:`flowing.message.Message`
            ``messages`` 的元素类型。
        :meth:`flowing.providers.Provider.generate`
            最终消费者。
        :class:`flowing.agent.TurnContext`
            ``pending_messages`` （回合开头附加式注入的载体）。
        :class:`flowing.errors.ContextLengthError`
            上下文超长的兜底异常。
    """

    system_prompt: list[PromptSegment]
    """分层 prompt 段（:class:`PromptSegment` 列表）：保留 cache 标记供 adapter
    缓存优化；顺序 = 块注册顺序（跳过已停用块）。

    行为边界：当次组装的全新求值产物；空列表合法；``before_provider_gen``
    钩子可增删改写。

    .. seealso:: :class:`PromptSegment`
    """

    tools: list[ToolDefinition]
    """当前启用的工具定义（:class:`flowing.tool.ToolDefinition` 列表）：工具声明
    独立于消息流存在，adapter 直接映射为各 API 的 function 声明。

    行为边界：只含 ``enabled=True`` 条目的 ``llm_definition()`` 产物；空
    列表合法。

    .. seealso:: :class:`flowing.tool.ToolDefinition`
    """

    messages: list[Message]
    """根→head 的消息路径（:class:`flowing.message.Message` 列表）：消息级树上
    沿 ``parent_id`` 上溯收集的对话历史，adapter 逐条做 kind→role 映射。

    行为边界：不含副线消息；半截 turn 不截断；tool_call 与其结果消息严格
    成对（配对锚为 PROVIDER 消息内 ``ToolCallBlock.id`` 与 TOOL 消息的
    ``tool_call_id`` 字段），孤立调用由 ``synthetic`` 占位封闭成对，规则见
    类 docstring。

    .. seealso:: :class:`flowing.message.Message`
    """


@dataclass
class ContextUsageEstimate:
    """当前上下文占用的估计——``flowing.agent.Agent.estimate_context_tokens`` 的
    返回类型。

    .. rubric:: 功能介绍

    「现在如果把上下文发给 LLM，大约多大」的快照值：``measured`` 是锚点
    （最近一次 Provider 实测）覆盖的部分，``estimated`` 是锚点之后（或无
    锚点时全段）的本地启发式估算部分，``tokens`` 为两者之和。两部分分开
    承载而非只给一个和——观测方可以区分「多少是实测、多少是猜的」。估算
    部分永不用于计费（计费走 ``TurnResult.token_usage`` 聚合口径，两条数据
    流不混）。

    .. rubric:: 使用示例

    .. code-block:: python

        est = agent.estimate_context_tokens()
        if est.usage_ratio is not None and est.usage_ratio > 0.85:
            ...   # 阈值判断与压缩是插件策略，框架核心只提供本观测值

    .. rubric:: 行为要点

    - 纯数据投影（dataclass）：读取无副作用。
    - ``tokens == (measured or 0) + estimated`` 恒成立（构造方保证）。
    - ``measured is None`` 表示当前路径无有效锚点（全新会话或锚点消息均被
      移除）——此时 ``tokens`` 全部为估算，观测方应视为低置信。
    - 边缘情况：路径为空 → ``tokens == 0``、``measured is None``、
      ``anchor_message_id is None``。
    - ``usage_ratio`` 为 ``tokens / context_window``，窗口未知时为 ``None``；
      不 clamp——大于 1.0 是合法的溢出信号（见 :attr:`usage_ratio`）。

    .. seealso::

        :meth:`flowing.agent.Agent.estimate_context_tokens`
            构造方与估算规则。
        :func:`flowing.message.estimate_message_tokens`
            逐条估算函数。
        :class:`flowing.providers.Usage`
            锚点实测数据的类型。
    """

    tokens: int
    """上下文占用总量估计：``(measured or 0) + estimated``。
    """
    measured: int | None
    """锚点实测部分（锚点 ``Usage.total_tokens``）；无有效锚点为 ``None``。
    """
    estimated: int
    """本地启发式估算部分：锚点之后路径上消息与新增工具的估算；无锚点时覆盖
    全路径消息、system prompt 与全部启用工具 schema。

    估算口径的权威说明在 ``flowing.agent.Agent.estimate_context_tokens``。
    """
    anchor_message_id: str | None
    """锚点消息 id（当前路径上最近一条有效 PROVIDER 消息）；无锚点为 ``None``。
    """
    context_window: int | None
    """当前模型的上下文窗口（``ModelConfig.context_window`` 求值结果）；模型未
    声明时为 ``None``。
    """

    @property
    def usage_ratio(self) -> float | None:
        """占用比率 ``tokens / context_window``；窗口未知时为 ``None``。

        .. rubric:: 行为要点

        - 不 clamp：大于 1.0 是合法的溢出信号，观测方自行决定阈值反应；本
          属性不做任何判断。
        """
        if self.context_window is None:
            return None  # 窗口未知 → None
        return self.tokens / self.context_window
