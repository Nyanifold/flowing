"""``flowing.subagents`` —— 子智能体子系统的绑定与结果类型。

.. rubric:: 功能介绍

本模块承载子智能体（子 Agent）子系统的绑定与结果类型：

- :class:`SubagentEntry` —— Agent 对子 Agent 类型的一次“用法声明”，
  三层能力描述（可执行对象 / LLM 可见声明 / Agent 级绑定三层）的
  Agent 级绑定层（与 :class:`flowing.tool.ToolEntry` 同构）：LLM 看到
  的别名与描述、参数覆写 / 指定值 / 注入。
- :class:`SubagentInvocation` —— ``on_subagent_invoke`` /
  ``on_subagent_returns`` 钩子的 value。
- :class:`SubagentResult` —— 子 Agent 一次唤起的返回结果。
- :data:`DEFAULT_SUBAGENT_CATALOG_TEMPLATE` —— ``<available_subagents>``
  渲染槽位的内置缺省模板。

唤起管线本体（``Agent.invoke_subagent`` / ``Agent.add_agent``）与
catalog 渲染留在 :mod:`flowing.agent`——本模块只定义管线操作的条目与
产物类型。

.. rubric:: 全局约定（跨符号、影响使用的约定）

- 子 Agent 唤起是两段式时序（见 :meth:`flowing.agent.Agent.invoke_subagent`）：
  准备段按别名查条目 → :meth:`SubagentEntry.resolve` 聚合参数 → 构造
  :class:`SubagentInvocation` 并 dispatch 亲代 Agent 的
  ``on_subagent_invoke`` → 新建 / 续接子 Agent（此段失败同步上抛）；
  运行段等待子 Agent 产出 → 构造 :class:`SubagentResult` → dispatch
  ``on_subagent_returns`` （先于交付，handler 可改写 result）→ 交付。
  两个钩子都挂在亲代 Agent 的 hooks 上。
- 子 Agent 产出有两条载体：``invoke_subagent`` 同步返回
  :class:`SubagentResult` （不入亲代队列，调用方自行处置）；后台唤起路径
  （``subagent-invoke`` 工具的 ``asynchronized=True``）完成时以独立
  ``Message(kind=SUBAGENT)`` 推入亲代 Agent 队列，LLM 在后续回合感知。
  两条路径内容同源：都采用 ``on_subagent_returns`` 改写后的 result。
- 没有子 Agent 专属错误钩子：唤起 / 运行失败以异常原样上抛调用方；
  ``subagent-invoke`` 工具路径由 ``ToolResult(status="error")`` 承载。

.. seealso::

    - :mod:`flowing.agent` —— 子 Agent 唤起管线与 catalog 渲染。
    - :mod:`flowing.hooks` —— ``on_subagent_invoke`` /
      ``on_subagent_returns`` 钩子点契约。
    - :mod:`flowing.tool` —— 同构的 Tool 绑定层（``ToolEntry``）。
"""

from __future__ import annotations   # 注解延迟求值：Agent 仅 TYPE_CHECKING 引用，破注解级循环边

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape as _xml_escape

from flowing.errors import FormatError
from flowing.params import apply_param_overrides
from flowing.paths import GLOB_META as _GLOB_META
from flowing.parsable import Parsable
from flowing.paths import NamingRules, classify_ref, resolve_path

if TYPE_CHECKING:
    from flowing.agent import Agent
    from flowing.parser import EntryRef

__all__ = [
    "SubagentEntry",
    "SubagentResult",
    "SubagentInvocation",
    "DEFAULT_SUBAGENT_CATALOG_TEMPLATE",
]


@dataclass
class SubagentResult:
    """子 Agent 一次唤起的返回结果（:meth:`flowing.agent.Agent.invoke_subagent` 的返回类型）。

    .. rubric:: 功能介绍

    也是 ``on_subagent_returns`` 钩子可改写的交付对象（经
    ``SubagentInvocation.result`` 回填，先于交付 dispatch——改写对
    return 值与 SUBAGENT 消息同时生效）。纯数据载体：只携带可 JSON
    序列化字段，不持有活实例引用。

    .. rubric:: 行为要点

    - ``subagent_id``：子 Agent 的 ``node_id``。调用方需要活实例时，
      经 ``runtime.get_agent(subagent_id)`` 按“有记录无实例 → 现场
      恢复”语义获取；不要在本对象上保留实例引用。
    - ``result``：子 Agent 的最终产出——默认是其最后回复文本（字符串）；
      子 Agent 调用 ``FinishTool`` 结构化返回时为结构化字段 dict；
      无任何产出（如取消且无完整响应）为 ``None``。取自
      ``Agent.last_result``。
    - ``subagent_status``：子 Agent 该回合的结局，透传
      ``TurnResult.status``。取消 / 异常信息与内容分离：被 cancel 时
      已置位的 finish 载荷或已产出文本照常出现在 ``result``，结局标记
      只看本字段。
    - 不携带子 Agent 的完整消息历史（历史在子 Agent 自己的 session
      中，可经 ``agent.chain`` / 快照访问）。

    .. seealso::

        - :meth:`flowing.agent.Agent.invoke_subagent` —— 产出入口。
        - :class:`SubagentInvocation` —— 唤起钩子 value。
        - :class:`flowing.message.MessageKind` —— ``SUBAGENT`` 消息种类。
    """

    name_alias: str
    """调用方可读标识：续接名（``resume``）/ 语义名（``name``）/ 唤起别名
    （``alias``），按此优先序取。语义名只存在亲代 Agent 的
    ``child_ids`` 表中，子实例不自持名字。
    """
    subagent_id: str
    """子 Agent 的 ``node_id``。纯数据字段，可 JSON 序列化；需要活实例
    时经 ``runtime.get_agent(subagent_id)`` 获取（默认存续，显式
    ``destroy()`` 才销毁实例；记录保留，可现场恢复）。
    """
    result: Any
    """子 Agent 最终产出：默认 ``str``；finish 结构化返回时为字段 dict；
    无产出（如取消且无任何完整响应）为 ``None``。取自
    ``Agent.last_result``——收尾段统一写入（``finish_output`` 置位 →
    该 dict；否则本轮最后一条 PROVIDER 消息文本；空 → ``None``），
    填充规则见 :attr:`flowing.agent.Agent.last_result`。
    """
    subagent_status: str
    """子 Agent 该回合的结局，透传 ``TurnResult.status``
    （``"completed"`` / ``"blocked"`` / ``"error"`` / ``"cancelled"``）。
    取消 / 异常信息的载体：被 cancel 时已置位的 finish 载荷或已产出
    文本仍经 ``result`` 照返，“子 Agent 取消”这一事实由本字段承载，
    不污染 ``result`` 的内容契约（结构化 dict 不塞标注、文本不拼后缀）。
    """


@dataclass
class SubagentInvocation:
    """``on_subagent_invoke`` / ``on_subagent_returns`` 钩子的 value。

    .. rubric:: 功能介绍

    :meth:`flowing.agent.Agent.invoke_subagent` 在 ``SubagentEntry.resolve()``
    之后、创建 / 续接子 Agent 之前构造本对象并 dispatch
    ``on_subagent_invoke`` （挂在亲代 Agent 的 hooks 上）；结果构造
    后回填 ``result`` 并 dispatch ``on_subagent_returns`` （先于交付：
    return 值与 SUBAGENT 消息均在其后）。

    .. rubric:: 行为要点

    - ``on_subagent_invoke`` handler 可改写本对象（调整 ``args`` /
      ``prompt``）或 ``raise Intercepted`` 硬阻断唤起——阻断同步上抛，
      子 Agent 未创建。
    - ``on_subagent_returns`` 是改写 ``result`` 的收尾点，先于交付
      dispatch——改写后的 result 对 return 值与 SUBAGENT 消息同时生效
      （两条路径同源）；本钩子不接 ``Intercepted`` （阻断闸在 before，
      答卷级处置用改写表达）。``result`` 在 before 阶段为 ``None``。
    - 唤起 / 运行失败（创建、校验、运行抛异常）没有专属错误钩子——
      异常直接上抛给 ``invoke_subagent`` 调用方；``subagent-invoke``
      工具路径由 ``ToolResult(status="error")`` 承载。

    .. seealso::

        - :meth:`flowing.agent.Agent.invoke_subagent` —— dispatch 点。
        - :class:`SubagentEntry` —— resolve 的产物构成本对象字段。
    """

    alias: str
    """唤起时使用的别名（亲代 Agent 条目表中本条目的 key）。"""
    agent_type: str | None
    """新建路径的 Agent 类型名；``resume`` 路径为 ``None``。取自
    ``SubagentEntry.name_ori``，可含命名空间前缀（``ns::name``，
    只查注册表）。
    """
    resume: str | None
    """续接路径的实例名（登记在亲代 Agent ``child_ids`` 的语义名）；
    与 ``agent_type`` 互斥（续接保持原类型）。
    """
    prompt: str | None
    """任务提示（自然语言）；可为 ``None`` （纯参数唤起）。
    """
    args: dict[str, Any]
    """``SubagentEntry.resolve()`` 产出的完整初始化 kwargs（别名已映射回
    规范名、specified（固定值 / 注入表达式）已注入）。
    """
    name: str | None = None
    """新建路径的语义名（登记进亲代 Agent ``child_ids``，供 ``resume=...``
    按名续接）；``None`` = 匿名。子实例不自持名字。
    """
    result: SubagentResult | None = None
    """``on_subagent_returns`` 阶段回填的唤起结果；before 阶段为 ``None``。
    """


@dataclass
class SubagentEntry:
    """Agent 对子 Agent 类型的一次“用法声明”——三层能力描述的 Agent 级绑定层。

    .. rubric:: 功能介绍

    与 :class:`flowing.tool.ToolEntry` 同构：回答“这个 Agent 如何使用
    这个子 Agent 类型”——LLM 看到的别名与描述、参数覆写 / 指定值 /
    注入。每个 Agent 实例的条目表（以别名为键）持有自己的 entry 集合。

    绑定层的存在理由：同一子 Agent 类型在不同亲代 Agent 上 LLM 应看到
    不同描述与参数默认值（如 ``currency`` 默认 ``USD`` vs ``CNY``）——
    通过 entry 覆写而不改子 Agent 类本身。子 Agent 的 LLM 可见声明不是
    独立 ``ToolDefinition``：工具只有一个 ``subagent-invoke``，各类型的
    参数描述经 ``<available_subagents>`` XML catalog 注入。

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
                user_id: "{{ self.inject('user_id') }}"        # 注入表达式

    编程式等价（``setup()`` 中，走同一 body 判别管线）：

    .. code-block:: python

        entry = self.add_agent(
            "payment", alias="pay",
            body={"description": "发起支付。",
                  "args": {"amount as sum": {"description": "支付金额（元）"},
                           "currency": "USD",
                           "user_id": "{{ self.inject('user_id') }}"}})

    .. rubric:: 行为要点

    - ``visible=False`` 时不渲染进 catalog（LLM 不可见），但仍可编程式
      ``invoke_subagent()``——可见性与可执行性分离。
    - ``specified`` 声明的参数（固定值与注入表达式）排除在 catalog
      渲染之外（LLM 不可见）；注入表达式求值结果是子 Agent 创建时的
      初始化参数（``create_subagent(user_id=...)``），不是修改子 Agent
      内部行为——与 Tool 侧注入（求值结果进 ``execute()`` 参数）语义不同。
    - 覆写 ``args:`` 的判别规则（``- amount as sum: {description: ...}``
      等形态 → ``override_params`` / ``specified`` / ``param_aliases``
      的归属、``as`` 键允许带值、``_`` = 空补丁）与 Tool 条目同构，
      规则本体见 :class:`flowing.tool.ToolEntry`；差异仅两点：
      ``specified`` 求值上下文为亲代 Agent 实例；注入表达式的求值结果
      落子 Agent 初始化参数（上一条）。
    - 深层具名块（``$subagents.<别名>.<字段>:`` 等）由装配层在绑定前
      填回：条目列表按别名精确寻址（未命中报 ``FormatError``，
      无下标语法）；含 ``as`` 的键只允许用别名段寻址；``_`` （PENDING）
      声明的空位可被块写入；末端已存在实际值 → ``FormatError`` （块只能
      写进映射键，不能整体替换条目）。块内可写的键为覆写声明名
      （``system_prompt`` / ``description`` / ``args`` 等）。
    - 省略 ``as`` 时别名由 :func:`flowing.parser.normalize_entries` 按
      引用形态推断（裸名如 ``payment``；限定名如 ``ns::payment`` 取
      name 段；路径形态经 :func:`flowing.paths.infer_name`），规则见该
      函数。
    - 同 alias 重复（``.fya`` ``subagents:`` 列表内，含推断撞名——如
      ``./a/payment`` 与 ``./b/payment`` 都推断出 ``payment``）→
      :class:`flowing.errors.EntryNameConflictError`——与 tool / skill
      绑定层统一 fail-fast，不做“后声明覆盖先声明”。例外（与 skills
      同构）：glob 展开命中与已显式声明条目规范名相同的同一资源时
      跳过（先解析显式条目，再展开 glob）；只有不同资源得出同别名才
      报错。glob 命中在进入本条判定前先经形态过滤（纯名字分析，见
      :func:`flowing.agent_registry._agent_glob_accept`——无入口目录 /
      对方显式标记 / 非资源后缀跳过并告警）。
    - 限定名引用与命名空间：条目支持 ``ns::规范名`` 形式（指向插件
      注册资源，只查注册表）。命名空间是内部身份标识，永不进入 LLM
      可见面——catalog 与调用名只暴露别名。冲突判定因而是 LLM 视角的：
      两个不同命名空间的同名资源（``@/a::payment`` 与 ``@/b::payment``、
      或插件注册资源）被同一 Agent 引用时必须给其一 ``as`` 别名，否则
      按同 alias 撞名抛 ``EntryNameConflictError``；不同 Agent 各引各的
      同名资源互不影响。
    - entry 不持有子 Agent 实例——实例的创建 / 复用由
      ``invoke_subagent`` 与 agent 池管理。

    .. seealso::

        - :class:`flowing.tool.ToolEntry` —— 同构的 Tool 绑定条目。
        - :meth:`flowing.agent.Agent.add_agent` —— 条目装配入口。
        - :meth:`flowing.agent.Agent.invoke_subagent` —— 经 ``resolve()``
          的唤起路径。
        - :meth:`flowing.parsable.Parsable.resolve` —— 覆写值的求值机制。
    """

    name_alias: str
    """别名——LLM 在 catalog 中看到的名字，也是 ``invoke_subagent()``
    引用本条目的名字（亲代 Agent 的条目表以别名为键）。多层具名块按
    别名寻址。
    """
    name_ori: str
    """规范类型名——惰性解析子 Agent 类的依据。支持 ``ns::规范名``
    限定名形式（指向插件注册资源，只查注册表）；命名空间是内部身份
    标识，永不进入 LLM 可见面（catalog / 调用名只暴露别名）。
    """
    override_system_prompt: Parsable | None = None
    """覆写声明的子 Agent system prompt；``None`` = 无覆写。由 ``.fya``
    的 ``system_prompt:`` 或编程式 body 写入；``_`` （PENDING）归一为
    ``None`` （空补丁：声明了覆写位但内容为空，从基底全量回填）。注意：
    当前版本子 Agent 创建时使用其类自身声明的 ``system_prompt``，本字段
    只保存覆写声明、尚未参与创建。
    """
    override_description: Parsable | None = None
    """覆写 LLM 看到的描述；``None`` 使用子类原描述。在
    ``catalog_view()`` 渲染 catalog 时以亲代 Agent 实例为上下文求值。
    """
    override_params: dict[str, dict[str, Any]] | None = None
    """参数局部覆写：``{规范参数名: {JSON Schema 关键字: 新值}}`` （如
    ``{"currency": {"default": "CNY"}}``）。应用点在 ``catalog_view()``
    的 ``<params>`` 生成（稀疏补丁：只写要改的关键字，未提及的从基底
    回填）。
    """
    specified: dict[str, Parsable] = field(default_factory=dict)
    """指定值初始化参数（LLM 不可见、不可被 LLM 覆盖）；``resolve()``
    以亲代 Agent 实例为上下文求值后，作为子 Agent 的初始化参数传入。
    两种值形态：固定值（``Parsable("USD")``）与注入表达式
    （``Parsable("{{ self.inject('key') }}")``——求值时沿 provide 链
    上溯，链断裂抛 ``MissingProvideError``）。默认空 dict。
    """
    param_aliases: dict[str, str] = field(default_factory=dict)
    """LLM 参数名 → 规范参数名（``catalog_view()`` 生成 ``<params>``
    时改名）。默认空 dict。
    """
    visible: bool = True
    """是否渲染进 catalog（LLM 可见）；``False`` 时 LLM 看不到该子
    Agent，但仍可编程式 ``invoke_subagent()`` 唤起。直接置 ``True`` /
    ``False`` 切换，下一次上下文组装生效（没有 ``ManagedList`` 那样的
    容器级停用 API；临时停用直接置 ``False``）。
    """

    def resolve(self, parent: Agent, args: dict[str, Any]) -> dict[str, Any]:
        """把 LLM 侧参数聚合为子 Agent 创建的完整 kwargs。

        .. rubric:: 功能介绍

        :meth:`flowing.agent.Agent.invoke_subagent` 内部调用：先把 LLM
        参数名经 ``param_aliases`` 映射回规范名，再以 ``specified``
        覆盖（求值上下文为亲代 Agent 实例：固定值直给；注入表达式经
        ``parent.inject(...)`` 沿 provide 链上溯）——``specified``
        优先级最高，可覆盖 LLM 传入的同名键。

        .. rubric:: 行为要点

        - 本方法只做聚合：产物直接作为子 Agent 的初始化参数传给
          ``create_subagent``。子 Agent 的 ``args_model`` 是 LLM 可见
          参数表（catalog ``<params>``）的来源，当前实现不据此校验
          聚合结果。
        - args 只传给子 Agent 一层——框架不继续向孙 Agent 透传；
          跨层共享改用 provide / inject。
        - :raises flowing.errors.MissingProvideError: 注入表达式中的
          key 沿 provide 链上溯不到任何提供者（调用时求值抛出）。

        .. rubric:: 使用示例

        .. code-block:: python

            entry = SubagentEntry(
                name_alias="pay", name_ori="payment",
                param_aliases={"sum": "amount"},
                specified={"currency": Parsable("CNY"),
                           "user_id": Parsable("{{ self.inject('user_id') }}")})
            resolved = entry.resolve(parent, {"sum": 100})
            # resolved == {"amount": 100, "currency": "CNY", "user_id": ...}
            # user_id 取自 provide 链上溯到的值（parent 为亲代 Agent 实例）

        .. seealso::

            - :meth:`flowing.tool.ToolEntry.resolve` —— Tool 侧同构聚合。
        """
        mapped: dict[str, Any] = {}
        for alias, value in args.items():
            mapped[self.param_aliases.get(alias, alias)] = value   # 别名映射回规范名
        for key, parsable in self.specified.items():
            mapped[key] = parsable.resolve(parent)   # specified 以亲代 Agent 实例上下文惰性求值后覆盖（固定值/注入表达式同路——注入表达式求值即 provide 链上溯）
        # 聚合结果直接作为子 Agent 初始化参数（args_model 校验未接线——
        # 见 docstring“行为要点”；创建管线只透传 kwargs 到 setup）
        return mapped

    def catalog_view(self, parent: Agent) -> dict[str, Any]:
        """为 catalog 模板预计算本条目的视图 dict。

        .. rubric:: 功能介绍

        Agent 每次组装上下文时对每个 ``visible=True`` 的条目调用本
        方法，产物进入
        :data:`DEFAULT_SUBAGENT_CATALOG_TEMPLATE` （或 Agent 级覆写模板）
        的 ``entries`` 上下文。预计算在 Python 侧完成，模板只负责排布。

        :return: ``{"name": 别名, "description": 已解析描述串, "params_xml": 已应用覆写 / 改名 / 排除规则后的参数段 XML}``。

        .. rubric:: 行为要点

        - ``description``：``override_description`` 非 ``None`` 时以它
          为值（以 ``parent`` 为上下文求值）；否则取子 Agent 类原
          ``description`` （缺失 / ``None`` → 空串）。
        - ``params_xml``：由子 Agent 类的 ``args_model`` 派生的 JSON
          Schema 生成——先应用 ``override_params`` 稀疏补丁（非法
          关键字报 ``FormatError``；未知参数名按新增参数处理），再排除
          ``specified`` 声明的参数（固定值与注入表达式对 LLM 隐藏），
          最后按 ``param_aliases`` 改名（改名撞名报 ``FormatError``）。
          无可见参数 → 空串；子 Agent 类无 ``args_model`` → 空串。
        - ``visible=False`` 的条目由调用方过滤，本方法不检查；直接对
          停用条目调用不报错，但产物无契约保证。
        - ``subagent-invoke`` 工具不随本条目自动可见——需用户显式声明
          （``tools:`` / ``add_tool("subagent-invoke")``）才进入
          ``Context.tools``；catalog XML 与工具条目是两个独立面。

        .. seealso::

            - :meth:`resolve` —— 唤起侧的参数聚合。
            - :data:`DEFAULT_SUBAGENT_CATALOG_TEMPLATE` —— 视图的
              消费模板。
            - :class:`flowing.context.Context` —— catalog 的最终归宿
              （system_prompt 段）。
        """
        cls = parent.get_agent_class(self.name_ori)   # 惰性解析 Agent 类（限定名只查注册表；路径形态走文件链，语义由 runtime 承载）
        if self.override_description is not None:
            description = str(self.override_description.resolve(parent))   # 以亲代 Agent 实例为上下文求值
        else:
            # 无覆写时回退子类原 description（Parsable，渲染上下文同样是亲代
            # Agent 实例——Agent.description 字段契约“实例创建前由亲代 Agent
            # 读取”）；缺失 / None → 空串
            raw_desc = getattr(cls, "description", None)
            if raw_desc is None:
                description = ""
            elif isinstance(raw_desc, Parsable):
                description = str(raw_desc.resolve(parent))
            else:
                description = str(raw_desc)
        return {"name": self.name_alias, "description": description,
                "params_xml": _entry_params_xml(self, cls)}


def _prop_type_text(prop: dict[str, Any]) -> str:
    """property schema → catalog ``<param>`` 的 type 文本。内部 API。

    ``type`` 直取（``[T, "null"]`` 可空列表形态滤掉 ``"null"`` 成员后
    ``|`` 连接）；pydantic ``Optional[...]`` 派生的 ``anyOf`` 形态同口径
    展开；皆无 → ``"any"``。
    """
    t: Any = prop.get("type")
    if isinstance(t, list):
        members = [str(x) for x in t if x != "null"]
        return "|".join(members) if members else "null"
    if t is None and isinstance(prop.get("anyOf"), list):   # pydantic Optional 形态
        members = [str(b.get("type")) for b in prop["anyOf"]
                   if isinstance(b, dict) and b.get("type") not in (None, "null")]
        return "|".join(members) if members else "any"
    return str(t) if t is not None else "any"


def _entry_params_xml(entry: SubagentEntry, cls: "type[Agent]") -> str:
    """生成 catalog 条目的 ``<params>`` 片段（无可见参数 → 空串）。内部 API。

    子类 ``args_model.model_json_schema()`` 派生的 properties 依次应用：
    ``override_params`` 稀疏覆写（:func:`flowing.params.apply_param_overrides`，
    非法关键字 fail-fast、未知参数名新增）→ 排除 ``specified`` 声明的参数
    （固定值与注入表达式同对 LLM 隐藏）→ ``param_aliases`` 改名
    （规范名 → LLM 别名；撞名 → ``FormatError``，与 tool 侧
    ``_apply_param_aliases`` 同口径）。

    排布格式（以“specified 排除”为硬契约）：
    ``<params><param name=".." type=".." required="true|false">[<description>..</description>]</param>...</params>``，无换行。
    """
    args_model = getattr(cls, "args_model", None)
    if args_model is None:
        return ""
    schema = args_model.model_json_schema()
    props: dict[str, dict[str, Any]] = {
        k: dict(v) for k, v in schema.get("properties", {}).items()}
    required: set[str] = set(schema.get("required", ()))
    if entry.override_params:
        props = apply_param_overrides(props, entry.override_params)
    for key in entry.specified:   # specified 参数（固定值/注入表达式）排除在 LLM 视图之外
        props.pop(key, None)
        required.discard(key)
    if entry.param_aliases:
        inverse = {canonical: alias for alias, canonical in entry.param_aliases.items()}
        renamed: dict[str, dict[str, Any]] = {}
        for key, prop in props.items():
            new_key = inverse.get(key, key)
            if new_key in renamed:   # 改名撞名 fail-fast（与 tool 侧同口径）
                raise FormatError(
                    f"parameter alias collision after mapping: {new_key!r} (subagent {entry.name_alias})")
            renamed[new_key] = prop
        props = renamed
        required = {inverse.get(k, k) for k in required}
    if not props:
        return ""
    parts: list[str] = []
    for pname, prop in props.items():
        text = (f'<param name="{_xml_escape(pname, {chr(34): "&quot;"})}" '
                f'type="{_prop_type_text(prop)}" '
                f'required="{str(pname in required).lower()}">')
        desc = prop.get("description")
        if desc:
            text += f"<description>{_xml_escape(str(desc))}</description>"
        parts.append(text + "</param>")
    return "<params>" + "".join(parts) + "</params>"


_logger = logging.getLogger("flowing.subagents")


def _expand_glob_entries(
    items: list[Any],
    *,
    naming: NamingRules,
    source_dir: Path,
    project_root: Path | None = None,
    glob_accept: "Callable[[Path], bool] | None" = None,
) -> "list[EntryRef]":
    """装配层的条目列表 glob 展开（“glob 显式优先”规则的落点）。

    内部 API，不属稳定契约——``.fya`` 装配层（compiler）对
    ``subagents:`` / ``tools:`` / ``skills:`` 条目列表统一调用。入参是
    规范化之前的原始 YAML 列表项（glob 模式的 ``raw`` 不具别名推断
    条件，不能先过 :func:`flowing.parser.normalize_entries`）。

    - 先收显式条目（原序透传，经 ``normalize_entries`` 规范化），再
      展开 glob 条目（引用串含 ``*`` / ``?`` / ``[``）；glob 模式支持
      ``./`` （相对 ``source_dir``）与 ``@/`` （相对 ``project_root``）
      前缀，每命中一条路径产一个条目（``raw`` 为命中路径的绝对形态
      字符串，别名经 ``normalize_entries`` → ``infer_name`` 推断；
      glob 条目带覆写映射时每个展开产物继承同一 body）。
    - 双空间并集：不含 ``/`` 的 glob 模式在路径展开之外**原样透传**
      一个名字模式条目（``raw`` 即模式串，``alias`` 与 ``raw`` 相同，
      占位用——装配运行期经 ``Agent._prepare_tool_refs`` /
      ``_prepare_agent_refs`` 按注册表键展开为逐条条目后才绑定，模式
      条目本身不进绑定流）；路径空间与名字空间的命中在运行期按解析
      身份（注册表键）判重合并。
    - 路径命中为 ``type: mcp`` 的工具组声明时产普通路径条目；骨架
      （未展开、无可执行 schema）在装配绑定段经
      ``ToolRegistry.expand_mcp`` 展开为逐工具条目（绑定段的统一行为，
      见 ``Agent._prepare_tool_refs``）。
    - ``project_root=None`` 时 ``@/`` 引用（显式条目与 glob 模式同样）
      → 内置 ``ValueError`` （显式报错，不静默退回 cwd——与
      ``ToolRegistry.get`` 的 ``@/`` 失败姿态一致）。
    - 同一资源跳过：glob 命中路径与已收条目（显式或先展开的 glob
      产物）解析后绝对路径相同 → 跳过，不报错；只有不同资源得出同
      别名才由下游 ``add_agent`` / ``add_tool`` 抛
      ``EntryNameConflictError`` （本层不做别名查重）。
    - 稳定序：glob 命中按路径排序。
    - ``glob_accept`` 命中过滤：提供时每个 glob 命中先经该回调判定
      （``tools:`` 传 ``flowing.tool.registry._tool_glob_accept``、
      ``subagents:`` 传 ``flowing.agent_registry._agent_glob_accept``）。
      两回调只做纯名字分析：目录探测本字段候选链（无入口跳过）；
      显式标记各纳各的（``*.tool.fya`` / ``*.agent.fya``）；其余
      ``.fya`` / ``.py`` 直接纳入——是否合法资源留给创建期 eager
      解析 fail-fast；其它后缀跳过。不通过的命中跳过并打 warning。
      缺省 ``None`` 不过滤（调用方自管命中面）。过滤只作用于 glob
      命中；显式条目不受影响（定点引用错误照常报错）。

    .. seealso:: :class:`SubagentEntry` 行为要点“同 alias 重复”的例外
        条款（规则文本的权威出处）。
    """
    from flowing.parser import EntryRef, normalize_entries   # 函数内 import：本模块头部对 parser 只留 TYPE_CHECKING 边

    # 失败姿态与 ToolRegistry.get 对齐：无 launch 上下文（project_root 缺失）
    # 时 @/ 引用（显式条目与 glob 模式同样）显式报错，不静默退回 cwd
    if project_root is None:
        for item in items:
            if isinstance(item, str):
                raw_str = item
            elif isinstance(item, Mapping) and len(item) == 1:
                raw_str = next(iter(item.keys()))
            else:
                continue   # 形态错误（含空映射）留给下游 normalize_entries 统一报
            if raw_str.startswith("@/"):
                raise ValueError(
                    "@/ entry expansion requires a launch context (project_root not provided)")
    root = project_root if project_root is not None else Path.cwd()   # 哑根：@/ 已在上方拒绝
    explicit_items: list[Any] = []
    glob_items: list[tuple[str, dict[str, Any]]] = []   # (模式, body)
    for item in items:
        if isinstance(item, str):
            raw_str, body = item, {}
        elif isinstance(item, Mapping) and len(item) == 1:
            raw_str, body = next(iter(item.items()))
        else:
            # 形态校验交给 normalize_entries 统一报（显式条目通道）
            explicit_items.append(item)
            continue
        if _GLOB_META.search(str(raw_str)):
            glob_items.append((str(raw_str), dict(body) if isinstance(body, Mapping) else {}))
        else:
            explicit_items.append(item)
    refs = normalize_entries(explicit_items, naming=naming)
    seen: set[Path] = {
        resolve_path(r.raw, project_root=root, source_dir=source_dir).resolve()
        for r in refs if classify_ref(r.raw) == "path"}
    for pattern_raw, body in glob_items:
        if pattern_raw.startswith("@/"):
            base, pattern = root, pattern_raw[2:]
        else:
            base, pattern = source_dir, pattern_raw.removeprefix("./")
        for hit in sorted(base.glob(pattern)):   # 稳定序
            resolved = hit.resolve()
            if glob_accept is not None and not glob_accept(hit):
                # 非本字段资源形态的命中（如 tools: 下的 impl.py / agent.fya /
                # 无入口子目录）：跳过并告警，不进条目流；显式条目不受影响
                _logger.warning(
                    "glob hit skipped (not a valid resource shape for this field): %s", hit)
                continue
            if resolved in seen:   # 与已收条目同一资源 → 跳过（glob 显式优先）
                continue
            seen.add(resolved)
            item: Any = {str(resolved): body} if body else str(resolved)
            refs.extend(normalize_entries([item], naming=naming))
        # 双轨：不含 / 的模式原样透传为名字模式条目（运行期按注册表键展开）
        if "/" not in pattern_raw:
            refs.append(EntryRef(raw=pattern_raw, alias=pattern_raw, body=body))
    return refs


DEFAULT_SUBAGENT_CATALOG_TEMPLATE: str = (
    '{% if entries %}<available_subagents>\n'
    '{% for e in entries %}<subagent><name>{{ e.name }}</name>'
    '<description>{{ e.description }}</description>{{ e.params_xml }}</subagent>\n'
    '{% endfor %}\n'
    '</available_subagents>{% endif %}'
)
"""内置默认子智能体 catalog 模板：把逐条 ``<subagent>`` 片段包进
``<available_subagents>``。

.. rubric:: 功能介绍

``<available_subagents>`` 渲染槽位的内置缺省值（与
:data:`flowing.plugins.skills.DEFAULT_CATALOG_TEMPLATE` 同一约定）：槽位
只收 Jinja2 模板字符串，不收 callable——定制即整体替换模板，本常量公开
供参考 / 派生。Agent 级覆写槽位为
:attr:`flowing.agent.Agent.subagent_catalog_template`。

.. rubric:: 模板上下文变量

- ``entries``：逐条预计算视图 dict 列表——每个元素是
  :meth:`SubagentEntry.catalog_view` 的产物：

  - ``name``：别名（``SubagentEntry.name_alias``）；
  - ``description``：已解析描述串（覆写求值在 Python 侧完成）；
  - ``params_xml``：已应用覆写 / 改名 / 排除规则后的参数段 XML
    （含前导 ``<params>`` 片段或空串）。

  预计算在 Python 侧完成，模板只负责排布（不在模板内
  ``.resolve()``——与 skills 模板的“模板内求值”不同，本模板的求值
  已前移到 ``catalog_view``）。
- ``agent``：亲代 Agent 实例。

.. rubric:: 行为要点

- 渲染经 Parsable TEMPLATE 语义（include 基准为亲代 Agent 的
  ``source_dir``）；渲染异常 fail-fast 上抛，不静默降级。
- 空列表经 ``{% if entries %}`` 渲染为 ``""`` （整块不注入；
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

.. seealso::

    - :attr:`flowing.agent.Agent.subagent_catalog_template` —— Agent 级
      覆写槽位。
    - :meth:`SubagentEntry.catalog_view` —— ``entries`` 元素的生产方。
    - :data:`flowing.plugins.skills.DEFAULT_CATALOG_TEMPLATE` —— 同
      约定的 Skill 侧缺省模板。
"""
