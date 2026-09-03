"""``flowing.parsable`` —— Parsable 统一抽象、惰性求值与双哨兵。

.. rubric:: 功能介绍

本模块是 ``.fya`` 文件层与运行时对象模型之间的桥梁：``.fya`` 中所有
「可解析字段」（``system_prompt``、``description`` 及各类具名块的内容）
在解析后统一表示为 :class:`Parsable`，运行时在使用方决定的时机现场求值。
本模块同时承载两个模块级单例哨兵：:data:`PENDING` （延迟定义承诺）与
``_UNSET`` （未设置）。

本模块只定义「存储求值指令 + 现场求值」的机制，不定义任何使用方的求值
策略（何时求值、用什么上下文）——求值时机由各使用方各自决定，见下文
「各使用方求值时机」表。

.. rubric:: 五种形式判定

``Parsable`` 的 ``type`` 按 ``source`` 字符串样式自动推断，不允许手动
指定。判定按下表自上而下进行，首个命中者生效：

.. list-table:: 五种形式判定
   :header-rows: 1

   * - type
     - source 样式
     - resolve 结果类型
     - 是否进入两步渲染
   * - ``RAW``
     - ``$"..."`` （从首个 ``$"`` 到最后一个 ``"`` 贪婪匹配，内部可含
       引号与 ``{{ }}``）
     - ``str``
     - 否（直接取引号内文本）
   * - ``FILE_REF``
     - 以 ``$`` 开头的路径（非 ``$"`` 形式），如 ``$./prompt.md``、
       ``$@/shared/x.md``
     - ``str``
     - 是
   * - ``EXPRESSION``
     - ``strip(source)`` 恰好是一个完整的 ``{{ expr }}``，无任何其它
       内容
     - 表达式原生类型（不转字符串）
     - 是
   * - ``TEMPLATE``
     - 含 ``{{ }}`` / ``{% %}`` 的混合文本
     - ``str``
     - 是
   * - ``LITERAL``
     - 以上皆非（含非字符串值 ``123`` / ``true``）
     - ``source`` 原样
     - 否（``resolve`` 直接返回 ``source``）

``.fya`` 解析层的补充规则（详见 :mod:`flowing.parser`）：

- 非字符串 YAML 值默认不构造 Parsable（``max_turns: 10`` 直接按 YAML
  类型解析为 ``int``）；例外：字段被宿主显式声明为 Parsable 承载时，
  非字符串 YAML 值也包装为 ``LITERAL`` Parsable——保证声明为 Parsable
  的字段拿到的永远是 Parsable 实例而非裸值。除此之外，``LITERAL`` 的
  非字符串 ``source`` 只会出现在 Python API 层手动 ``Parsable(123)``
  的场景。
- 延迟定义不构造 Parsable：``.fya`` 中的 ``field: _`` 在解析阶段直接
  映射为模块级哨兵 :data:`PENDING` （见 :data:`PENDING`）；``$"_"`` 是
  ``RAW`` 形式，结果为普通字符串 ``"_"``，不触发 PENDING 语义。位置
  分流（字段位 / 覆写位 / 资源列表项位）见 :data:`PENDING` 的 docstring。
- YAML 引号约束：双花括号表达式在 ``.fya`` 中必须写在引号内
  （``max_turns: "{{ config.limits.turns }}"``）——``{`` 是 YAML flow
  mapping 起始符，不加引号会在 YAML 层直接报错。多行内容可改用 YAML
  块标量（``system_prompt: |`` 起）规避引号。

.. rubric:: 两步渲染

``FILE_REF`` / ``EXPRESSION`` / ``TEMPLATE`` 三种形式走统一的两步渲染，
顺序固定、不可配置、两步分开执行无交错：

1. ``$`` 引用展开：``FILE_REF`` 在此步现场读取被引用文件内容（仅顶层、
   不递归）。
2. 模板渲染（Jinja2）：``EXPRESSION`` 形式返回表达式原生值；其余形式
   返回 ``str``；``{% include %}`` 在此步解析。

``RAW`` 与 ``LITERAL`` 是两步渲染的例外：``RAW`` 跳过全部渲染直接取
引号内文本；``LITERAL`` 由 ``resolve`` 直接返回 ``source`` 原样。

模板继承（``{% extends %}`` / ``{% block %}``）暂不支持：不在上面枚举
内的 Jinja2 语法属契约外行为——引擎是完整 Jinja2、写了可能渲染出结果，
但框架不保证其语义，未来版本可能改变或收紧，不要在 ``.fya`` 中依赖。
跨文件组合只有 ``$`` 引用与 ``{% include %}`` 两个契约机制。

``$`` 引用与 ``{% include %}`` 是两种不同阶段的跨文件组合机制：

.. list-table::
   :header-rows: 1

   * -
     - ``$`` 引用
     - ``{% include %}``
   * - 执行阶段
     - 第一步：``$`` 展开
     - 第二步：Jinja2 渲染
   * - 使用位置
     - 仅 Parsable 顶层
     - 模板内部任意位置
   * - 可搭配
     - 不与其它 Jinja2 语法混用
     - 可与 ``{% if %}`` 等控制结构组合
   * - 路径前缀
     - ``./``、``../``、``@/``、多级 ``../../``
     - 与 ``$`` 相同的前缀规则

路径解析是字符串前缀判断，不引入专门的路径类型：``./`` = 当前文件所在
目录，``../`` = 上级目录，``@/`` = 项目根，多级 ``../../`` 按层级向上。
前缀集合的唯一权威表是 :data:`flowing.paths.PATH_PREFIXES`；实际解析
委托 ``Runtime.resolve_path(path, *, source_dir)``，``source_dir`` 基准
由 ``Agent.source_dir`` 统一供给。裸名（如 ``payment``）不是路径形态，
但在资源查找口径下可以命中当前目录。判定规则：
``ToolRegistry.get`` 在 ``source_dir`` 可用时先走文件链，当前目录下的
同名文件覆盖 ``default::`` / ``builtin::`` 注册项；``source_dir``
缺省时只查注册表。形态判别见 :func:`flowing.paths.classify_ref`。

两条边界约定：

- ``{% include %}`` 的路径基准与 ``$`` 引用同源：经同一个
  ``resolve_path``、同一个 ``source_dir`` （绑定实例的
  ``Agent.source_dir``）。内联模板（无文件身份的 ``source`` 字符串）中
  的 include 亦然——不存在「相对于包含者文件」的解析，基准永远来自
  绑定实例。
- ``FILE_REF`` 先剥 ``$`` 再进 ``resolve_path``：``source`` 的首字符
  ``$`` 只是形式标记、不是路径成分；``resolve_path`` 永远看不到 ``$``
  前缀（否则 ``$./x.md`` 会被拼成 ``base/"$./x.md"``）。

.. rubric:: 渲染上下文

第二步渲染的上下文变量：

.. list-table::
   :header-rows: 1

   * - 变量
     - 内容
     - 可用范围
   * - ``env``
     - ``os.environ`` 的只读映射视图
     - 所有 ``.fya``
   * - ``config``
     - 项目配置合并视图（优先级链合并结果）
     - 所有 ``.fya``
   * - ``agent`` / ``self``
     - Agent 实例自身（经 ``getattr`` 可达类属性、描述符、方法——
       ``{{ agent.system_prompt }}``、``{{ self.my_method() }}`` 均合法；
       两名字同值，按书写习惯自选）
     - Agent ``.fya``
   * - 摊平属性
     - Agent 实例属性逐一提升为顶层变量（不写 ``self.`` 前缀；不含
       类属性与方法——它们经 ``agent.`` / ``self.`` 前缀访问）
     - Agent ``.fya``
   * - ``_extra``
     - ``_extra`` 原值
     - Agent ``.fya``

- ``env`` / ``config`` / ``agent`` / ``self`` 是保留名：Agent 实例属性
  禁止同名，框架检测到抛 ``ReservedAttributeError``——否则摊平会覆盖
  框架注入的对应值。
- 非 Agent 的 ``.fya`` （Tool / Skill 定义的 content）渲染上下文中可能
  没有 Agent 实例；可用字段由使用方（扩展或核心模块）在调用求值时
  决定。
- 框架不注册任何模板全局函数 / 包（无 ``now()``、无 ``datetime``）。
  模板中调用函数的唯一通道是本对象的方法（``{{ self.my_method() }}``
  / ``{{ agent.my_method() }}``，经上表 ``agent`` / ``self`` 入口）。
- 求值时取最新值：``env`` 与 ``config`` 在每次求值时现场读取，不在
  解析阶段固化。

.. rubric:: 惰性求值

``Parsable`` 存储的是「如何解析」的指令，不是解析结果。真正求值发生在
实际使用时——这是功能正确性前提而非性能优化：若在文件解析阶段即求值，
会出现三类正确性问题：

- 环境变量：``{{ env.API_KEY }}`` 在文件解析时可能尚未加载。
- 动态属性：``{{ current_mode }}`` 在 Agent 构造前没有实例可取值；模式
  切换后已求值的结果也不会反映新模式。
- 模板引用：``$./system-prompt.md`` 在解析阶段展开时，被引用文件可能
  尚未就绪；且文件修改后应自动反映新内容。

因此 ``FILE_REF`` 的文件读取、``EXPRESSION`` / ``TEMPLATE`` 的 Jinja2
执行都发生在求值时；框架不做任何本地缓存，每次求值现场执行。

.. rubric:: 使用示例

手写 Agent 子类声明 + ``setup()`` 中创建：

.. code-block:: python

    from flowing import Agent, Parsable

    class OrderAgent(Agent):
        system_prompt = Parsable("$./system-prompt.md")    # FILE_REF
        max_turns = Parsable("{{ config.limits.turns }}")  # EXPRESSION → int

        async def setup(self, user_id: int):
            self.user_id = user_id
            greeting = self.parsable("你好 {{ user_id }}")  # TEMPLATE
            text = greeting.resolved   # 已绑定，现场求值
            # str(greeting) 只展示模板原文，不求值

``.fya`` 声明式入口（五种形式）与模板内 ``{{ x.resolved }}`` 引用见
:class:`Parsable` 类 docstring。

.. rubric:: 各使用方求值时机

.. list-table::
   :header-rows: 1

   * - 使用方
     - 求值时机
   * - ``PromptBlock.content`` （含 ``system_prompt`` 惰性引用块）
     - 上下文组装（``Agent._assemble_context()``）每次调用时现场渲染，
       每轮 ``provider_gen`` 重新求值，天然反映最新实例属性
   * - ``model_tag`` （若为模板）
     - 每轮 ``provider_gen`` 前求值
   * - ``ToolEntry.specified`` 与子 Agent 的 ``specified`` 参数
     - ``tool_call()`` / ``invoke_subagent()`` 内，调用方 Agent 实例
       上下文，每次调用现场求值
   * - ``SubagentEntry.override_system_prompt``
     - 子 Agent 创建时
   * - Tool / Skill / Subagent 的 description 覆盖
     - ``ToolEntry.llm_definition()`` / catalog 渲染 / 亲代 Agent 路由
       决策时
   * - ``_extra`` 中的任意字段
     - 框架不自动 resolve——由声明该字段的插件在 ``use_xxx()`` 中接管

.. rubric:: 插件自定义可解析字段

``.fya`` 解析时默认构造为 ``Parsable`` 的字段集合由框架核心固定（即上表
「求值面内」字段，外加宿主显式声明 Parsable 承载的字段）。除此之外的
可解析对象由插件 / 组件自行定义：

- 插件自行决定自己的哪些字段是可解析的，并以对开发者的约定形式写进
  插件文档（「本插件的 X 字段是 Parsable」是插件契约的一部分，框架不
  提供通用注册表）。
- 机制：在 ``use_xxx()`` 启用钩子里，插件把对应变量重赋值为「由该变量
  的 ``.fya`` 解析结果创建的 :class:`Parsable` 对象」——框架解析层不为
  插件字段自动包装，包装动作显式发生在启用时。
- 求值同样由插件负责：重赋值后的 Parsable 由插件在需要时手动
  ``resolve(context)`` （见下「求值面内 vs 求值面外」）。

.. rubric:: 求值面内 vs 求值面外

示意图（无行列语义）：

.. code-block:: text

    求值面内（框架自动 resolve）：
      Agent._assemble_context() 的 PromptBlock.content
      ToolEntry.resolve()
      SubagentEntry.resolve()
      → 用户无感，拿到字符串 / 表达式解析值

    求值面外（用户 / 扩展显式 resolve）：
      agent._extra["greeting"]
      插件自定义字段（use_xxx() 中重赋值为 Parsable 的那些）
      → 拿到的是 Parsable 对象本身
      → 需要时手动 .resolve(context)

框架不为 Parsable 做隐式解包（类比 Vue 的 ref 但刻意不自动拆箱）：
隐式解包会掩盖求值开销，且求值上下文未必总是 ``self``。判断标准：字段
是否在上表「求值面内」；面外字段拿到的永远是 :class:`Parsable` 对象。

.. rubric:: 行为要点

- 不缓存求值结果、不缓存文件内容、不缓存渲染输出；每次 resolve 现场
  执行。
- 不递归展开被引用文件中的 ``$path``。
- 不提供响应式重算：``watch(name, handler)`` 只监听属性赋值事件，不
  监听解析值的逻辑变化（``self.c = Parsable("{{ a == b }}")`` 之后修改
  ``self.a``，``watch("c")`` 不触发——因为 ``self.c`` 从未被重新赋值）。
  要「a 变导致 c 重算」，把 ``c`` 放进某个求值面让框架每次自动 resolve，
  或自己 ``watch("a")`` / ``watch("b")`` 在 handler 中给 ``self.c``
  直接赋值。
- 不监听运行期文件变更（无文件系统 watcher）：``FILE_REF`` 的「文件
  修改后下次求值反映新内容」仅指进程内再次求值。
- 双哨兵不混用：:data:`PENDING` （延迟定义承诺，``.fya`` 中
  ``field: _`` 的解析结果）与 ``_UNSET`` （未设置，现仅用于
  ``Agent.source_file`` 的自动推算触发条件）语义不同、不可混用。
  ``PENDING`` 等待的是用户代码赋值；``_UNSET`` 表示「该位置从无默认 /
  显式值」。创建管线 ``setup()`` 返回后、``after_create`` 前检查仍为
  ``PENDING`` 的字段，抛 ``MissingFieldError``。
- 稳定性分级：本模块全部公开签名（:class:`Parsable` 及其公开方法、
  五个形式常量、:data:`PENDING`）属于跨版本稳定契约；``_`` 前缀符号为
  内部 API，不属稳定契约。

.. seealso::

    :class:`flowing.agent.Agent`
        Parsable 的主要宿主；``Agent.parsable()`` 工厂方法与
        ``Agent.watch()`` 响应式边界。
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

``source`` 为普通字面量（``"hello"``、``123``、``true`` 等，且不命中
``RAW`` / ``FILE_REF`` / ``EXPRESSION`` / ``TEMPLATE`` 判定）时的
``Parsable.type`` 取值；``resolve()`` 直接返回 ``source`` 原样，不进入
两步渲染。

.. rubric:: 行为要点

- 值为模块级常量，比较必须用 ``is`` 或与 ``Parsable.type`` 相等性比较，
  不依赖具体字面值。
- ``.fya`` 解析层不会为非字符串 YAML 值构造 Parsable（例外：字段被宿主
  声明为 Parsable 承载时，非字符串值也包装为 ``LITERAL``，见模块
  docstring）；``LITERAL`` 的非字符串 ``source`` 其余只会出现在 Python
  API 层手动构造的场景（如 ``Parsable(123)``）。
- 不做任何 Jinja2 解析：若字面量文本恰好含 ``{{ }}``，判定会先命中
  ``TEMPLATE``；要保留原始文本须使用 ``$"..."`` （``RAW``）。

.. seealso:: :class:`Parsable`、:data:`RAW`、:data:`TEMPLATE`
"""

FILE_REF: Final[str] = "FILE_REF"
"""``$`` 文件引用形式标记。

``source`` 以 ``$`` 开头（且非 ``$"..."`` 形式）时的 ``Parsable.type``
取值，如 ``$./system-prompt.md``、``$@/shared/base.md``；``resolve()``
时现场读取被引用文件内容并进入第二步 Jinja2 渲染。

.. rubric:: 行为要点

- ``$`` 展开只发生在 Parsable 顶层，不递归：被引用文件中的 ``$path``
  作为普通文本进入第二步。
- 文件读取发生在求值时而非解析时；文件修改后下次求值自动反映新内容
  （进程内语义，无文件系统监听）。
- 路径前缀（``./``、``../``、``@/``、多级 ``../../``）经
  ``Runtime.resolve_path(path, *, source_dir)`` 解析；``source_dir`` 由
  ``Agent.source_dir`` 统一供给。先剥 ``$`` 再进 ``resolve_path``：
  ``$`` 只是形式标记而非路径成分。
- 边缘情况：想让结果字符串本身以 ``$`` 开头（如 ``"$200.00"``），必须
  写 ``$"$200.00"`` （``RAW`` 形式），否则会被当作文件引用。

.. seealso:: :class:`Parsable`、:data:`RAW`、
    ``flowing.runtime.Runtime.resolve_path``
"""

EXPRESSION: Final[str] = "EXPRESSION"
"""纯表达式形式标记。

``strip(source)`` 恰好是一个完整的 ``{{ expr }}`` （无任何其它内容）时的
``Parsable.type`` 取值；``resolve()`` 返回表达式的原生类型值（``int`` /
``bool`` / ``dict`` 等），不做字符串化——这是 Parsable 结果类型「不一定
是 ``str``」的唯一来源。

.. rubric:: 行为要点

- 判定式：``strip(source)`` 以 ``{{`` 开头、``}}`` 结尾，且内部恰好一个
  完整表达式（无嵌套未配对的 ``{{`` / ``}}``、无首尾其它字符）。
- 混合文本（``"Hello {{ user_name }}, welcome!"``）与 ``{% if %}`` 控制
  的值均不是纯表达式，落入 ``TEMPLATE``，结果为 ``str``。
- ``.fya`` 中双花括号必须写在 YAML 引号内，否则 YAML 解析先行报错。
- 结果收尾（一层）：表达式求值结果是 :class:`Parsable` 时，经其
  ``.resolved`` 渲染一层后返回（类属性经描述符 ``__get__`` 已绑定实例，
  内层模板随之渲染）。这保证 ``{{ self.system_prompt }}`` 这类「引用另
  一个 Parsable」的惰性引用拿到的是渲染后文本而非 Parsable 对象；递归
  深度仍只有一层。注意 ``__str__`` 只展示模板源（不求值），本收尾必须
  用 ``.resolved`` 而非 ``str()``。

.. seealso:: :class:`Parsable`、:data:`TEMPLATE`、:attr:`Parsable.resolved`
"""

TEMPLATE: Final[str] = "TEMPLATE"
"""模板形式标记。

``source`` 含 Jinja2 语法（``{{ }}`` 插值 / ``{% %}`` 控制结构）但不
满足纯表达式判定时的 ``Parsable.type`` 取值；``resolve()`` 返回 ``str``。

.. rubric:: 行为要点

- 走完整两步渲染；``{% include %}`` 可在模板内任意位置组合外部文件，
  路径前缀规则与 ``$`` 引用相同，解析基准也同源（经框架自定义 Jinja2
  加载器 → ``Runtime.resolve_path``，基准为绑定实例的
  ``Agent.source_dir``）。
- 结果恒为 ``str``；需要原生类型时改写为纯表达式（``EXPRESSION``）。
- 模板引用了不存在的上下文变量时按 Jinja2 默认行为处理：未定义变量
  渲染为空（含 ``config.limits.turns`` 这类链式访问），框架不额外做
  严格模式校验。
- 模板继承（``{% extends %}`` / ``{% block %}``）暂不支持——不在契约
  枚举内，写了可能碰巧渲染但语义不保证；跨文件组合请用 ``$`` 引用或
  ``{% include %}``。

.. rubric:: 混合内容中的 Parsable 引用

模板是混合内容——字面文本、控制结构与插值共存。插值位上的
:class:`Parsable` 对象不隐式渲染（``__str__`` 只展示模板源）：要它的
渲染结果，写 ``{{ x.resolved }}``；写 ``{{ x }}`` 得到的是它未渲染的
模板原文。

一个典型场景——system prompt 引用了共享的规则片段文件：

.. code-block:: jinja

    {# system-prompt.md；refund_policy 是 setup 中挂上的 Parsable #}
    你是一个订单处理助手。
    当前用户：{{ user_id }}
    {{ refund_policy.resolved }}

这里 ``user_id`` 是普通实例属性（摊平上下文），直接插值；
``refund_policy`` 是 Parsable——``.resolved`` 明确表示「此处发生一次
求值」，不写则渲染出 ``$./refund-rules.md`` 这个模板原文。被引用片段
内部若再引用 Parsable，同样各写各的 ``.resolved``——嵌套渲染的每一步
都是显式的，不存在无限递归通道。

.. seealso:: :class:`Parsable`、:data:`EXPRESSION`、:data:`FILE_REF`
"""

RAW: Final[str] = "RAW"
"""原始字面量形式标记（``$"..."``）。

``source`` 形如 ``$"..."`` 时的 ``Parsable.type`` 取值；从首个 ``$"``
到最后一个 ``"`` 贪婪匹配，内部可含双引号与 ``{{ }}`` 等 Jinja2 语法
字符而无需转义。``resolve()`` 跳过全部两步渲染，直接返回引号内文本
（恒为 ``str``）。

.. rubric:: 行为要点

- 边缘情况一：``$"含有 "引号" 的原始文本"`` 的值为
  ``含有 "引号" 的原始文本`` （贪婪匹配到最后一个引号）。
- 边缘情况二：``$"_"`` 的值是普通字符串 ``"_"``，不触发 PENDING 语义。
- 边缘情况三：``$"$200.00"`` 的值是字符串 ``$200.00``，不会被当作文件
  引用。
- 不做任何渲染、不读文件、不访问渲染上下文；短路发生在绑定检查之前，
  未绑定也能 ``resolve``、不抛 ``MissingContextError``。

.. seealso:: :class:`Parsable`、:data:`PENDING`、:data:`LITERAL`
"""

# ---------------------------------------------------------------------------
# 哨兵
# ---------------------------------------------------------------------------

class _MissingType:
    """``PENDING`` 的单例类型。内部 API，不属稳定契约。

    保证 ``PENDING`` 是进程内唯一实例，支持 ``is`` 精确判断。外部代码只应
    使用 :data:`PENDING` 本身，不应实例化或子类化本类型。不提供布尔语义
    保证——判定「是否未兑现承诺」唯一合法方式是 ``field is PENDING``；
    ``__repr__`` 返回字符串 ``"PENDING"``，便于调试输出与错误信息。
    """

    _instance: ClassVar["_MissingType | None"] = None

    def __new__(cls) -> "_MissingType":
        """返回进程内唯一实例。内部 API。
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance  # type: ignore[return-value]  # 收窄 Optional
    def __repr__(self) -> str:
        """返回 ``"PENDING"``。内部 API。
        """
        return "PENDING"

PENDING: Final[_MissingType] = _MissingType()
"""延迟定义哨兵——``.fya`` 中 ``field: _`` 的解析结果。

.. rubric:: 功能介绍

``_`` 在解析阶段不映射为 ``None`` 或空字符串，而是映射为本模块级单例
哨兵，表示「我承诺稍后（``setup()`` 或具名块中）赋值」。独立哨兵使
「承诺了但没兑现」能被精确检测——``None`` / ``""`` / ``[]`` 都是合法的
显式值（``field: null`` 对可选字段表示「确实无值」），不能兼作「未赋值」
标记。

.. rubric:: 使用示例

.. code-block:: yaml

    # agent.fya —— _ 作为延迟定义
    description: _
    system_prompt: _

.. code-block:: python

    # setup() 中兑现
    if self.description is PENDING:
        ...   # 尚未兑现
    self.description = self.parsable("处理订单查询")

.. rubric:: 行为要点

- 写法映射：``field: _`` → ``PENDING``；字段缺失或 ``field: null`` →
  可选字段 ``None``、必填字段视同 ``_`` （``PENDING``）；``field: $"_"``
  → 字符串 ``"_"`` （RAW 形式，不触发 PENDING）。
- 位置分流：字段位 ``_`` = 「必须兑现的承诺」（创建管线检查点）；覆写位
  （entry 覆写 / ``args`` 覆写）``_`` = 空补丁（装配层解析为空、可从
  基底回填，不报错）；资源列表项位禁止 ``_``
  （``flowing.parser.normalize_entries`` 抛 ``FormatError``）。
- ``PENDING`` / ``None`` / ``""`` / ``[]`` 四者用 ``is`` 可精确区分；
  不提供布尔语义保证——禁止用真值判断代替 ``is`` 判断，``if field:``
  的结果不作契约。
- 检查时机：不在解析时（解析只标记），而在创建管线 ``setup()`` 返回后、
  ``after_create`` 前——此时 ``before_create`` 钩子与 Composable 已有
  充分赋值机会，而 ``after_create`` 观察者能保证看到完整字段。字段位
  检查点抛 :class:`flowing.errors.MissingFieldError` （覆写位 PENDING 无
  检查点——空补丁语义）。
- 替换语义：多层具名块（如 ``$subagents.pay.system_prompt:``）赋值时，
  源字段为 ``PENDING`` → 合法替换；源字段已有实际值 → 报冲突。即
  ``_`` 不是「忽略」，而是「该位置的 PENDING 可被具名块替换」的合法
  前提。
- 不变量：进程内只有一个 ``PENDING`` 实例；可安全跨模块 ``is`` 比较。

.. seealso::

    :data:`_UNSET`
        语义不同的另一个哨兵（「未设置」），不可混用。
    ``flowing.runtime.Runtime.create_agent``
        PENDING 检查点所在的创建管线。
"""

_UNSET: Final[Any] = object()
"""「未设置」哨兵。内部 API，不属稳定契约。

表示「该位置从无默认 / 显式值」，现用于 ``Agent.source_file`` 的自动推算
触发（``source_file is _UNSET`` 时才推算，用户显式写 ``None`` 表示禁用
相对路径）。与 :data:`PENDING` 语义不同：``PENDING`` 等待用户代码赋值，
``_UNSET`` 是静态判定标记，不可混用。唯一合法判定方式是 ``is`` /
``is not``；不提供布尔语义保证；进程内唯一实例。

.. seealso:: :data:`PENDING`
"""

# ---------------------------------------------------------------------------
# Parsable
# ---------------------------------------------------------------------------

class Parsable(Generic[T]):
    """五种可解析赋值形式的统一抽象——存储求值指令，惰性现场求值。

    .. rubric:: 功能介绍

    ``.fya`` 中所有「可解析字段」（``system_prompt``、``description`` 及
    各类具名块内容）与手写子类中的等价声明，统一表示为 ``Parsable[T]``。
    ``Parsable`` 不是固定数据类型：``type`` 由 ``source`` 样式自动推断
    （``LITERAL`` / ``FILE_REF`` / ``EXPRESSION`` / ``TEMPLATE`` / ``RAW``），
    渲染结果的类型由形式决定（``EXPRESSION`` 返回表达式原生类型，其余返回
    ``str`` 或 ``source`` 原样）。它是描述符，存放在 Agent 类属性上；实例
    访问时自动绑定实例，无参 :meth:`resolve` 即以绑定实例为渲染上下文。

    惰性求值是功能正确性前提而非性能优化：文件解析阶段求值会在环境变量未
    加载、Agent 实例尚不存在、被引用文件未就绪三类场景下产生错误结果。
    统一抽象使 ``.fya`` 五种写法与 Python 手写声明走同一条渲染管线，
    「用到的时候重新算」取代了响应式系统（无 Proxy、无依赖图、无缓存）。

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

    手写子类与 Python API 侧用法见模块 docstring「使用示例」。

    .. rubric:: 行为要点

    - ``type`` 自动推断，构造时不接受手动指定；推断规则见模块级
      「五种形式判定」。
    - 描述符行为：类级别访问返回自身（未绑定）；实例访问返回一个绑定了
      实例的浅拷贝（共享 ``source`` / ``type``，拷贝间互不影响）。本类只
      实现 ``__get__``，是非数据描述符——实例属性赋值（``self.x = ...``）
      正常遮蔽类级 Parsable，不会触发描述符协议。
    - 求值上下文：见 :meth:`resolve`；渲染上下文构成见模块级「渲染上下文」。
    - 不变量：``source`` 与 ``type`` 创建后不变；求值不改变 Parsable 自身
      状态（无缓存、无记忆）。
    - 非行为：不做隐式解包（求值面外拿到的永远是 Parsable 对象）；不缓存
      任何求值结果；不递归展开被引用文件中的 ``$path``。
    - 边缘情况：构造 ``Parsable(123)`` 等非字符串 ``source`` 合法，推断为
      ``LITERAL``；构造时 ``source`` 为 :data:`PENDING` 属调用方错误
      （PENDING 不应被包进 Parsable，两者是解析层的两种不同产物）。

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

        创建存储求值指令的 Parsable 对象；构造是同步、无副作用的——不读文件、
        不渲染、不访问任何运行时状态。构造（发生在 ``.fya`` 解析或类定义时）
        只做形式推断，所有昂贵或依赖运行时的操作推迟到 :meth:`resolve`。

        .. rubric:: 使用示例

        .. code-block:: python

            Parsable("你好 {{ user_id }}")         # TEMPLATE
            Parsable("{{ config.limits.turns }}")  # EXPRESSION
            Parsable("$./prompt.md")               # FILE_REF
            Parsable('$"原始 {{ 文本 }}"')          # RAW
            Parsable("普通文本")                    # LITERAL
            Parsable(123)                          # LITERAL（非字符串）

        :param source: 求值指令（模板文本 / ``$`` 路径 / 表达式 / 字面量）。
            不应为 :data:`PENDING`——``_`` 在 ``.fya`` 解析层直接映射为 PENDING
            哨兵，不构造 Parsable。

        .. rubric:: 行为要点

        - 按模块级「五种形式判定」自上而下推断 ``type``。
        - 构造产物未绑定（``_instance is None``），需经
          ``flowing.agent.Agent.parsable`` 或描述符协议绑定后才能无参
          :meth:`resolve`。
        - 不校验 ``$`` 路径存在性、不做 Jinja2 语法预检——错误在求值时暴露。

        .. seealso:: :meth:`resolve`
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
        返回原对象（未绑定）；通过实例访问（``agent.p``）返回一个绑定了该实例
        的浅拷贝（共享 ``source`` / ``type``）。浅拷贝绑定使「类级声明共享、
        实例级上下文独立」：``source`` / ``type`` 只有一份（声明是类级共享的），
        而求值上下文按访问的实例区分。

        .. rubric:: 使用示例

        .. code-block:: python

            class A(Agent):
                p = Parsable("{{ locale }}")

            A.p            # Parsable 自身，未绑定
            a.p            # 浅拷贝，已绑定 a
            a.p.resolve()  # 默认上下文 = a

        .. rubric:: 行为要点

        - 实例访问每次返回新的浅拷贝：``a.p is not a.p``，但
          ``a.p.source is A.p.source``。
        - 本类不实现 ``__set__`` / ``__delete__``，是非数据描述符：
          ``self.p = ...`` 走普通实例属性赋值，遮蔽类级声明，同时触发
          ``watch("p")`` （赋值事件）。
        - 不在 ``__get__`` 中触发任何求值——绑定不等于 resolve。

        .. seealso:: :meth:`resolve`、:meth:`flowing.agent.Agent.parsable`、
            :meth:`flowing.agent.Agent.watch`
        """
        if instance is None:
            # 类级别访问：返回自身（未绑定）
            return self
        # 实例访问：返回绑定浅拷贝（共享 source/type）：copy.copy 浅拷贝，
        # 不重跑 __init__/_infer_type，仅重设 _instance
        import copy   # 模块级 import 冻结，函数内局部引入（同 tool.py 先例）
        bound: "Parsable[T]" = copy.copy(self)
        bound._instance = instance
        return bound

    def resolve(self, context: "Agent | Mapping[str, Any] | None" = None) -> T:
        """惰性求值：执行（可能的）两步渲染并返回结果。

        .. rubric:: 功能介绍

        对 ``source`` 按 ``type`` 求值：``RAW`` 直接返回引号内文本；``LITERAL``
        返回 ``source`` 原样；``FILE_REF`` 现场读文件后渲染；``EXPRESSION`` 返回
        表达式原生值；``TEMPLATE`` 返回渲染后的 ``str``。同步方法——求值可能
        涉及文件读取，但均为本地小文件，不引入异步。「用到的时候重新算」是
        框架取代响应式系统的核心机制：每次调用现场求值（无缓存）保证拿到的
        永远是最新的 ``env`` / ``config`` / 实例属性 / 被引用文件内容。

        .. rubric:: 使用示例

        .. code-block:: python

            # 已绑定（描述符访问或 self.parsable() 创建）
            self.greeting.resolve()          # 默认上下文 = 绑定实例
            self.greeting.resolve(other)     # 显式覆盖上下文为另一个 Agent

        :param context: 渲染上下文。为 ``None`` 时使用绑定实例；为
            :class:`flowing.agent.Agent` 时以其为上下文；为 ``Mapping`` 时以其
            内容为基底（见行为要点）。
        :return: 按 ``type`` 求值的结果（各形式的结果类型见 :data:`LITERAL` 等
            五个形式常量的 docstring）。

        .. rubric:: 行为要点

        - ``context=None``：使用绑定实例；未绑定（类级别访问得到的对象或手动
          ``Parsable(...)`` 构造的）→ 抛 ``MissingContextError``。
        - ``context`` 为 Agent：渲染上下文 = 实例属性摊平（``vars(agent)`` 逐键
          提升为顶层变量）+ ``agent`` / ``self`` 入口（实例自身，可经 ``getattr``
          到达类属性、描述符、方法）+ ``env`` （``os.environ`` 只读视图）+
          ``config`` （项目配置合并视图）+ ``_extra`` （原值，其中 Parsable 经
          ``.resolved`` 惰性触发）。``env`` 与 ``config`` 始终从该 Agent 的
          ``runtime`` 现场获取并合并。
        - 保留名约束：Agent 属性命名为 ``env`` / ``config`` / ``agent`` /
          ``self`` 会覆盖框架注入的值，框架检测到抛
          ``ReservedAttributeError``。
        - ``context`` 为 ``Mapping``：以其内容为基底，同样自动注入 ``env``
          （恒为 ``os.environ`` 只读视图）与 ``config`` （取自绑定实例的
          ``runtime``；未绑定时不注入 ``config``，模板引用 ``config.*`` 按
          Jinja2 默认行为渲染为空）。键冲突时 Mapping 中的显式键优先于注入值。
          ``FILE_REF`` 的路径解析以绑定实例的 ``runtime`` 进行；未绑定 +
          ``FILE_REF`` → ``MissingContextError``。
        - 同一次调用内两步渲染按固定顺序执行、无交错；多次调用各自独立现场
          求值（无缓存、无记忆）。
        - 不递归 resolve 结果：``EXPRESSION`` 求值得到 Parsable 时经其
          ``.resolved`` 渲染一层即止（见 :data:`EXPRESSION` 的结果收尾规约）；
          不修改自身状态。
        - 边缘情况：``FILE_REF`` 的被引用文件不存在 → 求值时抛出底层
          ``OSError`` （框架不预检、不兜底）；模板引用未定义变量按 Jinja2 默认
          行为渲染为空。

        :raises flowing.errors.MissingContextError:
            未绑定且未传 ``context`` 时；``FILE_REF`` + ``Mapping`` 上下文 +
            未绑定实例时（路径解析无 runtime 来源）。
        :raises flowing.errors.ReservedAttributeError:
            渲染上下文摊平检测到保留名 ``env`` / ``config`` 被实例属性占用时。

        .. seealso::

            :attr:`resolved`
                模板内嵌套引用的等价触发入口。
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
            # Mapping 分支：以其内容为基底并始终注入 env/config——
            # 由 resolve() 本体承载（_do_resolve 只服务 Agent 分支；
            # Mapping 只服务测试与手动求值）
            ctx: dict[str, Any] = dict(context)
            ctx.setdefault("env", MappingProxyType(os.environ))  # os.environ 只读视图
            if self._instance is not None:
                ctx.setdefault("config", self._instance.runtime.config)
                # {% include %} 基准永远来自绑定实例：绑定实例存在时，
                # Mapping 分支的 include 与 $ 引用同源于 resolve_path
                ctx["__include_resolver__"] = _make_include_resolver(
                    self._instance.runtime, self._instance.source_dir
                )
            # 未绑定时不注入 config，模板引用 config.* 按 Jinja2 默认渲染为空
            if self.type is FILE_REF:
                # FILE_REF 需 runtime 做路径解析，Mapping 路径下唯一来源是
                # 绑定实例；未绑定 -> MissingContextError
                if self._instance is None:
                    raise MissingContextError()
                resolved_path = self._instance.runtime.resolve_path(
                    self.source[1:],   # 先剥 $ 形式标记再进 resolve_path
                    source_dir=self._instance.source_dir,  # 文件→目录换算的唯一承担者是 Agent.source_dir
                )
                ctx["__file_ref_path__"] = resolved_path  # 供 _render 第一步读文件（内部约定键名）
            return self._render(ctx)
        # context 为 Agent：摊平实例属性 + env/config 注入与保留名检测
        # （ReservedAttributeError 检测点在上下文构建中，见 _do_resolve）
        return self._do_resolve(context)

    @property
    def resolved(self) -> T:
        """模板内嵌套引用的现场求值入口——等价于无参 :meth:`resolve`。

        .. rubric:: 功能介绍

        当 Agent 实例上的 Parsable 属性被摊平为模板顶层变量时，模板通过
        ``{{ greeting.resolved }}`` 触发该子 Parsable 的现场求值。外层渲染上下文
        已是当前 Agent，子求值内部拿到同样的完整上下文。

        Jinja2 渲染任意对象时默认调用 ``str()``——对 Parsable 而言 ``str()``
        只展示模板源（不求值，见 :meth:`__str__`）；因此模板内引用 Parsable
        必须写显式的 ``.resolved`` （「这里是一次求值」意图可读，不依赖隐式
        字符串化）。模板中显式 ``{{ x.resolve() }}`` （无参、已绑定）与
        ``.resolved`` 语义等价，但 ``.resolved`` 是惯用写法。模板内完整示例见
        :data:`TEMPLATE` 的「混合内容中的 Parsable 引用」。

        .. rubric:: 行为要点

        - 每次访问现场求值，不缓存：两次 ``.resolved`` 之间修改了被引用的属性，
          第二次访问反映新值。
        - 必须已绑定（``_instance`` 非空），否则与 :meth:`resolve` 无参调用同样
          抛 ``MissingContextError``。
        - 不是响应式订阅——访问它不会注册任何依赖关系。

        :raises flowing.errors.MissingContextError: 未绑定时。

        .. seealso:: :meth:`resolve`、:meth:`__str__`
        """
        return self.resolve()  # 默认上下文 = 绑定实例；未绑定由 resolve 抛 MissingContextError

    def __str__(self) -> str:
        """展示 Parsable 的模板源本身，不求值。

        .. rubric:: 功能介绍

        返回未渲染的 ``source`` 文本（非 ``str`` 类型的 ``source`` ——
        ``LITERAL`` 裸值——经 ``str()`` 转换）。字符串拼接、``str()``、f-string、
        Jinja2 隐式字符串化拿到的是模板原文，不是求值结果。「展示模板」与
        「渲染结果」分离：求值只有一个显式入口（:meth:`resolve` /
        :attr:`resolved`），``str()`` 不暗中触发渲染——Jinja2 模板内引用 Parsable
        必须写 ``{{ x.resolved }}``，隐式字符串化只用于调试与日志。

        .. rubric:: 行为要点

        - 不触发 resolve、不读文件、不访问运行时；未绑定不抛错。
        - ``source`` 含换行 / 引号时原样展示（不转义——转义展示是
          :meth:`__repr__` 的职责）。

        .. seealso:: :meth:`__repr__`、:meth:`resolve`、:attr:`resolved`
        """
        return str(self.source)  # 模板原文，不求值（LITERAL 裸值经 str 转换）

    def __repr__(self) -> str:
        """返回 ``Parsable(source=..., type=...)``，显示原始指令而非求值结果。

        .. rubric:: 功能介绍

        调试表示，始终展示未渲染的 ``source`` 与推断出的 ``type``，无论绑定
        与否。调试 / 日志 / traceback 中需要看到「模板原文」而非「某次求值的
        瞬时结果」；repr 无副作用、无前置条件（未绑定也能 repr）。

        .. rubric:: 行为要点

        - 格式固定为 ``Parsable(source=<repr>, type=<常量名>)``；不触发 resolve、
          不读文件、不访问运行时。
        - ``source`` 含换行 / 引号时按其 ``repr`` 转义展示。

        .. seealso:: :meth:`__str__` （与之对立的求值行为）
        """
        return f"Parsable(source={self.source!r}, type={self.type})"

    # -------------------------------------------------------------------
    # 内部方法（不属稳定契约）
    # -------------------------------------------------------------------

    def _do_resolve(self, agent: "Agent") -> T:
        """构建渲染上下文并执行渲染（Agent 分支）。内部 API，不属稳定契约。

        :meth:`resolve` 的核心实现：把 Agent 实例属性摊平为顶层变量，合并
        ``env`` 与 ``config``，然后委托 :meth:`_render` 执行两步渲染。每次调用
        现场构建上下文（``env`` / ``config`` / 实例属性均取当前值），构建结果
        不跨调用复用；``FILE_REF`` 的路径解析经 ``Runtime.resolve_path`` 完成
        （传入前已剥去 ``$`` 形式标记）。
        """
        # 模板上下文不自动暴露状态量——状态量若需进模板，走
        # env / config / _extra 等既有显式通道
        # 保留名检测（摊平构建前检测 agent.__dict__）：
        # 实例属性占用 env/config/agent/self 会覆盖框架注入值，fail fast
        for reserved in ("env", "config", "agent", "self"):
            if reserved in agent.__dict__:
                raise ReservedAttributeError(reserved)
        ctx: dict[str, Any] = {
            **agent._extra,
            **agent.__dict__,
            "agent": agent,   # 实例自身入口（Jinja 属性解析走 getattr，描述符 __get__ 正常触发）
            "self": agent,     # agent 的别名——模板里 self.xxx 与 agent.xxx 等价，按书写习惯自选
            "env": agent.runtime.env,
            "config": agent.runtime.config,
        }
        # {% include %} 与 $ 引用同源：include 名经同一
        # resolve_path、同一 source_dir 基准（绑定实例的 Agent.source_dir）
        ctx["__include_resolver__"] = _make_include_resolver(agent.runtime, agent.source_dir)
        if self.type is FILE_REF:
            # FILE_REF 路径解析（每次求值）：source_dir 基准由
            # agent.source_dir 属性统一供给（「文件→所在目录」换算的
            # 唯一承担者；source_file is None 时为 None，./ 前缀
            # 由 resolve_path 报错）
            resolved_path = agent.runtime.resolve_path(
                self.source[1:],   # 先剥 $ 形式标记再进 resolve_path
                source_dir=agent.source_dir,
            )
            ctx["__file_ref_path__"] = resolved_path  # 供 _render 第一步读文件（内部约定键名）
        result: T = self._render(ctx)
        return result

    def _render(self, ctx: Mapping[str, Any]) -> T:
        """按固定顺序执行两步渲染。内部 API，不属稳定契约。

        1. ``$`` 引用展开——仅顶层、不递归（``FILE_REF`` 形式在此读文件）。
        2. Jinja2 渲染——``EXPRESSION`` 返回表达式原生值，其余返回 ``str``；
           ``{% include %}`` 在此阶段解析。

        两步分开执行、无交错。``RAW`` 与 ``LITERAL`` 在 :meth:`resolve` 层短路，
        不进入本方法。``{% include %}`` 的落点是框架为渲染 Environment 提供的
        自定义加载器，include 名经与 ``FILE_REF`` 同源的路径解析（同一个
        ``Runtime.resolve_path``、同一个 ``source_dir`` 基准——绑定实例的
        ``Agent.source_dir``）；内联模板无「包含者文件」身份，include 基准一律
        来自绑定实例。
        """
        # 第一步：$ 引用展开——仅 FILE_REF 在此现场读文件（不递归展开被引用
        # 文件中的 $path）；路径解析已在 _do_resolve / resolve 的 Mapping
        # 分支经 Runtime.resolve_path 完成；文件不存在时底层 OSError 直接
        # 抛出（框架不预检、不兜底）
        if self.type is FILE_REF:
            path = ctx["__file_ref_path__"]  # 由 resolve/_do_resolve 解析后放入（内部约定键名）
            expanded: str = Path(path).read_text(encoding="utf-8")  # 现场读取，无缓存
        else:
            expanded = self.source  # 其余形式的 source 文本中不会出现待展开的顶层 $path
        # 第二步：Jinja2 渲染——EXPRESSION 返回表达式原生值，其余返回 str；
        # {% include %} 在此阶段经自定义加载器解析（与 $ 引用同源于
        # resolve_path）。每次求值现场新建 Environment——框架级
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

        实现模块级「五种形式判定」的自上而下顺序：``$"..."`` → ``RAW``；
        ``$`` 开头 → ``FILE_REF``；``strip(source)`` 恰为一个完整 ``{{ expr }}``
        → ``EXPRESSION``；含 Jinja2 语法 → ``TEMPLATE``；其余（含非字符串）→
        ``LITERAL``。纯函数：同输入恒同输出，无副作用。``$"_"`` → ``RAW``；
        ``"_"`` （裸字符串，非哨兵）→ ``LITERAL`` （PENDING 语义只存在于
        ``.fya`` 解析层，不经本函数）。
        """
        # 按模块级「五种形式判定」自上而下判定，首个命中者生效
        if not isinstance(source, str):
            # 非字符串值（123/true 等）：LITERAL（宿主声明 Parsable 承载的
            # 例外与 Python API 手动构造）
            return LITERAL
        if source.startswith('$"') and source.endswith('"'):
            # $"..." 贪婪匹配形式（含 $"_" 与 $"$200.00" 边缘情况）
            return RAW
        if source.startswith("$"):
            # $ 开头的路径（非 $" 形式）：$./x.md、$@/shared/x.md
            return FILE_REF
        stripped: str = source.strip()
        if stripped.startswith("{{") and stripped.endswith("}}"):
            # strip(source) 恰好是一个完整 {{ expr }}：内部不得再出现
            # {{ / }}（即首个 {{ 的配对 }} 恰落在末尾），否则是混合文本
            # （如 "{{ a }} {{ b }}"），落 TEMPLATE
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
    ``Runtime.resolve_path``、同一 ``source_dir`` 基准。内部 API。

    include 名不剥 ``$`` （Jinja2 模板内的 include 语法本就不带 ``$`` 形式
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
    """构造 include 名 → 文件文本的解析闭包（与 FILE_REF 同一 ``resolve_path``
    与 ``source_dir`` 基准）。内部 API。
    """

    def _resolve(name: str) -> str:
        path = runtime.resolve_path(name, source_dir=source_dir)
        return Path(path).read_text(encoding="utf-8")

    return _resolve


def _build_jinja_env(resolver: Any) -> jinja2.Environment:
    """按是否具备 include 解析基准构建渲染 Environment。内部 API。

    框架不注册任何模板全局函数 / 包；autoescape 关闭（prompt 文本场景，
    非 HTML）；undefined 取 ``ChainableUndefined``——「未定义变量渲染为空」
    的规约对链式访问（``config.limits.turns`` 这类）同样成立。
    """
    return _FlowingEnvironment(
        loader=_ResolvePathLoader(resolver),
        autoescape=False,
        undefined=jinja2.ChainableUndefined,
    )


class _SelfToAgentTransformer(jinja2.visitor.NodeTransformer):
    """把模板 AST 中的名字 ``self`` 改写为 ``agent``。内部 API。

    动机：Jinja2 代码生成器把根作用域中未声明的 ``self`` 名字劫持为
    ``TemplateReference`` （服务 ``{% block %}`` 模板继承机制），导致渲染
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
