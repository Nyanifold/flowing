"""flowing.plugins.skills —— Skill 扩展（SkillPlugin / Skill / SkillEntry / SkillRegistry / SkillLoadTool / use_skill）。

.. rubric:: 功能介绍

Skill 扩展是 Flowing 的内置扩展（随 ``flowing`` 包发布但不自动
启用），为 Agent 提供「按名加载一段提示词」的能力：Agent 在
``agent.fya`` 的 ``skills:`` 字段声明技能，LLM 从 catalog 中看到
可用技能清单，需要时经 ``skill-load`` 工具按名加载详细执行指南。

框架核心不感知 Skill：``agent.fya`` 的 ``skills:`` 字段不是核心
保留字，宽松 YAML 解析把它静默保留在 Agent 的 ``_extra`` 中，由本
模块的 ``use_skill(self)`` 在 ``setup()`` 阶段解析（``Agent.__getattr__``
对未定义属性回退 ``_extra`` 查找，扩展直接读 ``agent.skills`` 即可）。
Skill 的加载路径也不走 Turn 引擎硬编码——LLM 经 ``skill-load`` 工具
调用加载，框架看到的只是一次普通 ``tool_call`` （Tool / 子 Agent 在
Turn 引擎中有硬编码调用点，Skill 完全复用 Tool 机制）。

启用遵循双层启用模型：阶段一 ``runtime.use(SkillPlugin(...))``
注册全局 ``SkillRegistry`` 与 ``skill-load`` 工具；阶段二
``use_skill(self)`` 为单个 Agent 实例启用。

.. rubric:: 注册面清单

- 启用方式：

  - 阶段一：``runtime.use(SkillPlugin(...))``——``install()`` 同步完成
    全部注册（重复 ``use()`` 同一插件实例属编程错误，见
    :class:`SkillPlugin`）；
  - 阶段二：``setup()`` 中 ``use_skill(self)``——recover 重跑
    ``setup()`` 时作用于新实例，安全；对同一实例重复调用本
    函数会在条目装配处抛 ``EntryNameConflictError`` （钩子点声明幂等、
    绑定函数「检查后跳过」，但条目别名不重复登记，见
    :func:`use_skill` 行为要点）。

- 注册的资源：

  - provide key：:data:`skill_registry_key` （``InjectionKey["SkillRegistry"]``，
    键名 ``"skill_registry"``），provide 到 Runtime 根；消费方式
    ``agent.inject(skill_registry_key)`` （沿父链上溯）；未安装
    ``SkillPlugin`` 时 inject 抛
    :class:`flowing.errors.MissingProvideError`。
  - 工具注册：:class:`SkillLoadTool` （``skill-load``）注册进 Runtime
    全局工具注册表——注册不等于可见：对 LLM 可见仍需 Agent 侧显式
    声明（``.fya`` ``tools:`` 或 ``agent.add_tool("skill-load")``）。
  - ``runtime.register_skill``：``install()`` 把
    :meth:`SkillRegistry.register` 绑定为该属性（绑函数约定：检查后
    跳过；未安装本插件时该属性不存在）。
  - Agent 侧 ``prompt_blocks`` 动态块：:class:`LazySkillsPrompt`，
    注册参数 ``cache="dynamic"``、``by="skill"``、
    ``tags=["skill.catalog"]``——整块可用
    ``agent.prompt_blocks.disable_by_tag("skill.catalog")`` /
    ``disable_by_owner("skill")`` 隐藏、``enable_by_tag`` 恢复、
    ``remove_by_owner("skill")`` 移除（只影响 LLM 可见性，不影响
    编程式加载）。
  - ``agent.skill_add`` / ``agent.skill_load`` / ``agent.skill_get``：
    ``use_skill()`` 按「检查后跳过」绑定（用户已自定义同名函数时
    保留用户的）。

- 声明的钩子点：``before_skill_load`` / ``after_skill_load`` （``by="skill"``，
  ``match_on="name"``），由 ``use_skill()`` 声明、
  由 ``skill_load`` 流程在该 Agent 实例上 dispatch（谁声明谁
  dispatch）。注册开放：声明后任何扩展都可挂 handler，无需再声明。

- 挂载的钩子：本插件不挂任何 handler（扫描 / 审计等属于策略层，
  由其他扩展按 ``by="guardrail"`` / ``by="audit"`` 等自行挂载）。

- 未启用时的行为（未安装 ``SkillPlugin`` 的 Runtime、未调用
  ``use_skill()`` 的 Agent）：零开销——无 catalog 注入、无
  ``skill-load`` 工具条目、无 ``before_skill_load`` /
  ``after_skill_load`` 钩子点（访问抛
  :class:`flowing.errors.UnknownHookPointError`）、无
  ``agent.skill_load`` 方法；此时调用 ``use_skill(self)`` 抛
  :class:`flowing.errors.MissingProvideError`。

.. rubric:: Skill 定义文件与查找规则

Skill 定义是文件资源，三种等价形式（``SkillRegistry`` 统一解析为
同一个 :class:`Skill` 类的实例，无「内置 / 自定义」类层次差异）：

1. ``.md`` 形式：YAML frontmatter（``---`` 包裹，字段与
   ``.skill.fya`` 的 YAML 块同构）和 Markdown 正文。``name`` 一律
   推断（命中 ``<name>.md`` 取文件名；命中通用名 ``SKILL.md`` /
   ``skill.md`` 取目录名，kebab-case）；frontmatter 中不禁止写
   ``name``，但仅作一致性断言——与推断值不符抛
   :class:`flowing.errors.NameMismatchError` （与 Agent / Tool 同一
   语义）。``description`` 必填；正文支持 Jinja2 模板。
2. ``.skill.fya`` 形式：块结构声明，字段见 :class:`Skill`；可含
   ``$script`` 块定义 ``on_load`` 回调。注意 ``.skill.fya`` 的
   ``$script`` 与 Agent ``.fya`` 的 ``$script`` 用途不同：前者只
   定义 ``on_load``，后者定义 ``setup()`` / ``@on()`` 钩子 / 实例方法。
3. 目录形式：复杂 Skill 用目录组织，正文可 ``{% include %}``
   引用目录内的参考资料 / 术语表。

定向查找优先级（按规范名 ``<name>``，首个存在者生效）：

1. ``<name>/`` 目录（若存在）→ 目录内 ``SKILL.fya`` > ``skill.fya`` >
   ``<name>.skill.fya`` > ``<name>.fya`` > ``SKILL.md`` > ``skill.md``
2. ``<name>.skill.fya``
3. ``<name>.fya``
4. ``<name>.md``

细则（与 Agent / Tool 查找链同口径）：

- ``.fya`` 系优先于 ``.md``：同名 ``.fya`` 系与 ``.md`` 并存 →
  告警且 ``.fya`` 系优先（对齐 Tool 的「``.fya`` 优先于同名 ``.py``
  并告警」）。
- 裸名 vs 显式路径分流：裸名条目语境下目录存在但无合法定义文件
  → 继续向下查找；显式路径条目语境下目录无候选 → 直接抛
  :class:`flowing.errors.FormatError` （定点引用的目录为空几乎必为
  笔误；分流在 ``use_skill()`` 声明期执行——条目规范化经
  :func:`flowing.parser.normalize_entries` （``naming=SKILL_NAMING``），
  形态判别经 :func:`flowing.paths.classify_ref`；
  :meth:`SkillRegistry.get` 只承接裸名语义）。
- 名字推断：机制本体 :func:`flowing.paths.infer_name` （规则表
  :data:`SKILL_NAMING`）——命中 ``<name>.xxx`` 候选 → 取文件名；
  命中通用名候选（``SKILL.fya`` / ``skill.fya`` / ``SKILL.md`` /
  ``skill.md``）→ 取目录名（对齐 Agent「``agent.fya`` 命中取
  目录名」的推断规则）。
- 插件注册通道：``runtime.register_skill()`` →
  :meth:`SkillRegistry.register` 编程式注册（key 为 ``ns::规范名``）。
  裸名条目先查注册表裸名视图（``default::`` 优先于 ``builtin::``，
  default 优先，即覆盖通道），未命中才走上述文件查找链；``ns::name``
  限定名条目只查注册表。命名空间永不进入 LLM 可见面——catalog
  与 ``skill-load`` 只暴露规范名 / 别名。
- 链上顺序只是确定性判定规则——不推荐同一链路真的同时存在多个
  候选文件（属组织异味：读者需回溯优先级才能确定生效者）。

全部不存在 → 解析失败（见 :meth:`SkillRegistry.get`）。
PENDING 是 ``.fya`` 里的 ``_`` 占位值，表示该字段留待运行期赋值；
除 ``skills: _`` 的 PENDING 声明外不存在自动扫描，所有 Skill
均经 Agent 显式引用触发定向查找；``skills:`` 条目支持裸名 / 显式
相对路径 / glob 三形态，glob 展开时规范名与已显式声明条目相同的跳过
（先解析显式条目，再展开 glob）。

.. rubric:: catalog 与加载流程

catalog 懒注入：``use_skill()`` 把 :class:`LazySkillsPrompt` 注册为
``prompt_blocks`` 的动态块，每次组装上下文现场渲染——catalog 内容
反映当刻的 ``enabled`` 状态与参数覆盖，无任何缓存（渲染惰性；定义
文件已在声明期一次读入）。正文（content）由 LLM 经 ``skill-load``
决定使用时才按需加载并渲染。catalog 中 ``<name>`` 是别名
（``SkillEntry.name_alias``）；catalog 无 ``<params>`` 段（LLM
不给 skill 传参，见下节）；只有 ``enabled=True`` 的条目进入 catalog。

加载流程（LLM 入口与代码入口是同一执行路径，详见 :func:`use_skill`
的「skill_load 契约」）：

1. ``before_skill_load`` 钩子（value 为 :class:`SkillLoadContext`，
   可改 args、可 ``raise Intercepted`` 阻止加载）。
2. ``skill.on_load(agent, args)``——Skill 自己的初始化（不拦截、
   不返回值）。
3. ``skill.content.resolve(...)``——先展开 ``$`` 引用，再执行 Jinja2
   模板渲染（两步渲染顺序不可配置，见 :mod:`flowing.parsable`）。
4. ``after_skill_load`` 钩子（value 为 :class:`SkillContent`，可改
   渲染后正文）。
5. 渲染结果不合并进 ToolResult，而是以独立 ``Message`` 入队：
   ``kind=MessageKind.PLUGIN``、``source=f"skill:{name}"``、
   ``priority=MessagePriority.NORMAL``；``skill_load()`` 返回
   :class:`SkillResult`。

PLUGIN 消息进入消息级树（``Message.id`` 与 ``parent_id`` 构成的链、正常
落盘），在后续逻辑 Turn 被消费时进入 LLM 上下文。Skill 结果
（PLUGIN）与子 Agent 结果（SUBAGENT）永远不会与 TOOL 结果混淆——
Provider adapter 构建 LLM 上下文时按 ``kind`` 单独处理。

.. rubric:: Skill 参数（全部来自声明期）

LLM 不给 skill 传参：``skill-load`` 工具与 ``agent.skill_load()``
只有 ``name`` 一个入口参数；catalog 不渲染 ``<params>`` 段。Skill
声明的每个参数（``args_schema``）必须在声明期被覆盖——来源只有
两个，``specified`` （固定值 / 注入表达式 ``{{ self.inject(...) }}``）
优先于 schema 默认值。``specified`` 的值语义与 Tool / 子 Agent
完全相同（包装 Parsable、加载时以调用方 Agent 实例上下文求值；
注入表达式在求值时沿 provide 链上溯、缺失即
:class:`flowing.errors.MissingProvideError`）；声明期覆盖校验：
``skill_add`` 时逐参数检查——不在 ``specified`` 又无 schema 默认值
→ :class:`flowing.errors.FormatError` （fail-fast：失败立即报错，
不静默降级；不留到运行期）。
需要 LLM 传参的能力应建模为 Tool。

.. rubric:: 使用示例

手写子类（``setup()`` 中启用并挂钩子）：

.. code-block:: python

    async def setup(self):
        use_skill(self)
        self.add_tool("skill-load")          # 工具本体已由 install() 注册；此处声明对 LLM 可见
        # 注册开放：声明后任何 Composable 都能挂 handler（handler 由你实现）
        self.hooks.before_skill_load(self._scan_content, by="guardrail")
        self.hooks.after_skill_load(self._log_usage, by="audit")

``.fya`` 声明（``skills:`` 字段与 ``$script`` 里启用）：

.. code-block:: yaml

    # assistant.fya
    skills:
      - summarize as sum:
          args:
            max_length: 500   # specified：声明期固定值（LLM 不传参）
            work_directory: "{{ self.inject('work_directory') }}"   # 注入表达式
    ---
    $script:
    from flowing.plugins.skills import use_skill

    async def setup(self):
        use_skill(self)

.. rubric:: 行为要点（跨符号约定）

- enabled 语义：「可见性」与「可加载性」分离：
  ``enabled=False`` 的条目不进 catalog、LLM 看不到、无法经
  ``skill-load`` 工具加载（LLM 入口做 enabled 检查），但仍可编程式
  ``await agent.skill_load("name")`` 加载——代码入口不做 enabled
  检查。与 ``ToolEntry.enabled`` / ``SubagentEntry.enabled`` 语义一致。
- 覆写策略（检查后跳过）：``use_skill()`` 挂载回调（``skill_load``
  绑定、prompt 块、工具条目）采用「检查后跳过」——实例上已有同名
  定义时保留用户定义（``setup()`` 中后执行的扩展覆盖先执行扩展挂载
  的回调，调用顺序即优先级）。
- 渲染模板：catalog 渲染只设一个槽位 ``catalog_template`` （Jinja2
  模板源字符串，含 ``$./file.j2`` FILE_REF 形式；不接受
  callable 渲染器——定制即整体替换模板，内置
  :data:`DEFAULT_CATALOG_TEMPLATE` 公开可参考）。配置来源两级：
  Agent 级（``use_skill(self, catalog_template=...)`` 参数）覆盖
  Runtime 级（``SkillPlugin`` 构造参数）；两者都缺省时用内置模板。
  模板上下文变量（``entries`` / ``agent``）见 :data:`CatalogTemplate`。

.. seealso::

    - :mod:`flowing.tool` —— ``Tool`` / ``ToolDefinition`` / ``ToolEntry``，
      Skill 复用 Tool 机制（参数优先级一致，见上文）。
    - :mod:`flowing.hooks` —— ``HookRegistry.declare`` / dispatch 算法。
    - :mod:`flowing.parsable` —— ``Parsable.resolve`` 与两步渲染。
    - :mod:`flowing.message` —— ``MessageKind.PLUGIN`` 与消息级树。
    - :mod:`flowing.errors` —— ``Intercepted`` / ``MissingProvideError``。
"""


from typing import Any, ClassVar

from collections.abc import Mapping

from flowing.agent import Agent
from flowing.errors import EntryNameConflictError, FlowingError, FormatError
from flowing.message import Message, MessageKind, MessagePriority, TextBlock
from flowing.parsable import PENDING, Parsable
from flowing.parser import EntryRef, normalize_entries, split_as
from flowing.plugins import Plugin
from .models import (
    SKILL_NAMING,
    CatalogTemplate,
    Skill,
    SkillContent,
    SkillEntry,
    SkillLoadContext,
    SkillResult,
)
from .registry import SkillRegistry, skill_registry_key
from flowing.runtime import Runtime
from flowing.subagents import _expand_glob_entries
from flowing.tool import Tool, ToolDefinition


__all__ = [
    "SkillPlugin",
    "LazySkillsPrompt",
    "SkillLoadTool",
    "DEFAULT_CATALOG_TEMPLATE",
    "use_skill",
]


class LazySkillsPrompt:
    """Skill catalog 的动态 prompt 块——组装上下文时现场渲染。

    .. rubric:: 功能介绍

    ``use_skill()`` 把本类实例包装为动态 Parsable 注册进
    ``agent.prompt_blocks`` （``cache="dynamic"``、``by="skill"``、
    ``tags=["skill.catalog"]``）。每次组装上下文时 :meth:`resolve`
    被调用，现场渲染当刻的 catalog（enabled 过滤、参数覆盖、别名都
    取当刻值）。「Lazy」只指渲染惰性：``enabled`` 状态运行时
    可变、Agent 局部变量（渲染上下文）随运行变化，因此 catalog 文本
    每次现场渲染、不缓存。读取不惰性：Skill 定义文件已在
    ``use_skill()`` 声明期全部解析入 ``SkillRegistry`` （含 disabled
    条目），``resolve`` 只做 Parsable 求值与字符串拼接，不触发文件
    IO。

    .. rubric:: 行为要点

    - 只收集 ``enabled=True`` 的条目（声明顺序），从
      ``SkillRegistry`` 取已解析的 ``Skill`` （声明期已读入，无文件
      IO），以 ``catalog_template`` 模板渲染整体文本（上下文
      ``entries`` / ``agent`` 见 :data:`CatalogTemplate`）。
    - ``enabled`` 是纯渲染时过滤（与 Tool / Subagent 条目语义一致）：
      只影响本 catalog 与 LLM 可见性，不影响编程式 ``skill_load()``。
    - 无 enabled 条目 → 解析为空串，``use_skill()`` 注册的包装逻辑
      跳过整块注入（catalog 不出现；``skill-load`` 工具条目的存在性
      不受本块影响）。
    - 不缓存渲染结果；不修改任何条目状态；不触发文件 IO。
    - 管理面：整块可被 ``prompt_blocks.disable_by_tag("skill.catalog")``
      / ``disable_by_owner("skill")`` 隐藏，``enable_by_tag`` 恢复，
      ``remove_by_owner("skill")`` 移除——隐藏只影响 LLM 可见性，
      不影响编程式加载。

    .. seealso:: :class:`flowing.context.PromptBlock`、
        :data:`CatalogTemplate`、:data:`DEFAULT_CATALOG_TEMPLATE`
    """

    def __init__(
        self,
        entries: dict[str, SkillEntry],
        registry: SkillRegistry,
        *,
        catalog_template: CatalogTemplate,
    ) -> None:
        """构造动态 catalog 块（渲染惰性；读取已在声明期完成）。

        .. rubric:: 行为要点

        - ``entries`` 存引用而非快照：``agent._skill_entries`` 后续的
          enabled 切换 / 条目增删在下一次 ``resolve`` 即生效。
        - 模板在构造时已全部就位（``use_skill()`` 按 Agent 级 >
          Runtime 级 > 内置默认解析完毕），本类不再做配置回退。
        - 渲染期取 ``Skill`` 经 ``registry.get(entry.name_ori)``——
          声明期已落账解析键（文件派生技能为派生限定键），精确命中、
          零文件 IO，因此本类不持有 ``source_dir``。

        :param entries: 别名 → 条目的映射（存引用，非快照）。
        :param registry: 已注入的 ``SkillRegistry`` 实例。
        :param catalog_template: 已解析就位的渲染模板（本类不做配置
            回退）。

        .. seealso:: :func:`use_skill`、:meth:`resolve`
        """
        self.entries = entries  # 存引用而非快照：enabled 切换/条目增删下次 resolve 即生效
        self.registry = registry
        self.catalog_template = catalog_template  # 构造时已按 Agent 级 > Runtime 级 > 内置解析完毕；本类不再做配置回退

    def resolve(self, agent: Agent) -> str:
        """现场渲染 catalog 文本（同步——只做 Parsable 求值与字符串拼接，
        无 IO 等待；定义文件已全部在声明期解析入注册表）。

        .. rubric:: 行为要点

        - enabled 条目按声明序取 ``(entry, skill)`` 对，以
          ``catalog_template`` 模板渲染整体文本（Parsable TEMPLATE
          语义）；无 enabled 条目返回 ``""``。
        - 边缘情况：运行期新增声明指向的 Skill 未解析成功 → 异常上抛，
          本次上下文组装失败（不静默跳过——catalog 缺条目会让 LLM 看到
          残缺的技能清单）；模板渲染异常（语法错等）同样 fail-fast
          上抛，不静默降级。

        :param agent: 调用方 Agent 实例（模板上下文与 Parsable 求值
            上下文）。
        :return: catalog 文本；无 enabled 条目时为空串。

        .. seealso:: :class:`SkillRegistry`、:meth:`flowing.agent.Agent._assemble_context`
        """
        items: list[tuple[SkillEntry, Skill]] = []
        for entry in self.entries.values():  # 声明顺序遍历
            if not entry.enabled:  # enabled 是纯渲染时过滤，不影响编程式 skill_load
                continue
            skill = self.registry.get(  # 声明期已落账解析键，精确命中、纯内存无文件 IO
                entry.name_ori,
            )
            items.append((entry, skill))
        if not items:  # 无 enabled 条目 -> 空串，包装逻辑跳过整块注入
            return ""
        # 模板渲染：Parsable TEMPLATE 语义，上下文 entries / agent；
        # 描述字段在模板内经 .resolve(agent) 现场求值（每次渲染现场 resolve、无缓存）
        return agent.parsable(self.catalog_template).resolve(
            {"entries": items, "agent": agent})


class SkillLoadTool(Tool):
    """内置 ``skill-load`` 工具——Skill 的 LLM 入口。

    .. rubric:: 功能介绍

    能力三正交中 Skill 的「LLM 可见声明」执行侧：LLM 从
    ``<available_skills>`` catalog 选择技能后调用本工具，工具按名
    透传给 ``caller.skill_load()``。工具级 schema 只声明 ``name``
    ——LLM 不给 skill 传参，保证 LLM 入口与代码入口行为完全一致。
    Skill 加载完全复用 Tool 机制：Turn 引擎看到的就是一次普通
    ``tool_call``。

    .. rubric:: 使用示例

    启用后对 LLM 可见需显式声明（工具本体由 ``SkillPlugin.install()``
    注册）：

    .. code-block:: python

        async def setup(self):
            use_skill(self)
            self.add_tool("skill-load")

    .. rubric:: 行为要点

    - ``name`` 按别名在 ``caller._skill_entries`` 查找；命中且
      ``enabled=True`` → 调 ``skill_load(name)``；加载流程（含 PLUGIN
      消息入队）见 :func:`use_skill` 的「skill_load 契约」。工具对
      LLM 的返回是简短收据（``{"loaded": <别名>}``），正文不经
      ToolResult——PLUGIN 消息在后续逻辑 Turn 进入上下文。
    - enabled 检查在 LLM 入口：条目 ``enabled=False`` 或未声明 →
      抛 :class:`flowing.errors.FlowingError` （由 ``Tool.__call__``
      包装为 ``status="error"`` 的 LLM 可见结果），不加载、不入队。
      编程式 ``agent.skill_load()`` 无此检查。
    - 边缘情况：``name`` 缺失（LLM 未传）→ 按常规参数校验失败处理
      （error 结果）；``before_skill_load`` 中 ``raise Intercepted``
      → 工具调用以 ``blocked`` 告终。
    - 前置条件：``caller`` 必须经 ``use_skill()`` 启用（否则
      ``skill_load`` 不存在——同名属性错误，属编程错误）。
    - 不变量：同一 Skill 一次调用只产生一条 PLUGIN 消息；
      ``skill-load`` 的 ToolResult 永不包含正文。

    .. seealso:: :class:`flowing.tool.Tool` （``execute()`` 签名契约与
        返回值自动包装）、:class:`SkillResult`、:class:`SkillLoadContext`
    """

    definition: ToolDefinition = ToolDefinition(
        name="skill-load",
        description="加载指定技能的详细执行指南。可用技能见 <available_skills>。",
        params_schema={"name": {"type": "string", "description": "要加载的技能名称"}},
    )
    """类级默认声明：``name="skill-load"``、``params`` 只含 ``name``——LLM
    不传参（参数合并在 ``skill_load()`` 内完成）。
    """

    async def execute(self, *, name: str, caller: Agent) -> dict[str, Any]:
        """按名透传加载：``name`` 按别名解析条目。

        .. rubric:: 行为要点

        - 等价于 ``return await caller.skill_load(name)`` 的收据化封装；
          返回 dict 由 ``Tool.__call__`` 自动包装为
          ``status="completed"`` 的 ToolResult。
        - 不自行构造 PLUGIN 消息、不做参数校验（都在 ``skill_load()``）；
          不认识 ``specified`` （本工具不经 ``ToolEntry`` 覆写使用——
          ``use_skill()`` 以默认条目注册）。

        :param name: 目标 Skill 的别名（与 catalog ``<name>`` 一致）。
        :param caller: 发起调用的 Agent 实例（须经 ``use_skill()``
            启用）。
        :return: 收据 ``{"loaded": <别名>}``；正文经 PLUGIN 消息送达。
        :raises flowing.errors.FlowingError: ``name`` 未声明或条目
            ``enabled=False`` 时（由 ``Tool.__call__`` 包装为 error
            结果，LLM 可见）。

        .. seealso:: :meth:`flowing.tool.Tool.execute`、:class:`SkillResult`
        """
        entry = caller._skill_entries.get(name)  # 按别名查找（LLM 入口做 enabled 检查）
        if entry is None or not entry.enabled:
            raise FlowingError(  # -> flowing.errors.FlowingError；由 Tool.__call__ 包装为 error 结果
                f"技能 {name!r} 未声明或已禁用（enabled=False 时 LLM 入口拒绝，编程式 skill_load 不受限）",
            )
        skill_result = await caller.skill_load(name)  # -> SkillResult（同一执行路径，五步流程见 use_skill）
        receipt = {"loaded": name}  # 收据化封装：正文不经 ToolResult（PLUGIN 消息在 skill_load 内入队）
        return receipt


class SkillPlugin(Plugin):
    """Skill 扩展插件——阶段一入口：注册 ``skill-load`` 工具与全局注册表。

    .. rubric:: 功能介绍

    ``runtime.use(SkillPlugin(...))`` 时框架调用 ``install(runtime)``：
    向 runtime 的工具注册表注册 :class:`SkillLoadTool`，并以
    :data:`skill_registry_key` provide 一个全局 :class:`SkillRegistry`。
    构造参数是 Runtime 级默认渲染模板，可被 ``use_skill()`` 的
    Agent 级参数覆盖。``install`` 只注册、不做业务、不查询其他插件、
    不修改运行期状态（遵循插件约定：``install`` 只注册 / 协作不查询 /
    注册只在 ``install`` / 依赖只声明）。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.use(SkillPlugin())   # 不设 Runtime 级默认模板
        # 或指定全局默认渲染模板（文件承载）：
        runtime.use(SkillPlugin(catalog_template="$./catalog.md.j2"))

    .. rubric:: 行为要点

    - ``install()`` 同步完成全部注册；之后任何 Agent 可
      ``use_skill(self)`` 启用实例级能力。
    - 构造参数缺省 → Agent 未指定模板时使用内置
      :data:`DEFAULT_CATALOG_TEMPLATE`。
    - 重复 ``use()`` 同一插件实例属编程错误（框架 ``use()`` 的通用
      规则，每插件只 install 一次）。
    - 不扫描任何目录、不解析任何 Skill 文件（声明期解析由
      :func:`use_skill` 在实例启用时对声明条目一次性完成）。

    .. seealso:: :func:`use_skill` （阶段二入口）、
        :class:`flowing.plugins.Plugin` （插件基类与插件约定：``install``
        只注册 / 协作不查询 / 注册只在 ``install`` / 依赖只声明）、
        :meth:`flowing.runtime.Runtime.use`
    """

    def __init__(
        self,
        *,
        catalog_template: CatalogTemplate | None = None,
    ) -> None:
        """构造插件（可携带 Runtime 级默认渲染模板）。

        .. rubric:: 行为要点

        - ``None`` 表示「不为 Runtime 级设默认」——最终模板解析顺序：
          ``use_skill()`` 参数 > 本构造参数 > 内置 catalog 模板。
        - 构造不产生任何注册副作用（注册只在 ``install``）。

        :param catalog_template: Runtime 级默认渲染模板（缺省
            ``None``）。

        .. seealso:: :data:`CatalogTemplate`、:data:`DEFAULT_CATALOG_TEMPLATE`
        """
        self.catalog_template = catalog_template  # None = 不为 Runtime 级设默认；最终解析：use_skill() 参数 > 本构造参数 > 内置

    name: ClassVar[str] = "skill"
    """注册名（显式声明，无框架默认；推荐格式见 ``flowing.plugins``
    命名约定）。
    """

    dependencies: ClassVar[list[str]] = []   # 与基类 Plugin 的 ClassVar 对齐
    """依赖声明（类属性元数据）。本插件无依赖，为空列表。

    .. seealso:: :meth:`flowing.runtime.Runtime._check_dependencies`
    """

    def install(self, runtime: Runtime) -> None:
        """阶段一：注册 ``skill-load`` 工具并 provide 全局 ``SkillRegistry``。

        .. rubric:: 行为要点

        - ``runtime.register_tool(SkillLoadTool())`` 与
          ``runtime.provide(skill_registry_key, SkillRegistry(...))``——
          Runtime 级默认模板随注册表注入（三级解析链中段通道）；
          另把 ``registry.register`` 绑定为 ``runtime.register_skill``——采用
          绑函数约定：检查后跳过（核心无 ``register_skill`` 方法，注册入口
          由本插件注入；未安装本插件时该属性不存在）。同步返回。
        - 边缘情况：``skill-load`` 规范名已被占用 →
          :class:`flowing.errors.ToolNameConflictError` （重名永远
          不允许）；``skill_registry_key`` 重复 provide 是覆盖更新
          （后者生效，provide 机制的全局约定——插件间靠键名前缀约定
          避免冲突）。

        :param runtime: 当前安装的 Runtime 实例。
        :raises flowing.errors.ToolNameConflictError: 工具规范名冲突时。

        .. seealso:: :class:`SkillLoadTool`、:class:`SkillRegistry`
        """
        registry = SkillRegistry(catalog_template=self.catalog_template)
        runtime.register_tool(SkillLoadTool())
        runtime.provide(skill_registry_key, registry)  # Runtime 级默认模板随注册表注入
        if not hasattr(runtime, "register_skill"):   # 绑函数约定：检查后跳过（不覆盖用户自定义）
            runtime.register_skill = registry.register   # 插件向 runtime 注入的注册入口（核心不认识 skills；未装本插件时 runtime.register_skill 不存在 -> AttributeError）


def use_skill(
    agent: Agent,
    *,
    catalog_template: CatalogTemplate | None = None,
) -> None:
    """阶段二：为单个 Agent 实例启用 Skill 能力（Composable）。

    .. rubric:: 功能介绍

    在 ``setup()`` 中调用。完成后该实例拥有：catalog 动态注入、
    ``skill-load`` 工具条目（对 LLM 可见仍需显式声明，见下）、
    ``before_skill_load`` / ``after_skill_load`` 钩子点、以及绑定
    方法 ``agent.skill_load``。声明的全部 Skill 定义文件（含 disabled
    条目）在本调用内一次性解析入注册表（读取不惰性；解析失败在此刻
    即报错，不推迟到运行期）。

    .. rubric:: 使用示例

    手写子类：

    .. code-block:: python

        async def setup(self):
            use_skill(self, catalog_template=DEFAULT_CATALOG_TEMPLATE)
            self.add_tool("skill-load")   # 工具本体已由 install() 注册；此处声明对 LLM 可见
            # 注册开放：声明后任何 Composable 都能挂 handler（handler 由你实现）
            self.hooks.before_skill_load(self._scan_content, by="guardrail")
            self.hooks.after_skill_load(self._log_usage, by="audit")

    ``.fya`` 入口：

    .. code-block:: yaml

        # assistant.fya
        skills:
          - summarize as sum:
              args:
                max_length: 500   # specified：声明期固定值（LLM 不传参）
                work_directory: "{{ self.inject('work_directory') }}"   # 注入表达式
        ---
        $script:
        from flowing.plugins.skills import use_skill

        async def setup(self):
            use_skill(self)

    .. rubric:: 行为要点

    严格按以下顺序执行（行为序列是契约，扩展协作依赖它）：

    1. ``registry = agent.inject(skill_registry_key)``——未安装
       ``SkillPlugin`` 时此处抛
       :class:`flowing.errors.MissingProvideError`。
    2. ``agent.hooks.declare("before_skill_load", by="skill")`` 与
       ``declare("after_skill_load", by="skill")``——幂等声明：同名且
       同 ``by`` 重复调用返回已有 ``HookList`` （不报错）；仅同名而
       不同 ``by`` 才抛
       :class:`flowing.errors.DuplicateHookPointError`。
    3. 绑定 ``agent.skill_add`` （「检查后跳过」，与第 6/7 步同律；
       契约见下「skill_add 契约」）——条目装配的单点，下一步
       逐条委托它。
    4. 解析 ``agent.skills`` （``.fya`` 的 ``skills:`` 落入 ``_extra``
       的原始声明；无该字段视为空列表；字段声明为 ``_`` （PENDING）
       但未赋值兑现 → :class:`flowing.errors.FormatError`）：裸名 /
       ``as`` 别名 / 显式路径 / glob 形态经
       :func:`flowing.parser.normalize_entries`
       （``naming=SKILL_NAMING``）规范化为 ``EntryRef``；glob 项先行
       展开并排除已显式声明的规范名。逐条委托 ``agent.skill_add(ref)``
       ——条目构造、注册表一次性预解析（含 disabled 条目，读取不
       惰性，失败此刻即报错）、``name_ori`` 落账都在 ``skill_add``
       内完成。
    5. 解析渲染模板（只收模板字符串）：本函数 ``catalog_template``
       参数 > ``SkillPlugin`` 构造参数 > 内置
       :data:`DEFAULT_CATALOG_TEMPLATE`；构造 :class:`LazySkillsPrompt`
       并注册进 ``agent.prompt_blocks`` （``cache="dynamic"``、
       ``by="skill"``、``tags=["skill.catalog"]``）。
    6. 绑定 ``agent.skill_load`` （见下「skill_load 契约」）；采用
       「检查后跳过」——仅当 agent 当前没有该函数才绑定
       （``hasattr`` 检查，含类级方法）。理由：开发者可能已定义了自己
       的加载逻辑，绑定方不得覆盖（``flowing.plugins`` 的绑函数约定）。
    7. 绑定 ``agent.skill_get`` （上下文感知的技能解析门面，薄委托
       ``SkillRegistry.get(name, source_dir=agent.source_dir())``）；
       同「检查后跳过」律。

    工具可见性：本函数不代绑 ``skill-load``——工具对 LLM 可见只经
    ``.fya`` ``tools:`` 声明或用户显式 ``agent.add_tool("skill-load")``，
    工具本体由 ``SkillPlugin.install()`` 在 Runtime 级注册。

    幂等性边界：第 2 步的钩子点声明与第 3/6/7 步的函数绑定对同一
    实例重复调用是安全的（幂等 / 检查后跳过）；但对同一实例第二次
    完整调用本函数会在第 4 步因别名已存在抛
    :class:`flowing.errors.EntryNameConflictError` （``skill_add`` 的
    fail-fast）。recover 重跑 ``setup()`` 时 ``setup`` 作用于新实例，
    不触发此路径。

    skill_load 契约（``async def skill_load(name: str) ->
    SkillResult``，由本函数绑定为实例方法）：

    1. 按别名查 ``_skill_entries``；未声明 → 抛 ``KeyError`` （编程
       错误，属调用方责任）。不做 enabled 检查（编程式入口的特权）。
    2. 经 ``registry.get(entry.name_ori)`` 取共享
       ``Skill`` （声明期已预解析并落账解析键——文件派生技能为派生
       限定键，本步为纯内存查找）。
    3. 参数合并（LLM 不传参；优先级低 → 高依次覆盖）：
       ``args_schema`` 默认值 → ``specified`` （以调用方 Agent 实例
       上下文现场求值；其中注入表达式 ``{{ self.inject('key') }}``
       在求值时沿 provide 链上溯，缺失 →
       :class:`flowing.errors.MissingProvideError`）。覆盖完整性已在
       ``skill_add`` 声明期校验，本步无缺参失败分支。
    4. dispatch ``before_skill_load`` （value 为
       :class:`SkillLoadContext`，可改 args；``Intercepted`` 终止后续
       步骤）。
    5. ``skill.on_load(agent, args)`` （若存在；不拦截、不返回值）。
    6. ``rendered = skill.content.resolve(...)`` （渲染上下文包含
       agent 局部变量与合并 args；先 ``$`` 展开再 Jinja2，同步）。
    7. dispatch ``after_skill_load`` （value 为 :class:`SkillContent`，
       可改正文）。
    8. ``agent.enqueue_message(Message(kind=MessageKind.PLUGIN,
       source=f"skill:{name}", content=[TextBlock(text=body)],
       priority=MessagePriority.NORMAL))``。
    9. 返回 ``SkillResult(content=body)``。

    - 不把正文合并进任何 ToolResult；不检查 LLM 是否「真的看过
      catalog」；不做加载次数限制。

    skill_add 契约（``def skill_add(name: str | EntryRef, *,
    alias: str | None = None, body: dict | None = None) -> SkillEntry``，
    由本函数绑定为实例方法——与 :meth:`flowing.agent.Agent.add_tool` /
    ``add_agent`` 同构的第三条资源管线）：

    1. 归一：``name`` 为 ``EntryRef`` 时 ``alias``/``body`` 必须
       缺省（重复 → :class:`flowing.errors.FormatError`）；为 ``str``
       时经 :func:`flowing.parser.normalize_entries`
       （``naming=SKILL_NAMING``）构造 EntryRef。``str`` 接受全部引用
       形态，与 ``.fya`` 声明一致：裸名 / ``ns::name`` / 相对或绝对
       路径 / 路径带裸名。
    2. body 判别（单点）：键集固定 ``description`` / ``args`` /
       ``enabled`` （无 ``inject`` 键——注入写 args 里的
       ``"{{ self.inject('key') }}"`` 表达式）；未知键 →
       :class:`flowing.errors.FormatError`。去向：``description`` →
       ``override_description`` （``str`` 包装 Parsable，``Parsable``
       透传，``_`` → 空补丁 ``None``；catalog 渲染时以调用方 Agent
       为上下文自动求值）；``args`` → 每个值进 ``specified`` （包装
       Parsable，加载时以调用方 Agent 实例上下文求值——注入表达式
       同路）；``_`` （PENDING）值视为未声明该参数（不进
       ``specified``）；``args`` 键含 ``as`` → ``FormatError``，无改名
       通道；``enabled`` → 布尔原样。
    3. 冲突：同 alias 已存在 →
       :class:`flowing.errors.EntryNameConflictError` （绑定层统一
       fail-fast）。
    4. 预解析、落账与覆盖校验：``registry.get(entry.name_ori,
       source_dir=agent.source_dir())`` 一次性解析（含 disabled，
       失败此刻即报错）；文件派生技能把 ``entry.name_ori`` 落账为
       派生限定键（``skill.registry_key``），注册表命中
       （``default::``/``builtin::``）保持裸名。覆盖校验：
       对 ``skill.args_schema`` 逐参数检查——不在 ``specified``
       又无 schema 默认值 → :class:`flowing.errors.FormatError`
       （声明期 fail-fast）。
    5. 写入 ``agent._skill_entries[entry.name_alias]`` 并返回条目。
       同步、立即生效（下一次 catalog 渲染可见）。

    - 时序约束：深层块（``$skills.<alias>.xxx:``）的填回先于
      ``skill_add`` 调用（装配层先 merge 具名块，与 tool 侧同律）。

    :param agent: 要启用的 Agent 实例。
    :param catalog_template: Agent 级渲染模板（覆盖 Runtime 级；
        缺省 ``None``）。
    :raises flowing.errors.MissingProvideError: 未安装 ``SkillPlugin``
        而调用本函数（第 1 步 inject）时。
    :raises flowing.errors.FormatError: ``agent.skills`` 声明形态非法
        （非列表 / PENDING 未兑现）、条目 body 含未知键或 ``args``
        键含 ``as``、覆盖校验未通过时。
    :raises flowing.errors.EntryNameConflictError: 对同一实例重复
        调用本函数（别名已存在）时。

    .. seealso:: :class:`SkillPlugin` （阶段一入口）、
        :class:`SkillLoadTool` （LLM 入口，与 ``skill_load`` 同路径）、
        :class:`SkillEntry` （``skills:`` 声明的绑定产物）
    """
    registry: SkillRegistry = agent.inject(skill_registry_key)  # 第 1 步：未安装 SkillPlugin -> MissingProvideError
    agent.hooks.declare("before_skill_load", by="skill")  # 第 2 步：幂等声明（同名同 by 重复调用幂等）
    agent.hooks.declare("after_skill_load", by="skill")
    if not hasattr(agent, "_skill_entries"):
        agent._skill_entries = {}  # 绑定层条目表（别名 -> SkillEntry）；核心不感知，本插件自建
    if not hasattr(agent, "skill_add"):  # 第 3 步：「检查后跳过」——条目装配单点（契约见 docstring「skill_add 契约」）
        def skill_add(name: str | EntryRef, *, alias: str | None = None,
                      body: dict[str, Any] | None = None) -> SkillEntry:
            if isinstance(name, EntryRef):   # 归一：EntryRef 与 alias/body 不可同传
                if alias is not None or body is not None:
                    raise FormatError("EntryRef 与 alias/body 不可同传")
                ref = name
            else:
                item = {f"{name} as {alias}" if alias is not None else name: body or {}}
                ref = normalize_entries([item], naming=SKILL_NAMING)[0]
            # body 判别（键集固定 description/args/enabled——无 inject 键；未知键 -> FormatError）
            override_description = None
            specified: dict[str, Parsable] = {}
            enabled = True
            for bkey, bvalue in ref.body.items():
                if bkey == "description":
                    # description -> override_description（str->Parsable 包装 / Parsable 透传 / _->None 空补丁）
                    if bvalue is PENDING:
                        override_description = None
                    elif isinstance(bvalue, Parsable):
                        override_description = bvalue
                    else:
                        override_description = Parsable(bvalue)
                elif bkey == "args":
                    if bvalue is PENDING:
                        continue   # args: _ —— 空补丁语义（与条目覆写位 PENDING 同口径）
                    if not isinstance(bvalue, Mapping):
                        raise FormatError(f"skill 条目的 args 必须是映射: {bvalue!r}")
                    for pname, pvalue in bvalue.items():
                        if split_as(str(pname))[1] is not None:
                            # 键含 as -> FormatError（无改名通道）
                            raise FormatError(f"skill 条目 args 的参数键不允许 as 改名: {pname!r}")
                        if pvalue is PENDING:
                            continue   # _（PENDING）值视为未声明该参数（不进 specified，覆盖校验照常）
                        specified[pname] = pvalue if isinstance(pvalue, Parsable) else Parsable(pvalue)
                elif bkey == "enabled":
                    enabled = bool(bvalue)
                else:
                    raise FormatError(
                        f"skill 条目含未知键: {bkey!r}（键集固定 description/args/enabled）")
            if ref.alias in agent._skill_entries:   # 同 alias = 笔误（绑定层统一 fail-fast）
                raise EntryNameConflictError(ref.alias, kind="skill")
            entry = SkillEntry(
                name_alias=ref.alias, name_ori=ref.raw,
                specified=specified,
                override_description=override_description, enabled=enabled,
            )
            skill = registry.get(entry.name_ori, agent.source_dir())  # 声明期一次性预解析（含 disabled），失败此刻即报错
            # 落账 name_ori：文件派生技能记派生限定键（skill.registry_key，热路径
            # 精确命中零文件 IO）；注册表命中（default::/builtin::）保持裸名
            if skill.registry_key is not None and skill.registry_key not in (
                    f"default::{entry.name_ori}", f"builtin::{entry.name_ori}"):
                entry.name_ori = skill.registry_key  # type: ignore[assignment]
            # 覆盖校验（声明期 fail-fast）：args_schema 每参数须被
            # specified（固定值/注入表达式）覆盖或带默认值
            uncovered = [
                pname for pname, prop in skill.args_schema.items()
                if pname not in entry.specified and "default" not in prop
            ]
            if uncovered:
                raise FormatError(
                    f"技能 {ref.raw!r} 的参数未被 specified 覆盖且无默认值: {uncovered}")
            agent._skill_entries[entry.name_alias] = entry
            return entry
        agent.skill_add = skill_add  # type: ignore[attr-defined]
    # 第 4 步：解析 agent.skills（_extra 原始声明；裸名 / as 别名 / 显式路径 / glob 形态，
    # glob 项先行展开并排除已显式声明的规范名）——逐条委托 agent.skill_add（装配单点）
    source_dir = agent.source_dir()  # 定向查找根：agent 定义文件所在目录本身（不设 skills/ 子目录；None → 纯注册表）
    raw_decl = getattr(agent, "skills", None)
    if raw_decl is None:
        raw_items: list[Any] = []   # 无 skills: 字段视为空列表
    elif raw_decl is PENDING:
        # skills: _ 是延迟定义承诺——须在调用本函数前赋值兑现；仍为 PENDING
        # 即承诺未兑现，fail-fast（插件层的 PENDING 检查点，核心检查不覆盖
        # _extra 字段）
        raise FormatError("skills 字段声明为 _（PENDING）但在 use_skill 前未赋值兑现")
    elif isinstance(raw_decl, list):
        raw_items = list(raw_decl)
    else:
        raise FormatError(f"skills 字段必须是列表: {raw_decl!r}")
    if source_dir is not None:
        # glob 展开复用阶段 3 落地的通用 helper（三类条目同一份实现）：
        # 入参是规范化之前的原始列表项；先收显式条目，glob 命中与已收条目
        # 解析后绝对路径相同（同一资源）的跳过
        refs = _expand_glob_entries(
            raw_items, naming=SKILL_NAMING, source_dir=source_dir,
            project_root=getattr(agent.runtime, "project_root", None))
    else:
        refs = normalize_entries(raw_items, naming=SKILL_NAMING)   # 无文件上下文：纯注册表条目
    for ref in refs:
        agent.skill_add(ref)  # type: ignore[attr-defined]
    # 第 5 步：渲染模板解析——本函数参数 > SkillPlugin 构造参数（随注册表注入）> 内置 DEFAULT_CATALOG_TEMPLATE
    catalog = catalog_template or registry.catalog_template or DEFAULT_CATALOG_TEMPLATE
    lazy = LazySkillsPrompt(
        agent._skill_entries, registry,
        catalog_template=catalog,
    )
    # 包装为动态块注册进 prompt_blocks——PromptBlock.content 的求值协议是
    # 「带 resolve(agent) 方法的对象」，LazySkillsPrompt 天然满足（duck-typed，
    # _assemble_context 只做 str(block.content.resolve(self))）；块名取
    # "skills"（标识与分组管理用，非唯一键）
    agent.prompt_blocks.append(  # type: ignore[arg-type]
        "skills", lazy, cache="dynamic", by="skill", tags=["skill.catalog"],
    )
    # skill-load 可见性走 .fya tools: 或用户显式 add_tool（本体由 SkillPlugin.install() 注册）
    if not hasattr(agent, "skill_load"):  # 第 6 步：「检查后跳过」——agent 当前已有此函数（实例属性或类级方法）则保留用户的，不绑定
        async def skill_load(name: str) -> SkillResult:
            return await _load_skill(agent, name)
        agent.skill_load = skill_load  # type: ignore[attr-defined]
    if not hasattr(agent, "skill_get"):  # 第 7 步：上下文感知的技能解析门面（同「检查后跳过」律）
        def skill_get(name: str) -> Skill:
            """薄委托 ``SkillRegistry.get(name, source_dir=agent.source_dir())``——
            裸名先查本 Agent 定义文件所在目录的文件链（文件覆盖 default::/builtin::），
            与 ``Agent.get_tool`` 同构。"""
            return registry.get(name, source_dir=agent.source_dir())
        agent.skill_get = skill_get  # type: ignore[attr-defined]


DEFAULT_CATALOG_TEMPLATE: str = (
    '{% if entries %}<available_skills>\n'
    '{% for entry, skill in entries %}<skill>\n'
    '  <name>{{ entry.name_alias }}</name>\n'
    '  <description>{{ (entry.override_description or skill.description).resolve(agent) }}</description>\n'
    '</skill>\n'
    '{% endfor %}</available_skills>{% endif %}'
)
"""内置默认 catalog 模板：把逐条 ``<skill>`` 片段包进 ``<available_skills>``。

.. rubric:: 功能介绍

唯一渲染槽位的内置缺省值。槽位只收 Jinja2 模板字符串，不接受
callable 渲染器——定制渲染风格即整体替换模板（本常量公开可参考 /
派生）。

模板上下文（:data:`CatalogTemplate` 变量表）：``entries`` 为
``(entry, skill)`` 对列表（顺序即 ``skills:`` 声明顺序，模板不得
重排）；``agent`` 为调用方 Agent。描述字段为 Parsable——
``override_description`` （非 ``None`` 时优先）或 ``skill.description``
在模板内经 ``.resolve(agent)`` 现场求值（每次渲染现场 resolve、
无缓存）。空列表经 ``{% if entries %}`` 渲染为 ``""`` （整块不注入；
``LazySkillsPrompt`` 在此之前也有短路）。

无 ``<params>`` 段——LLM 不给 skill 传参，条目对 LLM 只暴露
名称与描述。

.. rubric:: 使用示例

产物形态：

.. code-block:: xml

    <available_skills>
    <skill>
      <name>sum</name>
      <description>将长文本压缩为结构化摘要。</description>
    </skill>
    </available_skills>

自定义（整体替换，文件承载）：

.. code-block:: python

    use_skill(self, catalog_template="$./my-catalog.md.j2")

.. rubric:: 行为要点

- 渲染经 Parsable TEMPLATE 语义（include 基准为该 agent 的
  ``source_dir``）；渲染异常 fail-fast 上抛，不静默降级。

.. seealso:: :data:`CatalogTemplate`、:class:`LazySkillsPrompt`
"""


async def _load_skill(
    agent: Agent, name: str
) -> SkillResult:
    """``agent.skill_load`` 的默认实现（加载流程编排）。

    内部 API，不属稳定契约——公开契约是 :func:`use_skill` 绑定后的实例
    方法 ``agent.skill_load(name)``；完整行为序列（五步主流程与
    参数合并规则）见 :func:`use_skill` 的「skill_load 契约」。独立成模块
    级函数是为了让「检查后跳过」的覆写策略可比较（``agent.skill_load``
    不是本函数的用户定义被保留）。

    .. seealso:: :func:`use_skill`、:class:`SkillResult`
    """
    entry = agent._skill_entries[name]  # 契约第 1 步：按别名查；未声明 -> KeyError；不做 enabled 检查
    registry: SkillRegistry = agent.inject(skill_registry_key)
    skill = registry.get(entry.name_ori)  # 第 2 步：声明期已预解析并落账解析键（文件派生技能为派生限定键），纯内存查找
    # 第 3 步：合并（LLM 不传参；低 -> 高：schema 默认值 -> specified 求值
    # ——specified 含固定值与注入表达式，注入表达式在求值时沿 provide 链上溯）；
    # 覆盖完整性已在 skill_add 声明期校验，本步无缺参分支
    merged: dict[str, Any] = {}
    for pname, prop in skill.args_schema.items():
        if "default" in prop:
            merged[pname] = prop["default"]
    for pname, ps in entry.specified.items():  # specified：调用方 Agent 实例上下文现场求值（注入表达式在此沿 provide 链上溯，缺失 -> MissingProvideError）
        merged[pname] = ps.resolve(agent)
    ctx = SkillLoadContext(agent=agent, name=name, args=merged)
    ctx = await agent.hooks.before_skill_load.dispatch(agent, ctx)  # 第 4 步：可改 args；raise Intercepted 终止后续
    if skill.on_load is not None:  # 第 5 步：不拦截、不返回值；抛异常则加载失败上抛
        skill.on_load(agent, ctx.args)
    # 第 6 步：渲染上下文 = agent 局部变量 + 合并 args（复刻 Parsable 摊平
    # 顺序：_extra < 实例属性 < agent/self 入口；合并 args 置最高优先级；
    # 经 agent.parsable 重绑定到调用方实例后，env/config 注入与
    # include/FILE_REF 的 source_dir 基准自动就位）
    render_context = {
        **agent._extra,
        **agent.__dict__,
        "agent": agent,
        "self": agent,
        **ctx.args,
    }
    rendered = agent.parsable(skill.content.source).resolve(render_context)  # 先 $ 展开再 Jinja2，同步
    content = SkillContent(name=name, body=rendered)
    content = await agent.hooks.after_skill_load.dispatch(agent, content)  # 第 7 步：可改渲染后正文
    await agent.enqueue_message(Message(  # 第 8 步：独立 PLUGIN 消息入队，不合并进 ToolResult
        kind=MessageKind.PLUGIN,  # -> flowing.message.MessageKind
        source=f"skill:{name}",
        content=[TextBlock(text=content.body)],
        priority=MessagePriority.NORMAL,
    ))
    return SkillResult(content=content.body)  # 第 9 步：与 PLUGIN 消息正文逐字相同
