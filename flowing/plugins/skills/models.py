"""Skill 扩展数据对象（SKILL_NAMING / CatalogTemplate / Skill / SkillEntry / SkillLoadContext / SkillContent / SkillResult）。"""


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
"""Skill 资源的路径形态身份名推断规则表（:class:`flowing.paths.NamingRules`）。

紧邻候选链声明（模块 docstring「专属角度一」）：命中通用名候选
（``SKILL.fya`` 等）→ 身份名取目录名；否则文件名去首个匹配后缀。
供 ``use_skill()`` 调 :func:`flowing.parser.normalize_entries` 时传入
``naming=SKILL_NAMING``，以及 name 断言的推断侧。
"""


CatalogTemplate: TypeAlias = str
"""功能/动机：catalog 渲染模板类型——**唯一渲染槽位**（S-42 裁决，原
single/catalog 双槽位合并；渲染器模板化裁决：只收 Jinja2 模板源
字符串，含 ``$./file.j2`` FILE_REF 形式，不再支持 callable）。

模板上下文变量表：

- ``entries``：``list[tuple[SkillEntry, Skill]]``——「(条目, 已解析
  Skill)」对列表（仅含 ``enabled=True`` 的条目，按 ``skills:`` 声明
  顺序排列）；空列表时模板应渲染为空串（``LazySkillsPrompt`` 据此
  跳过整块注入，内置模板以 ``{% if entries %}`` 保证）。
- ``agent``：调用方 Agent 实例。条目描述字段为 Parsable，模板内以
  ``(entry.override_description or skill.description).resolve(agent)``
  现场求值（每次渲染现场 resolve、无缓存）。

行为边界：渲染经 Parsable TEMPLATE 语义（第二步 Jinja2，include 走
框架自定义加载器、基准为该 ``agent`` 的 ``source_dir``，P3-08）；
渲染异常 fail-fast 上抛，不静默降级。条目中的 ``Skill`` 已在声明期
解析入注册表（M-98：读取不惰性）——模板渲染本身不触发文件 IO
（``$`` FILE_REF 模板除外：模板源自身的读取发生在求值时）。

.. seealso:: :data:`DEFAULT_CATALOG_TEMPLATE`
"""


class Skill:
    """Skill 对象模型——「可执行对象」层的统一类型（所有 Skill 同一个类）。

    .. rubric:: 功能介绍

    能力三正交中 Skill 的「可执行对象」：``SkillRegistry`` 把三种定义形式
    （``.md`` / ``.skill.fya`` / 目录，见模块 docstring）统一解析为本类的
    实例。框架核心不认识本类——它由 ``SkillPlugin`` 定义与消费。

    .. rubric:: 设计动机

    - **单一类型、无内置/自定义层次**：Skill 的全部差异在数据（description /
      content / args_schema / on_load），不在行为，因此不需要类继承体系；
      这也使「未来纳入核心零迁移」成立（类型签名不变，只换包路径）。
    - **content 存「如何解析」而非解析结果**：``content`` 是
      :class:`flowing.parsable.Parsable`，加载时才现场求值（先 ``$`` 展开
      再 Jinja2），保证渲染上下文（Agent 局部变量 + 调用方 args）是当刻的。
    - **额外字段透明访问**：``.skill.fya`` 中框架不认识的字段落入
      ``_extra_fields``，经 ``__getattr__`` 透明读取——与 ``Agent._extra``
      的宽松解析策略同构，给 Skill 作者留出携带自定义元数据的空间。

    .. rubric:: 使用示例

    ``.md`` 形式：

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

    ``.skill.fya`` 形式：

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
            \"\"\"Skill 加载时执行。在所有 before_skill_load 钩子完成后调用。\"\"\"
            agent._summarize_count = getattr(agent, '_summarize_count', 0) + 1

    .. rubric:: 行为规约

    - 期待行为：实例由 :meth:`SkillRegistry.get` 产出并在注册表
      中跨 Agent 共享；``description`` / ``content`` 的 Parsable 求值发生在
      **每次使用时**（catalog 渲染 / skill_load），渲染上下文是调用方
      Agent——因此共享实例上绝不缓存渲染结果。
    - 非行为：``Skill`` 不认识钩子、不触发加载——加载流程编排在
      ``skill_load()`` 一侧；``on_load`` 不是钩子（不走 dispatch，不可
      ``raise Intercepted`` 阻断加载，抛普通异常则加载失败并上抛）。
    - 边缘情况：``.md`` 形式无 ``args_schema``（空 dict）与 ``on_load``
      （None）；``.md`` 缺 ``description`` → 解析失败（必填字段缺失，抛
      :class:`flowing.errors.MissingFieldError`）。
    - 不变量：``name`` 为规范名，与 ``SkillRegistry`` 的 key 一致；
      ``content`` 必填（``.md`` 形式即 frontmatter 之后的正文）。

    .. rubric:: 调用关系（审计）

    - 调用：无（纯数据对象；仅 ``__getattr__`` 回退 ``_extra_fields``）
    - 被调：``flowing.plugins.skills.LazySkillsPrompt.resolve()``（时机：
      每次 ``Agent._assemble_context()`` 现场渲染 catalog，读取
      ``description`` / ``args_schema``）；
      ``flowing.plugins.skills._load_skill()`` skill_load 契约第 5/6 步
      调 ``on_load`` 与 ``content.resolve()``（时机：每次 skill_load）
    - 实例化方：``flowing.plugins.skills.SkillRegistry.get()``
      缓存未命中分支经 ``_parse_skill_file()``（时机：``use_skill()``
      声明期一次性解析，或声明外编程式按需解析）

    .. seealso::

        - :class:`SkillRegistry` —— 声明期解析与缓存。
        - :class:`SkillEntry` —— Agent 级绑定（别名/参数覆盖/enabled）。
        - :class:`flowing.parsable.Parsable` —— 惰性求值与两步渲染。
    """

    name: str
    """规范名——``SkillRegistry`` 中的 key。一律由文件名/目录名推断
    （kebab-case）：命中 ``<name>.skill.fya`` / ``<name>.fya`` /
    ``<name>.md`` 候选取**文件名**；命中通用名候选（``SKILL.fya`` /
    ``skill.fya`` / ``SKILL.md`` / ``skill.md``）取**目录名**（对齐
    Agent「``agent.fya`` 命中取目录名」）。``.skill.fya`` / frontmatter
    中**不禁止**写 ``name``，但仅作一致性断言——与推断值不符抛
    :class:`flowing.errors.NameMismatchError`（与 Agent / Tool 同一语义）。

    .. seealso:: :class:`SkillRegistry`
    """
    description: Parsable[str]
    """catalog ``<description>`` 的来源。保持 Parsable 而非渲染后字符串：
    渲染上下文是**调用方 Agent** 的局部变量，而 Skill 实例在注册表中跨
    Agent 共享，只有使用时才能求值。catalog 渲染时由模板经
    ``.resolve(agent)`` 现场求值（见 :data:`DEFAULT_CATALOG_TEMPLATE`）。

    .. seealso:: :data:`DEFAULT_CATALOG_TEMPLATE`
    """
    content: Parsable[str]
    """Skill 正文（执行指南），惰性求值——存的是「如何解析」的指令，不是
    解析结果。``skill_load()`` 第 3 步对其 ``resolve``：先展开 ``$`` 文件
    引用，再执行 Jinja2 模板渲染；渲染上下文 = 调用方 Agent 局部变量 +
    合并后的 args。
    
    .. seealso:: :meth:`flowing.parsable.Parsable.resolve`
    """
    args_schema: dict[str, dict[str, Any]]
    """Skill 自身声明的参数 schema（``.skill.fya`` 的 ``args:`` 块经
    :func:`flowing.params.expand_args_schema` 归一化的 JSON Schema
    properties；``.md`` 形式为空 dict）。**LLM 不传参**（角度五裁决）
    后的两个消费者：``skill_add`` 的声明期覆盖校验（每个参数须被
    ``specified``（固定值/注入表达式）覆盖或有默认值，否则
    ``FormatError``）与 ``skill_load()`` 的默认值填充。空 dict 的技能
    开箱可用（无参数即无覆盖义务）。
    
    .. seealso:: :mod:`flowing.params`、:class:`SkillEntry`
    """
    on_load: Callable[[Agent, dict[str, Any]], Any] | None
    """``$script`` 中定义的加载回调；无 ``$script`` 或未定义则为 ``None``。
    ``$script`` 里的 ``def on_load(self, agent, args)`` 经描述符绑定后，
    框架以 ``(agent, args)`` 调用（``self`` 即本 Skill 实例）。时序：
    ``before_skill_load`` 钩子之后、``content`` 渲染之前；**不拦截、
    不返回值**（返回值被忽略）；抛异常则加载失败、异常上抛。
    
    .. seealso:: :class:`SkillLoadContext`
    """
    _extra_fields: dict[str, Any]
    """``.skill.fya`` / frontmatter 中框架不认识的任意额外字段，经
    ``__getattr__`` 透明访问。内部 API，不属稳定契约。
    """
    registry_key: str | None
    """注册表全键（``ns::name``），``SkillRegistry`` 落账时回写；未注册
    实例为 ``None``。``use_skill`` 预解析后对**文件派生技能**把本字段
    落账为 ``SkillEntry.name_ori``（含目录派生命名空间的限定键，
    catalog 渲染 / ``skill_load`` 热路径精确命中、零文件 IO）。内部 API。
    """

    def __getattr__(self, name: str) -> Any:
        """未定义属性回退 ``_extra_fields`` 查找。

        .. rubric:: 行为规约

        - ``name`` 在 ``_extra_fields`` 中 → 返回对应值；否则抛
          ``AttributeError(name)``（正常触发 ``getattr`` 默认值语义）。
        - 非行为：不做 Parsable 求值——额外字段原样返回，需要求值的字段
          应使用保留字段（``description`` / ``content``）。

        .. rubric:: 调用关系（审计）

        - 调用：无（``_extra_fields`` dict 查找，命中返回 / 未命中抛
          ``AttributeError``）
        - 被调：无框架内显式调用方（Python 属性查找协议隐式触发；
          时机：未见规约）

        .. seealso:: :attr:`_extra_fields`
        """
        if name in self._extra_fields:
            return self._extra_fields[name]  # 原样返回，不做 Parsable 求值
        raise AttributeError(name)  # 未命中：正常触发 getattr 默认值语义


@dataclass
class SkillEntry:
    """Skill 的 Agent 级绑定——三正交中的「绑定」层。

    .. rubric:: 功能介绍

    同一个 ``Skill`` 在不同 Agent 上可以有不同的别名、参数覆盖与可见性，
    差异全部记录在本条目上，**不修改** 全局 ``SkillRegistry`` 中的共享
    实例。``use_skill()`` 解析 ``agent.skills``（``.fya`` 的 ``skills:``
    字段）填充 ``agent._skill_entries``（别名 → 条目）。

    .. rubric:: 设计动机

    绑定层的存在理由与 ``ToolEntry`` 相同：LLM 可见声明（catalog 条目）必须
    能按 Agent 定制，而可执行对象（Skill 内容）全局共享。别名同时是
    catalog ``<name>`` 与多层具名块引用的匹配目标
    （``$skills.<alias>.<属性>:`` 匹配别名，不匹配规范名）。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # agent.fya
        skills:
          - summarize as sum:
              args:
                max_length: 500        # specified：声明期固定值（LLM 不传参——角度五）
                work_directory: "{{ self.inject('work_directory') }}"   # 注入表达式（R-4：无 inject 键）
              description: "本 Agent 专用的长文摘要（覆写 Skill 本体描述）"
          - translate                  # 裸名：别名=规范名，无覆盖
          - audit:
              enabled: false           # 不出 catalog，仍可编程式 skill_load

    .. rubric:: 行为规约

    - ``enabled=False``：不进 catalog、LLM 不可见、``skill-load`` 工具入口
      拒绝加载；``agent.skill_load()`` 编程式加载**不受影响**（可见性与
      可加载性分离）。
    - ``args`` 的每个值 → ``specified``（包装 Parsable，加载时以调用方
      Agent 实例上下文求值）；``_``（PENDING）值视为未声明该参数
      （不进 ``specified``，覆盖校验照常）。**无 dict 子键补丁、无
      ``as`` 改名**（``override_params`` / ``param_aliases`` 随「LLM
      不传参」裁决删除）；条目中含 ``as`` 的参数键 →
      :class:`flowing.errors.FormatError`。
    - **覆盖校验（声明期 fail-fast）**：``skill_add`` 时对
      ``skill.args_schema`` 逐参数检查——不在 ``specified`` 又无
      schema 默认值 → ``FormatError``。
    - ``specified`` 中的注入表达式（``{{ self.inject(...) }}``）在加载时
      沿 provide 链求值，优先级最高（与 Tool 一致，见模块 docstring
      专属角度五）。
    - ``override_description`` 非 ``None`` 时 catalog ``<description>``
      取覆写值而非 ``Skill.description``（M-99：与 Tool 条目覆写同构）；
      覆写是纯 LLM 可见文本，不影响加载与渲染正文。
    - 边缘情况：``name_alias == name_ori`` 是常态（未用 ``as``）；同一
      Agent 内别名唯一（重复别名 → ``EntryNameConflictError``，与
      tool / subagent 绑定层统一 fail-fast——覆盖语义已废弃：声明式
      与编程式写法背后是同一作者，写重了即笔误）。

    .. rubric:: 测试案例

    - 前置：``skills: [summarize as sum]``。操作：``use_skill(agent)``。
      期望：``agent._skill_entries["sum"].name_ori == "summarize"``，且
      catalog 中 ``<name>sum</name>``。
    - 前置：条目 ``enabled=False``。操作：渲染 catalog。期望：该条目
      不出现；随后 ``await agent.skill_load("audit")`` 正常加载。
    - 前置：Skill ``args_schema`` 含无默认值参数 ``lang``，条目未在
      ``args`` 覆盖它。操作：``use_skill(agent)`` → 期望：
      ``skill_add`` 内抛 ``FormatError``（声明期覆盖校验）。
    - 前置：条目 ``args: {max_length: 500, work_directory:
      "{{ self.inject('work_directory') }}"}`` 且 provide 链可提供
      ``work_directory``。操作：``skill_load("sum")``。期望：渲染上下文
      ``max_length == 500``、``work_directory`` 取 provide 链值。
    - 前置：条目设 ``override_description="覆写文本"``。操作：渲染
      catalog。期望：``<description>`` 为覆写文本；另一未覆写条目仍渲染
      ``Skill.description`` 的 resolve 结果。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.plugins.skills.use_skill()`` 行为序列第 4 步逐条
      委托 ``agent.skill_add()``（时机：setup 管线，每次 ``use_skill()``）；
      ``LazySkillsPrompt.resolve()`` 渲染时读取（时机：每次 catalog
      渲染）；``SkillLoadTool.execute()`` 与 ``_load_skill()`` 按别名
      查找时读取（时机：每次 skill_load）
    - 实例化方：``agent.skill_add()``（``use_skill()`` 第 3 步绑定的
      装配单点——``.fya`` 声明与程序化调用的统一入口）

    .. seealso::

        - :class:`Skill` —— 绑定指向的共享可执行对象。
        - :class:`flowing.tool.ToolEntry` —— Tool 侧同构绑定（参数
          优先级一致：specified 最高）。
    """

    name_alias: str
    """LLM 看到的别名——catalog ``<name>`` 与 ``skill-load`` 的 ``name``
    参数、``agent.skill_load()`` 的 ``name`` 参数均按**别名**匹配。
    """
    name_ori: str
    """规范名——``SkillRegistry`` 中的 key，文件定向查找的依据。
    """
    specified: dict[str, Parsable] = field(default_factory=dict)
    """指定值参数——``.fya`` ``args:`` 声明的固定值（包装 Parsable，
    ``skill_load`` 合并时以调用方 Agent 实例为上下文现场求值）。与
    ``ToolEntry.specified`` 值语义相同。**LLM 不传参**（角度五裁决）：
    本字段是 Skill 参数仅有的声明期来源（R-4：inject 键已删除，
    注入写 ``"{{ self.inject('key') }}"`` 表达式落入本字段）。
    """
    override_description: Parsable | None = None
    """Agent 级描述覆写（``None`` 用 ``Skill.description`` 本体）——与
    ``ToolEntry.override_description`` 同构。**是 Parsable**（用户裁决，
    取代 M-99 的「普通字符串」）：catalog 渲染时以调用方 Agent 为上下文
    **自动求值**（求值面内）。
    
    .. seealso:: :attr:`flowing.tool.ToolEntry.override_description`
    """
    enabled: bool = True
    """是否出现在 catalog。``False`` 时 LLM 不可见且 ``skill-load`` 入口
    拒绝，编程式 ``skill_load()`` 仍可加载。
    """


@dataclass
class SkillLoadContext:
    """``before_skill_load`` 钩子的 value——加载前参数上下文（可改写）。

    .. rubric:: 功能介绍

    加载流程第 1 步的钩子 value：携带调用方 Agent、目标别名与**合并后的
    参数**（两来源合并完毕：schema 默认值 → ``specified``（含注入表达式），
    见模块 docstring 专属角度五）。handler 可就地
    修改 ``args``，后续 ``on_load`` 与正文渲染使用改写后的值。

    .. rubric:: 设计动机

    给 Guardrail / 审计 / 参数注入类 Composable 一个统一拦截点：它们不需要
    认识 Skill 内部结构，只需面对「哪个 Agent 在加载哪个 Skill、参数是
    什么」。阻止加载经 ``raise Intercepted`` 表达（机制），「什么该拦」是
    策略，框架不内置。

    .. rubric:: 使用示例

    .. code-block:: python

        async def _scan(self, agent, ctx: SkillLoadContext):
            if ctx.name in self.blocked_skills:
                raise Intercepted(f"技能 {ctx.name} 被策略禁用")
            ctx.args.setdefault("locale", agent.locale)
            return ctx

        self.hooks.before_skill_load(_scan, by="guardrail")

    .. rubric:: 行为规约

    - handler 三种合法出口：返回（可能改写的）value / ``raise Intercepted``
      （硬阻断——加载流程终止，``on_load`` 与渲染都不执行，
      ``after_skill_load`` 不触发）/ 普通异常（直接上抛，加载失败）。
    - 改写语义：就地改 ``args`` dict 或整体返回替换均可被下一个 handler
      看到（dispatch 改写链，见 :meth:`flowing.hooks.HookList.dispatch`）。
    - 边缘情况：无 handler 时 value 原样通过；dispatch 的 ``shortcut``
      协商短路对本钩子点同样生效（value 带 ``shortcut`` 属性时链停止）。

    .. rubric:: 测试案例

    - 前置：handler 把 ``ctx.args["max_length"] = 100``。操作：dispatch 后
      继续加载。期望：``on_load`` 与正文渲染收到 ``max_length == 100``。
    - 前置：handler ``raise Intercepted("禁用")``。操作：LLM 经
      ``skill-load`` 调用。期望：``on_load`` 未执行、无 PLUGIN 消息入队，
      ``Intercepted`` 传播为工具调用的 ``blocked`` 结果。

    .. rubric:: 调用关系（审计）

    - 被调：``before_skill_load`` 钩子链 handler（时机：每次 skill_load
      的 dispatch，见 :meth:`flowing.hooks.HookList.dispatch`）
    - 实例化方：``flowing.plugins.skills._load_skill()`` skill_load 契约
      第 4 步 dispatch 前构造（时机：每次 skill_load）

    .. seealso::

        - :class:`SkillContent` —— 加载后钩子 value。
        - :class:`flowing.errors.Intercepted`、:mod:`flowing.hooks`
    """

    agent: Agent
    """发起加载的 Agent 实例（渲染上下文与 provide 链起点）。
    """
    name: str
    """目标 Skill 的**别名**（``SkillEntry.name_alias``），与 catalog
    ``<name>`` 一致；字段名取 ``name`` 是为对齐钩子点
    ``match_on="name"`` 的 pattern 匹配（dispatch 时
    ``getattr(value, "name")``，P1-10 裁决）与 :class:`SkillContent`
    的同名属性。
    """
    args: dict[str, Any]
    """合并后的参数字典（specified > Skill 默认——LLM 不传参，
    specified 最高；注入表达式在求值时沿 provide 链取值）。handler 可
    自由改写；后续 ``on_load`` 与正文渲染均基于改写结果。
    """


@dataclass
class SkillContent:
    """``after_skill_load`` 钩子的 value——渲染后正文（可改写）。

    .. rubric:: 功能介绍

    加载流程第 4 步的钩子 value：``content.resolve()`` 的产物。handler
    可修改 ``body``（追加审计标记、裁减敏感段等），改写后的正文进入
    PLUGIN 消息与 :class:`SkillResult`。

    .. rubric:: 使用示例

    .. code-block:: python

        def _log_usage(self, agent, content: SkillContent):
            self.audit.log("skill_loaded", skill=content.name,
                           bytes=len(content.body))
            return content

        self.hooks.after_skill_load(_log_usage, by="audit")

    .. rubric:: 行为规约

    - handler 出口规则与 ``before_skill_load`` 相同；此处
      ``raise Intercepted`` 使加载「看似完成但结果被丢弃」——PLUGIN 消息
      不入队、``skill_load()`` 不返回（异常上抛给调用方）。慎用。
    - 边缘情况：``body`` 被改为空串是合法的（PLUGIN 消息照常入队，内容
      为空文本块）。

    .. rubric:: 调用关系（审计）

    - 被调：``after_skill_load`` 钩子链 handler（时机：每次 skill_load
      的 dispatch）
    - 实例化方：``flowing.plugins.skills._load_skill()`` skill_load 契约
      第 7 步（``content.resolve()`` 之后、dispatch 之前；时机：每次
      skill_load）

    .. seealso:: :class:`SkillLoadContext`、:class:`SkillResult`
    """

    name: str
    """已加载 Skill 的别名。
    """
    body: str
    """渲染后的 Skill 正文。handler 可改写；最终值同时进入 PLUGIN 消息
    的 ``TextBlock`` 与 ``SkillResult.content``。
    """


@dataclass
class SkillResult:
    """Skill 加载结果——``skill_load()`` 的返回值。

    .. rubric:: 功能介绍 / 设计动机

    加载流程的编程式产物。它与 PLUGIN 消息承载**同一份渲染正文**，但两者
    通道不同：``SkillResult`` 给代码调用方（含 ``skill-load`` 工具内部），
    PLUGIN 消息给 LLM（后续逻辑 Turn 的上下文）。分开的原因是 Skill 结果
    **不合并进 ToolResult**——``skill-load`` 对 LLM 只回简短收据，正文经
    消息队列送达。

    .. rubric:: 行为规约

    - 不变量：``content`` 是 ``after_skill_load`` 钩子链之后的最终正文，
      与入队 PLUGIN 消息的文本逐字相同。
    - 非行为：不携带渲染耗时、来源路径等观测数据（观测走钩子）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.plugins.skills.SkillLoadTool.execute()`` 收据化
      封装侧消费（时机：每次 LLM 经 ``skill-load`` 工具调用）；其余为
      用户代码（编程式 ``skill_load()`` 的调用方）
    - 实例化方：``flowing.plugins.skills._load_skill()`` skill_load 契约
      第 9 步（时机：每次 skill_load 正常完成）

    .. seealso:: :class:`SkillContent`、:class:`SkillLoadTool`
    """

    content: str
    """渲染后的 Skill 正文（钩子改写后的最终值）。
    """
