"""``flowing.plugins.skills.models`` —— Skill 扩展的数据对象。

本模块定义 Skill 扩展的全部数据对象：:data:`SKILL_NAMING`（技能文件
的身份名推断规则表）、:data:`CatalogTemplate`（catalog 渲染模板的
类型别名）、:class:`Skill`（可执行对象）、:class:`SkillEntry`
（Agent 级绑定）、:class:`SkillLoadContext` / :class:`SkillContent`
（加载流程两个钩子点的 value）、:class:`SkillResult`（``skill_load``
的返回值）。

.. seealso:: :mod:`flowing.plugins.skills`（扩展的启用方式与整体契约）、
    :mod:`flowing.plugins.skills.registry`（注册表）、
    :mod:`flowing.plugins.skills.skills`（插件与加载流程）
"""


from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from flowing.agent import Agent
from flowing.params import expand_args_schema, schema_to_model
from flowing.parsable import Parsable
from flowing.paths import NamingRules


SKILL_NAMING = NamingRules(
    suffixes=(".skill.fya", ".fya", ".md"),
    generic_names=frozenset({"SKILL.fya", "skill.fya", "SKILL.md", "skill.md"}),
)
"""Skill 定义文件的身份名推断规则表（:class:`flowing.paths.NamingRules`）。

``use_skill()`` 解析 ``skills:`` 条目时把本规则表传给
:func:`flowing.parser.normalize_entries`，技能名的推断规则如下：

- 命中 ``<name>.skill.fya`` / ``<name>.fya`` / ``<name>.md`` 候选 →
  身份名取文件名去扩展名的部分；
- 命中通用名候选（``SKILL.fya`` / ``skill.fya`` / ``SKILL.md`` /
  ``skill.md``）→ 身份名取所在目录名（与 Agent 的
  ``agent.fya`` 命中取目录名同一规则）。

.. seealso:: :func:`flowing.paths.infer_name`、:func:`flowing.parser.normalize_entries`
"""


CatalogTemplate: TypeAlias = str
"""catalog 渲染模板的类型别名——只收 Jinja2 模板源字符串。

本类型是 catalog 的唯一渲染槽位：模板内容是 Jinja2 模板源字符串
（可以是 ``$./file.j2`` 这类文件引用形式），不接受 callable
渲染器——定制渲染风格即整体替换模板字符串，内置模板
:data:`DEFAULT_CATALOG_TEMPLATE` 公开可参考。

模板上下文变量：

- ``entries``：``list[tuple[SkillEntry, Skill]]``——「(条目, 已解析
  Skill)」对列表，只含 ``enabled=True`` 的条目，顺序即 ``skills:``
  声明顺序；空列表时模板应渲染为空串（:class:`LazySkillsPrompt`
  据此跳过整块注入，内置模板以 ``{% if entries %}`` 保证）。
- ``agent``：调用方 Agent 实例。条目的描述字段是
  :class:`flowing.parsable.Parsable`，模板内以
  ``(entry.override_description or skill.description).resolve(agent)``
  现场求值（每次渲染现场 resolve、无缓存）。

.. rubric:: 行为要点

- 渲染经 Parsable TEMPLATE 语义（先展开 ``$`` 文件引用，再执行
  Jinja2 模板渲染；``include`` 的基准目录是调用方 Agent 的
  ``source_dir``）。
- 渲染异常 fail-fast 上抛，不静默降级。
- 条目中的 ``Skill`` 已在声明期解析入注册表，模板渲染本身不触发
  文件 IO（``$`` FILE_REF 模板例外：模板源自身的读取发生在求值时）。

.. seealso:: :data:`DEFAULT_CATALOG_TEMPLATE`
"""


class Skill:
    """Skill 对象模型——三种定义文件统一解析出的「可执行对象」。

    .. rubric:: 功能介绍

    能力三正交中 Skill 的「可执行对象」层：:class:`SkillRegistry` 把
    三种定义形式（``.md`` / ``.skill.fya`` / 目录，见模块 docstring
    「Skill 定义文件与查找规则」）统一解析为本类的实例。框架核心不
    认识本类——它由 ``SkillPlugin`` 定义与消费。所有 Skill 同一个类，
    差异全部在数据（``description`` / ``content`` / ``args_schema`` /
    ``on_load``），不存在「内置 / 自定义」的类层次。

    .. rubric:: 使用示例

    ``.md`` 形式（frontmatter 为 YAML，正文为 Markdown）:

    .. code-block:: markdown

        ---
        name: summarize
        description: 将长文本压缩为结构化摘要，优先保留数字、结论和行动项。
        ---

        {% if locale == 'zh' %}
        ## 执行逻辑
        提取核心结论、关键数字和行动项，用简体中文输出。
        {% else %}
        ## Execution Logic
        Extract key conclusions, metrics, and action items. Reply in English.
        {% endif %}

    ``.skill.fya`` 形式（块结构声明，``$script`` 只用于定义
    ``on_load``）:

    .. code-block:: yaml

        # summarize.skill.fya
        name: summarize
        description: 将长文本压缩为结构化摘要。
        content: $./summarize-body.md
        args:
          max_length:                 # 完整写法 + default（可选）
            type: integer
            default: 1000
            description: 摘要最大长度（字符）
          work_directory: ""          # 糖：字面量 → {type: string, default: ""}

        $script:
        def on_load(self, agent, args):
            \"\"\"Skill 加载时执行（before_skill_load 钩子之后、正文渲染之前）。\"\"\"
            agent.state.register("last_skill", self.name)

    .. rubric:: 行为要点

    - 实例由 :meth:`SkillRegistry.get` 产出，在注册表中跨 Agent 共享
      （同一 ``ns::name`` 全键同一实例）；调用方不得修改共享实例。
    - ``description`` / ``content`` 的 Parsable 求值发生在每次使用
      时（catalog 渲染 / ``skill_load``），渲染上下文是调用方 Agent
      ——共享实例上绝不缓存渲染结果。
    - 本类不触发加载：加载流程编排在 ``skill_load()`` 一侧；``on_load``
      不是钩子（不走钩子 dispatch、不可 ``raise Intercepted`` 阻断加载；
      抛普通异常则加载失败并上抛）。
    - 边缘情况：``.md`` 形式无 ``args_schema`` （空 dict），``on_load``
      为 ``None``；``.md`` 缺 ``description`` → 解析失败（必填字段缺失，
      抛 :class:`flowing.errors.MissingFieldError`）。
    - 不变量：``name`` 为规范名，与 ``SkillRegistry`` 的 key 一致；
      ``content`` 必填（``.md`` 形式即 frontmatter 之后的正文）。

    .. seealso:: :class:`SkillRegistry`、:class:`SkillEntry`、
        :class:`flowing.parsable.Parsable`
    """

    name: str
    """规范名——``SkillRegistry`` 中的 key，一律由文件名 / 目录名推断
    （kebab-case）。命中 ``<name>.skill.fya`` / ``<name>.fya`` /
    ``<name>.md`` 候选取文件名；命中通用名候选（``SKILL.fya`` /
    ``skill.fya`` / ``SKILL.md`` / ``skill.md``）取目录名。
    定义文件里不禁止写 ``name``，但仅作一致性断言——与推断值
    不符抛 :class:`flowing.errors.NameMismatchError`（与 Agent /
    Tool 同一语义）。

    .. seealso:: :class:`SkillRegistry`
    """
    description: Parsable[str]
    """catalog ``<description>`` 的来源。保持 Parsable 而非渲染后字符串：
    渲染上下文是调用方 Agent 的局部变量，而 Skill 实例在注册表中
    跨 Agent 共享，只有使用时才能求值。catalog 渲染时由模板经
    ``.resolve(agent)`` 现场求值（见 :data:`DEFAULT_CATALOG_TEMPLATE`）。

    .. seealso:: :data:`DEFAULT_CATALOG_TEMPLATE`
    """
    content: Parsable[str]
    """Skill 正文（执行指南），惰性求值——存的是「如何解析」的指令，
    不是解析结果。``skill_load()`` 对其 ``resolve``：先展开 ``$`` 文件
    引用，再执行 Jinja2 模板渲染；渲染上下文包含调用方 Agent 的局部
    变量与合并后的 args。

    .. seealso:: :meth:`flowing.parsable.Parsable.resolve`
    """
    args_schema: dict[str, dict[str, Any]]
    """Skill 自身声明的参数 schema——``.skill.fya`` 的 ``args:`` 块经
    :func:`flowing.params.expand_args_schema` 归一化后的 JSON Schema
    properties；``.md`` 形式为空 dict。LLM 不给 skill 传参，本字段的
    两个消费者：``skill_add`` 的声明期覆盖校验（每个参数须被
    ``specified`` 覆盖或有默认值，否则 ``FormatError``）与
    ``skill_load`` 的默认值填充。空 dict 的技能开箱可用（无参数即无
    覆盖义务）。

    .. seealso:: :mod:`flowing.params`、:class:`SkillEntry`
    """
    on_load: Callable[[Agent, dict[str, Any]], Any] | None
    """``$script`` 中定义的加载回调；无 ``$script`` 或未定义则为 ``None``。
    ``$script`` 里的 ``def on_load(self, agent, args)`` 经描述符绑定后，
    框架以 ``(agent, args)`` 调用（``self`` 即本 Skill 实例）。时序：
    ``before_skill_load`` 钩子之后、``content`` 渲染之前；不拦截、
    不返回值（返回值被忽略）；抛异常则加载失败、异常上抛。

    .. seealso:: :class:`SkillLoadContext`
    """
    _extra_fields: dict[str, Any]
    """``.skill.fya`` / frontmatter 中框架不认识的任意额外字段，经
    ``__getattr__`` 透明访问。内部 API，不属稳定契约。
    """
    registry_key: str | None
    """注册表全键（``ns::name``），``SkillRegistry`` 落账时回写；未注册
    实例为 ``None``。``use_skill`` 预解析后对文件派生技能把本字段
    落账为 ``SkillEntry.name_ori`` （含目录派生命名空间的限定键，catalog
    渲染 / ``skill_load`` 热路径精确命中、零文件 IO）。内部 API，不属
    稳定契约。
    """

    def __init__(
        self,
        name: str,
        description: "str | Parsable[str]" = "",
        content: "str | Parsable[str]" = "",
        *,
        args_schema: dict[str, dict[str, Any]] | None = None,
        on_load: Callable[[Agent, dict[str, Any]], Any] | None = None,
        _extra_fields: dict[str, Any] | None = None,
        registry_key: str | None = None,
    ) -> None:
        """构造 Skill 实例（解析器与编程式注册的统一入口）。

        .. rubric:: 行为要点

        - ``description`` / ``content`` 收 ``str`` （包装为
          :class:`flowing.parsable.Parsable`）或已构造的 ``Parsable`` （透传）——保持「存如何解析、不存结果」的惰性语义。
        - ``args_schema`` 缺省为空 dict（无参数即无覆盖义务）；
          ``registry_key`` 缺省 ``None`` （未注册实例）。
        - 本构造不读文件、不求值、不注册（注册是
          :class:`SkillRegistry` 的职责）。

        :param name: 规范名（与 ``SkillRegistry`` 的 key 一致）。
        :param description: 描述；``str`` 或 ``Parsable``。
        :param content: 正文；``str`` 或 ``Parsable``。
        :param args_schema: 参数 schema properties（缺省空 dict）。
        :param on_load: 加载回调（缺省 ``None``）。
        :param _extra_fields: 额外字段（缺省空 dict；内部用途）。
        :param registry_key: 注册表全键（缺省 ``None``；内部用途）。
        """
        self.name = name
        self.description = description if isinstance(description, Parsable) else Parsable(description)
        self.content = content if isinstance(content, Parsable) else Parsable(content)
        self.args_schema = {} if args_schema is None else dict(args_schema)
        self.on_load = on_load
        self._extra_fields = {} if _extra_fields is None else dict(_extra_fields)
        self.registry_key = registry_key

    def __getattr__(self, name: str) -> Any:
        """未定义属性回退 ``_extra_fields`` 查找。

        .. rubric:: 行为要点

        - ``name`` 在 ``_extra_fields`` 中 → 返回对应值（原样返回，
          不做 Parsable 求值——需要求值的字段应使用保留字段
          ``description`` / ``content``）；否则抛
          ``AttributeError(name)`` （正常触发 ``getattr`` 默认值语义）。

        :param name: 要访问的属性名。
        """
        # 护栏：只经 __dict__ 探表（不经属性查找）——_extra_fields 尚未建立
        # 时（如构造中途）回退路径整体短路，干净抛 AttributeError 而非
        # RecursionError（与 Agent.__getattr__ 的护栏同构）
        extra = self.__dict__.get("_extra_fields")
        if extra is not None and name in extra:
            return extra[name]  # 原样返回，不做 Parsable 求值
        raise AttributeError(name)  # 未命中：正常触发 getattr 默认值语义


@dataclass
class SkillEntry:
    """Skill 的 Agent 级绑定——能力三正交中的「绑定」层。

    .. rubric:: 功能介绍

    同一个 ``Skill`` 在不同 Agent 上可以有不同的别名、参数覆盖与
    可见性，差异全部记录在本条目上，不修改 全局
    ``SkillRegistry`` 中的共享实例。``use_skill()`` 解析
    ``agent.skills`` （``.fya`` 的 ``skills:`` 字段）填充
    ``agent._skill_entries``，
    键为别名。别名同时是 catalog ``<name>`` 与 ``skill-load`` /
    ``agent.skill_load()`` 的 ``name`` 参数的匹配目标。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # agent.fya
        skills:
          - summarize as sum:
              args:
                max_length: 500        # specified：声明期固定值（LLM 不传参）
                work_directory: "{{ self.inject('work_directory') }}"   # 注入表达式
              description: "本 Agent 专用的长文摘要（覆写 Skill 本体描述）"
          - translate                  # 裸名：别名 = 规范名，无覆盖
          - audit:
              enabled: false           # 不出 catalog，仍可编程式 skill_load

    .. rubric:: 行为要点

    - ``enabled=False``：不进 catalog、LLM 不可见、``skill-load`` 工具
      入口拒绝加载；``agent.skill_load()`` 编程式加载不受影响
      （可见性与可加载性分离）。
    - ``args`` 的每个值进入 ``specified`` （包装 Parsable，加载时以
      调用方 Agent 实例上下文求值）；``_`` （PENDING）值视为未声明
      该参数（不进 ``specified``，覆盖校验照常）。条目 ``args`` 的
      参数键含 ``as`` → :class:`flowing.errors.FormatError`（无改名
      通道——``param_aliases`` 已随「LLM 不传参」删除）。
    - 覆盖校验（声明期 fail-fast）：``skill_add`` 时对
      ``skill.args_schema`` 逐参数检查——不在 ``specified`` 又无
      schema 默认值 → ``FormatError``。
    - ``specified`` 中的注入表达式（``{{ self.inject(...) }}``）在
      加载时沿 provide 链求值，缺失即
      :class:`flowing.errors.MissingProvideError`（与 Tool 侧一致）。
    - ``override_description`` 非 ``None`` 时 catalog ``<description>``
      取覆写值而非 ``Skill.description``；覆写是纯 LLM 可见文本，
      不影响加载与渲染正文。
    - 边缘情况：``name_alias == name_ori`` 是常态（未用 ``as``）；
      同一 Agent 内别名唯一（重复别名 →
      :class:`flowing.errors.EntryNameConflictError`，与 tool /
      subagent 绑定层统一 fail-fast——声明式与编程式写法背后是同一
      作者，写重了即笔误）。

    .. seealso:: :class:`Skill`、:class:`flowing.tool.ToolEntry`
    """

    name_alias: str
    """LLM 看到的别名——catalog ``<name>`` 与 ``skill-load`` 的
    ``name`` 参数、``agent.skill_load()`` 的 ``name`` 参数均按别名
    匹配。
    """
    name_ori: str
    """规范名——``SkillRegistry`` 中的 key，文件定向查找的依据。
    """
    specified: dict[str, Parsable] = field(default_factory=dict)
    """指定值参数——``.fya`` ``args:`` 声明的固定值（包装 Parsable，
    ``skill_load`` 合并时以调用方 Agent 实例为上下文现场求值）。LLM
    不给 skill 传参：本字段是 Skill 参数仅有的声明期来源；注入写
    ``"{{ self.inject('key') }}"`` 表达式落入本字段。
    """
    override_description: Parsable | None = None
    """Agent 级描述覆写（``None`` 用 ``Skill.description`` 本体）——
    与 :attr:`flowing.tool.ToolEntry.override_description` 同构。是
    Parsable：catalog 渲染时以调用方 Agent 为上下文自动求值。

    .. seealso:: :attr:`flowing.tool.ToolEntry.override_description`
    """
    enabled: bool = True
    """是否出现在 catalog。``False`` 时 LLM 不可见且 ``skill-load``
    入口拒绝，编程式 ``skill_load()`` 仍可加载。
    """


@dataclass
class SkillLoadContext:
    """``before_skill_load`` 钩子的 value——加载前参数上下文（可改写）。

    .. rubric:: 功能介绍

    加载流程第 1 步的钩子 value：携带调用方 Agent、目标别名与合并后
    的参数（schema 默认值 → ``specified`` （含注入表达式），见
    :func:`use_skill` 的「skill_load 契约」）。handler 可就地修改
    ``args``，后续 ``on_load`` 与正文渲染使用改写后的值。

    .. rubric:: 使用示例

    .. code-block:: python

        async def _scan(self, agent, ctx: SkillLoadContext):
            if ctx.name in self.blocked_skills:
                raise Intercepted(f"技能 {ctx.name} 被策略禁用")
            ctx.args.setdefault("max_length", 500)
            return ctx

        self.hooks.before_skill_load(_scan, by="guardrail")

    .. rubric:: 行为要点

    - handler 三种合法出口：返回（可能改写的）value /
      ``raise Intercepted`` （硬阻断——加载流程终止，``on_load`` 与渲染
      都不执行，``after_skill_load`` 不触发）/ 普通异常（直接上抛，
      加载失败）。
    - 改写语义：就地改 ``args`` dict 或整体返回替换均可被下一个
      handler 看到（dispatch 改写链，见
      :meth:`flowing.hooks.HookList.dispatch`）。
    - 边缘情况：无 handler 时 value 原样通过。

    .. seealso:: :class:`SkillContent`、
        :class:`flowing.errors.Intercepted`、:mod:`flowing.hooks`
    """

    agent: Agent
    """发起加载的 Agent 实例（渲染上下文与 provide 链起点）。
    """
    name: str
    """目标 Skill 的别名（``SkillEntry.name_alias``），与 catalog
    ``<name>`` 一致；字段名取 ``name`` 是为对齐钩子点
    ``match_on="name"`` 的 pattern 匹配与 :class:`SkillContent` 的
    同名属性。
    """
    args: dict[str, Any]
    """合并后的参数字典（schema 默认值 → ``specified``——LLM 不传参；
    注入表达式在求值时沿 provide 链取值）。handler 可自由改写；
    后续 ``on_load`` 与正文渲染均基于改写结果。
    """


@dataclass
class SkillContent:
    """``after_skill_load`` 钩子的 value——渲染后正文（可改写）。

    .. rubric:: 功能介绍

    加载流程第 4 步的钩子 value：``content.resolve()`` 的产物。
    handler 可修改 ``body`` （追加审计标记、裁减敏感段等），改写后的
    正文进入 PLUGIN 消息与 :class:`SkillResult`。

    .. rubric:: 使用示例

    .. code-block:: python

        def _log_usage(self, agent, content: SkillContent):
            self.audit.log("skill_loaded", skill=content.name,
                           bytes=len(content.body))
            return content

        self.hooks.after_skill_load(_log_usage, by="audit")

    .. rubric:: 行为要点

    - handler 出口规则与 ``before_skill_load`` 相同；此处
      ``raise Intercepted`` 使加载「看似完成但结果被丢弃」——PLUGIN
      消息不入队、``skill_load()`` 不返回（异常上抛给调用方）。慎用。
    - 边缘情况：``body`` 被改为空串是合法的（PLUGIN 消息照常入队，
      内容为空文本块）。

    .. seealso:: :class:`SkillLoadContext`、:class:`SkillResult`
    """

    name: str
    """已加载 Skill 的别名。
    """
    body: str
    """渲染后的 Skill 正文。handler 可改写；最终值同时进入 PLUGIN
    消息的 ``TextBlock`` 与 ``SkillResult.content``。
    """


@dataclass
class SkillResult:
    """Skill 加载结果——``skill_load()`` 的返回值。

    .. rubric:: 功能介绍

    加载流程的编程式产物。它与 PLUGIN 消息承载同一份渲染正文，
    但两者通道不同：``SkillResult`` 给代码调用方（含 ``skill-load``
    工具内部），PLUGIN 消息给 LLM（后续逻辑 Turn 的上下文）。Skill
    结果不合并进 ToolResult——``skill-load`` 对 LLM 只回简短收据，
    正文经消息队列送达。

    .. rubric:: 行为要点

    - 不变量：``content`` 是 ``after_skill_load`` 钩子链之后的最终
      正文，与入队 PLUGIN 消息的文本逐字相同。
    - 不携带渲染耗时、来源路径等观测数据（观测走钩子）。

    .. seealso:: :class:`SkillContent`、:class:`SkillLoadTool`
    """

    content: str
    """渲染后的 Skill 正文（钩子改写后的最终值）。
    """
