"""flowing.composables.validate —— JSON Schema 校验 Composable：回合最终回复与入站消息（可选、非默认）。

.. rubric:: 功能介绍

本模块提供两个 ``use_xxx()``，分别校验“出”与“入”两个方向的 JSON 负载：

- ``use_validate_turn_result()`` —— 回合最终回复校验：主回合每一次终态
  LLM 响应（``after_provider_gen``，``finish=True`` 且无工具调用块）到达
  时，把响应消息中的 ``TextBlock`` 拼接为最终文本，先 ``json.loads`` 再
  用挂载时经 :func:`flowing.params.schema_to_model` 编译好的 Pydantic
  模型 ``model_validate`` 校验；文本不是合法 JSON 或不符合 schema 都视为
  校验不通过，并按 ``on_fail`` 策略处置：``"steer"`` （默认）把
  ``response.finish`` 改写为 ``False`` 并经
  :meth:`flowing.agent.Agent.steer` 入队修复模板消息、同回合内层循环
  续跑修复；``"warn"`` 告警后放行；``"raise"`` 抛
  :class:`flowing.errors.OutputValidationError`，经回合异常通道上抛
  （``on_provider_error`` 照常分发，本回合以 ``"error"`` 结局终止，
  Agent 存活）。
- ``use_validate_enqueued_message()`` —— 入站消息校验：消息经
  ``on_enqueue`` 入队前，先按 ``kinds`` / ``source`` / ``match`` 三维
  过滤（AND 合取，全部满足才校验）；命中者把全部 ``TextBlock`` 拼接为
  文本、把每个 ``StructBlock`` 的 ``data`` 直校 Pydantic 模型，任一
  不通过即按 ``on_fail`` 策略处置：``"reject"`` （默认）抛
  :class:`flowing.errors.Intercepted` 拒绝入队；``"warn"`` 英文告警后
  放行；``"raise"`` 抛 :class:`flowing.errors.InputValidationError`，
  原样上抛至入队调用方（无回合异常通道兜底）。

两个函数都随 ``flowing`` 包发布但不自动启用，必须由 Agent 开发者在
``setup()`` 中显式调用。不调用的 Agent 不持有任何校验相关 handler 与
状态——行为与不调用时逐字节等价。

“什么算合规、不合规怎么办”都是策略：校验模型、过滤维度与三态处置
全部由本模块提供一份可整体替换的默认实现。替换方式同 ``use_retry``：
``remove_by_owner(by)`` 移除默认 handler 后自注册。

.. rubric:: 注册面清单（use_validate_turn_result）

- 启用方式：仅阶段二——``setup()`` 中调用
  ``use_validate_turn_result(self, ...)`` （恢复时 ``setup()`` 在新实例
  上执行，天然不叠加）。未启用时零开销：``after_provider_gen`` 链上无
  本函数 handler。
- 注册的资源：无 provide key、无工具注册、无 Agent 状态键、不改
  prompt 块。schema 缺省读 Agent 的 ``output`` 属性（ ``.fya`` 顶层
  ``output:`` 声明或实例属性赋值均可，只读探测不写入）、模板缺省读
  ``validate_output_error`` 属性（同样只读）。
- 声明的钩子点：无（本函数只往核心已有的钩子点挂 handler，纯挂载）。
- 挂载的钩子：``after_provider_gen["_turn"]`` 校验 handler（pattern
  注册：``match_on="by"``，字面量 ``"_turn"`` 精确匹配主回合来源；
  ``by`` 取函数参数，默认 ``"validate-turn-result"``）。
  ``remove_by_owner(by)`` 可整组移除。
- 作用域：只影响调用它的那一个 Agent 实例；修复消息随本回合批次挂树
  并持久化，本函数自身不持久化任何状态。

.. rubric:: 注册面清单（use_validate_enqueued_message）

- 启用方式：仅阶段二——``setup()`` 中调用
  ``use_validate_enqueued_message(self, ...)`` （恢复时 ``setup()`` 在
  新实例上执行，天然不叠加）。未启用时零开销：``on_enqueue`` 链上无
  本函数 handler。
- 注册的资源：无 provide key、无工具注册、无 Agent 状态键、不改
  prompt 块。schema 缺省读 Agent 的 ``input`` 属性（ ``.fya`` 顶层
  ``input:`` 声明或实例属性赋值均可，只读探测不写入）。
- 声明的钩子点：无（本函数只往核心已有的钩子点挂 handler，纯挂载）。
- 挂载的钩子：``on_enqueue`` 校验 handler（普通注册，无 pattern；
  ``by`` 取函数参数，默认 ``"validate-enqueued-message"``）。
  ``remove_by_owner(by)`` 可整组移除。
- 作用域：只影响调用它的那一个 Agent 实例；无计数、无重试、不持久化
  任何状态。

.. rubric:: 使用示例

.. code-block:: yaml

    # my-agent.fya —— 出方向最小启用：schema 读本文件顶层的 output:
    # 声明，不合规时同回合内 steer 修复（至多 3 次）
    output:
      answer:
        type: string

    ---
    $script:
    from flowing.composables import use_validate_turn_result


    async def setup(self):
        use_validate_turn_result(self)

.. code-block:: yaml

    # peer-inbox.fya —— 入方向最小启用：schema 读本文件顶层的 input:
    # 声明，只闸 peer 来源、glob 匹配 peer_*，不合规拒绝入队
    input:
      question:
        type: string

    ---
    $script:
    from flowing.composables import use_validate_enqueued_message


    async def setup(self):
        use_validate_enqueued_message(self, source="peer_*")

.. rubric:: 行为要点

- 校验口径统一：文本校验走同一条路径——全部 ``TextBlock`` 拼接（与
  :func:`flowing.agent.build_turn_result` 的 ``final_text`` 同口径）、
  ``json.loads`` 、挂载时编译的 Pydantic 模型 ``model_validate``；
  ``json.JSONDecodeError`` 与 ``pydantic.ValidationError`` 都视为校验
  不通过。入站方向另对每个 ``StructBlock`` 的 ``data`` 直校（已是
  JSON 兼容结构，无 ``json.loads`` 回解）。
- 校验诊断格式统一：JSON 解析失败使用
  ``Invalid JSON at line <行>, column <列> (char <位置>): <原因>`` 单行
  文本，不回放输入；Pydantic 失败按 ``- <字段路径>: <原因>; received <值>``
  逐行排列，嵌套字段用点分路径、数组用 ``[n]``、根路径用 ``root``。
  缺失字段不附输入值，其余输入值以紧凑 JSON 表示（非 JSON 值用 ``repr``），
  单值最多 120 字符；最多显示前 5 条错误，其余以
  ``... and N more errors`` 计数。诊断不含动态模型名、Pydantic URL 或完整
  输入回放，同一失败的正文在各 ``on_fail`` 策略中一致。入站 StructBlock
  失败在正文前加 ``StructBlock <zero-based-ordinal>:``，序号只在
  StructBlock 之间计算。
- schema 回落链同构：函数参数 → Agent 的同名属性（ ``output`` /
  ``input``， ``.fya`` 顶层声明落在实例扩展属性 ``_extra`` 上、经
  ``__getattr__`` 回退解析，与实例属性赋值等价可读；经 ``getattr``
  只读探测）→ 两者皆无则挂载时抛内置 ``ValueError`` （编程错误，
  fail-fast，在注册任何 handler 之前）。schema 在挂载时经
  :func:`flowing.params.schema_to_model` 编译一次，闭包复用；schema
  含非法关键字时编译即抛
  :class:`flowing.errors.FormatError` （同样在挂载期暴露）。
- 异常通道不同向：出方向（``use_validate_turn_result``）的 raise 策略
  经回合异常通道上抛，``on_provider_error`` 照常分发；入方向
  （``use_validate_enqueued_message``）的 raise 策略经 ``on_enqueue``
  分发链原样上抛至入队调用方——dispatch 对普通异常不捕获，入队通常
  发生在回合外，无回合异常通道、无 ``on_provider_error`` 兜底。
- 两函数各自独立：互不注册对方的 handler，可在同一 Agent 上同时
  挂载；入站校验只闸入队消息，不触碰回合内产生的 PROVIDER 响应
  （不进队列）。steer 修复消息与异步工具结果经 ``on_enqueue`` 入队，
  同样过闸——本模块不为其他钩子或工具做隐式排除，同挂
  ``use_validate_turn_result`` 时其 steer 修复消息会被入站校验审查，
  需要豁免是应用层的显式选择（挂载时用 ``match`` 谓词或 ``kinds`` /
  ``source`` 过滤）。

.. seealso::

    :func:`flowing.params.schema_to_model` —— 校验模型的编译入口。
    :class:`flowing.errors.OutputValidationError` —— 出方向 raise 策略
    的异常类型。
    :class:`flowing.errors.InputValidationError` —— 入方向 raise 策略
    的异常类型。
    :class:`flowing.errors.Intercepted` —— 入方向 reject 策略的阻断信号。
    :func:`flowing.composables.prompt_until.use_prompt_until` —— 同构的
    steer 续跑 Composable。
"""

from __future__ import annotations

import fnmatch
import json
import warnings
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from flowing.errors import InputValidationError, Intercepted, OutputValidationError
from flowing.message import MessageKind, StructBlock, TextBlock, ToolCallBlock
from flowing.parsable import Parsable
from flowing.params import _format_validation_errors, schema_to_model
from flowing.providers import ProviderResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from flowing.agent import Agent
    from flowing.message import Message

__all__ = ["use_validate_turn_result", "use_validate_enqueued_message",
           "DEFAULT_VALIDATE_TEMPLATE"]

DEFAULT_VALIDATE_TEMPLATE: str = (
    "The final response does not conform to the required output schema.\n"
    "Validation error:\n{{ error }}\n"
    "Please re-emit the final response as a single JSON object matching the schema."
)
"""默认修复提示模板（``template`` 回落链的末位；英文运行时文本）。

每次 steer 前经 Jinja2 现场渲染，上下文变量：``error`` （格式化校验
诊断正文）、``schema`` （挂载时解析出的 properties dict）、``attempt``
（本回合内第几次校验失败，从 1 起）、``max_retries``。``error`` 在
``Validation error:`` 标题后的独立行开始。

行为边界：这是策略的一部分，随 ``use_validate_turn_result`` 整体可被
替换（函数参数或 Agent 的 ``validate_output_error`` 属性优先）。

.. seealso:: :func:`flowing.composables.validate.use_validate_turn_result`
"""


def _check(text: str, model: Any) -> "str | None":
    """校验 JSON 文本并返回格式化诊断：``json.loads`` + Pydantic 模型校验。

    通过返回 ``None``；文本不是合法 JSON（``json.JSONDecodeError``）时
    返回含行列与字符位置的单行诊断，解析结果不符合模型
    （``pydantic.ValidationError``）时返回逐字段诊断正文。两个 ``use_xxx``
    的文本校验共用本助手，各处置策略复用同一份返回值。
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return (f"Invalid JSON at line {exc.lineno}, column {exc.colno} "
                f"(char {exc.pos}): {exc.msg}")
    try:
        model.model_validate(data)
    except ValidationError as exc:
        return _format_validation_errors(exc)
    return None


def _resolve_schema(schema: dict[str, dict[str, Any]] | None,
                    agent: "Agent", attr: str, func: str) -> dict[str, dict[str, Any]]:
    """schema 回落链：函数参数 → Agent 的同名属性（只读探测）→ fail-fast。

    ``attr`` 为 ``"output"`` （出方向）或 ``"input"`` （入方向）；两者
    皆无时抛内置 ``ValueError`` （编程错误，在注册任何 handler 之前）。
    """
    if schema is None:
        schema = getattr(agent, attr, None)
    if schema is None:
        raise ValueError(
            f"{func} requires a schema: pass schema=... or "
            f"define an {attr!r} attribute on the agent")
    return schema


def use_validate_turn_result(
    agent: "Agent",
    schema: dict[str, dict[str, Any]] | None = None,
    *,
    template: str | None = None,
    on_fail: Literal["steer", "warn", "raise"] = "steer",
    max_retries: int = 3,
    by: str = "validate-turn-result",
) -> None:
    """为单个 Agent 实例启用“回合最终回复 JSON Schema 校验”策略（可选、非默认）。

    .. rubric:: 功能介绍

    在 ``agent.hooks.after_provider_gen["_turn"]`` 上注册一个校验
    handler（pattern 注册：``match_on="by"``，``"_turn"`` 精确匹配主
    回合来源；owner 取 ``by`` 参数）。主回合终态响应（``finish=True``
    且无工具调用块、finish 未置位）到达时，把响应消息中的
    ``TextBlock`` 拼接为最终文本，经 ``json.loads`` + 挂载时编译的
    Pydantic 模型 ``model_validate`` 校验；不通过则按 ``on_fail``
    处置——``"steer"`` （默认）把 ``response.finish`` 置 ``False`` 并
    入队渲染后的修复模板消息，由回合内层循环 drain 进同一回合续跑；
    ``"warn"`` 告警后放行；``"raise"`` 抛
    :class:`flowing.errors.OutputValidationError`。

    校验模型在挂载时编译一次闭包复用；失败计数是 Agent 普通实例属性
    ``_validate_turn_result_failures``，挂载时初始化为 0，校验通过或超限
    放行时重置，不注册持久化状态。本函数是双层启用的阶段二入口，只能
    在 ``setup()`` （或实例存活期内的任意代码）中对已完成初始化的
    实例调用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # 1. 最小启用（.fya 声明形态）：schema 读本文件顶层的 output:
        # 声明，不合规时同回合内 steer 修复（至多 3 次）
        output:
          answer:
            type: string

        ---
        $script:
        from flowing.composables import use_validate_turn_result


        async def setup(self):
            use_validate_turn_result(self)

    .. code-block:: python

        # 2. 显式 schema + 告警策略
        async def setup(self) -> None:
            use_validate_turn_result(
                self,
                schema={"report": {"type": "string"}},
                on_fail="warn",
            )

        # 3. 自定义修复模板（Jinja2：error / schema / attempt / max_retries）
        async def setup(self) -> None:
            use_validate_turn_result(
                self,
                on_fail="steer",
                max_retries=2,
                template=(
                    "Attempt {{ attempt }}/{{ max_retries }}: {{ error }} "
                    "Re-emit as JSON."
                ),
            )

    .. rubric:: 行为要点

    - 校验口径：仅主回合终态响应（``after_provider_gen["_turn"]`` 且
      ``finish=True``、``message`` 非 ``None``、无 ``ToolCallBlock``、
      ``current_turn.finish_output`` 为 ``None``）；文本为响应消息全部
      ``TextBlock`` 的拼接。``json.JSONDecodeError`` 与
      ``pydantic.ValidationError`` 都视为校验不通过。
    - 诊断正文：JSON 解析失败生成含行列与字符位置的单行文本，Pydantic
      失败生成逐字段 bullet；steer / warn / raise 复用同一正文。
      :class:`flowing.errors.OutputValidationError` 的 ``error`` 字段只存
      正文，默认模板的 ``Validation error:`` 标题后换行再插入正文。
    - schema 回落链：参数 → Agent 的 ``output`` 属性（ ``.fya`` 顶层
      ``output:`` 声明落在实例扩展属性 ``_extra`` 上、经 ``__getattr__``
      回退解析，与实例属性赋值等价可读）→ 皆无则挂载时抛
      内置 ``ValueError`` （fail-fast）；schema 含
      :func:`flowing.params.schema_to_model` 不识别的关键字时挂载时抛
      :class:`flowing.errors.FormatError`。
    - template 回落链：参数 → Agent 的 ``validate_output_error`` 属性 →
      :data:`DEFAULT_VALIDATE_TEMPLATE`；每次 steer 前以
      ``error`` / ``schema`` / ``attempt`` / ``max_retries`` 为上下文
      现场渲染（``attempt`` 从 1 起）。
    - steer 与计数：未超 ``max_retries`` 时置 ``response.finish = False``
      并经 :meth:`flowing.agent.Agent.steer` 入队修复消息
      （``kind=EVENT`` / ``priority=STEER`` / ``source=by`` /
      ``tags=[by]``），同一回合内层循环 drain 续跑；超限后放行且计数
      重置，下一回合再失败从第 1 次重新计数。
    - warn：``warnings.warn`` 英文告警后放行；raise：抛
      :class:`flowing.errors.OutputValidationError`，回合以
      ``"error"`` 结局终止（``query()`` 等待者拿到
      ``status == "error"`` 的 :class:`flowing.agent.TurnResult`）。
    - 重复调用不做幂等去重：每次调用各叠加一个 handler（编译模型独立），
      每次挂载都会把 Agent 共享的计数属性重置为 0；
      ``remove_by_owner(by)`` 整组移除。
    - finish 路径自动跳过：``finish_output`` 已置位的回合不做文本校验；
      finish 的 ``.fya`` ``output:`` 声明与 ``agent.output`` 是相互
      独立的声明通道，框架不校验二者一致性，同时声明时需自行保持
      一致。走 finish 结构化交卷的 Agent 不需要本 Composable。

    :param agent: 目标 Agent 实例；标准用法是在 ``setup()`` 中传 ``self``。
    :param schema: JSON Schema 的 properties 形态（字段名 → property
        dict）；缺省读 Agent 的 ``output`` 属性（ ``.fya`` 顶层
        ``output:`` 声明或实例属性赋值均可），两者皆无抛
        ``ValueError``。
    :param template: 修复提示 Jinja2 模板；缺省读 Agent 的
        ``validate_output_error`` 属性，再缺省用
        :data:`DEFAULT_VALIDATE_TEMPLATE`。
    :param on_fail: 校验不通过的处置策略：``"steer"`` （默认，同回合
        修复续跑）/ ``"warn"`` （告警后放行）/ ``"raise"`` （抛
        :class:`flowing.errors.OutputValidationError`）。
    :param max_retries: steer 策略下单个回合内允许的最大修复次数，
        ``>= 0``，默认 3；计数从 1 起，仅 ``attempt <= max_retries``
        才 steer，``0`` 表示首次失败即放行。warn / raise 策略下
        该参数不生效。
    :param by: handler 的 owner 标识（短横线小写惯例），默认
        ``"validate-turn-result"``；``remove_by_owner(by)`` 按它整组
        移除，修复消息的 ``source`` / ``tags`` 也取它。
    :raises ValueError: ``on_fail`` 不在 ``{"steer", "warn", "raise"}``
        中、``max_retries < 0``、或参数与 ``agent.output`` 皆无
        schema——都在注册任何 handler 之前抛出（内置 ``ValueError``，
        编程错误）。
    :raises flowing.errors.FormatError: schema 含
        :func:`flowing.params.schema_to_model` 不识别的关键字
        （挂载时编译模型即暴露）。

    .. seealso::

        :func:`flowing.params.schema_to_model` —— 校验模型的编译入口。
        :meth:`flowing.agent.Agent.steer` —— 修复消息的入队通道。
        :class:`flowing.errors.OutputValidationError` —— raise 策略的
        异常类型。
    """
    # 先校验后注册：非法参数抛 ValueError，不产生任何注册副作用
    if on_fail not in ("steer", "warn", "raise"):
        raise ValueError("on_fail must be 'steer', 'warn' or 'raise'")
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")

    # schema 回落链：参数 → agent.output（对象级属性，只读探测）→ 挂载期
    # fail-fast（内置 ValueError，编程错误）；编译模型同样在挂载期一次
    # 完成，闭包复用；schema 含非法关键字时 schema_to_model 抛 FormatError
    schema = _resolve_schema(schema, agent, "output", "use_validate_turn_result")
    model = schema_to_model("ValidatedTurnOutput", schema)

    # template 回落链：参数 → agent.validate_output_error（只读探测）→
    # 模块级默认模板； Parsable 归一（同 use_prompt_until 的裸 str 归一），
    # 每次 steer 以 Mapping 为上下文基底现场渲染
    if template is None:
        template = getattr(agent, "validate_output_error", None)
    if template is None:
        template = DEFAULT_VALIDATE_TEMPLATE
    render = Parsable(template)

    # 失败计数：Agent 普通实例属性，挂载时初始化（不注册持久化状态）；
    # 校验通过或超限放行时重置为 0。计数从 1 起：首次失败为第 1 次，
    # attempt <= max_retries 才 steer，因此 max_retries=0 首次失败即放行
    agent._validate_turn_result_failures = 0

    async def _validate_after_provider_gen(
        agent: "Agent", response: ProviderResponse
    ) -> ProviderResponse:
        """校验 handler（内部 API，经 ``by`` 定位与移除）。

        只处理主回合终态响应（pattern 注册保证来源；``finish=False`` /
        ``message=None`` / 含 ``ToolCallBlock`` / ``finish_output`` 已置位
        的响应原样放行）；必须 ``return response``；仅 raise 策略会抛
        异常（``OutputValidationError``，经回合异常通道上抛）。
        """
        if not response.finish or response.message is None:
            return response   # 非终态 / abort 路径：放行
        if any(isinstance(b, ToolCallBlock) for b in response.message.content):
            return response   # 带工具调用块的响应不是最终回复：放行
        turn = agent.current_turn
        if turn is not None and turn.finish_output is not None:
            return response   # finish 结构化交卷路径：自动跳过，不做文本校验
        text = "".join(b.text for b in response.message.content
                       if isinstance(b, TextBlock))
        failure = _check(text, model)
        if failure is None:
            agent._validate_turn_result_failures = 0   # 校验通过：计数重置（按回合计数）
            return response
        if on_fail == "warn":
            warnings.warn(f"turn output failed schema validation: {failure}")
            return response   # 告警后放行，response.finish 不改写
        if on_fail == "raise":
            raise OutputValidationError(failure)
        # steer：计数加 1，未超 max_retries 置 finish=False 并入队修复消息
        agent._validate_turn_result_failures += 1
        attempt = agent._validate_turn_result_failures
        if attempt > max_retries:
            agent._validate_turn_result_failures = 0   # 超限放行并复位：下一回合再失败重新计数
            return response
        response.finish = False   # 回合内层循环续跑：下一轮 drain 修复消息
        rendered = render.resolve({
            "error": failure,
            "schema": schema,
            "attempt": attempt,
            "max_retries": max_retries,
        })
        await agent.steer(rendered, kind=MessageKind.EVENT,
                          source=by, tags=[by])
        return response

    # 唯一 handler：pattern 注册（match_on="by"，"_turn" 精确匹配主回合
    # 来源）；remove_by_owner(by) 整组移除
    agent.hooks.after_provider_gen["_turn"](
        _validate_after_provider_gen, by=by)


def use_validate_enqueued_message(
    agent: "Agent",
    schema: dict[str, dict[str, Any]] | None = None,
    *,
    on_fail: Literal["reject", "warn", "raise"] = "reject",
    kinds: "tuple[MessageKind, ...] | None" = None,
    source: str | list[str] | None = None,
    match: "Callable[[Message], bool] | None" = None,
    by: str = "validate-enqueued-message",
) -> None:
    """为单个 Agent 实例启用“入站消息 JSON Schema 校验”策略（可选、非默认）。

    .. rubric:: 功能介绍

    在 ``agent.hooks.on_enqueue`` 上注册一个校验 handler（普通注册，
    owner 取 ``by`` 参数）。每条消息入队前，先按 ``kinds`` /
    ``source`` / ``match`` 三维过滤（AND 合取，全部满足才进入校验，
    未通过过滤的消息原样放行）；命中者经 ``json.loads`` + 挂载时编译的
    Pydantic 模型 ``model_validate`` 校验，不通过则按 ``on_fail``
    处置——``"reject"`` （默认）抛
    :class:`flowing.errors.Intercepted` 拒绝入队；``"warn"`` 发出英文
    告警后放行；``"raise"`` 抛
    :class:`flowing.errors.InputValidationError`。

    校验模型在挂载时编译一次闭包复用；本函数无计数、无重试、不持久化
    任何状态。本函数是双层启用的阶段二入口，只能在 ``setup()`` （或
    实例存活期内的任意代码）中对已完成初始化的实例调用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # 1. 最小启用（.fya 声明形态）：schema 读本文件顶层的 input:
        # 声明，所有入队消息都过闸，不合规拒绝入队
        input:
          question:
            type: string

        ---
        $script:
        from flowing.composables import use_validate_enqueued_message


        async def setup(self):
            use_validate_enqueued_message(self)

    .. code-block:: python

        # 2. 只闸 peer 来源（glob 匹配 peer_*），schema 显式给出
        async def setup(self) -> None:
            use_validate_enqueued_message(
                self,
                schema={"question": {"type": "string"}},
                source="peer_*",
            )

        # 3. 告警策略 + kinds 限定 USER/PEER + 自定义谓词
        async def setup(self) -> None:
            use_validate_enqueued_message(
                self,
                on_fail="warn",
                kinds=(MessageKind.USER, MessageKind.PEER),
                match=lambda msg: not msg.tags or "raw" in msg.tags,
            )

    .. rubric:: 行为要点

    - 过滤（AND 合取）：``kinds`` 缺省不过滤 kind 维度，给了值则
      ``msg.kind`` 须在元组内；``source`` 缺省不过滤，给了值则归一为
      模式元组、用 ``fnmatch`` 命中任一模式即通过（``source=""`` 只匹配
      未填 ``source`` 的消息——外部投递消息 ``source`` 默认为空串）；
      ``match`` 非空则须 ``match(msg)`` 为真。任一维度不满足即原样
      ``return msg``，不做校验。三个维度全部缺省 = 所有入队消息都过闸：
      本函数不为其他钩子或工具做隐式排除，豁免是应用层的显式选择。
    - 校验口径：全部 ``TextBlock`` 拼接为文本（与
      :func:`flowing.agent.build_turn_result` 的 ``final_text`` 同口径），
      非空则 ``json.loads`` 再 ``model_validate``；每个 ``StructBlock``
      的 ``data`` 直校 ``model.model_validate`` （已是 JSON 兼容结构，
      无 ``json.loads`` 回解）。``json.JSONDecodeError`` 与
      ``pydantic.ValidationError`` 都视为不通过，收集首个格式化诊断正文；
      文本先失败即停止。StructBlock 失败在正文前加
      ``StructBlock <zero-based-ordinal>:``，序号只在 StructBlock 之间计算。
      消息无 ``TextBlock`` / ``StructBlock`` 负载（纯媒体等）放行。
    - 处置：reject / warn / raise 复用同一诊断正文。``warn`` 路径
      ``warnings.warn`` 英文告警（含诊断正文）后原样放行；``raise`` 路径抛
      :class:`flowing.errors.InputValidationError`（``error`` 字段为含
      StructBlock 定位上下文的诊断正文），异常经 ``on_enqueue`` 分发链
      原样上抛至入队调用方
      ——dispatch 对普通异常不捕获，入队通常发生在回合外，无回合异常
      通道、无 ``on_provider_error`` 兜底；
      ``reject`` （默认）路径抛 :class:`flowing.errors.Intercepted`
      （英文原因文本），消息不入队、队列不变。
    - schema 回落链：参数 → Agent 的 ``input`` 属性（ ``.fya`` 顶层
      ``input:`` 声明落在实例扩展属性 ``_extra`` 上、经 ``__getattr__``
      回退解析，与实例属性赋值等价可读）→ 皆无则挂载时抛内置
      ``ValueError`` （fail-fast）；schema 含
      :func:`flowing.params.schema_to_model` 不识别的关键字时挂载时抛
      :class:`flowing.errors.FormatError`。
    - 重复调用不做幂等去重：每次调用各叠加一个独立 handler（各自持有
      独立的编译模型）；``remove_by_owner(by)`` 整组移除。
    - 与框架内部消息的关系：回合内产生的 PROVIDER 响应不进队列、不经过
      ``on_enqueue``；异步工具结果与 steer 修复消息经 ``on_enqueue`` 入队
      （EVENT kind），缺省过滤下同样过闸——本函数不为其他钩子或工具做
      隐式排除，同挂 ``use_validate_turn_result`` 时其 steer 修复消息会被
      本函数审查，豁免须在挂载时显式给出（``kinds`` / ``source`` /
      ``match`` 过滤）。

    :param agent: 目标 Agent 实例；标准用法是在 ``setup()`` 中传 ``self``。
    :param schema: JSON Schema 的 properties 形态（字段名 → property
        dict）；缺省读 Agent 的 ``input`` 属性（ ``.fya`` 顶层
        ``input:`` 声明或实例属性赋值均可），两者皆无抛
        ``ValueError``。
    :param on_fail: 校验不通过的处置策略：``"reject"`` （默认，抛
        :class:`flowing.errors.Intercepted` 拒绝入队）/ ``"warn"``
        （英文告警后放行）/ ``"raise"`` （抛
        :class:`flowing.errors.InputValidationError`）。
    :param kinds: 消息 kind 过滤：``None`` （缺省）不过滤本维度，所有
        kind 都过闸；给出元组则只校验这些 kind。显式传空元组为编程错误，
        挂载期抛 ``ValueError``。
    :param source: 来源过滤：``None`` （缺省）不过滤本维度；``str``
        或 ``str`` 列表按 ``fnmatch`` 匹配消息的 ``source`` 字段，命中
        任一模式即通过。
    :param match: 自定义谓词 ``Callable[[Message], bool]``；非空时
        ``match(msg)`` 为真才进入校验。
    :param by: handler 的 owner 标识（短横线小写惯例），默认
        ``"validate-enqueued-message"``；``remove_by_owner(by)`` 按它
        整组移除。
    :raises ValueError: ``on_fail`` 不在 ``{"reject", "warn", "raise"}``
        中、``kinds`` 为空、或参数与 ``agent.input`` 皆无 schema——
        都在注册任何 handler 之前抛出（内置 ``ValueError``，编程错误）。
    :raises flowing.errors.FormatError: schema 含
        :func:`flowing.params.schema_to_model` 不识别的关键字
        （挂载时编译模型即暴露）。

    .. seealso::

        :func:`flowing.params.schema_to_model` —— 校验模型的编译入口。
        :class:`flowing.errors.InputValidationError` —— raise 策略的
        异常类型。
        :class:`flowing.errors.Intercepted` —— reject 策略的阻断信号。
    """
    # 先校验后注册：非法参数抛 ValueError，不产生任何注册副作用
    if on_fail not in ("reject", "warn", "raise"):
        raise ValueError("on_fail must be 'reject', 'warn' or 'raise'")
    if kinds is not None and not kinds:
        raise ValueError("kinds must not be empty")

    # schema 回落链：参数 → agent.input（对象级属性，只读探测）→ 挂载期
    # fail-fast（内置 ValueError，编程错误）；编译模型同样在挂载期一次
    # 完成，闭包复用；schema 含非法关键字时 schema_to_model 抛 FormatError
    schema = _resolve_schema(schema, agent, "input",
                             "use_validate_enqueued_message")
    model = schema_to_model("ValidatedEnqueuedInput", schema)

    # source 归一：None 不过滤本维度；str 归一为单元素元组；list 转元组
    source_patterns: "tuple[str, ...] | None" = None
    if source is not None:
        source_patterns = (source,) if isinstance(source, str) else tuple(source)

    async def _validate_on_enqueue(agent: "Agent", msg: "Message") -> "Message":
        """校验 handler（内部 API，经 ``by`` 定位与移除）。

        三维过滤（kinds / source / match）AND 合取，未通过过滤原样放行；
        warn 路径不抛异常，reject 路径抛 ``Intercepted``，raise 路径抛
        ``InputValidationError``（原样上抛至入队调用方）；handler 必须
        ``return msg``。
        """
        if kinds is not None and msg.kind not in kinds:
            return msg   # kind 不在闸口范围内：放行
        if source_patterns is not None and not any(
                fnmatch.fnmatch(msg.source, p) for p in source_patterns):
            return msg   # source 未命中任一模式：放行
        if match is not None and not match(msg):
            return msg   # 自定义谓词未通过：放行
        failure = None
        text = "".join(b.text for b in msg.content
                       if isinstance(b, TextBlock))
        if text:
            failure = _check(text, model)
        if failure is None:
            # StructBlock 的 data 已是 JSON 兼容结构：直校模型，不回解
            struct_blocks = (
                block for block in msg.content if isinstance(block, StructBlock))
            for ordinal, block in enumerate(struct_blocks):
                try:
                    model.model_validate(block.data)
                except ValidationError as exc:
                    failure = (f"StructBlock {ordinal}:\n"
                               f"{_format_validation_errors(exc)}")
                    break
        if failure is None:
            return msg   # 校验通过（或无 TextBlock/StructBlock 负载）：放行
        if on_fail == "warn":
            warnings.warn(
                f"enqueued message failed schema validation: {failure}")
            return msg   # 告警后放行
        if on_fail == "raise":
            raise InputValidationError(failure)
        raise Intercepted(
            f"enqueued message failed schema validation: {failure}")

    # 唯一 handler：on_enqueue 普通注册（该钩子点 match_on="name" 而
    # Message 无 name 字段，pattern 注册会在分发时 AttributeError）；
    # remove_by_owner(by) 整组移除
    agent.hooks.on_enqueue(_validate_on_enqueue, by=by)
