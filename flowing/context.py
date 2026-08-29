"""Flowing 上下文组装规约（``flowing.context``）。

本模块定义「system prompt 分层注册 → 惰性求值 → Provider adapter 消费」
这条链路上的全部数据结构：:class:`PromptBlock`（注册声明）、
:class:`PromptBlockList`（Agent 级有序容器，ManagedList 语义）、
:class:`PromptSegment`（求值产物）、:class:`Context`（组装输出，
``Provider.generate()`` 的唯一输入类型）。

.. rubric:: 模块定位

框架核心层。本模块只有**纯数据结构与同步容器操作**：

- 全部类与方法都是**同步**的。上下文组装不 await 任何东西——
  ``Parsable.resolve()`` 是同步惰性求值（见 :mod:`flowing.parsable`），
  消息沿 ``parent_id`` 链的收集是纯内存遍历，工具定义来自已就位的
  ``ToolEntry``。因此 :class:`Context` 的组装方
  ``Agent._assemble_context()`` 也是同步方法（定义在
  :class:`flowing.agent.Agent`，本模块只规约其输出契约）。
- 本模块**不持有** Agent / Runtime 引用，不知道消息树、钩子、持久化的
  存在；这些由组装方（Agent）负责，本模块只定义「装结果的容器长什么样」。

.. rubric:: 专属角度一：``Context`` 三字段结构（不是扁平消息列表）

:class:`Context` 由三个**正交**字段组成，任何 adapter 都不得把它们
合并成单一消息流后再自行拆分：

1. ``system_prompt: list[PromptSegment]`` —— 分层 system prompt，
   **保留 cache 标记**，供 Provider adapter 做缓存优化（Anthropic 的
   ``cache_control``、DeepSeek 的字节稳定前缀等）。
2. ``tools: list[ToolDefinition]`` —— 当前 ``enabled=True`` 的工具
   定义，**独立存在、不混进消息**。
3. ``messages: list[Message]`` —— 消息级树上从根到
   ``current_head_id`` 的上溯路径（文档 14 后：**消息级**，沿
   ``Message.parent_id`` 逐条收集，根→head 顺序）。

设计动机：把 cache 意图、工具声明、对话历史混成一个扁平列表会丢失
adapter 所需的全部结构信息（哪段 prompt 可缓存、哪些是工具声明）。
三字段结构让 adapter 的映射逻辑是机械的逐字段转换，不做猜测。

.. rubric:: 专属角度二：``PromptBlock.cache`` 三值语义——只是 adapter 意图

``cache`` 取值 ``"static" | "dynamic" | "session"``：

- ``static``：字节稳定，适合前缀缓存（adapter 行为示例：Anthropic
  标 ``cache_control``；DeepSeek 放请求体头部）。
- ``dynamic``：每轮可能变化（adapter 行为示例：不标记缓存）。
- ``session``：会话启动渲染一次、会话内不变（adapter 行为示例：
  记忆冻结场景）。

**本地不缓存（机制 vs 策略）**：框架本地**不实现任何缓存策略**——
每次 ``provider_gen`` 前都现场组装、现场求值。``cache`` 只是框架提供给
Provider adapter 的**意图标记通道**；是否缓存、如何缓存由 adapter /
服务端决定。设计理由：①简单性——框架核心不承担失效、一致性、内存
管理；②正确性优先——现场求值保证环境变量、实例属性、模式状态永远
最新（与 Parsable 惰性求值「是功能正确性前提而非性能优化」呼应）；
③缓存本来就是 Provider 侧的优化。

**现状与使用建议**（用户裁决）：该标记**暂无消费方**——内置 adapter
不消费它，框架任何时候都重新解析（上文「本地不缓存」），取值当前
只影响 docstring 语义表达；因此框架注入的 ``prompt_blocks[0]``
直接取 ``append`` 默认值 ``"dynamic"``，不刻意标 ``static``。
同时**不推荐在提示词块中放真正会频繁变动的内容**——一旦未来
adapter 开始消费该标记，频繁变动内容会破坏 provider 侧前缀缓存；
真正易变的信息应走消息（尾部追加）而非 prompt 块。

.. rubric:: 专属角度三：``PromptBlockList`` 的 ManagedList 语义

:class:`PromptBlockList` 遵循 :class:`flowing.lists.ManagedList`
公共容器抽象，与 ``HookList`` 完全同构：

- 元素契约：元素有 ``enabled`` / ``by`` / ``tags`` 三个管理属性。
- **注册顺序即拼接顺序**——组装时按注册顺序遍历求值。
- ``disable_*`` 只翻 ``enabled``，元素**保留原位**、可逆；
  ``enable_*`` 恢复且保持原注册顺序。
- ``remove_*`` 是物理删除，后续元素前移，不可逆。
- ``__iter__`` 自动跳过 ``enabled=False`` 的元素。
- 按 ``tag`` / ``owner``（即 ``by``）的分组操作无匹配元素时不报错、
  无操作；对已处于目标状态的元素重复操作是幂等 no-op。

**反模式（明确非行为）**：不推荐每 Turn 做 ``append`` +
``remove_by_tag`` 来注入动态内容——频繁增删破坏注册顺序稳定性。
正确做法是把动态性放进 ``PromptBlock.content`` 的模板引用里：
Parsable 在每次组装时现场求值，``{{ current_mode }}`` 之类的低频
变量引用自然拿到最新值，不需要增删 block。注意**高频更新值**（如
当前时间）不应进 prompt 块——Prompt 变动会极大破坏 provider 前缀
缓存；这类信息应走消息通道（``before_turn`` 附加式注入，见
``flowing.agent.TurnContext.pending_messages``；现成实现见
:func:`flowing.composables.reminder.use_system_reminder`），且框架不注册
``now()`` 之类的模板全局函数（``flowing.parsable`` 渲染上下文节的
定稿裁决），模板里调用函数只能经 ``self``/``agent`` 的方法。

.. rubric:: 专属角度四：``PromptBlockList[0]`` 惰性引用块（单一数据源）

Agent 的 ``__init__`` 会向 ``prompt_blocks`` 注入第一个元素——
**指向类属性 ``system_prompt`` 的惰性引用块**（不是内容副本）：

.. code-block:: python

    self.prompt_blocks.append(
        name="system_prompt",
        content=Parsable("{{ system_prompt }}"),   # 惰性引用，不复制
        cache="dynamic",   # append 默认值（取值现状见「专属角度二」）
        by="core",
    )

求值链：组装时 ``{{ system_prompt }}`` 在当前 Agent 实例上下文
resolve → Jinja2 拿到 ``self.system_prompt``（一个 Parsable）→
触发 ``str()`` → 自动 ``resolve()`` → 最终文本。

不变量：

- ``self.system_prompt`` 是 system prompt 内容的**唯一数据源**；
  ``setup()`` 中改它，下次 provider_gen 自动反映，引用块无需感知变化。
- ``[0]`` 块由框架注入（``by="core"``），应用与扩展**不得**移除、
  替换或在它之前插入元素；``remove_by_owner("core")`` 会破坏本
  约定，属使用错误。
- ``.fya`` 的 ``system_prompt`` 字段语法不变——变化的只是解析后的
  存储位置（类属性）与求值时机（每次组装现场求值）。

.. rubric:: 专属角度五：assemble 输出契约（供 Provider adapter 消费）

``Agent._assemble_context() -> Context``（内部 API，定义在
:class:`flowing.agent.Agent`；此处规约其**输出契约**，adapter 作者
只依赖本节描述，不依赖其内部实现）：

1. **三段组装**：遍历 ``prompt_blocks``（跳过 disabled）逐个
   ``resolve()`` → ``list[PromptSegment]``；沿 ``current_head_id``
   的 ``parent_id`` 链上溯到根、反转为根→head 顺序 →
   ``list[Message]``；``enabled=True`` 的 ``ToolEntry`` 经
   ``llm_definition()`` → ``list[ToolDefinition]``。
2. **现场求值、零缓存**：每次调用重新求值所有 Parsable、重新收集
   消息路径、重新生成工具定义。框架不缓存组装结果。
3. **活跃逻辑 turn 的可见性**：turn 执行中（``current_turn`` 存活）
   组装时，本 turn 已 append 到树的消息自然在路径上（它们在
   ``TurnContext.message_ids`` 中有记录）。（M-29 裁决：原
   ``TurnContext.inject`` 临时注入通道已删除，且**不推荐**在
   ``before_provider_gen`` 钩子中直接改写 ``messages``——一切进入上下文
   的内容都走持久化路径：回合开头的注入用 ``before_turn`` 向
   ``TurnContext.pending_messages`` **附加式**追加（随批次挂树
   持久化）；回合中途的追加用 ``chain.insert`` 挂到 head 路径上。
   擦除用 ``remove``。代价是注入/擦除产生额外的持久化条目
   （tombstone），换来「所有内容崩溃可恢复」的统一性质。）
4. **半截 turn 不截断**（M-28 裁决：已放弃半截 turn 截断）：崩溃恢复后，
   最后一个 ``turn_end=True`` 之后的半截 turn 已落盘消息**照常进入**
   ``messages``——它们是已产生的完整历史；孤立 tool_call 由规则 5 的
   合成占位封闭成对，保证上下文合法。执行状态不恢复、不续跑。
5. **成对匹配**：配对锚点在消息层——同一分支上 PROVIDER 消息内
   ``ToolCallBlock.id`` ↔ 后续 TOOL 消息的 ``Message.tool_call_id``
   字段，1:1 严格成对（05 文档 D1/D3：结果摊平到消息层，块层
   ``ToolResultBlock`` 已删除）。孤立 tool_call 由恢复时合成的
   ``synthetic=True`` 占位 TOOL 消息封闭（``tool_call_id`` 等于
   该孤立调用的 ``id``、``tool_status="error"``、
   ``content=[TextBlock(占位说明)]``），照常出现在 ``messages``
   中——adapter 不需要（也不应该）自行修补。
6. **副线消息不出现**：主流程 ``_assemble_context()`` 产出的
   ``Context`` 中不会出现 ``side_query`` 的消息——它们不进树、不落盘
   （「副线」是调用路径属性，``Message.side`` 字段已删除）。唯一例外
   是 ``side_query`` 调用内部：它把本次 ``msg`` 追加进**临时**
   ``Context.messages`` 后立即消费，不写回消息树。
7. **可改写**：组装结果先经 ``before_provider_gen`` 钩子（value 即
   :class:`Context`，handler 可整体改写三个字段）再交给
   ``Provider.generate()``；``after_provider_gen`` 对称地可改写
   ``ProviderResponse``。

.. rubric:: SYSTEM 注入双通道（边界澄清）

向 LLM 注入 system 类内容有两条正交通道，本模块承载其中一条：

- **本模块通道（不触发新回合）**：``prompt_blocks.append(...)`` 注册
  的块在每次组装时进入 ``Context.system_prompt``——适合「每轮自动
  追加的上下文」。
- **消息队列通道（触发新回合）**：``enqueue_message(Message(kind=SYSTEM,
  ...))`` 投递的消息进入消息历史——适合「需要独立处理的通知」。
  见 :class:`flowing.message.Message` 与
  :meth:`flowing.agent.Agent.enqueue_message`。

.. seealso::

    :class:`flowing.parsable.Parsable`
        ``PromptBlock.content`` 的类型；惰性求值与两步渲染规约。
    :class:`flowing.lists.ManagedList`
        ``PromptBlockList`` 遵循的公共容器抽象与元素契约。
    :class:`flowing.message.Message`
        ``Context.messages`` 的元素类型；``parent_id`` / ``turn_end``
        / ``synthetic`` 字段规约。
    :class:`flowing.tool.ToolDefinition`
        ``Context.tools`` 的元素类型。
    :meth:`flowing.providers.Provider.generate`
        :class:`Context` 的最终消费者。
    :meth:`flowing.agent.Agent._assemble_context`
        本模块输出契约的生产方（内部 API，不属稳定契约）。
"""

from __future__ import annotations  # R-1：注解延迟求值，ToolDefinition 仅 TYPE_CHECKING 引用

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from flowing.lists import ManagedList
from flowing.message import Message
from flowing.parsable import Parsable

if TYPE_CHECKING:
    # R-1（S-43 裁决③先例）：只做注解级引用，
    # 运行期不需要真实类（dataclass 字段注解惰性求值）
    from flowing.tool import ToolDefinition


@dataclass
class PromptBlock:
    """system prompt 分层组装的**注册声明**（注册时记录，求值推迟）。

    .. rubric:: 功能介绍

    框架核心层数据结构。一个 ``PromptBlock`` 是「一段 system prompt
    的注册声明」：它记录**惰性内容**（``content: Parsable``）与**管理
    元数据**（``cache`` / ``tags`` / ``by`` / ``enabled``），注册时不
    解析内容，直到每次 ``Agent._assemble_context()`` 现场求值产出
    :class:`PromptSegment`。

    .. rubric:: 设计动机

    system prompt 从「一段完整文本」变为「按注册顺序拼接的分层片段」，
    使框架骨架（``by="core"``）、内置扩展（``by="skill"`` /
    ``by="comm"``）与应用 Composable 能各自注册、各自管理自己的片段，
    互不感知。内容用 :class:`flowing.parsable.Parsable` 承载是因为
    **惰性求值是功能正确性的前提**：注册时环境变量、配置值、Agent
    实例属性可能尚不存在，只有使用时才能解析（求值面内契约——框架
    在组装时自动 resolve，用户无感）。

    .. rubric:: 使用示例

    .. code-block:: python

        # setup() 中注册（.fya $script 或手写子类中相同）
        async def setup(self):
            # 动态块：每轮 provider_gen 现场求值
            self.prompt_blocks.append(
                "session-info",
                Parsable("当前模式：{{ current_mode }}，cwd：{{ cwd }}"),
                cache="dynamic",
                by="reminder",
                tags=["session"],
            )
            # 静态块：引用文件，字节稳定
            self.prompt_blocks.append(
                "mode-plan",
                Parsable("$./prompts/plan-mode.md"),
                cache="static",
                by="mode-switcher",
                tags=["mode-plan"],
            )

    ``.fya`` 入口——``system_prompt`` 字段本身不需要注册 block，它经
    ``PromptBlockList[0]`` 惰性引用块自动进入（见模块 docstring）：

    .. code-block:: yaml

        # order-agent.fya（name 省略——由文件名推断；写了仅作一致性断言）
        system_prompt: $./system-prompt.md   # 成为 prompt_blocks[0] 的引用源

    .. rubric:: 行为规约

    期待行为：

    - ``content`` 在**每次**组装时现场求值；两次 provider_gen 之间实例属性
      变化会在下一次组装中反映。
    - ``enabled=False`` 的块保留在列表原位，被 ``__iter__`` 与组装
      跳过；重新 enable 后恢复原注册顺序位置。
    - 块的内容**应当**求值为 ``str``（字面量 / 文件引用 / 混合模板
      均如此）；使用纯表达式形式求值出非 ``str`` 结果属使用错误，
      组装产物 ``PromptSegment.content`` 的类型契约是 ``str``。

    非行为：

    - 注册时**不**求值、**不**校验内容合法性（文件是否存在、模板
      变量是否存在都在求值时才暴露）。
    - 框架**不**根据 ``cache`` 做任何本地缓存或跳过求值。
    - 块之间**不**自动插入分隔符；拼接格式（换行、标题层级）由块
      内容自带或 adapter 决定。

    边缘情况：

    - ``tags=None`` 归一化为空列表；``by`` 省略时为空串 ``""``（M-30 裁决：
      默认不标来源；只有框架真正内部 append 的块才显式填 ``"core"``）。
    - 同名 block 允许共存（``name`` 是标识与排查用标签，不是唯一
      键）；需要唯一定位时用 ``tags`` / ``by`` 分组管理。

    .. rubric:: 测试案例

    - 前置：Agent ``setup()`` 中 append 一个 ``Parsable("值：{{ x }}")``
      块，``self.x = 1``。操作：改 ``self.x = 2`` 后触发组装。
      期望：产出的 ``PromptSegment.content`` 为 ``"值：2"``。
    - 前置：块 ``enabled=False``。操作：遍历 ``prompt_blocks``。
      期望：该块不出现在迭代序列中，但仍存在于列表（可用
      ``enable_by_tag`` 找回）。

    .. rubric:: 调用关系（审计）

    - 被调：无（纯数据类；字段由组装方 ``Agent._assemble_context``
      读取求值）
    - 实例化方：``flowing.context.PromptBlockList.append``（工厂式
      构造，每次注册块）；``flowing.agent.Agent.__init__`` 经
      ``prompt_blocks.append`` 注入 ``[0]`` 惰性引用块（每次创建
      Agent，``Runtime.create_agent`` 管线）；``flowing.plugins.
      skills.use_skill`` 第 4 步注册 catalog 动态块
      （``use_skill()`` 挂载时）

    .. seealso::

        :class:`PromptSegment`
            本类的求值产物。
        :class:`PromptBlockList`
            本类的容器，ManagedList 语义。
        :class:`flowing.parsable.Parsable`
            ``content`` 的类型与求值规则。
    """

    name: str
    """块名（功能 + 动机：标识与日志/排查用标签，供 ``PromptSegment.name``
    透传给 adapter 与调试输出）。
    
    行为边界：非唯一键，允许重名；不做格式校验。
    
    .. seealso:: :attr:`PromptSegment.name`
    """

    content: Parsable
    """惰性内容（功能 + 动机：``Parsable`` 承载五种赋值形式，注册时不解析，
    使块可以引用注册时尚不存在的实例属性 / 环境变量 / 配置）。
    
    行为边界：组装时由框架在求值面内自动 ``resolve()``；用户不应在
    注册前手动求值（那会把动态内容固化成静态字符串）。
    
    .. seealso:: :class:`flowing.parsable.Parsable`
    """

    cache: Literal["static", "dynamic", "session"]
    """缓存意图标记（功能 + 动机：给 Provider adapter 的意图通道，三值
    ``"static"`` / ``"dynamic"`` / ``"session"`` 语义见模块
    docstring「专属角度二」）。
    
    行为边界：框架本地不读、不解释、不据此缓存；adapter 可以忽略它。
    非法取值由 adapter 侧报错，框架核心不校验。
    
    .. seealso:: :attr:`PromptSegment.cache`
    """

    tags: list[str]
    """分组标签（功能 + 动机：``enable_by_tag`` / ``disable_by_tag`` /
    ``remove_by_tag`` 的操作目标，支持一块多标签）。
    
    行为边界：框架不枚举合法值；标签是自由字符串。
    
    .. seealso:: :meth:`PromptBlockList.disable_by_tag`
    """

    by: str = ""
    """来源标识（功能 + 动机：ManagedList「按属主管理」的操作目标；
    默认空串 ``""``（M-30 裁决：不标来源）。约定值 ``"core"``（框架骨架——
    框架内部 append 时显式传入，非默认值）/ ``"skill"``（SkillPlugin）/
    应用自定义如 ``"mode-switcher"``。
    
    类型桥接（C-11 裁决）：本字段恒为 ``str``，省略 by 记为 ``""`` 而非
    hooks 侧的 ``None``——故 `PromptBlockList` 的 ``*_by_owner(owner: str)``
    参数已覆盖全定义域（``""`` 命中省略 by 的块），不构成实际 LSP 收窄。
    
    行为边界：扩展注册块时**必须**使用自己插件的 ``by`` 值，否则
    ``disable_by_owner`` / ``remove_by_owner`` 无法正确回收；
    对 ``"core"`` 属主做 remove 属使用错误（会破坏 ``[0]`` 引用块
    约定）。
    
    .. seealso:: :meth:`PromptBlockList.remove_by_owner`
    """

    enabled: bool = True
    """启用开关（功能 + 动机：ManagedList 可逆开关——``disable`` 只翻
    本字段、元素保留原位，``enable`` 恢复且保持注册顺序）。
    
    行为边界：``False`` 时被 ``__iter__`` 与组装跳过；直接赋值
    ``block.enabled = False`` 与 ``disable_by_*`` 等价。
    
    .. seealso:: :class:`flowing.lists.ManagedList`
    """


@dataclass
class PromptSegment:
    """prompt 块惰性求值的**产物**（已解析字符串 + cache 标记）。

    .. rubric:: 功能介绍

    框架核心层数据结构。``PromptSegment`` 是 :class:`PromptBlock` 在
    一次 ``_assemble_context()`` 中求值后的结果，是
    ``Context.system_prompt`` 的元素类型，直接面向 Provider adapter。

    .. rubric:: 设计动机

    声明（``PromptBlock``）与产物（``PromptSegment``）分离，使 adapter
    拿到的是**纯数据**：管理元数据（``tags`` / ``by`` / ``enabled``）
    对 adapter 无意义，被剥离；cache 意图与来源名保留，因为 adapter
    的缓存优化与调试输出需要它们。每次组装产生**新的** segment 对象，
    天然杜绝「adapter 持有了过期 prompt」这一类错误（本地不缓存原则
    在数据形态上的落地）。

    .. rubric:: 使用示例

    .. code-block:: python

        # adapter 侧消费（示意）
        for seg in context.system_prompt:
            if seg.cache == "static":
                ...  # Anthropic: 标记 cache_control
            print(seg.name, seg.content)

    .. rubric:: 行为规约

    期待行为：

    - ``content`` 是**当次组装**的解析结果，与列表中顺序即拼接顺序
      （= 块注册顺序，跳过 disabled）。
    - ``cache`` / ``name`` 从来源 ``PromptBlock`` 原样透传，组装方
      不改写。

    非行为：

    - 不携带 ``tags`` / ``by`` / ``enabled``——这些是管理元数据，
      求值后失去意义；adapter 需要分组信息属设计错误。
    - 不是缓存条目——框架不因 ``cache="static"`` 而复用上一轮的
      segment；下轮组装产生全新对象，内容可能不同（若引用源变了，
      那正是正确行为）。

    边缘情况：

    - 空列表合法（全部块 disabled 或仅 ``[0]`` 块被禁用——后者本身
      是使用错误，见模块 docstring「专属角度四」）。
    - 同名块产生同名 segment，adapter 不得把 ``name`` 当唯一键。

    .. rubric:: 测试案例

    - 前置：``prompt_blocks`` 含 ``[0]`` 引用块（默认值
      ``cache="dynamic"``）与一个 ``cache="static"`` 块。操作：
      组装。期望：``Context.system_prompt == [PromptSegment(
      name="system_prompt", cache="dynamic", ...),
      PromptSegment(cache="static", ...)]``，顺序与注册顺序一致。
    - 前置：同上。操作：连续两次组装。期望：两次得到的是不同对象
      （``is`` 不等），且第二次内容反映最新的实例状态。

    .. rubric:: 调用关系（审计）

    - 被调：无（纯数据类；最终由 Provider adapter 在
      ``Provider.generate`` 内消费，adapter 侧不列）
    - 实例化方：``flowing.agent.Agent._assemble_context``（每次
      query 前现场组装：``_run_turn`` 内循环；``side_query``
      同一机制）

    .. seealso::

        :class:`PromptBlock`
            本类的来源声明。
        :class:`Context`
            本类的容器（``system_prompt`` 字段）。
        :meth:`flowing.providers.Provider.generate`
            最终消费者。
    """

    content: str
    """已解析的 prompt 文本（功能 + 动机：adapter 直接拼接/映射的最终
    内容）。
    
    行为边界：类型契约是 ``str``；来源块求值为非 ``str`` 属使用
    错误。
    
    .. seealso:: :attr:`PromptBlock.content`
    """

    cache: Literal["static", "dynamic", "session"]
    """cache 意图标记，从来源块透传（功能 + 动机：adapter 缓存优化的
    唯一依据；框架本地不解释）。
    
    行为边界：三值语义见模块 docstring「专属角度二」。
    
    .. seealso:: :attr:`PromptBlock.cache`
    """

    name: str
    """来源块名，原样透传（功能 + 动机：调试输出与 adapter 日志的
    可追踪性）。
    
    行为边界：非唯一键。
    
    .. seealso:: :attr:`PromptBlock.name`
    """


class PromptBlockList(ManagedList[PromptBlock]):
    """prompt 块的有序容器（ManagedList 语义），Agent 实例属性
    ``prompt_blocks`` 的类型。

    .. rubric:: 功能介绍

    框架核心层容器。每个 Agent 实例持有一个 ``PromptBlockList``；
    ``Agent.__init__`` 注入 ``[0]`` 惰性引用块（``by="core"``，见模块
    docstring「专属角度四」），之后 ``setup()``、Composable、内置扩展
    按注册顺序追加自己的块。

    .. rubric:: 设计动机

    与 ``HookList`` 共用 :class:`flowing.lists.ManagedList` 抽象：
    「分组启停 / 按属主回收 / 注册顺序稳定」是框架容器的普遍需求，
    同一套语义学一次处处适用。声明式分层注册 + 惰性求值取代了「每
    Turn 手工拼 prompt」的命令式做法。

    .. rubric:: 使用示例

    模式切换（Coding Agent）——切换 system prompt 的激活 section：

    .. code-block:: python

        async def setup(self):
            self.prompt_blocks.append(
                "mode-plan", Parsable("$./prompts/plan-mode.md"),
                cache="static", by="mode-switcher", tags=["mode-plan"])
            self.prompt_blocks.append(
                "mode-review", Parsable("$./prompts/review-mode.md"),
                cache="static", by="mode-switcher", tags=["mode-review"])
            self.prompt_blocks.disable_by_tag("mode-plan")
            self.prompt_blocks.disable_by_tag("mode-review")

        def _on_mode_change(self, new, old):
            self.prompt_blocks.disable_by_owner("mode-switcher")
            self.prompt_blocks.enable_by_tag(f"mode-{new}")

    ``.fya`` 入口：``system_prompt`` 字段自动成为 ``[0]`` 引用块的
    引用源，无需手工注册；额外的块在 ``$script`` 的 ``setup()`` 中
    用上述同款代码注册。

    .. rubric:: 行为规约

    期待行为：

    - **注册顺序即拼接顺序**：组装按列表顺序遍历（跳过 disabled），
      产出的 ``PromptSegment`` 序列与之严格同序。
    - ``[0]`` 是框架注入的 ``system_prompt`` 惰性引用块（
      ``name="system_prompt"``、``cache="dynamic"``（``append``
      默认值）、``by="core"``），在所有用户/扩展块之前。
    - ``__iter__`` 产出的是块对象本身（非求值产物），求值只发生在
      组装时。

    非行为：

    - **不用于每 Turn 的内容增删**（反模式，见模块 docstring
      「专属角度三」）；动态内容走块内模板引用。
    - 不做内容去重、不做顺序自动调整、不在 append 时求值。

    边缘情况：

    - 分组操作（``*_by_tag`` / ``*_by_owner``）无匹配元素时不报错、
      无操作；对已处于目标状态的元素重复操作是幂等 no-op。
    - ``remove_*`` 物理删除后后续元素前移——依赖固定下标（除
      ``[0]`` 约定外）是脆弱的，应用按 ``tags`` / ``by`` 管理。
    - 框架核心永不移除 ``[0]`` 块；应用对 ``by="core"`` 做
      ``remove_by_owner`` 属使用错误。

    .. rubric:: 测试案例

    - 前置：新创建 Agent。操作：读 ``agent.prompt_blocks[0]``。
      期望：``name == "system_prompt"``、``by == "core"``、
      ``cache == "dynamic"``（``append`` 默认值）、``content``
      是 source 为 ``"{{ system_prompt }}"`` 的 Parsable。
    - 前置：``setup()`` 中 ``self.system_prompt = Parsable("旧")``，
      注册若干块。操作：``self.system_prompt = Parsable("新")`` 后
      组装。期望：``Context.system_prompt[0].content == "新"``——
      单一数据源，无需触碰 prompt_blocks。
    - 前置：``by="mode-switcher"`` 的两块均 disabled。操作：
      ``enable_by_tag("mode-plan")``。期望：仅带该 tag 的块进入
      迭代序列，且位置仍在 ``[0]`` 之后、原注册位。

    .. rubric:: 调用关系（审计）

    - 被调：无（容器类；各方法的被调方见方法级标注）
    - 实例化方：``flowing.agent.Agent.__init__``（每次创建 Agent，
      经 ``Runtime.create_agent`` / ``Runtime.recover_agent``
      管线）

    .. seealso::

        :class:`flowing.lists.ManagedList`
            公共容器抽象与完整元素契约。
        :class:`PromptBlock`
            元素类型。
        :meth:`flowing.agent.Agent.setup`
            注册块的唯一推荐时机。
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
        """构造并追加一个 prompt 块（工厂式 append）。

        .. rubric:: 功能介绍

        以零散参数构造 :class:`PromptBlock` 并追加到列表尾部（注册
        顺序末尾），返回构造出的块对象。

        .. rubric:: 设计动机

        工厂式签名把「构造 + 注册」合并为一步，与 ``HookList`` 的
        注册形态同构；``cache`` / ``by`` / ``tags`` 收为 keyword-only
        使调用点自描述。默认 ``cache="dynamic"`` 是保守选择：不标记
        缓存永远正确，需要缓存优化的块（如 ``[0]`` 引用块、模式文件）
        显式声明 ``"static"``。

        .. rubric:: 使用示例

        .. code-block:: python

            block = self.prompt_blocks.append(
                "session-info",
                Parsable("当前模式：{{ current_mode }}"),
                cache="dynamic",
                by="reminder",
                tags=["session"],
            )
            block.enabled   # True

        .. rubric:: 行为规约

        期待行为：追加到尾部；``tags=None`` 归一化为 ``[]``；返回的
        块已挂入列表，对其字段的后续修改（如 ``enabled``）直接生效。

        非行为：不求值 ``content``；不检查 ``name`` 唯一性；不检查
        ``cache`` 取值合法性（非法值由 adapter 侧暴露）。

        类型注记（C-11 裁决）：本方法是对 `ManagedList.append` 的
        **有意工厂式特化**（spec-draft 06 §18.1 的全部实际示例均为
        工厂式），父类元素式 ``append(item)`` 接口在本类上不可用；
        父类 docstring 已声明不对子类 append 形态做约束。

        前置条件：推荐在 ``setup()``（或 Composable）中调用；框架对
        ``[0]`` 块的注入发生在 ``__init__``，早于任何 ``setup()``
        追加，故用户 append 的块永远在 ``[0]`` 之后。

        后置条件：列表长度 +1；新块 ``enabled=True``。

        .. rubric:: 测试案例

        - 前置：新创建 Agent。操作：``append("x", Parsable("hi"))``。
          期望：``prompt_blocks[1].name == "x"`` 且
          ``prompt_blocks[1].cache == "dynamic"``（默认值生效）。
        - 前置：已 append 块 ``b``。操作：``b.enabled = False`` 后
          遍历。期望：``b`` 不在迭代序列中。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.context.PromptBlock`` 构造（每次注册块，
          工厂式语义）
        - 被调：``flowing.agent.Agent.__init__`` 注入 ``[0]`` 惰性
          引用块（每次创建 Agent，``Runtime.create_agent`` 管线）；
          ``flowing.plugins.skills.use_skill`` 第 4 步注册
          catalog 动态块（``use_skill()`` 挂载时）。应用
          ``setup()`` 中的注册属用户代码，不列。

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
        return block  # -> PromptBlock（返回构造产物本身，见行为规约）

    def disable_by_tag(self, tag: str) -> int:
        """按标签禁用块（可逆，元素保留原位）。

        .. rubric:: 功能介绍

        把所有 ``tag in block.tags`` 的块置 ``enabled=False``。

        .. rubric:: 设计动机

        ManagedList 分组语义的落地：标签是「一组块共同的功能身份」
        （如 ``"mode-plan"``），启停一组块不应依赖各自的位置或名字。

        .. rubric:: 使用示例

        .. code-block:: python

            self.prompt_blocks.disable_by_tag("mode-review")

        .. rubric:: 行为规约

        期待行为：只翻 ``enabled``，元素**保留原位**、原注册顺序不变；
        已 disabled 的匹配块为幂等 no-op；无匹配元素不报错。

        非行为：不删除元素、不求值、不触发任何钩子。

        :return: 匹配元素数（int）。计数口径注记：本模块的测试案例要求
          重复调用返回相同计数（``disable_by_tag("g")`` 两次均返回 2），
          与 ``lists.ManagedList`` 的「本次新置数」口径不同——本类四个
          ``enable_*`` / ``disable_*`` 按**匹配数**计数（对已处目标状态的
          匹配块是状态幂等 no-op，但仍计入返回数）；``remove_*`` 两口径
          天然一致（删除后不再匹配），沿用父类实现。

        .. rubric:: 测试案例

        - 前置：两块带 ``tags=["g"]``，一块不带。操作：
          ``disable_by_tag("g")``。期望：两块 enabled=False 且位置
          不变；第三块不受影响；返回 2；再次调用结果相同（返回 2）。

        .. rubric:: 调用关系（审计）

        - 调用：无（直接遍历 ``_items`` 原地翻转 ``enabled``，不走父类
          的「新置数」实现——见 ``:return:`` 口径注记）
        - 被调：无（框架内未见调用方；skills 管理面示例
          ``prompt_blocks.disable_by_tag("skill.catalog")`` 属应用
          操作，不列）

        .. seealso::

            :meth:`enable_by_tag`
                逆操作。
            :class:`flowing.lists.ManagedList`
                分组语义来源（计数口径不同，见 ``:return:`` 注记）。
        """
        # 匹配数口径（见 docstring 注记）：已 disabled 的匹配块仍计入
        matched = [b for b in self._items if tag in b.tags]
        for b in matched:
            b.enabled = False  # 只翻 enabled，元素保留原位、顺序不变
        return len(matched)

    def enable_by_tag(self, tag: str) -> int:
        """按标签重新启用块（恢复原注册顺序位置）。

        .. rubric:: 功能介绍

        把所有 ``tag in block.tags`` 的块置 ``enabled=True``。

        .. rubric:: 设计动机

        与 :meth:`disable_by_tag` 构成可逆对——模式切换等场景
        「关一组、开一组」是常态操作，可逆启停保证注册顺序稳定
        （对比反模式：remove + 重新 append 会把块移到尾部）。

        .. rubric:: 使用示例

        .. code-block:: python

            self.prompt_blocks.enable_by_tag(f"mode-{new_mode}")

        .. rubric:: 行为规约

        期待行为：只翻 ``enabled``；元素从未离开列表，因此「恢复」
        后天然处于原注册位；已 enabled 的匹配块为幂等 no-op；无匹配
        元素不报错。

        非行为：不改变任何块的内容与顺序。

        :return: 受影响元素数（C-11 裁决：与父类统一返回 ``int``）。

        .. rubric:: 测试案例

        - 前置：``disable_by_tag("g")`` 之后。操作：
          ``enable_by_tag("g")``。期望：组装产出的 segment 顺序与
          最初注册顺序完全一致；返回受影响数。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（框架内未见调用方；LazySkillsPrompt 管理面
          提到的恢复操作属应用操作，不列）

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
        """按属主（``by``）重新启用块（恢复原注册顺序位置）。

        把所有 ``block.by == owner`` 的块置 ``enabled=True``。
        参数桥接与返回值语义同 :meth:`disable_by_owner`（C-11 裁决：
        ``owner=""`` 命中省略 by 的块；返回受影响元素数 ``int``）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（框架内未见调用方）

        .. seealso::

            :meth:`disable_by_owner`
                逆操作。
        """
        # 匹配数口径（同 disable_by_tag 的注记）；owner="" 命中省略 by 的块（C-11）
        matched = [b for b in self._items if b.by == owner]
        for b in matched:
            b.enabled = True
        return len(matched)

    def remove_by_tag(self, tag: str) -> int:
        """按标签物理删除块（不可逆）。

        .. rubric:: 功能介绍

        从列表中删除所有 ``tag in block.tags`` 的块。

        .. rubric:: 设计动机

        与 disable 系列互补：disable 是「暂时不用」（可逆、保序），
        remove 是「永久退场」（如扩展卸载、模式机制整体移除）。

        .. rubric:: 使用示例

        .. code-block:: python

            self.prompt_blocks.remove_by_tag("deprecated-feature")

        .. rubric:: 行为规约

        期待行为：物理删除，后续元素前移；无匹配元素不报错。

        非行为：不级联删除其他 tag 重叠的块之外的任何东西；**不推荐**
        用于每 Turn 的内容清洗（反模式，见模块 docstring
        「专属角度三」）。

        边缘情况：删除发生在两次组装之间时，下一次组装即生效；正在
        进行的 provider_gen 不受影响（Context 已组装完毕）。

        :return: 受影响（被删除）元素数（C-11 裁决：与父类统一返回
          ``int``）。

        .. rubric:: 测试案例

        - 前置：``[0]`` 引用块 + 两块带 ``tags=["g"]``。操作：
          ``remove_by_tag("g")``。期望：列表只剩 ``[0]`` 块，
          ``prompt_blocks[0]`` 仍是 ``system_prompt`` 引用块；返回 2。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（框架内未见调用方；docstring 示例为应用/扩展
          侧操作，不列）

        .. seealso::

            :meth:`remove_by_owner`
                按属主回收。
            :meth:`disable_by_tag`
                可逆的替代方案。
        """
        return super().remove_by_tag(tag)  # 物理删除语义由 ManagedList.remove_by_tag 承载；返回删除数

    def disable_by_owner(self, owner: str) -> int:
        """按属主（``by``）禁用块（可逆）。

        .. rubric:: 功能介绍

        把所有 ``block.by == owner`` 的块置 ``enabled=False``。

        .. rubric:: 设计动机

        属主是「谁注册的」——扩展或 Composable 需要整体停用自己
        注册的全部块时，不应枚举各自的 tag。这也是扩展必须如实填写
        ``by`` 的原因。

        .. rubric:: 使用示例

        .. code-block:: python

            self.prompt_blocks.disable_by_owner("mode-switcher")

        .. rubric:: 行为规约

        期待行为：只翻 ``enabled``、保序；幂等；无匹配不报错。

        非行为：不删除元素；对 ``owner="core"`` 调用属使用错误
        （会禁用 ``[0]`` 引用块，使 system prompt 消失）。

        参数桥接（C-11 裁决）：`PromptBlock.by` 恒为 ``str``（省略
        by 记为 ``""``，M-30），故本类 ``owner: str`` 已覆盖全部
        定义域——``disable_by_owner("")`` 即命中省略 by 的块；
        hooks 侧 ``by=None`` 语义不进入本类。

        :return: 受影响元素数（C-11 裁决：与父类统一返回 ``int``）。

        .. rubric:: 测试案例

        - 前置：``by="skill"`` 两块与其他属主若干块。操作：
          ``disable_by_owner("skill")``。期望：仅这两块
          enabled=False，组装产物中无其 segment；返回 2。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（框架内未见调用方；skills 管理面示例
          ``disable_by_owner("skill")`` 属应用操作，不列）

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
        """按属主（``by``）物理删除块（不可逆）。

        .. rubric:: 功能介绍

        从列表中删除所有 ``block.by == owner`` 的块——扩展卸载时
        回收自己注册的全部块的规范通道。

        .. rubric:: 设计动机

        「谁注册、谁回收」：属主维度比 tag 更贴近扩展的生命周期
        边界（一个扩展可能注册多个不同 tag 的块）。

        .. rubric:: 使用示例

        .. code-block:: python

            # 扩展卸载（插件收尾逻辑中）
            agent.prompt_blocks.remove_by_owner("my-plugin")

        .. rubric:: 行为规约

        期待行为：物理删除、后续元素前移；无匹配不报错。

        非行为：框架核心永不以 ``"core"`` 为参数调用本方法；应用
        如此调用属使用错误（删除 ``[0]`` 引用块后 system prompt
        不再进入上下文，框架不兜底）。

        参数桥接：同 :meth:`disable_by_owner`（C-11 裁决——
        ``owner=""`` 命中省略 by 的块）。

        :return: 受影响（被删除）元素数（C-11 裁决：与父类统一返回
          ``int``）。

        .. rubric:: 测试案例

        - 前置：``by="comm"`` 的块存在。操作：
          ``remove_by_owner("comm")`` 后组装。期望：
          ``Context.system_prompt`` 中无对应 segment，其余块顺序
          不变；返回 1。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（框架内未见调用方；docstring 示例的插件卸载
          收尾属扩展侧代码，不列）

        .. seealso::

            :meth:`disable_by_owner`
                可逆版本。
            :attr:`PromptBlock.by`
                属主字段约定。
        """
        return super().remove_by_owner(owner)  # 物理删除语义由 ManagedList.remove_by_owner 承载；返回删除数

    def __getitem__(self, index: int) -> PromptBlock:
        """按下标访问块——``[0]`` 惰性引用块约定的承载接口。

        .. rubric:: 功能介绍

        支持下标读取（含负数下标的常规序列语义）。``[0]`` 恒为框架
        注入的 ``system_prompt`` 惰性引用块（``by="core"``）。

        .. rubric:: 设计动机

        「``[0]`` 是 system_prompt 引用块」是一条**位置约定**而非
        查找接口：框架注入发生在 ``__init__``、早于一切用户注册，
        位置由构造顺序结构性地保证，不需要按名字查找。

        .. rubric:: 使用示例

        .. code-block:: python

            block0 = self.prompt_blocks[0]
            assert block0.by == "core" and block0.name == "system_prompt"

        .. rubric:: 行为规约

        期待行为：返回列表中对应位置的块（含 disabled 块——下标
        语义作用于底层列表，与 ``__iter__`` 的过滤语义正交）。

        非行为：不支持按名字/标签索引（那是 ``*_by_tag`` /
        ``*_by_owner`` 的职责）。

        .. rubric:: 测试案例

        - 前置：新创建 Agent，``setup()`` 追加三块。操作：读
          ``prompt_blocks[0]`` 与 ``prompt_blocks[3]``。期望：
          ``[0]`` 是 ``by="core"`` 的引用块；``[3]`` 是最后追加
          的块。
        - 前置：某块被 disable。操作：按下标读它。期望：仍可
          读到（下标语义不过滤 enabled）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（时机：未见规约——组装路径经 ``__iter__``
          而非下标；``[0]`` 读取主要见类 docstring 测试案例与
          调试断言）

        .. seealso::

            :class:`PromptBlockList`
                类 docstring 的不变量与测试案例。
        """
        block = self._items[index]  # 下标作用于底层列表，含 disabled（与 __iter__ 正交）
        return block  # -> PromptBlock（返回对应位置的块，见行为规约）

    def __iter__(self) -> Iterator[PromptBlock]:
        """迭代**已启用**的块，顺序 = 注册顺序。

        .. rubric:: 功能介绍

        产出 ``enabled=True`` 的块，自动跳过 disabled；组装方
        （``Agent._assemble_context``）依赖本方法遍历。

        .. rubric:: 设计动机

        「跳过 disabled」收敛在迭代协议里一处实现，所有消费方
        （组装、调试遍历）天然一致，不会各自重写过滤条件。

        .. rubric:: 使用示例

        .. code-block:: python

            for block in self.prompt_blocks:   # 只见 enabled 的
                print(block.name)

        .. rubric:: 行为规约

        期待行为：顺序严格等于注册顺序（过滤后保持相对序）；产出
        块对象本身，不求值。

        非行为：不是快照——迭代过程中其他代码增删块属使用错误
        （单进程 asyncio 模型下，同步迭代本身不会被协程切换打断）。

        .. rubric:: 测试案例

        - 前置：三块中中间一块 disabled。操作：``list(iter)``。
          期望：得到首尾两块，顺序不变。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.agent.Agent._assemble_context``（每次
          query 前现场组装：``_run_turn`` 内循环；``side_query``
          同一机制）；``flowing.lists.ManagedList`` 总则要求一切
          消费活跃元素的路径经本迭代（时机：未见规约）

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

    框架核心层数据结构。``Context`` 由 ``Agent._assemble_context()``
    在**每次** provider_gen 前现场组装，经 ``before_provider_gen`` 钩子（可整体
    改写）后交给 Provider adapter。三字段正交结构：

    - ``system_prompt``：分层 prompt 段（保留 cache 标记）；
    - ``tools``：当前启用的工具定义；
    - ``messages``：消息级树上根→head 的消息路径。

    .. rubric:: 设计动机

    **Context 不是扁平消息列表**。cache 意图与工具声明一旦混入消息
    流就无法无损还原，adapter 的映射逻辑将被迫猜测；三字段结构让
    adapter 只做机械的逐字段转换（kind→role、cache→cache_control、
    ToolDefinition→function 声明）。「每次现场组装、零本地缓存」
    是「机制 vs 策略」中「本地不缓存」原则的核心落点：正确性优先，
    缓存是 Provider 侧的优化。

    .. rubric:: 使用示例

    .. code-block:: python

        # Provider adapter 侧消费（generate 实现示意）
        async def generate(self, context: Context, model: ModelConfig
                           ) -> ProviderResponse:
            body = {
                "system": [
                    {"text": seg.content,
                     **({"cache_control": {"type": "ephemeral"}}
                        if seg.cache == "static" else {})}
                    for seg in context.system_prompt
                ],
                "tools": [self._map_tool(t) for t in context.tools],
                "messages": [self._map_message(m) for m in context.messages],
            }
            ...

        # before_provider_gen 钩子整体改写（应用层）
        @on('before_provider_gen')
        def _(self, context):
            context.system_prompt.append(PromptSegment(
                content=f"本次请求 ID：{self.request_id}",
                cache="dynamic", name="trace"))
            return context

    ``.fya`` 入口：``.fya`` 作者不直接构造 ``Context``；``system_prompt``
    字段经 ``prompt_blocks[0]`` 自动进入，``tools:`` 列表经
    ``ToolEntry.llm_definition()`` 自动进入。

    .. rubric:: 行为规约

    期待行为：

    - ``messages`` 为根→head 顺序的 ``Message`` 列表：从
      ``current_head_id`` 沿 ``Message.parent_id`` 上溯到根后反转；
      活跃逻辑 turn 内包含本 turn 已 append 的消息。
    - 无活跃逻辑 turn 时（崩溃恢复后、新 turn 开始前）行为相同：
      半截 turn 的已落盘消息**照常进入** ``messages``（M-28 裁决，
      不再截断）；孤立 tool_call 由合成占位封闭。
    - ``synthetic=True`` 的占位 tool_result 消息照常出现，保证
      tool_call / tool_result 严格成对。
    - ``tools`` 只含 ``enabled=True`` 的 ``ToolEntry`` 经
      ``llm_definition()`` 的产物；与消息流完全分离。**无隐式附加**——
      ``subagent-invoke`` / ``finish`` 等内置工具同样需用户显式声明
      （``tools:`` / ``add_tool``）才出现在此（S-19 最终裁决：
      一切工具以用户声明为准，框架不隐式添加）。
    - 每次组装产生全新对象与全新列表；两次组装的 ``Context``
      互不共享可变状态。

    非行为：

    - 主流程组装的 ``messages`` 中**不出现**副线消息（``side_query`` 的
      消息不进树、不落盘）；``side_query`` 调用内部的临时 ``Context``
      会把本次 ``msg`` 追加进 ``messages``，不落盘。
    - 框架**不**对 Context 做 token 计数、窗口裁剪、长度校验——
      超长由 Provider adapter 抛
      :class:`flowing.errors.ContextLengthError` 兜底（该异常
      不经过 ``on_provider_error``，直接上抛）。
    - 框架**不**为 ``messages`` 做 role 映射——kind→API role 是
      adapter 的职责（映射表见设计文档 06 §18.4.1）。
    - 实例**不**保证线程安全（单进程 asyncio 模型，无此需求）。

    边缘情况：

    - 全新 Agent（树上只有 ``[0]`` 引用块，无任何消息）：
      ``messages == []``、``system_prompt`` 至少含 ``[0]`` 块的
      segment、``tools`` 可为空列表——三者空列表均合法，adapter
      不得假设非空。
    - fork 切换 ``current_head_id`` 后，下一次组装自动反映新分支
      路径——fork 是纯上下文操作，不需要任何额外同步。

    不变量：

    - ``messages`` 中 tool_call 与其结果消息严格成对：PROVIDER 消息内
      ``ToolCallBlock`` 的 ``id`` ↔ 同分支后续 TOOL 消息的
      ``tool_call_id`` 字段，1:1（05 文档 D1/D3：结果摊平到消息层，
      配对锚从块层搬到消息字段）；
    - ``messages`` 中不出现副线消息；
    - ``system_prompt`` 的顺序 = ``prompt_blocks`` 注册顺序
      （跳过 disabled）。

    .. rubric:: 测试案例

    - 前置：树上有完整 turn（user → provider[turn_end=False] →
      tool → provider[turn_end=True]）。操作：组装。期望：
      ``messages`` 按 append 顺序全部出现，根→head。
    - 前置：崩溃恢复后，树上最后一条 ``turn_end=True`` 之后还有
      两条半截 turn 消息；无活跃 turn。操作：组装。期望：
      两条半截 turn 消息**照常出现**在 ``messages`` 末尾（M-28
      裁决，不截断）。
    - 前置：恢复时存在孤立 tool_call（PROVIDER 消息中的
      ``ToolCallBlock`` 无对应结果消息）。操作：组装。期望：
      ``messages`` 中该调用之后紧跟一条 ``synthetic=True`` 的 TOOL
      消息，其 ``tool_call_id`` 等于该 ``ToolCallBlock.id``、
      ``tool_status="error"``（05 文档 D3 占位形态），成对匹配成立。
    - 前置：钩子想在回合开头注入一条 reminder（M-29 裁决后）：
      ``before_turn`` 中 ``turn.pending_messages.append(...)``。操作：组装。
      期望：该 reminder 随批次挂树，出现在 ``messages`` 路径上触发消息
      之后；后续 ``remove`` 擦除后（tombstone 生效）不再出现。
    - 前置：两个 ``ToolEntry``，其一 ``enabled=False``。操作：
      组装。期望：``tools`` 只含 enabled 条目的定义。
    - 前置：``side_query`` 执行过一次。操作：主流程组装。期望：
      side 消息不出现在 ``messages`` 中。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.provider_gen`` 内 dispatch
      ``before_provider_gen``（value 即本类，可整体改写，每次 provider_gen）；
      ``flowing.providers.Provider.generate``（每次 provider_gen，经
      ``Agent.provider_gen`` 传入为唯一输入）
    - 实例化方：``flowing.agent.Agent._assemble_context``（每次
      query 前现场组装：``_run_turn`` 内循环；``side_query``
      同一机制）

    .. seealso::

        :class:`PromptSegment`
            ``system_prompt`` 的元素类型。
        :class:`flowing.tool.ToolDefinition`
            ``tools`` 的元素类型。
        :class:`flowing.message.Message`
            ``messages`` 的元素类型；``parent_id`` / ``turn_end`` /
            ``synthetic`` 字段规约。
        :meth:`flowing.providers.Provider.generate`
            最终消费者。
        :class:`flowing.agent.TurnContext`
            ``pending_messages``（回合开头附加式注入的载体，M-29 裁决）。
        :class:`flowing.errors.ContextLengthError`
            上下文超长的兜底异常。
    """

    system_prompt: list[PromptSegment]
    """分层 system prompt 段（功能 + 动机：保留 cache 标记供 adapter
    缓存优化；顺序 = 块注册顺序）。
    
    行为边界：当次组装全新求值产物；空列表合法；``before_provider_gen``
    可增删改写。
    
    .. seealso:: :class:`PromptSegment`
    """

    tools: list[ToolDefinition]
    """当前启用的工具定义（功能 + 动机：工具声明独立于消息流存在，
    adapter 直接映射为各 API 的 function 声明）。
    
    行为边界：只含 ``enabled=True`` 条目的 ``llm_definition()``
    产物；空列表合法。
    
    .. seealso:: :class:`flowing.tool.ToolDefinition`
    """

    messages: list[Message]
    """根→head 的消息路径（功能 + 动机：消息级树上沿 ``parent_id``
    上溯收集的对话历史，adapter 逐条做 kind→role 映射）。
    
    行为边界：不含副线消息；半截 turn 不截断（M-28）；配对锚为
    PROVIDER 消息内 ``ToolCallBlock.id`` ↔ TOOL 消息的
    ``tool_call_id`` 字段（结果摊平到消息层），孤立调用由
    ``synthetic`` 占位封闭成对，规则见类 docstring「行为规约」。
    
    .. seealso:: :class:`flowing.message.Message`
    """


@dataclass
class ContextUsageEstimate:
    """当前上下文占用的估计——:meth:`Agent.estimate_context_tokens` 的返回类型。

    .. rubric:: 功能介绍

    「现在如果把上下文发给 LLM，大约多大」的快照值：``measured`` 是锚点
    （最近一次 provider 实测）覆盖的部分，``estimated`` 是锚点之后（或无
    锚点时全段）的本地启发式估算部分，``tokens`` 为两者之和。

    .. rubric:: 设计动机

    混合计数模型（kimi-code 锚点账本 / pi 锚点+尾部估算的 Flowing 化）：
    provider 实测是唯一精确来源，但锚点之后新积累的内容（工具结果、新
    消息、注入）从未被实测，只能估算。本结构把两部分**分开承载**而非只给
    一个和——观测方可以区分「多少是实测、多少是猜的」。估算部分永不用于
    计费（计费走 ``TurnResult.token_usage`` 聚合口径，两条数据流不混）。

    .. rubric:: 使用示例

    .. code-block:: python

        est = agent.estimate_context_tokens()
        if est.usage_ratio is not None and est.usage_ratio > 0.85:
            ...   # 阈值判断与压缩是插件策略，框架核心只提供本观测值

    .. rubric:: 行为规约

    - 纯数据投影：构造后不可变的语义（dataclass），读取无副作用。
    - ``tokens == (measured or 0) + estimated`` 恒成立（构造方保证）。
    - ``measured is None`` ⟺ 当前路径无有效锚点（全新会话或锚点消息
      均被移除）——此时 ``tokens`` 全部为估算，观测方应视为低置信。
    - 边缘情况：路径为空 → ``tokens == 0``、``measured is None``、
      ``anchor_message_id is None``。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.snapshot``（时机：每次快照，投影为
      ``AgentSnapshot.context_usage``）
    - 实例化方：``flowing.agent.Agent.estimate_context_tokens``（每次
      调用，现场扫描构造）

    .. seealso::

        :meth:`Agent.estimate_context_tokens`、
        :func:`flowing.message.estimate_message_tokens`、
        :class:`flowing.providers.Usage`（锚点实测数据的类型）。
    """

    tokens: int
    """上下文占用总量估计：``(measured or 0) + estimated``。
    """
    measured: int | None
    """锚点实测部分（锚点 ``Usage.total_tokens``）；无有效锚点时为 ``None``。
    """
    estimated: int
    """本地启发式估算部分（锚点后消息 + 规则内 system prompt / 工具 schema）。
    """
    anchor_message_id: str | None
    """锚点消息 id（当前路径上最近一条有效 PROVIDER 消息）；无锚点为 ``None``。
    """
    context_window: int | None
    """当前模型的上下文窗口（``ModelConfig.context_window`` 求值结果）；
    模型未声明时为 ``None``。
    """

    @property
    def usage_ratio(self) -> float | None:
        """占用比率 ``tokens / context_window``；窗口未知时为 ``None``.

        .. rubric:: 行为规约

        - **不 clamp**：``> 1.0`` 是合法的溢出信号（kimi-code SDK 同口径），
          观测方自行决定阈值反应；本属性不做任何判断。
        """
        if self.context_window is None:
            return None  # 窗口未知 → None
        return self.tokens / self.context_window
