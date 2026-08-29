"""``flowing.parsable`` —— Parsable 统一抽象、惰性求值与双哨兵。

本模块是 ``.fya`` 文件层与运行时对象模型之间的桥梁：``.fya`` 中所有「可解析字段」
（``system_prompt``、``description`` 及各类具名块的内容）在解析后统一表示为
:class:`Parsable`，运行时在使用方决定的时机现场求值。本模块同时承载两个模块级
单例哨兵：``PENDING``（延迟定义承诺）与 ``_UNSET``（未设置）。

.. rubric:: 模块定位与分层

- 属于**框架核心**（``flowing/parsable.py``），不依赖任何内置扩展。
- 本模块只定义「存储求值指令 + 现场求值」的机制，不定义任何使用方的求值
  策略（何时求值、用什么上下文）——求值时机由各使用方（``_assemble_context()``、
  ``ToolEntry.resolve()``、``SubagentEntry.resolve()`` 等）各自决定，见下文
  「各使用方求值时机表」。

.. rubric:: 五种形式判定表

``Parsable`` 的 ``type`` 按 ``source`` 字符串样式**自动推断**，不允许手动指定。
判定按下表自上而下进行，首个命中者生效：

::

    ┌─────────────┬────────────────────────────────────┬──────────────────┬──────────────┐
    │ type        │ source 样式                        │ resolve 结果类型 │ 走两步渲染   │
    ├─────────────┼────────────────────────────────────┼──────────────────┼──────────────┤
    │ RAW         │ $"..."（首个 $" 到最后一个 "       │ str              │ 否           │
    │             │  贪婪匹配，内部可含引号与 {{ }}）  │                  │              │
    ├─────────────┼────────────────────────────────────┼──────────────────┼──────────────┤
    │ FILE_REF    │ 以 $ 开头的路径（非 $" 形式）      │ str              │ 是           │
    │             │  如 $./prompt.md、$@/shared/x.md   │                  │              │
    ├─────────────┼────────────────────────────────────┼──────────────────┼──────────────┤
    │ EXPRESSION  │ strip(source) 恰好是一个完整的     │ 表达式原生类型   │ 是           │
    │             │ {{ expr }}，无任何其它内容         │ （不转字符串）   │              │
    ├─────────────┼────────────────────────────────────┼──────────────────┼──────────────┤
    │ TEMPLATE    │ 含 {{ }} / {% %} 的混合文本        │ str              │ 是           │
    ├─────────────┼────────────────────────────────────┼──────────────────┼──────────────┤
    │ LITERAL     │ 以上皆非（含非字符串值 123/true）  │ source 原样      │ 是（幂等）   │
    └─────────────┴────────────────────────────────────┴──────────────────┴──────────────┘

``.fya`` 解析层（具名所指：:mod:`flowing.parser`）的三条补充规则：

- **非字符串 YAML 值不构造 Parsable（默认情形）**：``max_turns: 10`` 直接按
  YAML 类型解析为 ``int``。**例外（M-14 裁决）**：字段被宿主显式声明为
  Parsable 承载（如 ``ModelConfig`` 的字段级求值设计）时，非字符串 YAML
  值也包装为 ``LITERAL`` Parsable——``source`` 原样持有该值，保证声明为
  Parsable 的字段拿到的永远是 Parsable 实例而非裸值。除此之外，``LITERAL``
  的非字符串 ``source`` 只会出现在 Python API 层手动 ``Parsable(123)``
  的场景。
- **延迟定义不构造 Parsable**：``.fya`` 中的 ``field: _`` 在解析阶段
  （:func:`flowing.parser.load_fya_yaml`）直接映射为模块级哨兵
  ``PENDING``，见 :data:`PENDING`。``$"_"`` 是 ``RAW`` 形式，结果
  为普通字符串 ``"_"``，不触发 PENDING 语义。**位置分流**：字段位
  ``_`` = 「必须兑现的承诺」（创建管线检查点）；覆写位（entry 覆写 /
  ``args`` 覆写）``_`` = **空补丁**（装配层解析为空，深层块可逐字段
  填充，未填充则从基底回填——见 :class:`flowing.tool.ToolEntry`）；
  **资源列表项位禁止** ``_``（无别名无法被深层块寻址，
  :func:`flowing.parser.normalize_entries` 抛 ``FormatError``）。
- **YAML 引号约束**：双花括号表达式在 ``.fya`` 中必须写在引号内
  （``max_turns: "{{ config.limits.turns }}"``）——``{`` 是 YAML flow mapping
  起始符，不加引号会在 YAML 层直接报错，根本到不了 Parsable 判定。
  多行内容可改用 YAML 块标量（``system_prompt: |`` 起）规避引号——
  块标量内容是字面文本，``{{ }}`` 无需引号。

.. rubric:: 两步渲染（顺序固定，不可配置）

``LITERAL`` / ``FILE_REF`` / ``EXPRESSION`` / ``TEMPLATE`` 四种形式走统一的两步
渲染，**顺序固定、不可配置、两步分开执行无交错**：

::

    第二步：模板渲染（Jinja2）—— 支持 {{ }}、{% if %}、{% for %}、{% include %}
            EXPRESSION 形式返回表达式原生值；其余形式返回 str

模板继承（``{% extends %}`` / ``{% block %}``）**暂不支持**：不在上表
枚举内的 Jinja2 语法属契约外行为——引擎是完整 Jinja2、写了可能渲染出
结果，但框架不保证其语义（含加载器对 ``extends`` 的解析），未来版本
可能改变或收紧，不要在 ``.fya`` 中依赖。跨文件组合只有 ``$`` 引用与
``{% include %}`` 两个契约机制。

``$`` 引用与 ``{% include %}`` 是两种不同阶段的跨文件组合机制：

::

    ┌───────────┬──────────────────────┬────────────────────────────┐
    │           │ $ 引用               │ {% include %}              │
    ├───────────┼──────────────────────┼────────────────────────────┤
    │ 执行阶段  │ 第一步：$ 展开       │ 第二步：Jinja2 渲染        │
    │ 使用位置  │ 仅 Parsable 顶层     │ 模板内部任意位置           │
    │ 可搭配    │ 不与其它 Jinja2      │ 可与 {% if %} 等控制结构   │
    │           │ 语法混用             │ 组合                       │
    │ 路径前缀  │ ./ ../ @/ ../../     │ 与 $ 相同的前缀规则        │
    └───────────┴──────────────────────┴────────────────────────────┘

路径解析就是**字符串前缀判断**，不引入专门的路径类型：``./`` = 当前
文件所在目录，``../`` = 父目录，``@/`` = 项目根，多级 ``../../`` 按层级向上。
前缀集合的唯一权威表是 :data:`flowing.paths.PATH_PREFIXES`；实际解析委托
``Runtime.resolve_path(path, *, source_dir)``（薄方法，体内委托
:func:`flowing.paths.resolve_path`），``source_dir`` 基准由
``Agent.source_dir`` 属性统一供给（P3-11：「文件 → 所在目录」换算的
唯一承担者）。裸名（如 ``payment``）不是路径形态（不在
``PATH_PREFIXES`` 内），但在资源查找口径下**可以命中当前目录**——
``get(name_or_path, *, source_dir)`` 在 ``source_dir`` 可用时先走文件链
（当前目录下的同名文件**覆盖** ``default::``/``builtin::`` 注册项，
P1-15/P1-18 裁决）；``source_dir`` 缺省时只查注册表。形态判别见
:func:`flowing.paths.classify_ref`。

两条边界约定（P3-08 / P3-09 裁决）：

- **``{% include %}`` 的路径基准与 ``$`` 引用同源**（P3-08）：经同一个
  ``resolve_path``、同一个 ``source_dir``（绑定实例的 ``Agent.source_dir``）。
  内联模板（无文件身份的 ``source`` 字符串）中的 ``include`` 亦然——
  **不存在「相对于包含者文件」的解析**，基准永远来自绑定实例。
- **``FILE_REF`` 先剥 ``$`` 再进 ``resolve_path``**（P3-09）：``source``
  的首字符 ``$`` 只是形式标记，不是路径成分；剥离在 Parsable 侧求值路径上
  完成，``resolve_path`` 永远看不到 ``$`` 前缀（否则 ``$./x.md`` 会被
  拼成 ``base/"$./x.md"``）。

``RAW`` 与 ``PENDING`` 是两步渲染的例外：``RAW`` 跳过全部渲染直接取引号内文本；
``PENDING`` 不参与渲染，等待 ``setup()`` 或具名块赋值为具体值。

.. rubric:: 渲染上下文

第二步渲染的上下文变量：

::

    ┌───────────┬──────────────────────────────────────┬────────────────┐
    │ 变量      │ 内容                                 │ 范围           │
    ├───────────┼──────────────────────────────────────┼────────────────┤
    │ env       │ os.environ 的只读映射视图            │ 所有 .fya      │
    │ config    │ 项目配置合并视图（优先级链合并结果） │ 所有 .fya      │
    │ agent     │ Agent 实例自身（经 ``getattr`` 可达  │ Agent .fya     │
    │ / self    │ 类属性、描述符、方法——               │                │
    │           │ ``{{ agent.system_prompt }}``、      │                │
    │           │ ``{{ self.my_method() }}`` 均合法；  │                │
    │           │ 两名字同值，按书写习惯自选）         │                │
    ├───────────┼──────────────────────────────────────┼────────────────┤
    │ 摊平属性  │ Agent 实例属性逐一提升为顶层变量     │ Agent .fya     │
    │           │ （不写 self. 前缀；**不含**类属性与  │                │
    │           │ 方法——它们经 ``agent.``/``self.``    │                │
    │           │ 前缀访问）                           │                │
    ├───────────┼──────────────────────────────────────┼────────────────┤
    │ 状态键    │ register_state 声明键的现场值        │ Agent .fya     │
    ├───────────┼──────────────────────────────────────┼────────────────┤
    │ _extra    │ ``_extra`` 原值                      │ Agent .fya     │
    └───────────┴──────────────────────────────────────┴────────────────┘

- ``env`` / ``config`` / ``agent`` / ``self`` 是**保留名**：Agent 实例属性禁止同名，
  框架检测到抛 ``ReservedAttributeError``——否则摊平会覆盖框架
  注入的对应值。
- 非 Agent 的 ``.fya``（Tool / Skill 定义的 content）渲染上下文中可能没有
  Agent 实例；可用字段由使用方（扩展或核心模块）在调用求值时决定。
- **框架不注册任何模板全局函数 / 包**（无 ``now()``、无 ``datetime``——
  没有「兼容所有包」的机制，就不开这个口子；定稿裁决）。模板中调用函数
  的唯一通道是**本对象的方法**（``{{ self.my_method() }}`` /
  ``{{ agent.my_method() }}``，经上表 ``agent`` / ``self`` 入口）。
- **求值时取最新值**：``env`` 与 ``config`` 在每次求值时现场读取，不在解析
  阶段固化。

.. rubric:: 惰性求值（功能正确性前提，非性能优化）

``Parsable`` 存储的是「如何解析」的**指令**，不是解析结果。真正求值发生在实际
使用时。若在文件解析阶段即求值，会出现三类正确性问题：

- **环境变量**：``{{ env.API_KEY }}`` 在文件解析时可能尚未加载。
- **动态属性**：``{{ current_mode }}`` 在 Agent 构造前没有实例可取值；模式
  切换后已求值的结果也不会反映新模式。
- **模板引用**：``$./system-prompt.md`` 在解析阶段展开时，被引用文件可能
  尚未就绪；且文件修改后应自动反映新内容。

因此 ``FILE_REF`` 的文件读取、``EXPRESSION``/``TEMPLATE`` 的 Jinja2 执行都
发生在求值时；框架**不做任何本地缓存**，每次求值现场执行（对应「本地不缓存」
的定稿减法）。

.. rubric:: 各使用方求值时机表（定稿）

::

    ┌──────────────────────────────────────┬────────────────────────────────────┐
    │ 使用方                               │ 求值时机                           │
    ├──────────────────────────────────────┼────────────────────────────────────┤
    │ PromptBlock.content                  │ _assemble_context() 每次调用时     │
    │ （含 system_prompt 惰性引用块）      │ 现场渲染——每轮 provider_gen 重新求值，    │
    │                                      │ 天然反映最新实例属性               │
    ├──────────────────────────────────────┼────────────────────────────────────┤
    │ model_tag（若为模板）                │ 每轮 provider_gen 前求值                  │
    ├──────────────────────────────────────┼────────────────────────────────────┤
    │ ToolEntry.specified                  │ tool_call() 内、before_tool_call   │
    │                                      │ 钩子之后、_normalize() 内部第 2    │
    │                                      │ 步，调用方 Agent 实例上下文——      │
    │                                      │ 每次调用现场求值                   │
    ├──────────────────────────────────────┼────────────────────────────────────┤
    │ 子 Agent 的 specified 参数           │ invoke_subagent() 内，父 Agent     │
    │                                      │ 实例上下文                         │
    ├──────────────────────────────────────┼────────────────────────────────────┤
    │ SubagentEntry.override_system_prompt │ 子 Agent 创建时                    │
    ├──────────────────────────────────────┼────────────────────────────────────┤
    │ Tool description 覆盖                │ ToolEntry.llm_definition() 时      │
    ├──────────────────────────────────────┼────────────────────────────────────┤
    │ Skill description 覆盖               │ catalog 渲染时                     │
    │ （SkillEntry.override_description）  │ （catalog 模板内 .resolve(agent)） │
    ├──────────────────────────────────────┼────────────────────────────────────┤
    │ Subagent description 覆盖            │ 父 Agent 路由决策 /                │
    │                                      │ catalog_view() 时，用父            │
    │                                      │ Agent 上下文（子 Agent 尚未创建）  │
    ├──────────────────────────────────────┼────────────────────────────────────┤
    │ _extra 中的任意字段                  │ 框架不自动 resolve——由声明该字段   │
    │                                      │ 的插件在 use_xxx() 中接管（见下）  │
    └──────────────────────────────────────┴────────────────────────────────────┘

.. rubric:: 插件自定义可解析字段（M-13 后续裁决）

``.fya`` 解析时默认构造为 ``Parsable`` 的字段集合由框架核心固定（即上表
「求值面内」字段，外加 M-14 裁决的「宿主显式声明 Parsable 承载」字段）。
**除此之外的可解析对象由插件 / 组件自行定义**，规则如下：

- 插件自行决定自己的哪些字段是可解析的，并以**对开发者的约定**形式写进
  插件文档（「本插件的 X 字段是 Parsable」是插件契约的一部分，框架不提供
  通用注册表）；
- 机制：在 ``use_xxx()`` 启用钩子里，插件将对应变量**重赋值**为「由该变量
  的 ``.fya`` 解析结果创建的 :class:`Parsable` 对象」——框架解析层不为
  插件字段自动包装，包装动作显式发生在启用时；
- 求值同样由插件负责：重赋值后的 Parsable 由插件在需要时手动
  ``resolve(context)``（见下「求值面外」）。

.. rubric:: 求值面内 vs 求值面外（框架边界契约）

::

    ┌─ 求值面内（框架自动 resolve）──────────────┐
    │  _assemble_context()                       │
    │  ToolEntry.resolve()                       │
    │  SubagentEntry.resolve()                   │
    │  → 用户无感，拿到字符串 / 表达式解析值     │
    └────────────────────────────────────────────┘

    ┌─ 求值面外（用户 / 扩展显式 resolve）───────┐
    │  agent._extra["greeting"]                  │
    │  插件自定义字段（use_xxx() 中重赋值为      │
    │  Parsable 的那些）                         │
    │  → 拿到的是 Parsable 对象本身              │
    │  → 需要时手动 .resolve(context)            │
    └────────────────────────────────────────────┘

Flowing **不为 Parsable 做隐式解包**（类比 Vue 的 ref 但刻意不自动拆箱）：
隐式解包会掩盖求值开销，且求值上下文未必总是 ``self``。判断标准：字段是否
在上表「求值面内」；面外字段拿到的永远是 :class:`Parsable` 对象。

.. rubric:: 响应式边界：watch 只监听属性赋值

框架**没有响应式系统**（无 Proxy、无依赖图），定稿的响应式边界是：

- ``watch(name, handler)`` 只监听**属性赋值事件**，不监听解析值的逻辑变化。
  ``self.c = Parsable("{{ a == b }}")`` 之后修改 ``self.a``，``watch("c")``
  **不触发**——因为 ``self.c`` 从未被重新赋值。
- 以下三种赋值会触发 ``watch("c")``：换一个新 Parsable、赋一个非 Parsable
  值、赋 ``_``（重置为 PENDING）。
- 要「a 变导致 c 重算」有两条路径：①把 ``c`` 放进某个求值面（如
  ``system_prompt``、Tool ``specified``），让框架每次自动 resolve，自然拿到
  最新值；②不用 Parsable，自己 ``watch("a")``/``watch("b")`` 做显式联动，
  在 handler 中给 ``self.c`` 直接赋值。
- 设计哲学：「用到的时候重新算」而非「变了就通知一切依赖方」。代价是每次
  求值面多跑一次 Jinja2，对 system prompt 体量的文本开销可忽略。

``watch`` 本身的签名与语义见 :meth:`flowing.agent.Agent.watch`。

.. rubric:: 双哨兵：PENDING 与 _UNSET 不混用

- :data:`PENDING`：「延迟定义承诺」——``.fya`` 中 ``field: _`` 的解析结果；
  检查点在创建管线 ``setup()`` 返回后、``after_create`` 前，仍为 PENDING 抛
  ``MissingFieldError``。
- :data:`_UNSET`：「未设置」——现仅用于 ``Agent.source_file`` 的自动推算触发条件。

两者语义不同、**不可混用**：``PENDING`` 等待的是用户代码赋值；``_UNSET``
表示「该位置从无默认/显式值」。

.. rubric:: 非行为（防止实现者加戏）

- 不在解析阶段求值任何 Parsable（包括 ``LITERAL``——解析层只推断 type）。
- 不缓存求值结果、不缓存文件内容、不缓存渲染输出；每次 resolve 现场执行。
- 不递归展开被引用文件中的 ``$path``。
- 不做隐式解包：求值面外的字段不会被框架自动 resolve。
- 不提供响应式重算：修改模板引用的属性不会触发任何通知。
- 不监听运行期文件变更（不允热重启）；``FILE_REF`` 的「文件修改后下次求值
  反映新内容」仅指进程内再次求值，不是文件系统 watcher。

.. rubric:: 稳定性分级

本模块全部公开签名（``Parsable`` 及其公开方法、五个形式常量、``PENDING``）
属于跨版本稳定契约；``_`` 前缀符号（``_MissingType``、``_UNSET``、
``_do_resolve``、``_render``、``_infer_type``、``_instance``）为内部 API，
不属稳定契约。

.. seealso::

    :class:`flowing.agent.Agent`
        Parsable 的主要宿主；``Agent.parsable()`` 工厂方法与
        ``Agent.watch()`` 响应式边界。
    :class:`flowing.agent.Agent`（``source_file`` 推算）
        ``default is _UNSET`` 的必填判定。
    :mod:`flowing.errors`
        ``MissingContextError`` / ``MissingFieldError`` /
        ``ReservedAttributeError`` 的定义。
"""

import os
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Final, Generic, Mapping, TypeVar, overload

import jinja2

from flowing.errors import MissingContextError, ReservedAttributeError

if TYPE_CHECKING:
    from flowing.agent import Agent

T = TypeVar("T")

# ---------------------------------------------------------------------------
# 形式常量（type 自动推断的取值）
# ---------------------------------------------------------------------------

LITERAL: Final[str] = "LITERAL"
"""字面量形式标记。

功能与动机：``source`` 为普通字面量（``"hello"``、``123``、``true`` 等，且
不命中 ``RAW`` / ``FILE_REF`` / ``EXPRESSION`` / ``TEMPLATE`` 判定）时的
``Parsable.type`` 取值；``resolve()`` 直接返回 ``source`` 原样，两步渲染对
它是幂等操作。

.. rubric:: 行为规约

- 值为模块级常量，实现可用任意不可变值承载；比较必须用 ``is`` 或与
  ``Parsable.type`` 相等性比较，不依赖具体字面值。
- ``.fya`` 解析层不会为非字符串 YAML 值构造 Parsable（**例外**：字段被宿主
  声明为 Parsable 承载时，非字符串值也包装为 ``LITERAL``，见模块 docstring
  M-14 裁决）；``LITERAL`` 的非字符串 ``source`` 其余只会出现在 Python
  API 层手动构造的场景。
- 非行为：``LITERAL`` 不做任何 Jinja2 解析——若字面量文本恰好含 ``{{ }}``，
  判定会先命中 ``TEMPLATE``，要保留原始文本须使用 ``$"..."``（``RAW``）。

.. seealso:: :class:`Parsable`、:data:`RAW`、:data:`TEMPLATE`
"""

FILE_REF: Final[str] = "FILE_REF"
"""``$`` 文件引用形式标记。

功能与动机：``source`` 以 ``$`` 开头（且非 ``$"..."`` 形式）时的
``Parsable.type`` 取值，如 ``$./system-prompt.md``、``$@/shared/base.md``；
``resolve()`` 时现场读取被引用文件内容并进入第二步 Jinja2 渲染。

.. rubric:: 行为规约

- ``$`` 展开只发生在 Parsable 顶层，**不递归**：被引用文件中的 ``$path``
  作为普通文本进入第二步。
- 文件读取发生在**求值时**而非解析时；文件修改后下次求值自动反映新内容
  （进程内语义，无文件系统监听）。
- 路径前缀（``./`` ``../`` ``@/`` 多级 ``../../``）经
  ``Runtime.resolve_path(path, *, source_dir)`` 解析；``source_dir`` 由
  ``Agent.source_dir`` 属性统一供给（P3-11）。**先剥 ``$`` 再进
  ``resolve_path``**（P3-09 裁决）：``$`` 只是形式标记而非路径成分，
  剥离在 Parsable 求值路径上完成，``resolve_path`` 永远看不到 ``$``
  前缀。
- 边缘情况：想让结果字符串本身以 ``$`` 开头（如 ``"$200.00"``），必须写
  ``$"$200.00"``（``RAW`` 形式），否则会被当作文件引用。

.. seealso:: :class:`Parsable`、:data:`RAW`、``flowing.runtime.Runtime.resolve_path``
"""

EXPRESSION: Final[str] = "EXPRESSION"
"""纯表达式形式标记。

功能与动机：``strip(source)`` 恰好是一个完整的 ``{{ expr }}``（无任何其它
内容）时的 ``Parsable.type`` 取值；``resolve()`` 返回表达式的**原生类型值**
（``int`` / ``bool`` / ``dict`` 等），不做字符串化——这是 Parsable 结果类型
「不一定是 str」的唯一来源。

.. rubric:: 行为规约

- 判定式：``strip(source)`` 以 ``{{`` 开头、``}}`` 结尾，且内部恰好一个
  完整表达式（无嵌套未配对的 ``{{``/``}}``、无首尾其它字符）。
- 混合文本（``"Hello {{ user_name }}, welcome!"``）与 ``{% if %}`` 控制的值
  均**不是**纯表达式，落入 ``TEMPLATE``，结果为 ``str``。
- ``.fya`` 中双花括号必须写在 YAML 引号内，否则 YAML 解析先行报错。
- 非行为：表达式结果不做 JSON 序列化、不做递归 resolve；除下条
  收尾外按原生类型原样返回（``int`` / ``bool`` / ``dict`` 等，不做
  字符串化——这是 Parsable 结果类型不一定是 ``str`` 的唯一来源）。
- **结果收尾（Parsable 一层渲染）**：表达式求值结果是
  ``Parsable`` 时，经其 ``.resolved`` 渲染**一层**后返回（类属性经
  描述符 ``__get__`` 已绑定实例，内层模板随之渲染）。这保证
  ``{{ self.system_prompt }}`` 这类「引用另一个 Parsable」的惰性
  引用块拿到的是渲染后文本而非 Parsable 对象；递归深度仍只有一层
  （``.resolved`` 渲染的是被引用 Parsable 自己的模板，不再向外传递
  EXPRESSION 语义）。注意 ``__str__`` 只展示模板源（不求值），
  本收尾**必须**用 ``.resolved`` 而非 ``str()``。

.. seealso:: :class:`Parsable`、:data:`TEMPLATE`、:attr:`Parsable.resolved`
"""

TEMPLATE: Final[str] = "TEMPLATE"
"""模板形式标记。

功能与动机：``source`` 含 Jinja2 语法（``{{ }}`` 插值 / ``{% %}`` 控制结构）
但不满足纯表达式判定时的 ``Parsable.type`` 取值；``resolve()`` 返回 ``str``。

.. rubric:: 行为规约

- 走完整两步渲染；``{% include %}`` 可在模板内任意位置组合外部文件，
  路径前缀规则与 ``$`` 引用相同，**解析基准也同源**（经框架自定义
  Jinja2 加载器 → ``Runtime.resolve_path``，基准为绑定实例的
  ``Agent.source_dir``，P3-08 裁决）。
- 结果恒为 ``str``；需要原生类型时改写为纯表达式（``EXPRESSION``）。
- 边缘情况：模板引用了不存在的上下文变量时按 Jinja2 默认行为处理
  （未定义变量渲染为空），框架不额外做严格模式校验。
- **非行为**：模板继承（``{% extends %}`` / ``{% block %}``）暂不支持
  ——不在契约枚举内，写了可能碰巧渲染但语义不保证（裁决记录见模块
  docstring 两步渲染段）；跨文件组合请用 ``$`` 引用或
  ``{% include %}``。

.. rubric:: 混合内容中的 Parsable 引用（定稿语义）

模板是混合内容——字面文本、控制结构与插值共存。插值位上的
**Parsable 对象不隐式渲染**（``__str__`` 只展示模板源，见
:meth:`Parsable.__str__`）：要它的渲染结果，写
``{{ x.resolved }}``；写 ``{{ x }}`` 得到的是它未渲染的模板原文。

一个典型场景——system prompt 引用了共享的规则片段文件：

.. code-block:: jinja

    {# 某 Agent 的 system prompt 模板；refund_policy 是 setup 中
       self.refund_policy = self.parsable("$./refund-rules.md")
       挂上的共享片段（Parsable，内容为「退款规则」正文模板） #}
    你是一个订单处理助手。
    当前用户：{{ user_id }}
    {{ refund_policy.resolved }}

这里 ``user_id`` 是普通实例属性（摊平上下文），直接插值；
``refund_policy`` 是 Parsable——``.resolved`` 明确表示「此处发生一次
求值」（与 Vue ``.value`` 心智对齐），不写则渲染出 ``$./refund-rules.md``
这个模板原文。被引用片段内部若再引用 Parsable，同样各写各的
``.resolved``——嵌套渲染的每一步都是显式的，不存在无限递归通道。

.. seealso:: :class:`Parsable`、:data:`EXPRESSION`、:data:`FILE_REF`
"""

RAW: Final[str] = "RAW"
"""原始字面量形式标记（``$"..."``）。

功能与动机：``source`` 形如 ``$"..."`` 时的 ``Parsable.type`` 取值；从首个
``$"`` 到最后一个 ``"`` **贪婪匹配**，内部可含双引号与 ``{{ }}`` 等 Jinja2
语法字符而无需转义。``resolve()`` 跳过全部两步渲染，直接返回引号内文本
（恒为 ``str``）。

.. rubric:: 行为规约

- 边缘情况一：``$"含有 "引号" 的原始文本"`` 的值为 ``含有 "引号" 的原始文本``
  （贪婪匹配到最后一个引号）。
- 边缘情况二：``$"_"`` 的值是普通字符串 ``"_"``，**不触发** PENDING 语义。
- 边缘情况三：``$"$200.00"`` 的值是字符串 ``$200.00``，不会被当作文件引用。
- 非行为：不做任何渲染、不读文件、不访问渲染上下文——未绑定的 ``RAW``
  Parsable 调用 :meth:`Parsable.resolve` 仍需要绑定实例（签名约束），但其
  结果与上下文无关。

.. seealso:: :class:`Parsable`、:data:`PENDING`、:data:`LITERAL`
"""

# ---------------------------------------------------------------------------
# 哨兵
# ---------------------------------------------------------------------------

class _MissingType:
    """``PENDING`` 的单例类型。内部 API，不属稳定契约。

    功能：保证 ``PENDING`` 是进程内唯一实例，支持 ``is`` 精确判断。
    外部代码只应使用 :data:`PENDING` 本身，不应实例化或子类化本类型。

    .. rubric:: 行为规约

    - ``__new__`` 返回唯一实例（模块级单例）。
    - **不提供布尔语义保证**（M-17 裁决扩展）：``if field:`` 的结果不作
      契约——判定「是否未兑现承诺」**唯一合法方式**是 ``field is PENDING``。
    - ``__repr__`` 返回字符串 ``"PENDING"``，便于调试输出与错误信息。

    .. rubric:: 调用关系（审计）

    - 调用：``无``
    - 被调：无框架内调用点（仅作为 ``PENDING`` 的类型载体）
    - 实例化方：``flowing.parsable`` 模块导入时创建 ``PENDING`` 单例
      （每进程一次，模块级）

    .. seealso:: :data:`PENDING`、:data:`_UNSET`
    """

    _instance: ClassVar["_MissingType | None"] = None

    def __new__(cls) -> "_MissingType":
        """返回进程内唯一实例。内部 API，不属稳定契约。

        .. rubric:: 调用关系（审计）

        - 调用：``无``
        - 被调：``_MissingType()`` 实例化（时机：``flowing.parsable`` 模块
          导入创建 ``PENDING`` 单例时，每进程一次）
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance  # type: ignore[return-value]  # 收窄 Optional
    def __repr__(self) -> str:
        """返回 ``"PENDING"``。内部 API，不属稳定契约。

        .. rubric:: 调用关系（审计）

        - 调用：``无``
        - 被调：无框架内显式调用点（调试输出 / 错误信息中隐式触发，
          时机：未见规约）
        """
        return "PENDING"

PENDING: Final[_MissingType] = _MissingType()
"""延迟定义哨兵——``.fya`` 中 ``field: _`` 的解析结果。

.. rubric:: 功能介绍与设计动机

``_`` 在解析阶段**不映射为 ``None`` 或空字符串**，而是映射为本模块级单例
哨兵，表示「我承诺稍后（``setup()`` 或具名块中）赋值」。独立哨兵的存在使
「承诺了但没兑现」能被精确检测——``None``/``""``/``[]`` 都是合法的显式值
（``field: null`` 对可选字段表示「确实无值」），不能兼作「未赋值」标记。

.. rubric:: 使用示例

.. code-block:: yaml

    # agent.fya —— _ 作为延迟定义与字段清单惯例（name 省略，由目录名推断）
    description: _
    system_prompt: _

.. code-block:: python

    # 解析层 / 管线中的判定
    if agent.description is PENDING:
        ...  # 尚未兑现
    agent.description = self.parsable("处理订单查询")  # setup() 中兑现

.. rubric:: 行为规约

- 写法映射：``field: _`` → ``PENDING``；字段缺失或 ``field: null`` →
  可选字段 ``None``、必填字段视同 ``_``（``PENDING``）、资源列表字段 ``[]``；
  ``field: $"_"`` → 字符串 ``"_"``（RAW 形式，不触发 PENDING）。
  **位置分流**：override 位（entry 覆写 / ``args`` 覆写）的 ``_`` =
  空补丁（装配层解析为空、可从基底回填，不报错）；资源列表项位禁止
  ``_``（:func:`flowing.parser.normalize_entries` 抛 ``FormatError``）。
- ``PENDING`` / ``None`` / ``""`` / ``[]``
  四者用 ``is`` 可精确区分；``PENDING`` **不提供布尔语义保证**（M-17
  裁决扩展）——**禁止**用真值判断代替 ``is`` 判断，``if field:`` 的
  结果不作契约。
- 检查时机：不在解析时（解析只标记），而在创建管线 ``setup()`` 返回后、
  ``after_create`` 前——此时 ``before_create`` 钩子与 Composable 已有充分赋值
  机会，而 ``after_create`` 观察者能保证看到完整字段。（字段位检查点；
  override 位 PENDING 无检查点——空补丁语义。）
- 替换语义：多层具名块（如 ``$subagents.pay.system_prompt:``）赋值时，源
  字段为 ``PENDING`` → 合法替换；源字段已有实际值 → 报冲突。即 ``_`` 不是
  「忽略」，而是「该位置的 PENDING 可被具名块替换」的合法前提。完整导航
  规则（split_as 回退、PENDING 物化、列表段按别名）见
  :class:`flowing.subagents.SubagentEntry` 行为规约。
- 不变量：进程内只有一个 ``PENDING`` 实例；可安全跨模块 ``is`` 比较。

.. rubric:: 测试案例

- 前置：``.fya`` 写 ``description: _``；操作：解析；
  期望：``Agent.description is PENDING``。
- 前置：同上；操作：走完创建管线且 ``setup()`` 未赋值；
  期望：PENDING 检查点抛 ``MissingFieldError``。
- 前置：``field: $"_"``；操作：解析 + resolve；
  期望：得到字符串 ``"_"``，全程不出现 PENDING。

:raises flowing.errors.MissingFieldError:
    创建管线 PENDING 检查点仍有字段为 ``PENDING`` 时（由管线抛出，
    非本哨兵自身行为）。

.. seealso::

    :data:`_UNSET`
        语义不同的另一个哨兵（「未设置」），不可混用。
    ``flowing.runtime.Runtime.create_agent``
        PENDING 检查点所在的创建管线。
"""

_UNSET: Final[Any] = object()
"""「未设置」哨兵。内部 API，不属稳定契约。

功能与动机：表示「该位置从无默认/显式值」，现用于 ``Agent.source_file``
的自动推算触发（``source_file is _UNSET`` 时才推算，用户显式写 ``None``
表示禁用相对路径）。与 :data:`PENDING` 语义不同：
``PENDING`` 等待用户代码赋值，``_UNSET`` 是静态判定标记，**不可混用**。

.. rubric:: 行为规约

- 唯一合法判定方式是 ``is`` / ``is not``；不提供布尔语义保证，禁止用
  真值判断。
- 不变量：进程内唯一实例。

.. seealso:: :data:`PENDING`、``flowing.agent.Agent.source_file``
"""

# ---------------------------------------------------------------------------
# Parsable
# ---------------------------------------------------------------------------

class Parsable(Generic[T]):
    """五种可解析赋值形式的统一抽象——存储求值指令，惰性现场求值。

    .. rubric:: 功能介绍

    ``.fya`` 中所有「可解析字段」（``system_prompt``、``description`` 及各类
    具名块内容）与手写子类中的等价声明，统一表示为 ``Parsable[T]``。
    ``Parsable`` 不是固定数据类型：``type`` 由 ``source`` 样式自动推断
    （``LITERAL`` / ``FILE_REF`` / ``EXPRESSION`` / ``TEMPLATE`` / ``RAW``），
    渲染结果的类型由形式决定（``EXPRESSION`` 返回表达式原生类型，其余返回
    ``str`` 或 ``source`` 原样）。它是描述符，存放在 Agent **类属性**上；
    实例访问时自动绑定实例。

    .. rubric:: 设计动机

    惰性求值是**功能正确性前提而非性能优化**：文件解析阶段求值会在环境变量
    未加载、Agent 实例尚不存在、被引用文件未就绪三类场景下产生错误结果。
    统一抽象使 ``.fya`` 五种写法与 Python 手写声明走同一条渲染管线，
    「用到的时候重新算」取代了响应式系统（无 Proxy、无依赖图、无缓存）。
    被它取代的旧设计：解析阶段即求值的早期方案，以及 ``lazy_resolve`` 这一
    曾混用的命名（定稿统一为 :meth:`resolve`）。

    .. rubric:: 使用示例

    ``.fya`` 声明式入口（五种形式）：

    .. code-block:: yaml

        # order-agent.fya（name 省略——由文件名推断）
        description: 处理订单查询            # LITERAL
        system_prompt: $./system-prompt.md   # FILE_REF

        ---
        $system_prompt:
        你是一个订单助手。请用 {{ locale }} 回复。   # TEMPLATE（具名块）
        ---
        $description:
        $"包含 {{ 花括号 }} 的原始文本"               # RAW

    手写子类（与 ``.fya`` 完全等价）：

    .. code-block:: python

        from flowing import Agent, Parsable

        class OrderAgent(Agent):
            system_prompt = Parsable("$./system-prompt.md")
            max_turns = Parsable("{{ config.limits.turns }}")  # EXPRESSION → int

            async def setup(self, user_id: int):
                self.user_id = user_id
                # setup() 中手动创建已绑定 Parsable（不走 __setattr__ 拦截）
                self.greeting = self.parsable("你好 {{ user_id }}")
                self.greeting.resolve()   # 默认上下文 = self；str() 只展示
                # 模板原文不求值（R-2 澄清：str 自动 resolve 形态已弃用）

    嵌套引用（模板中触发子 Parsable 现场求值）：

    .. code-block:: jinja

        {# system-prompt.md #}
        你的问候语是：{{ greeting.resolved }}
        {% if debug_info.resolved %}详情：{{ debug_info.resolved }}{% endif %}

    .. rubric:: 行为规约

    - ``type`` 自动推断，构造时不接受手动指定；推断规则见模块级
      「五种形式判定表」。
    - 描述符行为：类级别访问返回自身（``_instance=None``，未绑定）；实例
      访问返回一个绑定了 ``_instance`` 的**浅拷贝**（共享 ``source`` /
      ``type``，拷贝间互不影响）。本类只实现 ``__get__``，是**非数据描述符**
      ——实例属性赋值（``self.x = ...``）正常遮蔽类级 Parsable，不会触发
      描述符协议。
    - 求值上下文：见 :meth:`resolve`；渲染上下文构成见模块级「渲染上下文」。
    - 不变量：``source`` 与 ``type`` 创建后不变；求值不改变 Parsable 自身
      状态（无缓存、无记忆）。
    - 非行为：不做隐式解包（求值面外拿到的永远是 Parsable 对象）；不缓存
      任何求值结果；不递归展开被引用文件中的 ``$path``。
    - 边缘情况：构造 ``Parsable(123)`` 等非字符串 ``source`` 合法，推断为
      ``LITERAL``；构造时``source`` 为 ``PENDING`` 属调用方错误（PENDING
      不应被包进 Parsable，两者是解析层的两种不同产物）。

    .. rubric:: 测试案例

    - 前置：``class A(Agent): p = Parsable("{{ 1 + 1 }}")``；
      操作：``A.p``；期望：返回原对象且 ``A.p._instance is None``。
    - 前置：同上，实例 ``a`` 已创建；操作：``a.p``；
      期望：返回浅拷贝，``a.p._instance is a``，且 ``a.p.source is A.p.source``。
    - 前置：``a.greeting = a.parsable("你好 {{ user_id }}")`` 且
      ``a.user_id == "u1"``；操作：``a.greeting.resolve()``；
      期望：``"你好 u1"``（str() 只展示模板原文，不求值——R-2 澄清）。
    - 前置：``p = Parsable("$./prompt.md")``，文件内容修改过；
      操作：连续两次 ``p.resolve(agent)``；期望：第二次反映新内容
      （无缓存）。
    - 前置：``a.c = Parsable("{{ a == b }}")``；操作：修改 ``a.a``；
      期望：``watch("c")`` 注册的 handler 不被触发（响应式边界）。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.parsable.Parsable._infer_type()``（时机：``__init__``
      构造时，每次创建）
    - 被调：无框架内调用点（类本身仅被实例化与作描述符宿主）
    - 实例化方：``flowing.agent.Agent.parsable()``（时机：``setup()`` 中
      手动创建已绑定实例）；``.fya`` 解析 / 编译层（时机：解析可解析字段，
      ``flowing compile`` 产物以类体赋值形式写出）；``flowing.context``
      的 ``PromptBlock`` 注册（时机：``_assemble_context()`` 管线注册的
      惰性内容块）；``flowing.model.ModelConfig`` 字段（M-14 宿主声明
      Parsable 承载）；``flowing.plugins.skills.Skill`` 的
      ``description`` / ``content`` 字段（时机：Skill 声明与加载管线）

    .. seealso::

        :data:`LITERAL`、:data:`FILE_REF`、:data:`EXPRESSION`、
        :data:`TEMPLATE`、:data:`RAW` —— 五种形式常量与判定规则。
        ``flowing.agent.Agent.parsable`` —— 创建已绑定 Parsable 的工厂方法。
        ``flowing.agent.Agent.watch`` —— 只监听属性赋值的响应式边界。
    """

    source: Any
    """原始求值指令（模板文本、``$`` 路径、表达式或字面量）。
    创建后不可变；绑定浅拷贝之间共享同一对象。
    
    .. seealso:: :data:`LITERAL` 等五个形式常量（样式判定规则）
    """

    type: str
    """自动推断的形式标记，取值为 :data:`LITERAL` / :data:`FILE_REF` /
    :data:`EXPRESSION` / :data:`TEMPLATE` / :data:`RAW` 之一。
    创建后不可变；不接受手动指定。
    
    .. seealso:: :meth:`_infer_type`（推断逻辑，内部 API）
    """

    _instance: "Agent | None"
    """描述符绑定的 Agent 实例；类级别（未绑定）为 ``None``。
    :meth:`resolve` 不传 ``context`` 时使用它作为默认上下文。
    内部 API，不属稳定契约。
    
    .. seealso:: :meth:`__get__`、:meth:`resolve`
    """

    def __init__(self, source: Any) -> None:
        """构造 Parsable，按 ``source`` 样式自动推断 ``type``。

        .. rubric:: 功能介绍

        创建存储求值指令的 Parsable 对象；构造是同步、无副作用的——不读
        文件、不渲染、不访问任何运行时状态。

        .. rubric:: 设计动机

        解析期与求值期严格分离：构造（发生在 ``.fya`` 解析或类定义时）只做
        形式推断，所有昂贵/依赖运行时的操作推迟到 :meth:`resolve`。

        .. rubric:: 使用示例

        .. code-block:: python

            Parsable("你好 {{ user_id }}")        # TEMPLATE
            Parsable("{{ config.limits.turns }}") # EXPRESSION
            Parsable("$./prompt.md")              # FILE_REF
            Parsable('$"原始 {{ 文本 }}"')          # RAW
            Parsable("普通文本")                    # LITERAL
            Parsable(123)                         # LITERAL（非字符串）

        .. rubric:: 行为规约

        - 期待行为：按模块级「五种形式判定表」自上而下判定 ``type``。
        - 前置条件：``source`` 不应为 :data:`PENDING`——``_`` 在 ``.fya``
          解析层直接映射为 PENDING 哨兵，不构造 Parsable。
        - 后置条件：``self.type`` 已确定；``self._instance is None``
          （手动构造的对象未绑定，需经 :meth:`flowing.agent.Agent.parsable`
          或描述符协议绑定后才能无参 :meth:`resolve`）。
        - 非行为：不校验 ``$`` 路径存在性、不做 Jinja2 语法预检——错误在
          求值时暴露。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.parsable.Parsable._infer_type()``（时机：每次
          构造）
        - 被调：``flowing.agent.Agent.parsable()``（时机：``setup()`` 中
          手动创建已绑定实例）；``.fya`` 解析 / 编译层（时机：解析
          ``.fya`` 可解析字段、编译产物类体赋值，每次解析 / 编译）

        .. seealso:: :meth:`resolve`、:meth:`_infer_type`
        """
        self.source = source
        self.type = self._infer_type(source)
        self._instance = None

    @overload
    def __get__(self, instance: None, owner: type | None = ...) -> "Parsable[T]": ...
    @overload
    def __get__(self, instance: "Agent", owner: type | None = ...) -> "Parsable[T]": ...
    def __get__(self, instance: "Agent | None", owner: type | None = None) -> "Parsable[T]":
        """描述符协议：实例访问返回绑定浅拷贝，类访问返回自身。

        .. rubric:: 功能介绍

        ``Parsable`` 存放在 Agent 类属性上。通过类访问（``OrderAgent.p``）
        返回原对象（``_instance=None``，未绑定）；通过实例访问
        （``agent.p``）返回一个 ``_instance=agent`` 的浅拷贝。

        .. rubric:: 设计动机

        浅拷贝绑定使「类级声明共享、实例级上下文独立」：``source``/``type``
        只有一份（声明是类级共享的），而求值上下文（``_instance``）按访问
        的实例区分；拷贝的开销可忽略，且避免在共享对象上可变状态。

        .. rubric:: 使用示例

        .. code-block:: python

            class A(Agent):
                p = Parsable("{{ locale }}")

            A.p            # Parsable 自身，未绑定
            a = ...        # 创建实例后
            a.p            # 浅拷贝，_instance is a
            a.p.resolve()  # 默认上下文 = a

        .. rubric:: 行为规约

        - 期待行为：实例访问每次返回**新的浅拷贝**——``a.p is not a.p``，
          但 ``a.p.source is A.p.source``。
        - 边缘情况：本类不实现 ``__set__``/``__delete__``，是非数据描述符；
          ``self.p = ...`` 走普通实例属性赋值，遮蔽类级声明，同时触发
          ``watch("p")``（赋值事件）。
        - 非行为：不在 ``__get__`` 中触发任何求值——绑定不等于 resolve。

        .. rubric:: 调用关系（审计）

        - 调用：``copy.copy``（标准库浅拷贝——S-16 裁决：不重跑
          ``__init__`` / ``_infer_type``，仅翻转 ``_instance``；时机：
          每次实例访问）
        - 被调：描述符协议——框架内触发点为实例访问 Agent 类级 Parsable
          属性，如 ``flowing.agent.Agent._assemble_context()`` 渲染链中
          Jinja2 访问 ``self.system_prompt``（时机：``_assemble_context()``
          每次调用现场求值）

        .. seealso:: :meth:`resolve`、:meth:`flowing.agent.Agent.parsable`、
            :meth:`flowing.agent.Agent.watch`
        """
        if instance is None:
            # 类级别访问：返回自身（未绑定）
            return self
        # 实例访问：返回绑定浅拷贝（共享 source/type）。S-16 裁决：
        # copy.copy 浅拷贝（不重跑 __init__/_infer_type），仅翻转 _instance
        import copy   # 模块级 import 冻结，函数内局部引入（同 tool.py 先例）
        bound: "Parsable[T]" = copy.copy(self)
        bound._instance = instance
        return bound

    def resolve(self, context: "Agent | Mapping[str, Any] | None" = None) -> T:
        """惰性求值：执行（可能的）两步渲染并返回结果。

        .. rubric:: 功能介绍

        对 ``source`` 按 ``type`` 求值：``RAW`` 直接返回引号内文本；
        ``LITERAL`` 返回 ``source`` 原样；``FILE_REF`` 现场读文件后渲染；
        ``EXPRESSION`` 返回表达式原生值；``TEMPLATE`` 返回渲染后的 ``str``。
        同步方法——求值可能涉及文件读取，但均为本地小文件，不引入异步。

        .. rubric:: 设计动机

        「用到的时候重新算」是框架取代响应式系统的核心机制；每次调用现场
        求值（无缓存）保证拿到的永远是最新的 ``env`` / ``config`` / 实例
        属性 / 被引用文件内容。草稿中混用的 ``lazy_resolve`` 已统一为本名。

        .. rubric:: 使用示例

        .. code-block:: python

            # 已绑定（描述符访问或 self.parsable() 创建）
            self.greeting.resolve()          # 默认上下文 = 绑定实例
            self.greeting.resolve(other)     # 显式覆盖上下文为另一个 Agent

            # 求值面外：_extra 中的字段框架不自动 resolve
            greeting = self._extra["greeting"]     # Parsable 对象
            text = greeting.resolve(self)          # 扩展手动求值

        .. rubric:: 行为规约

        - ``context=None``：使用 ``_instance``；未绑定（类级别访问得到的
          对象或手动 ``Parsable(...)`` 构造的）→ 抛 ``MissingContextError``。
        - ``context`` 为 Agent：渲染上下文 = 已注册状态键现场值 +
          ``_extra`` 扩展字段（原值；其中 Parsable 经 ``.resolved`` /
          ``resolved`` 惰性触发）+ 实例属性摊平（``vars(agent)`` 逐键
          提升为顶层变量；三层优先级从低到高，与属性查找回退链
          ``__dict__`` → ``_extra`` → state 反向一致）+ ``env``
          （``agent.runtime.env``，``os.environ`` 只读视图）+
          ``config``（``agent.runtime.config``，项目配置合并视图）。
          无论传入的还是绑定的 Agent，``env`` 与 ``config`` 始终从该
          Agent 的 ``runtime`` 现场获取并合并。
        - ``context`` 为 ``Mapping``：以其内容为基底，**同样自动注入**
          ``env``/``config``（M-13 裁决：始终注入，Mapping 不是豁免通道）。
          来源：``env`` 恒为 ``os.environ`` 只读视图；``config`` 取自绑定
          实例的 ``runtime``，未绑定时不注入 ``config``（模板引用
          ``config.*`` 按 Jinja2 默认行为渲染为空）。键冲突时 Mapping 中
          的显式键优先于注入值。本分支由 ``resolve()`` 本体承载（S-16
          裁决：不拆具名内部函数——``_do_resolve`` 只服务 Agent 分支；
          框架执行链路全部传 Agent，Mapping 只服务测试与手动求值）。
          ``FILE_REF`` 的路径解析在本分支以**绑定实例**的 ``runtime``
          进行；未绑定 + ``FILE_REF`` → ``MissingContextError``（S-16）。
        - 摊平保留名约束：Agent 属性命名为 ``env``/``config``/``agent``/``self`` 会覆盖
          框架注入的值，框架检测到抛 ``ReservedAttributeError``。
        - 期待行为：同一次调用内两步渲染按固定顺序执行、无交错；多次调用
          各自独立现场求值（无缓存、无记忆）。
        - 非行为：不递归 resolve 结果（``EXPRESSION`` 求值得到
          Parsable 时经其 ``.resolved`` 渲染一层即止，见
          :data:`EXPRESSION` 的结果收尾规约）；不修改自身状态。
        - 边缘情况：``FILE_REF`` 的被引用文件不存在 → 求值时抛出底层
          ``OSError``（框架不预检、不兜底）；模板引用未定义变量按 Jinja2
          默认行为渲染为空；``FILE_REF`` + Mapping 上下文 + 未绑定实例
          → ``MissingContextError``（路径解析无 runtime 来源，S-16）。

        .. rubric:: 测试案例

        - 前置：未绑定 ``p = Parsable("{{ x }}")``；操作：``p.resolve()``；
          期望：抛 ``MissingContextError``。
        - 前置：未绑定 ``p = Parsable("$./prompt.md")``（FILE_REF）；
          操作：``p.resolve({"x": 1})``；期望：抛 ``MissingContextError``。
        - 前置：``a.user_id = "u1"``；操作：
          ``a.parsable("{{ user_id }}").resolve()``；期望：``"u1"``。
        - 前置：``EXPRESSION`` 形式 ``Parsable("{{ config.limits.turns }}")``
          且配置值为 ``10``；操作：resolve；期望：返回 ``int(10)`` 而非
          ``"10"``。
        - 前置：Agent 实例属性含 ``env``；操作：resolve 任意 Parsable；
          期望：抛 ``ReservedAttributeError``。

        :raises flowing.errors.MissingContextError:
            未绑定且未传 ``context`` 时。
        :raises flowing.errors.ReservedAttributeError:
            渲染上下文摊平检测到保留名 ``env``/``config`` 被实例属性占用时。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.parsable.Parsable._do_resolve()``（时机：
          ``FILE_REF`` / ``EXPRESSION`` / ``TEMPLATE`` 每次求值；
          ``RAW`` / ``LITERAL`` 在本层短路，不进入）
        - 被调：``flowing.tool.ToolEntry.resolve()``（时机：``tool_call()``
          内、``before_tool_call`` 钩子之后、``_normalize()`` 内部第 2
          步，specified 每次调用现场求值）；``flowing.subagents.SubagentEntry.resolve()``（时机：
          ``invoke_subagent()`` 内，specified 参数求值）；
          ``flowing.model.ModelConfig.resolve()``（时机：每轮 provider_gen 前
          字段级求值）；``flowing.plugins.skills`` 加载流程第 6 步
          ``skill.content.resolve(...)``（时机：每次 Skill 加载）；
          ``flowing.parsable.Parsable.resolved`` 与
          ``flowing.parsable.Parsable.resolved``（时机：模板渲染每次
          触发）；求值面外字段由扩展显式调用（docstring 声明的边界，
          非框架自动）

        .. seealso::

            :attr:`resolved`
                模板内嵌套引用的等价触发入口。
            :meth:`_do_resolve`、:meth:`_render`
                内部实现（不属稳定契约）。
            :data:`EXPRESSION`、:data:`RAW`
                各形式的结果类型契约。
        """
        if self.type is RAW:
            # RAW 短路：跳过全部两步渲染，贪婪取首个 $" 到最后一个 " 之间文本
            text: str = self.source[2:-1]
            return text  # type: ignore[return-value]
        if self.type is LITERAL:
            # LITERAL 短路：返回 source 原样（两步渲染对其幂等）
            return self.source  # type: ignore[return-value]
        if context is None:
            context = self._instance
        if context is None:
            # 未绑定且未传 context -> MissingContextError（无参构造，固定消息）
            raise MissingContextError()
        if isinstance(context, Mapping):
            # Mapping 分支（M-13：以其内容为基底并始终注入 env/config）——
            # S-16 裁决：由 resolve() 本体承载，不拆具名内部函数
            # （_do_resolve 只服务 Agent 分支；Mapping 只服务测试与手动求值）
            ctx: dict[str, Any] = dict(context)
            ctx.setdefault("env", MappingProxyType(os.environ))  # os.environ 只读视图（R-5 落实）
            if self._instance is not None:
                ctx.setdefault("config", self._instance.runtime.config)
                # {% include %} 基准永远来自绑定实例（P3-08）：绑定实例存在时
                # Mapping 分支的 include 与 $ 引用同源于 resolve_path
                ctx["__include_resolver__"] = _make_include_resolver(
                    self._instance.runtime, self._instance.source_dir
                )
            # 未绑定时不注入 config，模板引用 config.* 按 Jinja2 默认渲染为空
            if self.type is FILE_REF:
                # FILE_REF 需 runtime 做路径解析，Mapping 路径下唯一来源是
                # 绑定实例（S-16 裁决）；未绑定 -> MissingContextError
                if self._instance is None:
                    raise MissingContextError()
                resolved_path = self._instance.runtime.resolve_path(
                    self.source[1:],   # P3-09：先剥 $ 形式标记再进 resolve_path
                    source_dir=self._instance.source_dir,  # P3-11：文件→目录换算唯一承担者是 Agent.source_dir
                )
                ctx["__file_ref_path__"] = resolved_path  # 供 _render 第一步读文件（R-11 内部约定键名）
            return self._render(ctx)
        # context 为 Agent：摊平实例属性 + env/config 注入与保留名检测
        # （ReservedAttributeError 检测点在上下文构建中，见 _do_resolve）
        return self._do_resolve(context)

    @property
    def resolved(self) -> T:
        """模板内嵌套引用的现场求值入口——等价于无参 :meth:`resolve`。

        .. rubric:: 功能介绍

        当 Agent 实例上的 Parsable 属性被摊平为模板顶层变量时，模板通过
        ``{{ greeting.resolved }}`` 触发该子 Parsable 的现场求值。外层渲染
        上下文已是当前 Agent，子求值内部拿到同样的完整上下文。

        .. rubric:: 设计动机

        Jinja2 渲染任意对象时默认调用 ``str()``——对 Parsable 而言
        ``str()`` 只展示模板源（不求值，见 :meth:`__str__`）；因此模板内
        引用 Parsable 必须写显式的 ``.resolved``（「这里是一次求值」
        意图可读，不依赖隐式字符串化），并与 Vue 的 ``.value`` 心智
        模型对齐。模板中显式 ``{{ x.resolve() }}``（无参、已绑定）与
        ``.resolved`` 语义等价，但 ``.resolved`` 是定稿惯用写法。

        .. rubric:: 使用示例

        .. code-block:: jinja

            {# system-prompt.md —— greeting 是实例上的 Parsable 属性 #}
            你的问候语是：{{ greeting.resolved }}
            {% if debug_info.resolved %}详情：{{ debug_info.resolved }}{% endif %}

        .. rubric:: 行为规约

        - 期待行为：每次访问现场求值，不缓存——两次 ``.resolved`` 之间
          修改了被引用的属性，第二次访问反映新值。
        - 前置条件：对象必须已绑定（``_instance`` 非空），否则与
          :meth:`resolve` 无参调用同样抛 ``MissingContextError``。
        - 非行为：不是响应式订阅——访问它不会注册任何依赖关系。

        .. rubric:: 测试案例

        - 前置：``a.greeting = a.parsable("你好 {{ user_id }}")``，
          ``a.system_prompt`` 模板含 ``{{ greeting.resolved }}``；
          操作：``_assemble_context()`` 触发渲染；
          期望：输出含当前 ``user_id`` 的问候文本。
        - 前置：未绑定 Parsable；操作：访问 ``.resolved``；
          期望：抛 ``MissingContextError``。

        :raises flowing.errors.MissingContextError:
            未绑定时。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.parsable.Parsable.resolve()``（时机：每次访问
          现场求值，无缓存）
        - 被调：Jinja2 模板内嵌套引用 ``{{ x.resolved }}``（时机：外层
          Parsable 渲染时——如 ``flowing.agent.Agent._assemble_context()``
          每次调用触发的渲染链）

        .. seealso:: :meth:`resolve`、:meth:`__str__`
        """
        return self.resolve()  # 默认上下文 = 绑定实例；未绑定由 resolve 抛 MissingContextError

    def __str__(self) -> str:
        """展示 Parsable 的模板源本身，**不求值**。

        .. rubric:: 功能介绍

        返回未渲染的 ``source`` 文本（非 ``str`` 类型的 ``source``
        ——``LITERAL`` 裸值——经 ``str()`` 转换）。字符串拼接、
        ``str()``、f-string、Jinja2 隐式字符串化拿到的是**模板原文**，
        不是求值结果。

        .. rubric:: 设计动机

        「展示模板」与「渲染结果」分离：求值只有一个显式入口
        （:meth:`resolve` / :attr:`resolved`），``str()`` 不再暗中触发
        渲染——Jinja2 模板内引用 Parsable 必须写 ``{{ x.resolved }}``，
        隐式字符串化只用于调试与日志。无副作用、无前置条件
        （未绑定也能 str），与 :meth:`resolve` 的求值语义刻意对立。

        .. rubric:: 行为规约

        - 期待行为：返回 ``source`` 的文本形式；不触发 resolve、不读
          文件、不访问运行时；未绑定不抛错。
        - 边缘情况：``source`` 含换行 / 引号时原样展示（不转义——
          转义展示是 :meth:`__repr__` 的职责）。

        .. rubric:: 调用关系（审计）

        - 调用：无（不触发 resolve）
        - 被调：无框架内求值调用点（调试 / 日志 / 字符串拼接隐式触发）

        .. seealso:: :meth:`__repr__`、:meth:`resolve`、:attr:`resolved`
        """
        return str(self.source)  # 模板原文，不求值（LITERAL 裸值经 str 转换）

    def __repr__(self) -> str:
        """返回 ``Parsable(source=..., type=...)``，显示原始指令而非求值结果。

        .. rubric:: 功能介绍

        调试表示，**始终**展示未渲染的 ``source`` 与推断出的 ``type``，
        无论绑定与否。

        .. rubric:: 设计动机

        调试 / 日志 / traceback 中需要看到「模板原文」而非「某次求值的
        瞬时结果」；且 ``repr`` 必须无副作用、无前置条件（未绑定也能
        repr），与 :meth:`__str__` 的求值语义刻意对立。

        .. rubric:: 行为规约

        - 期待行为：格式固定为 ``Parsable(source=<repr>, type=<常量名>)``；
          不触发 resolve、不读文件、不访问运行时。
        - 边缘情况：``source`` 含换行 / 引号时按其 ``repr`` 转义展示。

        .. rubric:: 测试案例

        - 前置：``p = Parsable("{{ x }}")`` 未绑定；操作：``repr(p)``；
          期望：形如 ``"Parsable(source='{{ x }}', type=EXPRESSION)"``，
          不抛异常。

        .. rubric:: 调用关系（审计）

        - 调用：``无``
        - 被调：无框架内显式调用点（调试 / 日志 / traceback 隐式触发，
          时机：未见规约）

        .. seealso:: :meth:`__str__`（与之对立的求值行为）
        """
        return f"Parsable(source={self.source!r}, type={self.type})"

    # -------------------------------------------------------------------
    # 内部方法（不属稳定契约，仅为说明时序而列出）
    # -------------------------------------------------------------------

    def _do_resolve(self, agent: "Agent") -> T:
        """构建渲染上下文并执行渲染。内部 API，不属稳定契约。

        .. rubric:: 功能介绍与动机

        :meth:`resolve` 的核心实现：把 Agent 实例属性摊平为顶层变量，合并
        ``env``（``agent.runtime.env``）与 ``config``
        （``agent.runtime.config``），然后委托 :meth:`_render` 执行两步
        渲染。单独成方法是为了让「上下文构建」与「渲染执行」两个关注点
        各自可测。

        .. rubric:: 行为规约

        - 上下文构建等价于（键优先级从低到高，后者覆盖前者）::

              ctx = {
                  **state_keys,              # 已注册状态键的现场值（模板
                                             # 自动暴露状态量的唯一通道——
                                             # P3-03 配套后 __getattr__ 无
                                             # 状态回退）
                  **agent._extra,            # .fya 扩展字段（原值并入；
                                             # Parsable 经 .resolved 惰性触发）
                  **agent.__dict__,          # 实例属性摊平（不含类属性/方法——
                                             # 那些经 agent 入口 getattr 访问）
                  "agent": agent,            # 实例自身入口（描述符/类属性/方法可达）
                  "self": agent,               # 与 agent 同值的别名（模板里两种写法等价）
                  "env": agent.runtime.env,
                  "config": agent.runtime.config,
              }

          ``env``/``config`` 在摊平之后写入——实例属性占用保留名时会被
          框架检测并抛 ``ReservedAttributeError``，而不是静默互相覆盖。
          状态键 / ``_extra`` 键与实例属性的重名在 ``register_state`` /
          解析期检测，运行期不撞车。
        - 时序约束：每次调用现场构建上下文（``env``/``config``/实例属性
          /状态键均取当前值），构建结果不跨调用复用。
        - ``FILE_REF`` 的路径解析需要运行时与 ``source_dir`` 基准
          （由 ``agent.source_dir`` 属性统一供给，P3-11），经
          ``Runtime.resolve_path(path, *, source_dir)`` 完成；
          传入前已剥去 ``$`` 形式标记（P3-09）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.parsable.Parsable._render()``（时机：构建渲染
          上下文后，每次 resolve）；``flowing.runtime.Runtime.resolve_path()``
          （时机：``FILE_REF`` 求值时路径解析，``source_dir`` 由
          ``Agent.source_dir`` 属性统一供给）
        - 被调：``flowing.parsable.Parsable.resolve()``（时机：每次非
          短路求值）

        .. seealso:: :meth:`resolve`、:meth:`_render`、
            ``flowing.runtime.Runtime.resolve_path``
        """
        state_keys: dict[str, Any] = dict(getattr(agent, "_state", None) or {})
        # R-6 落实：状态袋最小接口约定为 agent._state: dict 直读
        # 保留名检测（摊平构建前检测 agent.__dict__）——
        # 实例属性占用 env/config/agent/self 会覆盖框架注入值，fail fast
        for reserved in ("env", "config", "agent", "self"):
            if reserved in agent.__dict__:
                raise ReservedAttributeError(reserved)
        ctx: dict[str, Any] = {
            **state_keys,   # 状态键（优先级最低；模板自动暴露状态量的唯一通道）
            **agent._extra,
            **agent.__dict__,
            "agent": agent,   # 实例自身入口（Jinja 属性解析走 getattr，描述符 __get__ 正常触发）
            "self": agent,     # agent 的别名——模板里 self.xxx 与 agent.xxx 等价，按书写习惯自选
            "env": agent.runtime.env,
            "config": agent.runtime.config,
        }
        # {% include %} 与 $ 引用同源（P3-08）：include 名经同一
        # resolve_path、同一 source_dir 基准（绑定实例的 Agent.source_dir）
        ctx["__include_resolver__"] = _make_include_resolver(agent.runtime, agent.source_dir)
        if self.type is FILE_REF:
            # FILE_REF 路径解析（每次求值）：source_dir 基准由
            # agent.source_dir 属性统一供给（P3-11：「文件→所在目录」
            # 换算的唯一承担者；source_file is None 时为 None，./ 前缀
            # 由 resolve_path 报错）
            resolved_path = agent.runtime.resolve_path(
                self.source[1:],   # P3-09：先剥 $ 形式标记再进 resolve_path
                source_dir=agent.source_dir,
            )
            ctx["__file_ref_path__"] = resolved_path  # 供 _render 第一步读文件（R-11 内部约定键名）
        result: T = self._render(ctx)
        return result

    def _render(self, ctx: Mapping[str, Any]) -> T:
        """按固定顺序执行两步渲染。内部 API，不属稳定契约。

        .. rubric:: 功能介绍与动机

        渲染执行体，承载「两步渲染顺序固定不可配置」的定稿约束：

        1. ``$`` 引用展开——仅顶层、不递归（``FILE_REF`` 形式在此读文件；
           其余形式的 ``source`` 文本中不会出现待展开的顶层 ``$path``）。
        2. Jinja2 渲染——``EXPRESSION`` 返回表达式原生值，其余返回
          ``str``；``{% include %}`` 在此阶段解析。

        ``{% include %}`` 的落点是 Jinja2 加载器：框架为渲染 Environment
        提供自定义加载器，模板内的 include 名（如 ``{% include "./header.md" %}``）
        经与 ``FILE_REF`` **同源**的路径解析——同一个
        ``Runtime.resolve_path``、同一个 ``source_dir`` 基准（绑定实例的
        ``Agent.source_dir``，P3-08 裁决）。内联模板无「包含者文件」身份，
        include 基准一律来自绑定实例；include 名不剥 ``$``（Jinja2 模板内
        的 include 语法本就不带 ``$`` 形式标记）。加载器对
        ``{% extends %}`` 的解析**不在契约内**（模板继承暂不支持，
        见模块 docstring 两步渲染段）。

        ``RAW`` 与 ``LITERAL`` 在 :meth:`resolve` 层短路，不进入本方法。

        .. rubric:: 行为规约

        - 两步**分开执行、无交错**：不会在 ``$`` 展开中触发 Jinja2，也
          不会在 Jinja2 渲染中再触发 ``$`` 展开。
        - 不缓存渲染结果与编译产物以外的任何输入（模板编译缓存属
          Jinja2 内部行为，不构成框架级结果缓存）。

        .. rubric:: 调用关系（审计）

        - 调用：无框架内调用目标（Jinja2 引擎为外部库；``FILE_REF`` 读
          文件与路径解析的框架侧入口为
          ``flowing.runtime.Runtime.resolve_path()``，时机：第一步
          ``$`` 展开、仅 ``FILE_REF`` 形式；第二步 ``{% include %}``
          经框架提供的自定义 Jinja2 加载器走同一个
          ``Runtime.resolve_path``、同一 ``source_dir`` 基准，P3-08）
        - 被调：``flowing.parsable.Parsable._do_resolve()``（时机：每次
          resolve 进入渲染时）

        .. seealso:: :meth:`resolve`、:meth:`_do_resolve`
        """
        # 第一步：$ 引用展开——仅 FILE_REF 在此现场读文件（不递归展开被引用
        # 文件中的 $path）；路径解析已在 _do_resolve / resolve 的 Mapping
        # 分支经 Runtime.resolve_path 完成；文件不存在时底层 OSError 直接
        # 抛出（框架不预检、不兜底）
        if self.type is FILE_REF:
            path = ctx["__file_ref_path__"]  # 由 resolve/_do_resolve 解析后放入（R-11 内部约定键名）
            expanded: str = Path(path).read_text(encoding="utf-8")  # 现场读取，无缓存
        else:
            expanded = self.source  # 其余形式的 source 文本中不会出现待展开的顶层 $path
        # 第二步：Jinja2 渲染——EXPRESSION 返回表达式原生值，其余返回 str；
        # {% include %} 在此阶段经自定义加载器解析（与 $ 引用同源于
        # resolve_path，P3-08）。每次求值现场新建 Environment——框架级
        # 不缓存编译产物跨调用复用之外的任何结果（无缓存原则）
        env = _build_jinja_env(ctx.get("__include_resolver__"))
        # 注意：渲染调用一律传位置形态 dict（render(ctx) / expr(ctx)），
        # 不能 **ctx 展开——ctx 里的 "self" 键（agent 别名）会与
        # Template.render / TemplateExpression.__call__ 的 self 形参撞车
        if self.type is EXPRESSION:
            # 纯表达式：compile_expression 返回表达式原生类型值（不字符串化）
            inner = str(self.source).strip()[2:-2]
            result: Any = env.compile_expression(inner)(ctx)
            if isinstance(result, Parsable):
                # 结果收尾（一层）：引用另一个 Parsable 时经其 .resolved 渲染
                # 一层后返回——拿到渲染后文本而非 Parsable 对象；递归深度
                # 仍只有一层（.resolved 渲染的是被引用 Parsable 自己的模板）
                result = result.resolved
            return result
        rendered: T = env.from_string(str(expanded)).render(ctx)  # type: ignore[assignment]
        return rendered

    @classmethod
    def _infer_type(cls, source: Any) -> str:
        """按 ``source`` 样式推断形式常量。内部 API，不属稳定契约。

        .. rubric:: 功能介绍与动机

        :meth:`__init__` 调用的判定逻辑，实现模块级「五种形式判定表」的
        自上而下顺序：``$"..."`` → ``RAW``；``$`` 开头 → ``FILE_REF``；
        ``strip(source)`` 恰为一个完整 ``{{ expr }}`` → ``EXPRESSION``；
        含 Jinja2 语法 → ``TEMPLATE``；其余（含非字符串）→ ``LITERAL``。

        .. rubric:: 行为规约

        - 纯函数：同输入恒同输出，无副作用。
        - 边缘情况：``$"_"`` → ``RAW``；``"_"``（裸字符串，非哨兵）→
          ``LITERAL``（PENDING 语义只存在于 ``.fya`` 解析层，不经本函数）。

        .. rubric:: 调用关系（审计）

        - 调用：``无``（纯函数）
        - 被调：``flowing.parsable.Parsable.__init__()``（时机：每次构造
          Parsable）

        .. seealso:: :data:`RAW`、:data:`FILE_REF`、:data:`EXPRESSION`、
            :data:`TEMPLATE`、:data:`LITERAL`
        """
        # 按模块级「五种形式判定表」自上而下判定，首个命中者生效
        if not isinstance(source, str):
            # 非字符串值（123/true 等）：LITERAL（M-14 例外承载与 Python API 手动构造）
            return LITERAL
        if source.startswith('$"') and source.endswith('"'):
            # $"..." 贪婪匹配形式（含 $"_" 与 $"$200.00" 边缘情况）
            return RAW
        if source.startswith("$"):
            # $ 开头的路径（非 $" 形式）：$./x.md、$@/shared/x.md
            return FILE_REF
        stripped: str = source.strip()
        if stripped.startswith("{{") and stripped.endswith("}}"):
            # strip(source) 恰好是一个完整 {{ expr }}：R-4 落实——内部不得
            # 再出现 {{ / }}（即首个 {{ 的配对 }} 恰落在末尾），否则是
            # 混合文本（如 "{{ a }} {{ b }}"），落 TEMPLATE
            inner = stripped[2:-2]
            if "{{" not in inner and "}}" not in inner:
                return EXPRESSION
        if "{{" in source or "{%" in source:
            # 含 {{ }} / {% %} 的混合文本
            return TEMPLATE
        return LITERAL


# ---------------------------------------------------------------------------
# Jinja2 渲染支撑（内部 API，不属稳定契约）
# ---------------------------------------------------------------------------

class _ResolvePathLoader(jinja2.BaseLoader):
    """``{% include %}`` 的自定义加载器：与 ``$`` 引用同源于
    ``Runtime.resolve_path``、同一 ``source_dir`` 基准（P3-08 裁决）。

    include 名不剥 ``$``（Jinja2 模板内的 include 语法本就不带 ``$`` 形式
    标记）；内联模板无「包含者文件」身份，基准永远来自绑定实例——无绑定
    实例（resolver 为 None）时 include 一律 ``TemplateNotFound``。
    """

    def __init__(self, resolver: Any) -> None:
        self._resolver = resolver

    def get_source(self, environment: Any, template: str) -> tuple[str, str, Any]:
        if self._resolver is None:
            raise jinja2.TemplateNotFound(template)  # 无绑定实例 → 无解析基准
        try:
            source = self._resolver(template)
        except (OSError, ValueError) as exc:
            raise jinja2.TemplateNotFound(template) from exc
        # uptodate 恒 True：框架每次求值新建 Environment，Jinja2 内部模板
        # 编译缓存不跨调用存在，不构成框架级结果缓存
        return source, template, lambda: True


def _make_include_resolver(runtime: Any, source_dir: Any) -> Any:
    """构造 include 名 → 文件文本的解析闭包（与 FILE_REF 同一 resolve_path
    与 source_dir 基准，P3-08）。内部 API。"""

    def _resolve(name: str) -> str:
        path = runtime.resolve_path(name, source_dir=source_dir)
        return Path(path).read_text(encoding="utf-8")

    return _resolve


def _build_jinja_env(resolver: Any) -> jinja2.Environment:
    """按是否具备 include 解析基准构建渲染 Environment。内部 API。

    框架不注册任何模板全局函数 / 包（定稿裁决：无 ``now()`` / ``datetime``
    等）；autoescape 关闭（prompt 文本场景，非 HTML）。undefined 取
    ``ChainableUndefined``——「未定义变量渲染为空」的规约对链式访问
    （``config.limits.turns`` 这类）同样成立（默认 ``Undefined`` 的链式
    取值会抛 UndefinedError，与规约的「渲染为空」不符）。
    """
    return _FlowingEnvironment(
        loader=_ResolvePathLoader(resolver),
        autoescape=False,
        undefined=jinja2.ChainableUndefined,
    )


class _SelfToAgentTransformer(jinja2.visitor.NodeTransformer):
    """把模板 AST 中的名字 ``self`` 改写为 ``agent``。内部 API。

    动机：Jinja2 代码生成器把根作用域中未声明的 ``self`` 名字劫持为
    ``TemplateReference``（服务 ``{% block %}`` 模板继承机制），导致渲染
    上下文里注入的 ``self`` 变量不可达；而框架渲染上下文契约要求
    ``self`` 是 Agent 实例别名（与 ``agent`` 同值，
    ``{{ self.my_method() }}`` 必须可用）。模板继承不在契约内，改写无
    副作用；AST 级改写不触碰字符串字面量中的 ``self`` 文本。
    """

    def visit_Name(self, node: Any) -> Any:
        if node.name == "self":
            return jinja2.nodes.Name("agent", node.ctx, lineno=node.lineno)
        return node


class _FlowingEnvironment(jinja2.Environment):
    """框架渲染环境：套用 ``self`` → ``agent`` 的 AST 改写。内部 API。"""

    def _generate(self, source: Any, name: Any = None, filename: Any = None, defer_init: bool = False) -> Any:
        # _generate 是 Jinja2 文档化的代码生成挂钩点；from_string 与
        # compile_expression 两条路径都经此，改写一处覆盖全部
        source = _SelfToAgentTransformer().visit(source)
        return super()._generate(source, name, filename, defer_init)
