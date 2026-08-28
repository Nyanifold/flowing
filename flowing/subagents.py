"""flowing.subagents —— 子智能体子系统的绑定与结果类型（SubagentEntry / SubagentResult / SubagentInvocation）。

.. rubric:: 功能介绍

本模块承载子智能体（子 Agent）子系统的**绑定与结果类型**：

- :class:`SubagentEntry` —— Agent 对子 Agent 类型的一次「用法声明」，
  能力三正交的 Agent 级绑定层（与 :class:`flowing.tool.ToolEntry`
  同构）：LLM 看到的别名与描述、system prompt 覆写、参数覆写 / 指定值 /
  注入。
- :class:`SubagentInvocation` —— ``before_subagent_invoke`` /
  ``after_subagent_invoke`` 钩子的 value。
- :class:`SubagentResult` —— 子 Agent 一次唤起的返回结果（代码路径
  载体）。

唤起管线本体（``invoke_subagent`` / ``add_agent`` /
``_render_subagent_catalog``）留在 :class:`flowing.agent.Agent` 上，
本模块只定义它操作的条目与产物类型。

.. rubric:: 设计动机

从 :mod:`flowing.agent` 拆出：「Agent 核心逻辑」（消息级树、逻辑 Turn
执行、异步执行管理）与「子智能体管理」（绑定条目、唤起钩子 value、
catalog 装配的条目侧）分文件——``agent.py`` 只保留 Agent 对象模型本体
与管线的具名代码点，条目与结果类型的完整契约集中在本模块。

.. rubric:: 依赖方向

本模块只在 ``TYPE_CHECKING`` 下引用 :class:`flowing.agent.Agent`
（注解级边）；运行期**不** import ``flowing.agent``，无运行期环。
依赖方向为单向：``flowing.agent`` → ``flowing.subagents``。
"""

from __future__ import annotations   # 注解延迟求值：Agent 仅 TYPE_CHECKING 引用，破注解级循环边

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from flowing.parsable import Parsable

if TYPE_CHECKING:
    from flowing.agent import Agent

__all__ = [
    "SubagentEntry",
    "SubagentResult",
    "SubagentInvocation",
    "DEFAULT_SUBAGENT_CATALOG_TEMPLATE",
]


@dataclass
class SubagentResult:
    """子 Agent 一次唤起的返回结果（代码路径载体）。

    .. rubric:: 功能介绍

    :meth:`Agent.invoke_subagent` 的返回类型，也是 ``after_subagent_invoke``
    钩子可改写的交付对象（经 ``SubagentInvocation.result`` 回填，先于交付
    dispatch——改写对 return 值与 SUBAGENT 消息同时生效）。子 Agent 的输出
    同时有两条载体：代码路径返回 ``SubagentResult``；消息路径以独立
    ``Message(kind=SUBAGENT)`` 推入父 Agent 队列（LLM 在后续回合感知）。

    .. rubric:: 设计动机

    取代旧 ``AgentResult`` 设计：拆为「返回值（``SubagentResult``）+ 消息
    （SUBAGENT）」两层——子 Agent 输出**永远是独立消息**，不合并进
    ``ToolResult``。``SubagentResult`` 是纯数据载体：只携带
    ``subagent_id`` 等可 JSON 序列化字段，不持有活实例引用。

    .. rubric:: 行为规约

    - ``subagent_id``：子 Agent 的 ``node_id``。调用方需要活实例时，
      经 ``runtime.get_agent(subagent_id)`` 按「有 key 无 value → 现场
      恢复」语义获取；不要在本对象上保留实例引用。
    - ``result``：子 Agent 的最终产出——默认是其最后回复文本（自由字符串）；
      子 Agent 调用 ``FinishTool`` 结构化返回时为结构化字段集。返回结构
      两种路径同构，「finish 的 output 声明只是把默认字符串值替换为结构化
      字段集」。
    - ``subagent_status``：回合结局透传（``TurnResult.status``）；取消 /
      异常信息与内容分离——cancel 时已置位的 finish 载荷或已产出文本照常
      出现在 ``result``，结局标记只看本字段。
    - 非行为：不携带子 Agent 的完整消息历史（历史在子 Agent 自己的
      session 中，可经 ``agent.chain`` / 快照访问）。

    .. rubric:: 调用关系（审计）

    - 被调：``after_subagent_invoke`` 钩子经 ``SubagentInvocation.result``
      间接持有（时机：结果构造后、交付前，可改写）；
      ``flowing.agent.Agent.invoke_subagent()`` 的返回类型
    - 实例化方：``flowing.agent.Agent._run_subagent``（时机：
      ``await child.query()`` 产出后、dispatch
      ``after_subagent_invoke`` 前）

    .. seealso::

        - :meth:`Agent.invoke_subagent` —— 产出入口。
        - :class:`SubagentInvocation` —— 唤起钩子 value。
        - :class:`flowing.message.MessageKind` —— ``SUBAGENT`` 消息种类。
    """

    name_alias: str
    """调用方可读标识：续接名（``resume``）/ 语义名（``name``）/ 唤起别名
    （``alias``），按此优先序取——语义名只存在父 Agent 的
    ``_child_ids`` 表中，子实例不自持（A15 裁决）。
    """
    subagent_id: str
    """子 Agent 的 ``node_id``。纯数据字段，可 JSON 序列化；需要活实例
    时经 ``runtime.get_agent(subagent_id)`` 获取（默认存续，显式
    ``destroy()`` 才销毁实例；记录保留，可现场恢复）。
    """
    result: Any
    """子 Agent 最终产出：默认 ``str``；finish 结构化返回时为字段 dict；
    无产出（如取消且无任何完整响应）为 ``None``。取自
    ``Agent.last_result``——子 Agent 收尾段在 resolve waiters 之前统一
    写入（``finish_output`` 置位 → 该 dict；否则本轮最后一条 PROVIDER
    消息文本，空 → ``None``），填充规则见 :attr:`flowing.agent.Agent.
    last_result`。
    """
    subagent_status: str
    """子 Agent 该回合的结局，透传 ``TurnResult.status``
    （``"completed"`` / ``"blocked"`` / ``"error"`` / ``"cancelled"``）
    ——**取消/异常信息的载体**：被 cancel 时已置位的 finish 载荷或已产出
    文本仍经 ``result`` 照返，「子 Agent 取消」这一事实由本字段承载，
    不污染 ``result`` 的内容契约（结构化 dict 不塞标注、文本不拼后缀）。
    """


@dataclass
class SubagentInvocation:
    """``before_subagent_invoke`` / ``after_subagent_invoke`` 钩子的 value。

    .. rubric:: 功能介绍

    :meth:`Agent.invoke_subagent` 在 ``SubagentEntry.resolve()`` 校验之后、
    创建 / 续接子 Agent 之前构造本对象并 dispatch ``before_subagent_invoke``
    （挂在**父 Agent** 的 hooks 上）；结果构造后回填 ``result`` 并 dispatch
    ``after_subagent_invoke``（先于交付：return 值与 SUBAGENT 消息均在其后）。

    .. rubric:: 设计动机

    唤起钩子与创建钩子不混用：``before/after_subagent_invoke`` 挂父 Agent、
    管「唤起」；``before/after_create`` 挂子实例、管「创建/生命周期」。两条
    钩子链的 dispatch 点不同（唤起钩子包在创建管线**之外**）。

    .. rubric:: 行为规约

    - ``before_subagent_invoke`` handler 可改写本对象（调整 ``args`` /
      ``prompt``）或 ``raise Intercepted`` 硬阻断唤起。
    - ``after_subagent_invoke`` 为观察 / 改写 ``result`` 的收尾点，
      **先于交付** dispatch——改写后的 result 对 return 值与 SUBAGENT
      消息同时生效（两条路径同源）；本钩子不接 ``Intercepted``（阻断闸
      在 before，答卷级处置用改写表达）。``result`` 在 before 阶段为
      ``None``。
    - ``resume`` 与 ``agent_type`` 互斥（续接保持原类型）；``resume`` 非空
      时 ``alias`` 为被续接实例名。
    - 唤起失败（创建 / 校验 / 运行抛异常）路径**没有**专属错误钩子
      （``on_subagent_error`` 已删除）——异常直接上抛给 ``invoke_subagent``
      调用方；``subagent-invoke`` 工具路径由 ``ToolResult(status="error")``
      承载。

    .. rubric:: 调用关系（审计）

    - 被调：``before_subagent_invoke`` / ``after_subagent_invoke`` 钩子
      dispatch 的 value（时机：唤起前后，挂在父 Agent 的 hooks 上）
    - 实例化方：``flowing.agent.Agent.invoke_subagent``（时机：
      ``SubagentEntry.resolve()`` 校验之后、创建/续接子 Agent 之前）

    .. seealso::

        - :meth:`Agent.invoke_subagent` —— dispatch 点。
        - :class:`SubagentEntry` —— resolve 的产物构成本对象字段。
    """

    alias: str
    """目标别名（``_subagent_entries`` 的 key）或续接实例名。
    """
    agent_type: str | None
    """新建路径的 Agent 类型名；``resume`` 路径为 ``None``。取自
    ``SubagentEntry.name_ori``，可含命名空间前缀（``ns::name``，
    只查注册表）。
    """
    resume: str | None
    """续接路径的实例名；与 ``agent_type`` 互斥。
    """
    prompt: str | None
    """任务提示（自然语言）；可为 ``None``（纯参数唤起）。
    """
    args: dict[str, Any]
    """``SubagentEntry.resolve()`` 产出的完整初始化 kwargs（别名已映射回
    规范名、specified（固定值/注入表达式）已注入）。
    """
    name: str | None = None
    """新建路径的语义名（登记进父 Agent ``_child_ids``，供
    ``resume=...`` 按名续接）；``None`` = 匿名。子实例不自持名字
    （A15 裁决：simplename 已删除）。
    """
    result: SubagentResult | None = None
    """``after_subagent_invoke`` 阶段回填的唤起结果；before 阶段为 ``None``。
    """


class SubagentEntry:
    """Agent 对子 Agent 的一次「用法声明」——能力三正交的 Agent 级绑定层。

    .. rubric:: 功能介绍

    与 :class:`flowing.tool.ToolEntry` 同构：回答「这个 Agent 如何使用这个
    子 Agent 类型」——LLM 看到的别名与描述、system prompt 覆写、参数覆写 /
    指定值 / 注入。每个 Agent 实例的 ``_subagent_entries`` 持有自己的 entry
    集合（key 为**别名**）。

    .. rubric:: 设计动机

    绑定层的存在理由：同一子 Agent 类型在不同父 Agent 上 LLM 应看到不同
    描述与参数默认值（如 ``currency`` 默认 ``USD`` vs ``CNY``）——通过
    entry 覆写而不改子 Agent 类本身。子 Agent 的 LLM 可见声明不是独立
    ``ToolDefinition``（``llm_definition()`` **不存在**）——工具只有一个
    ``subagent-invoke``，各类型的参数描述经 ``<available_subagents>`` XML
    catalog 注入。

    .. rubric:: 使用示例

    ``.fya`` 声明（``order-agent.fya`` 的 ``subagents:`` 条目）：

    .. code-block:: yaml

        subagents:
          - payment as pay:
              description: "发起支付。请在用户明确确认后调用。"
              args:
                amount as sum:
                  description: "支付金额（元）"
                currency: USD                                   # 裸值 → specified
                user_id: "{{ self.inject('user_id') }}"          # 注入表达式（R-4：无 inject 键）

    编程式等价：

    .. code-block:: python

        entry = self._subagent_entries["pay"]
        entry.override_system_prompt = Parsable("你是支付处理助手。当前模式：{{ mode }}")
        entry.override_params["currency"]["default"] = "CNY"

    .. rubric:: 行为规约

    - ``enabled=False`` 时不渲染进 catalog（LLM 不可见），但仍可编程式
      ``invoke_subagent()``——「可见性」与「可执行性」分离。
    - ``specified`` 声明的参数（固定值与注入表达式）**排除**在 catalog
      渲染之外（LLM 不可见）；注入表达式求值结果是**子 Agent 创建时的
      初始化参数**（``create_subagent(user_id=...)``），不是修改子 Agent
      内部行为——与 Tool 侧注入（求值结果进 ``execute()`` 参数）语义不同。
    - **覆写 ``args:`` 的判别规则**（``- amount as sum: {description:
      ...}`` 等形态 → ``override_params``/``specified``/``param_aliases``
      的归属、``as`` 键允许带值、``_`` = 空补丁）与 Tool 条目同构，
      规则本体见 :class:`flowing.tool.ToolEntry`；差异仅两点：
      ``specified`` 求值上下文为**父** Agent 实例；注入表达式的求值
      结果落子 Agent 初始化参数（上一条）。
    - **多层具名块导航**（``$subagents.<alias>.xxx:``，装配层执行；块
      词法见 :func:`flowing.parser.split_fya`）四条规则：

      1. **列表段**（``subagents``/``tools`` 等 entry 列表）：按条目
         **别名**精确匹配（声明了 ``as`` 必须写别名）；未命中 →
         ``FormatError``。**无下标语法**（已禁止——列表一律按名寻址）；
      2. **dict 段**：精确 key；key 含 ``as`` 时**仅以别名段寻址**
         （用户裁决：有了别名就不允许规范名段寻址——与规则 1 的
         「声明了 ``as`` 必须写别名」同一原则）；无 ``as`` 的 key 整体
         精确匹配。覆写 ``args`` 内的参数寻址同此规则
         （``$tools.pay.args.cwd.description:`` 命中
         ``working_dir as cwd`` 键；``$tools.pay.args.working_dir...``
         不命中）；
      3. **PENDING 槽**：其后还有路径段 → 物化为空映射继续深入
         （override 位 ``_`` = 空补丁语义）；即末端 → 按规则 4 写入；
      4. **末端**：目标缺失或为 ``PENDING`` → 写入；已有实际值 →
         冲突 ``FormatError``（``_`` 不是「忽略」，是「可被具名块
         替换」的合法前提，见 :data:`flowing.parsable.PENDING`）。

      别名段之后进入条目的**覆写声明空间**（键为声明名
      ``system_prompt``/``description``/``args`` 等，装配层
      映射到 entry 字段 ``override_system_prompt`` 等；R-4：无
      ``inject`` 键）。
    - **省略 ``as`` 的别名推断**：解析期由
      :func:`flowing.parser.normalize_entries` 完成——裸名 = 裸名
      本身；限定名 = ``::`` 后 name 段；路径形态经
      :func:`flowing.paths.infer_name`（去 ``.agent.fya`` / ``.fya`` /
      ``.py`` 后缀、通用文件名 ``agent.fya`` / ``AGENT.fya`` 取目录名、
      snake→kebab；规则表 ``AGENT_NAMING`` 见 ``flowing.runtime``，
      目录形态候选链探测见 :func:`flowing.paths.probe_candidates`）。
    - 同 alias 重复（``.fya`` ``subagents:`` 列表内，**含推断撞名**——如
      ``./a/payment`` 与 ``./b/payment`` 都推断出 ``payment``）→
      :class:`flowing.errors.EntryNameConflictError`——与 tool / skill
      绑定层统一 fail-fast，不做「后声明覆盖先声明」。**例外**（与 skills
      同构）：glob 展开命中与已显式声明条目**规范名相同**的同一资源时跳过
      （先解析显式条目，再展开 glob）；只有**不同资源**得出同别名才报错。
    - **限定名引用与命名空间**：条目支持 ``ns::规范名`` 形式（指向插件
      注册资源，只查注册表；见 ``flowing.runtime`` 模块 docstring §7a）。
      命名空间是内部身份标识，**永不进入 LLM 可见面**——catalog 与调用名
      只暴露别名（无别名时规范名）。冲突判定因而是 **LLM 视角**的：两个
      不同命名空间的同名资源（``@/a::payment`` 与 ``@/b::payment``、或
      插件注册资源）被**同一 Agent** 引用时必须给其一``as`` 别名，否则
      按同 alias 撞名抛 ``EntryNameConflictError``；不同 Agent 各引各的
      同名资源互不影响。
    - 非行为：entry 不持有子 Agent 实例——实例的创建 / 复用由
      ``invoke_subagent`` 与 agent 池管理。

    .. rubric:: 测试案例

    - 前置：``.fya`` 声明 ``- payment as pay`` 且其 ``args:`` 含
      ``currency: USD``（specified 固定值）与 ``user_id:
      "{{ self.inject('user_id') }}"``（注入表达式）→ 操作：
      ``catalog_view(parent)`` → 期望：``params_xml`` 中 ``currency``
      不出现（specified 参数排除），``user_id`` 不出现。
    - 前置：``entry.enabled = False`` → 操作：``_assemble_context()`` →
      期望：catalog 无该条目。``Context.tools`` 是否含
      ``subagent-invoke`` 与 entry 的 enabled 无关——该工具需用户
      显式声明（``tools:`` / ``add_tool``）才进入可见面
      （S-19 最终裁决：无隐式附加）。
    - 前置：``.fya`` 声明 ``- ./a/payment`` 与 ``- ./b/payment``（两处
      目标均未声明 ``name``）→ 操作：解析 ``subagents:`` → 期望：
      两者别名均推断为 ``payment``，抛 ``EntryNameConflictError``。
    - 前置：显式声明 ``- ./a/payment`` + glob ``- ./*/`` 展开命中同一
      资源 → 期望：glob 条目跳过，不报错。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.invoke_subagent()``（时机：唤起时序
      第 1 步，按别名查 ``_subagent_entries``）；
      ``SubagentEntry.catalog_view`` 由
      ``Agent._render_subagent_catalog`` 逐条调用
      （时机：``_assemble_context()`` 时——S-01 裁决：子智能体是核心
      特性，catalog 注入不依赖任何 use composable）
    - 实例化方：:meth:`flowing.agent.Agent.add_agent`（``.fya``
      ``subagents:`` 条目装配与程序化调用的统一入口；解析经
      :func:`flowing.parser.parse_fya`，条目规范化经
      :func:`flowing.parser.normalize_entries`）

    .. seealso::

        - :class:`flowing.tool.ToolEntry` —— 同构的 Tool 绑定条目。
        - :meth:`Agent.invoke_subagent` —— 经 ``resolve()`` 的唤起路径。
        - :meth:`flowing.parsable.Parsable.resolve` —— 覆写值的求值机制。
    """

    name_alias: str
    """别名——``_subagent_entries`` 的 key、catalog ``<name>``、多层具名块
    的匹配键。LLM 与父 Agent 代码均按别名引用。
    """
    name_ori: str
    """规范类型名（``agent_type``）——惰性解析 Agent 类的唯一依据。支持
    ``ns::规范名`` 限定名形式（指向插件注册资源，只查注册表）；命名空间
    是内部身份标识，**永不进入 LLM 可见面**（catalog/调用名只暴露别名），
    详见类级行为规约「限定名引用与命名空间」。
    """
    override_system_prompt: Parsable | None = None
    """覆写子 Agent 的 system prompt；**子 Agent 创建时**求值一次（求值
    上下文为父 Agent 实例）。``None`` 使用子类原声明。
    """
    override_description: Parsable | None = None
    """覆写 LLM 看到的描述；在父 Agent 路由决策 / ``catalog_view()``
    时以**父 Agent** 实例为上下文求值。
    """
    override_params: dict[str, dict[str, Any]] | None = None
    """参数局部覆写：``{规范参数名: {子属性: 新值}}``（如
    ``["currency"]["default"]``）。
    """
    specified: dict[str, Parsable] = ...
    """指定值初始化参数（LLM 不可见）；``invoke_subagent()`` 内以父 Agent
    实例上下文求值。两种值形态（R-4 裁决，``inject`` 字段已删除）：
    固定值与**注入表达式**（``"{{ self.inject('key') }}"``——求值时
    沿 provide 链上溯，结果落子 Agent **初始化参数**；链断裂抛
    ``MissingProvideError``）。默认空 dict。
    """
    param_aliases: dict[str, str] = ...
    """LLM 参数名 → 规范参数名。默认空 dict。
    """
    enabled: bool = True
    """是否渲染进 catalog；``False`` 时 LLM 不可见但仍可编程式唤起。
    """

    def resolve(self, parent: Agent, args: dict[str, Any]) -> dict[str, Any]:
        """把 LLM args 聚合为子 Agent 创建的完整 kwargs。

        .. rubric:: 功能介绍

        :meth:`Agent.invoke_subagent` 内部调用：参数别名映射回规范名 →
        ``specified`` 以父 Agent 实例上下文惰性求值后覆盖（固定值直给；
        注入表达式在求值时经 ``parent.inject(...)`` 沿 provide 链上溯，
        R-4：无独立 inject 步骤，优先级最高）。

        .. rubric:: 行为规约

        - 产物经子 Agent ``args_model`` 校验（``_normalize`` 同构
          流程，B1：声明即模型）：**以调用方视角校验**（LLM 可见
          参数集与命名，specified 参数不进调用方校验空间）；校验失败
          异常上抛给调用方。
        - args 仅向下传**一层**——中间层不显式转发，孙 Agent 收不到；
          跨层共享改用 provide / inject。
        - :raises flowing.errors.MissingProvideError: 注入表达式中的 key
          沿 provide 链上溯不到任何提供者（调用时求值抛出）。

        .. rubric:: 测试案例

        - 前置：``args={"sum": 100}``、``param_aliases={"sum": "amount"}``、
          ``specified={"currency": Parsable("CNY"), "user_id":
          Parsable("{{ self.inject('user_id') }}")}``、provide 链可提供
          ``user_id`` → 操作：``resolve(parent, args)`` → 期望：
          ``{"amount": 100, "currency": "CNY", "user_id": ...}``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.inject()``（时机：注入表达式
          求值时沿 provide 链上溯，优先级最高）
        - 被调：``flowing.agent.Agent.invoke_subagent``（时机：唤起时序
          的 resolve 步，LLM args → 完整 kwargs）

        .. seealso::

            - :meth:`flowing.tool.ToolEntry.resolve` —— Tool 侧同构三步。
        """
        mapped: dict[str, Any] = {}
        for alias, value in args.items():
            mapped[self.param_aliases.get(alias, alias)] = value   # 别名映射回规范名
        for key, parsable in self.specified.items():
            mapped[key] = parsable.resolve(parent)   # specified 以父 Agent 实例上下文惰性求值后覆盖（固定值/注入表达式同路——注入表达式求值即 provide 链上溯）
        # 产物经子 Agent args_model 校验（_normalize 同构流程，B1 声明即模型，
        # 调用方视角；失败异常上抛调用方）
        return mapped

    def catalog_view(self, parent: Agent) -> dict[str, Any]:
        """为 catalog 模板预计算本条目的视图 dict。

        .. rubric:: 功能介绍

        :data:`DEFAULT_SUBAGENT_CATALOG_TEMPLATE` 的 ``entries`` 上下文中
        本条目的元素：``{"name": 别名, "description": 已解析字符串,
        "params_xml": 已应用覆写/改名/排除规则后的 params 段字符串}``。
        预计算在 Python 侧完成（本方法），模板只负责排布。

        .. rubric:: 行为规约

        - ``name`` 键为别名；``description`` 键取
          ``override_description``（以 ``parent`` 为上下文求值）或子类
          原 ``description``；``params_xml`` 键由子类 ``args_model``
          派生的 JSON Schema 经
          :func:`flowing.params.apply_param_overrides` 应用
          ``override_params``（非法关键字 fail-fast、未知参数名允许——
          合法性推迟到 invoke 时由子类模型校验兜底），再应用
          ``param_aliases`` 改名生成，**排除** ``specified``
          声明的参数（固定值与注入表达式）。
        - ``enabled=False`` 时本方法不应被调用（调用方负责过滤）；直接
          调用不报错但产物无契约保证。
        - ``subagent-invoke`` 工具**不随本条目自动可见**——需用户显式
          声明（``tools:`` / ``add_tool("subagent-invoke")``）才
          进入 ``Context.tools``（S-19 最终裁决：一切工具以用户声明为
          准，框架不隐式附加；catalog XML 与工具条目是两个独立面）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.parsable.Parsable.resolve()``（时机：
          ``override_description`` 以父 Agent 实例为上下文求值）
        - 被调：``flowing.agent.Agent._render_subagent_catalog`` 逐条
          调用（时机：``_assemble_context()`` 时对每个
          ``enabled=True`` 的 entry；S-01 裁决：不依赖任何 use
          composable）

        .. seealso::

            - :meth:`resolve` —— 唤起侧的参数聚合。
            - :data:`DEFAULT_SUBAGENT_CATALOG_TEMPLATE` —— 视图的
              消费模板。
            - :class:`flowing.context.Context` —— catalog 的最终归宿
              （system_prompt 段）。
        """
        description: str = ""
        if self.override_description is not None:
            description = str(self.override_description.resolve(parent))   # 以父 Agent 实例为上下文求值
        # else：取子类原 description（name_ori -> Agent 类经 Runtime.get_agent_class 惰性解析）
        # params_xml：子类 args_model 派生 schema 经 params.apply_param_overrides
        # 应用 override_params，再应用 param_aliases 改名，排除 specified 声明的参数
        params_xml: str = ""   # 占位：args_model 派生 schema 的覆写产物
        return {"name": self.name_alias, "description": description,
                "params_xml": params_xml}


DEFAULT_SUBAGENT_CATALOG_TEMPLATE: str = (
    '{% if entries %}<available_subagents>\n'
    '{% for e in entries %}<subagent><name>{{ e.name }}</name>'
    '<description>{{ e.description }}</description>{{ e.params_xml }}</subagent>\n'
    '{% endfor %}\n'
    '</available_subagents>{% endif %}'
)
"""内置默认子智能体 catalog 模板：把逐条 ``<subagent>`` 片段包进
``<available_subagents>``。

.. rubric:: 功能介绍 / 设计动机

**唯一渲染槽位的内置缺省值**（与 skills / cron 同一约定；渲染器模板化
裁决：槽位只收 Jinja2 模板字符串，**不收 callable**——定制即整体替换
模板，本常量公开可参考 / 派生）。覆写槽位为
:attr:`flowing.agent.Agent.subagent_catalog_template`。

.. rubric:: 模板上下文变量表

- ``entries``：per-entry **预计算视图 dict 列表**——每个元素即
  :meth:`SubagentEntry.catalog_view` 的产物，键：

  - ``name``：别名（``SubagentEntry.name_alias``）；
  - ``description``：**已解析**字符串（override 已在 Python 侧以父
    Agent 为上下文求值完毕）；
  - ``params_xml``：已应用覆写 / 改名 / 排除规则后的 params 段字符串
    （含前导 ``<params>`` 片段或空串）。

  预计算在 Python 侧完成，**模板只负责排布**（不在模板内
  ``.resolve()``——与 skills 模板的「模板内现场求值」不同，本模板的
  求值已前移到 ``catalog_view``）。
- ``agent``：父 Agent 实例。

.. rubric:: 行为规约

- 渲染经 Parsable TEMPLATE 语义（include 基准为父 Agent 的
  ``source_dir``，P3-08）；渲染异常 fail-fast 上抛，不静默降级。
- 空列表经 ``{% if entries %}`` 渲染为 ``""``（整块不注入；
  ``Agent._render_subagent_catalog`` 在此之前也有短路，双保险）。

.. rubric:: 使用示例

产物形态：

.. code-block:: xml

    <available_subagents>
    <subagent><name>pay</name><description>发起支付。</description><params>...</params></subagent>
    </available_subagents>

自定义（整体替换；类属性或 ``.fya`` 同名字段均可，``getattr`` 同样命中）：

.. code-block:: python

    class OrderAgent(Agent):
        subagent_catalog_template = "$./my-catalog.j2"

.. rubric:: 调用关系（审计）

- 被调：``flowing.agent.Agent._render_subagent_catalog`` 作为缺省选用
  （时机：每次上下文组装的子智能体 catalog 块现场渲染；
  ``cache="dynamic"`` 语义）

.. seealso::

    - :attr:`flowing.agent.Agent.subagent_catalog_template` —— Agent 级
      覆写槽位。
    - :meth:`SubagentEntry.catalog_view` —— ``entries`` 元素的生产方。
    - :data:`flowing.plugins.skills.DEFAULT_CATALOG_TEMPLATE` —— 同
      约定的 Skill 侧缺省模板。
"""
