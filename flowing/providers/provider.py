"""Provider 机制层：抽象基类、调用产物、注册表与懒实例化（``flowing.providers.provider``）。

本模块承载 :class:`Provider` 抽象基类、调用产物 :class:`ProviderResponse` /
:class:`ProviderDelta`、条目配置 :class:`ProviderConfig`、内置测试替身
:class:`FakeProvider`、进程级 adapter 注册（:func:`register_provider` /
``_provider_adapters``）、Runtime 级懒实例化表 :class:`ProviderRegistry`
与 providers.yaml 加载器 :func:`load_provider_candidates`。

adapter 继承树的两个格式家族基类与内置厂商 adapter 分别在
:mod:`flowing.providers.openai` 与 :mod:`flowing.providers.anthropic`；
包级设计动机（显式继承树、懒创建、adapter/条目两层术语、providers.yaml
schema、异常分类、凭证安全边界）见 :mod:`flowing.providers` 包 docstring。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from flowing.context import Context
from flowing.errors import ProviderNameConflictError
from flowing.message import Message
from flowing.model import ModelConfig




class ProviderConfig(dict[str, Any]):
    """Provider 条目配置：构造时绑定的 dict 风格配置包。

    .. rubric:: 功能介绍

    单个 provider 条目（``providers.yaml`` 中的一个 key）解析后的全部
    字段容器。作为 :class:`Provider` 的初始化属性在构造时绑定，
    ``generate()`` 不再接收 config 参数；``get_credential()`` 默认从
    ``self.config.get("api_key")`` 读取。

    .. rubric:: 设计动机

    provider 配置在进程生命周期内静态（api_key / base_url 不变），因此
    在加载时完成 ``{{env.VAR}}`` 纯字符串替换后一次成型，无需 Parsable。
    继承 ``dict`` 而非自定义结构，是因为字段集合开放——``adapter`` /
    ``api_key`` / ``base_url`` 之外全是 adapter 自读字段，框架核心不
    解释。

    .. rubric:: 使用示例

    .. code-block:: python

        # 由 Runtime 的 provider 加载器构造；用户代码通常不直接实例化
        config = ProviderConfig({
            "adapter": "deepseek",
            "api_key": "sk-...",            # {{env.VAR}} 已在加载时替换
            "base_url": "https://proxy.company.com/deepseek",
        })

    .. rubric:: 行为规约

    - 前置条件：构造传入的 ``{{env.VAR}}`` 必须已被加载器替换完毕；
      替换失败（env 缺失）在加载期抛
      :class:`flowing.errors.MissingEnvironmentVariableError`，本类不会
      见到未替换的 ``{{env.`` 引用。
    - 不变量：实例化 Provider 后按只读对待；运行期修改 config 不属于
      支持的行为。
    - 非行为：不做 schema 校验；``adapter`` 键仅由加载器用于选类，
      Provider 实现不应依赖它存在。
    - 安全边界：含凭证，禁止写入消息、``_provided`` 与任何落盘文件。

    .. rubric:: 测试案例

    - 前置：``api_key: "{{env.MISSING_KEY}}"`` 且环境变量不存在。操作：
      加载 providers.yaml。期望：加载期抛
      ``MissingEnvironmentVariableError``，ProviderConfig 不产生。
    - 前置：字段值为 ``"{{other.x}}"``（非 ``{{env.`` 前缀）。操作：
      加载。期望：保持原样，不报错不替换。

    .. rubric:: 调用关系（审计）

    - 被调：``Provider.__init__``（懒创建链构造时绑定为初始化属性，
      每条目一次）
    - 实例化方：providers.yaml 加载器 :func:`load_provider_candidates`
      （S-03 裁决具名；加载时一次性构造，``{{env.VAR}}`` 替换完成后）

    .. seealso::

        :class:`Provider` 本配置的唯一消费方。
        :func:`register_provider` adapter 名到类的注册表。
    """
    ...


@dataclass
class ProviderDelta:
    """流式增量：``on_provider_delta`` 钩子的 value。

    .. rubric:: 功能介绍

    流式模式下 Provider 每产生一小段输出就产出一个 ``ProviderDelta``，
    由 ``Agent.provider_gen()`` 逐条累积进 ``Message.content`` 并转发给
    ``on_provider_delta`` 钩子——这是 delta 的**唯一观察点**。
    **非流式路径同样产生一条**：``stream=False`` 时 ``provider_gen()`` 在拿到
    完整响应后合成一条全量 delta dispatch（「增量」语义成立——从空到
    全量即一个增量），两种路径的 delta **数据格式完全一致**（用户
    裁决）；订阅者因此永远能依赖「每次 provider_gen 至少一条 delta」。

    .. rubric:: 设计动机

    流式被「provider_gen() 返回完整 ProviderResponse」的契约挡在逻辑 Turn
    循环之外；UI/观测层消费 delta 做实时渲染，Turn 循环、持久化、
    工具调用完全不感知流式。delta 本身 **volatile，不落盘**——落盘的
    是累积完成（或中断时 ``partial=True`` 保留）的 ``Message``。

    .. rubric:: 使用示例

    .. code-block:: python

        @on("on_provider_delta")
        def render(agent, delta: ProviderDelta) -> ProviderDelta:
            if delta.kind == "text" and delta.by != "_side":
                ui.write(delta.text)
            return delta

    .. rubric:: 行为规约

    - 期待行为：同一响应内 ``content_index`` 单调不减；同一 content
      block 的 delta 按序到达，拼接 ``text`` 即得该 block 完整文本。
    - ``by``：来源标记，由 ``provider_gen()`` 盖写（adapter 不填、无法伪造）——
      主 Turn 内调用为 ``"_turn"``，副线为 ``"_side"``，下划线开头为
      框架保留值；``on_provider_delta`` 钩子点以 ``match_on="by"``
      声明，handler 可按来源模式过滤注册。
    - 边缘情况：流式中 abort——已累积内容保留为 ``partial=True`` 的
      消息落盘，未到达的 delta 不再产生（取消点起不再 dispatch）。
    - 非行为：delta 不进消息树、不进 ``tree.jsonl``；钩子 handler 对
      delta 的改写不影响已累积内容（纯观察语义，调用时序保证：
      ``provider_gen()`` 丢弃 dispatch 返回值、不回写累积——改写单条 delta
      无意义，需改写走 ``after_provider_gen`` 改整条消息）。

    .. rubric:: 测试案例

    - 前置：订阅 ``on_provider_delta`` 后发起 ``provider_gen()``（默认
      ``stream=True``）。操作：流式返回 "hello world" 分两个 delta。
      期望：钩子收到两条 ``ProviderDelta``，最终 ``Message.content``
      文本为完整拼接。
    - 前置：``provider_gen(context, stream=False)`` → 期望：钩子恰好收到
      **一条**全量 delta，字段格式与流式路径一致。
    - 前置：注册 ``on_provider_delta["_side"]`` 的 pattern handler →
      操作：主 Turn 产生 delta → 期望：该 handler 不触发；
      ``side_query`` 时触发。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.provider_gen()``（流式路径逐条累积进
      ``Message.content``，每次 delta 到达；非流式路径合成单条全量
      delta）；``on_provider_delta`` 钩子 dispatch（每次 delta 到达，
      value 即本类，volatile 不落盘，见 ``flowing.hooks``）
    - 实例化方：``Provider.generate_stream()``（流式模式下每产出一段
      输出构造一条；默认实现将完整文本包成单条）；非流式路径的合成
      delta 由 ``Agent.provider_gen()`` 构造

    .. seealso::

        :meth:`Provider.generate_stream` delta 的生产方。
        :class:`flowing.message.Message` ``partial`` 字段的语义。
    """

    kind: str
    """delta 种类，开放字符串。内置约定 ``"text"`` / ``"thinking"``；
    adapter 可产出其他种类（如 ``"tool_use"``），消费方按种类过滤。
    """
    text: str
    """本 delta 携带的增量文本；非文本类 delta 允许为空字符串。
    """
    content_index: int
    """所属 content block 在消息 ``content`` 列表中的下标；多 block
    响应（文本 + 思考 + 工具调用）借此归位。从 0 起单调不减。
    """
    by: str | None = None
    """来源标记（``provider_gen()`` 盖写，adapter 不填）：主 Turn ``"_turn"``、
    副线 ``"_side"``；下划线开头为框架保留值。钩子过滤依据
    （``on_provider_delta`` 以 ``match_on="by"`` 声明）。
    """


@dataclass
class Usage:
    """一次模型调用的 token 用量：七个计数字段 + 原始字段。

    .. rubric:: 功能介绍

    :class:`ProviderResponse` 的组成部分，由各 adapter 从原始响应映射
    产出并归一到统一口径。消费方（成本、预算、统计、UI）只依赖计数
    字段，无需知道 provider。

    .. rubric:: 设计动机

    字段集 = 七个计数 + ``raw`` 兜底（S-13 修订，取代 spec-draft 07
    §21.6「cache 字段降为 raw」的旧裁决）：

    - cache 读/写为一等字段：它们是可求和的正交桶，且有真实消费方
      （成本拆分——cache 读与新鲜输入单价不同；缓存断裂遥测）。
      「不是所有 provider 都有 cache 概念」由**零填充**解决，不构成
      降级理由（kimi-code / pi 两个参考实现均如此）。
    - ``input`` 与 ``fresh_input`` 并存：``input`` 回答「总共读了
      多少」，``fresh_input`` 回答「其中多少是新算的」——计费与缓存
      分析各取所需。
    - ``reasoning`` 是 ``output`` 的**子集标注**，不是正交桶——参与
      聚合（「本 turn 累计推理 token」），但不参与下面的恒等式。
    - 恒等式由 adapter 归一保证（违反属 adapter 缺陷，框架不运行时
      校验）：

      ``input == fresh_input + cache_read + cache_write``

      ``total_tokens == input + output``

    .. rubric:: 使用示例

    .. code-block:: python

        # adapter 内部（Anthropic：原生 input_tokens 不含 cache）
        usage = Usage(
            input=resp.usage.input_tokens
                  + resp.usage.cache_read_input_tokens
                  + resp.usage.cache_creation_input_tokens,
            fresh_input=resp.usage.input_tokens,
            output=resp.usage.output_tokens,
            cache_read=resp.usage.cache_read_input_tokens,
            cache_write=resp.usage.cache_creation_input_tokens,
            reasoning=getattr(resp.usage, "reasoning_tokens", 0) or 0,
            total_tokens=0,   # 按恒等式 = input + output 回填
            raw={**resp.usage.__dict__},   # 原始字段全保留
        )

        # adapter 内部（OpenAI 系：prompt_tokens 含 cache 读）
        cached = resp.usage.prompt_tokens_details.cached_tokens
        usage = Usage(input=resp.usage.prompt_tokens,
                      fresh_input=resp.usage.prompt_tokens - cached,
                      cache_read=cached, ...)

        # 消费方（after_provider_gen 钩子：读响应消息上附着的 usage）
        cost = estimate(response.message.usage.fresh_input,
                        response.message.usage.output)

    .. rubric:: 行为规约

    - 期待行为：七个计数字段为非负 int；provider 无某概念（如无
      cache、不报告推理 token）时对应字段**零填充**——「未上报」
      信号只存在于整体层（``message.usage is None``），
      不做逐字段 None 区分。
    - 边缘情况：provider 未返回用量时 ``message.usage`` 为
      ``None``，不产生 ``Usage`` 实例；``raw`` 为空 dict 合法。
    - 非行为：框架核心不读 ``raw`` 做任何决策；``raw`` 中未文档化
      字段不属于稳定契约。
    - **存续面唯一**（S-13 裁决，单源化修订——用户确认：事实只存在
      一处）：``Message.usage``（PROVIDER 消息携带，随树落盘）是
      唯一权威；``TurnResult.token_usage`` 仍由 ``TurnContext.usages``
      累加器聚合（管线不变），但累加器持有的是消息上同一 ``Usage``
      对象的**引用**，不是第二处存储。框架核心不产生副作用——
      「未注入 Composable 则静默丢弃」的旧措辞废止：无消费者 ≠ 丢弃。

    .. rubric:: 测试案例

    - 前置：OpenAI 原始响应 ``prompt_tokens=10``（其中 cached 4）、
      ``completion_tokens=5``。操作：adapter 映射。期望：
      ``input == 10``、``fresh_input == 6``、``cache_read == 4``、
      ``output == 5``、``total_tokens == 15``、``raw`` 含原始全部
      字段。

    .. rubric:: 调用关系（审计）

    - 被调：``after_provider_gen`` 钩子消费方（成本 / 预算 / 统计
      Composable，每次 provider_gen 后，经 ``response.message.usage`` 读取）；
      ``flowing.agent`` 回合结果的 ``token_usage`` 字段
      （``Usage | None``，经 ``TurnContext.usages`` 累加器聚合，
      见 ``flowing.agent.build_turn_result``）；
      ``flowing.agent.Agent.estimate_context_tokens``（锚点实测值，
      每次估计时读取）
    - 实例化方：Provider adapter（每次 ``generate()`` 响应映射时，于
      adapter 实现内部构造并附着到 ``message.usage``）

    .. seealso::

        :class:`flowing.message.Message` 本结构的宿主（``usage`` 字段，
        唯一权威）。
    """

    input: int
    """全部输入 token。恒等式：``input == fresh_input + cache_read +
    cache_write``（adapter 归一保证）。
    """
    fresh_input: int
    """排除 cache 的新输入（实际新算的部分）；计费与缓存分析的主口径。
    """
    output: int
    """输出 token（``reasoning`` 是其子集标注）。
    """
    cache_read: int
    """缓存命中的输入；provider 无 cache 概念时零填充。
    """
    cache_write: int
    """缓存写入的输入；零填充规则同上。
    """
    reasoning: int
    """推理 token——``output`` 的子集标注，不重复计入 ``total_tokens``；
    provider 不报告时零填充。聚合语义为「本 turn 累计推理 token」。
    """
    total_tokens: int
    """总 token 数。恒等式：``total_tokens == input + output``。
    """
    raw: dict[str, Any] = ...
    """provider 返回的全部原始用量字段原样保留（未文档化字段不稳定）。
    行为边界：框架核心不依赖其内容；turn 聚合时不求和（聚合体为空
    dict——逐次原始字段的消费方走 ``after_provider_gen``）。参见 :class:`Usage`。
    """


@dataclass
class ProviderResponse:
    """单次模型调用的完整产物：Turn 循环与 Provider 之间的唯一契约。

    .. rubric:: 功能介绍

    ``Provider.generate()`` 的返回类型，也是 ``Agent.provider_gen()`` 的返回
    类型。无论底层是否流式，Turn 循环只见完整的 ``ProviderResponse``
    ——「provider_gen() 返回完整响应」是把流式挡在逻辑 Turn 循环外的契约。

    .. rubric:: 设计动机

    - ``finish: bool`` 取代旧 ``finish_reason: str`` 四值枚举：框架只
      定义「本 Turn 是否结束」一个布尔，原始停止原因（``end_turn`` /
      ``length`` 等）如需保留放 :attr:`provider_data`，不占独立字段。
    - abort 语义显式化：Turn 被取消时返回
      ``ProviderResponse(message=None, finish=False, cancelled=True)``
      **而非抛异常**——cancel 是正常终止，不是错误。
    - ``model`` 保持 ``str``：它是**响应侧记录**（实际响应的模型 ID，
      如 OpenRouter ``auto`` → ``anthropic/claude-sonnet-4-6``），不是
      请求侧规格，不回填 ``ModelConfig``。

    .. rubric:: 使用示例

    .. code-block:: python

        # adapter 正常返回（usage 不交给 Response——直接附着在消息上）
        msg = Message(kind=MessageKind.PROVIDER, content=blocks)
        msg.usage = usage   # 唯一权威落点：锚点机制载体，随消息落盘
        return ProviderResponse(
            message=msg,
            model=resp.model,
            finish=not has_tool_call,   # 默认准则：有 tool_call → False
            provider_data={"stop_reason": "end_turn"},
        )

        # provider_gen() 内 abort 检查命中（无需 adapter 参与）
        return ProviderResponse(message=None, finish=False, cancelled=True)

    .. rubric:: 行为规约

    各字段消费者（框架内的唯一权威分工）：

    - ``message``：Turn 循环追加进消息级树（``_append_message``），参与
      后续上下文组装；``None`` 时跳过 append。
    - ``usage``：**本结构不再携带**（单源化裁决，用户确认）——usage
      的唯一家是 ``message.usage``（adapter 构造时附着，随消息落盘）。
      钩子 / Composable 经 ``response.message.usage`` 读取（先判
      ``message`` 非 None）；回合聚合管线不变——``_run_turn`` 把消息上
      同一对象的引用追加进 ``TurnContext.usages``，由
      ``build_turn_result`` 求和。边缘代价：abort 于消息成形前
      （``message=None``）的已耗 token 框架内不留痕——逐次不漏的计费
      属插件策略，可在 adapter 层自行拦截。
    - ``model``：观测与计费记录；框架核心不据此做路由决策。
    - ``finish``：**provider 层**概念——「provider 完成了本次响应（无
      待执行 tool_call）」；是 turn 关闭的**原因之一**（另一原因是
      cancel/abort）。turn 关闭判断与 ``turn_end`` 写入在 agent 层
      （``_run_turn``），本字段不直接承担（S-14 裁决）。
    - ``cancelled``：``after_turn`` handler 据此区分正常结束与取消。
    - ``provider_data``：透明传递 provider 特有元信息；框架核心**不
      依赖其内容做任何决策**；未文档化字段不属于稳定契约。

    边缘情况与不变量：

    - 不变量：``cancelled=True`` 时 ``finish`` 保持 ``False``（中断的
      流式没有 finish——provider 从未完成；S-14 翻转旧不变量），且
      ``message`` 允许为 ``None``；其余路径 ``message`` 非 None。
    - ``finish`` 的得出方式**框架不强制**——adapter 可覆写判断逻辑；
      推荐默认准则：响应含 ``tool_call`` block → ``False``，其它
      （stop / length / error）→ ``True``。
    - 非行为：本结构不携带异常；错误一律以 :mod:`flowing.errors` 中的
      分类异常抛出，不包装进响应。

    .. rubric:: 测试案例

    - 前置：``_turn_abort`` 已置位。操作：``provider_gen()`` 开头检查。期望：
      返回 ``message=None, finish=False, cancelled=True``，Turn 循环
      跳过 append 并走 abort 收尾。
    - 前置：adapter 响应含两个 tool_call block。操作：``generate()``。
      期望：``finish is False``，Turn 循环进入工具调用循环。
    - 前置：OpenRouter ``model="auto"``。操作：``generate()``。期望：
      ``response.model`` 为实际响应模型 ID 字符串。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn``（Turn 循环消费
      ``message`` / ``finish``，每次 provider_gen 返回）；``after_provider_gen``
      钩子（可改写本结构，每次 provider_gen）；``after_turn`` handler（读
      ``cancelled`` 区分结局，每 Turn 收尾）
    - 实例化方：``Provider.generate()``（每次非流式调用，adapter 构造）；
      ``flowing.agent.Agent.provider_gen()``（abort 检查命中时直接构造
      ``message=None, finish=False, cancelled=True``；流式路径结束时由
      provider_gen 组装完整响应）

    .. seealso::

        :meth:`Provider.generate` 生产方。
        :meth:`flowing.agent.Agent.provider_gen` 消费方与双模式判定处。
        :class:`flowing.agent.TurnContext` 逻辑 Turn 的执行期载体。
    """

    message: Message | None
    """模型产生的消息（``kind=MessageKind.PROVIDER``，永远回合内产生、
    不进队列）。行为边界：abort 路径为 ``None``，调用方必须先判
    ``if response.message is not None`` 再 append。参见
    :class:`flowing.message.Message`。
    """
    finish: bool
    """**provider 层**概念：``True`` = provider 完成本次响应（无待执行
    工具调用）；``False`` = 有工具调用（进入工具循环后继续 provider_gen）
    或本次响应被中断（中断的流式没有 finish）。turn 是否关闭是
    agent 层判断（``finish or cancelled`` → ``Message.turn_end``，
    S-14 分层），本字段不直接承担。得出方式由 adapter 决定（推荐
    准则见类 docstring）。
    """
    model: str = ...
    """实际响应的模型 ID（响应侧记录，保持 ``str``）。行为边界：不要求
    与请求侧 ``ModelConfig.model`` 相同；不回填任何结构体。
    """
    cancelled: bool = ...
    """Turn 被取消标记。``True`` 时 ``finish`` 保持 ``False``（provider
    未完成，S-14）、``message`` 可为 ``None``；``after_turn`` 据此
    区分结局。缺省 ``False``。
    """
    by: str | None = None
    """来源标记（``provider_gen()`` 盖写，adapter 不填、无法伪造）：主 Turn
    ``"_turn"``、副线 ``"_side"``；下划线开头为框架保留值。
    ``after_provider_gen`` 钩子点以 ``match_on="by"`` 声明，handler 可按
    来源模式过滤注册。缺省 ``None``。
    """
    provider_data: dict[str, Any] = ...
    """provider 特有元信息（原始 stop_reason 等），透明传递。行为边界：
    框架核心不依赖其内容做决策；未文档化字段不稳定。缺省空 dict。
    """


class Provider(ABC):
    """Provider 抽象基类：一种 API 格式 / 服务商一个 adapter 类。

    .. rubric:: 功能介绍

    模型调用的执行端。每个 provider **条目**（``providers.yaml`` 的一个
    key，绑定唯一 API key 身份）懒创建一个本类实例——一条目一实例，
    首次 ``provider_registry.get(条目名)`` 时才实例化并缓存，对调用方
    透明。adapter 类经 :func:`register_provider` 全局（进程级）注册，
    同一 adapter 类可实例化多个条目实例（不同 config）。

    .. rubric:: 设计动机

    - **显式继承树**：格式差异写在子类覆写中，类型层可见，IDE 可推断；
      禁止 compat flags 与运行时格式检测（见模块 docstring）。
    - **config 是初始化属性**：``generate()`` 无 config 参数——配置
      在进程内静态，构造时绑定一次即可（取代旧
      ``generate(context, model: str, config)`` 签名：``model`` 从裸
      字符串改为 :class:`ModelConfig`，``config`` 从调用参数改为
      初始化属性）。
    - **懒创建**：零启动成本——配 20 个条目只用 1 个时，其余 19 个
      的连接池 / HTTP 会话成本完全不发生。

    .. rubric:: 使用示例

    .. code-block:: python

        @register_provider
        class DeepSeekProvider(OpenAICompletionsProvider):
            name = "deepseek"
            known_model_fields = frozenset({"thinking_budget"})

            def get_credential(self) -> str | None:
                return self.config.get("api_key")

    .. code-block:: yaml

        # providers.yaml —— 同一 adapter 多个 key = 多个条目
        deepseek-team:
          adapter: deepseek
          api_key: "{{env.DEEPSEEK_TEAM_KEY}}"
          base_url: https://proxy.company.com/deepseek

    .. rubric:: 测试替身建议模式

    框架内置最小 Fake 替身 :class:`FakeProvider`（实例化后注入
    ``generate_fn`` / ``stream_fn``，内置 ``received`` Spy 记录）——
    同构样板由它承载；带**领域逻辑**的替身仍推荐各测试文件自行
    子类化本类、经 :func:`register_provider` 以测试专属 adapter 名
    注册（import 期完成，见 ``conftest.py`` 惯例）。替身输出**可以
    依赖输入**——按「测试替身」分类，带逻辑的替身是 Fake（逻辑简单
    到一眼可验证者为佳），纯队列回放是 Stub/Scripted。常用逻辑模式
    （示意，字段细节见 :class:`ProviderResponse` /
    :class:`flowing.message.Message`）::

        async def generate(self, context, model):
            texts = [b.text for m in context.messages
                     for b in m.content if b.type == "text"]
            joined = " ".join(texts)

            # 1) 关键词分支：模拟领域应答
            if "退货" in joined:
                return ProviderResponse(message=_text_msg("请提供订单号"),
                                        model="fake", finish=True)

            # 2) 块计数：断言管线不丢块（回声输入结构）
            return ProviderResponse(
                message=_text_msg(f"收到 {len(context.messages)} 条 / "
                                  f"{sum(len(m.content) for m in context.messages)} 块"),
                model="fake", finish=True)

        # 3) 模式化 tool call：触发固定工具路径（如消息含 "查" 即调工具）
        #    content=[ToolCallBlock(id="t1", name="search", args={"q": joined})]
        #    finish=False（有 tool_call → 本 Turn 未结束）

        # 4) 有状态剧本：实例属性计数，前 N 次抛 RateLimitedError 后正常
        #    —— 驱动 use_retry 等路径；状态随条目实例缓存，测试间用
        #    独立 Runtime 隔离

    行为边界：替身同样受本类契约约束——不读写消息树、不落盘、异常
    归类为 :mod:`flowing.errors` 类型；记录收到的 ``context`` 供断言
    （Spy 成分）允许，但不要断言框架内部调用次序。

    .. rubric:: 行为规约

    - 不变量（懒创建）：实例化只发生在首次按条目名获取时；实例化后
      按条目名缓存，同条目后续获取返回同一实例。
    - 子类约束：必须定义类属性 ``name``（adapter 注册键，全局唯一）；
      ``api_format`` 由框架基类决定，子类通常不再改。
    - 非行为：Provider 不读 ``model_tag``、不感知标签映射；不做重试
      与 fallback（策略属于 ``use_retry()``）；不缓存响应。
    - 边缘情况：构造期**不应**建立网络连接（连接成本推迟到首次
      ``generate()``），以保证懒创建的零启动成本语义成立。

    .. rubric:: 测试案例

    - 前置：providers.yaml 配 20 个条目。操作：Runtime 初始化。期望：
      无任何 Provider 实例化（仅候选清单）；首次 ``get("x")`` 后仅
      ``"x"`` 一个实例存在。

    .. rubric:: 调用关系（审计）

    - 被调：``register_provider``（子类以其 ``name`` 注册进进程级注册表，
      import 期）；``flowing.agent.Agent.provider_gen()``（经
      ``provider_registry.get(model.provider)`` 懒取实例后调用
      ``generate()`` / ``generate_stream()``，每次 provider_gen）
    - 实例化方：``flowing.runtime.Runtime`` 懒创建链（首次按条目名
      ``get`` 时 ``adapter_cls(config)``，一条目一实例；承载者为
      :class:`ProviderRegistry`（C-04 裁决具名），挂在
      ``Runtime.provider_registry``）

    .. seealso::

        :class:`OpenAICompletionsProvider` / :class:`AnthropicMessagesProvider`
            两个框架基类（决定 ``api_format``）。
        :func:`register_provider` adapter 类注册入口。
        :class:`flowing.runtime.Runtime` ``provider_registry`` 的宿主。
    """

    name: ClassVar[str]
    """adapter 名，:func:`register_provider` 的注册键（如 ``"deepseek"``），
    全局唯一。功能与动机：providers.yaml 条目的 ``adapter`` 字段按它
    选类。行为边界：同名冲突须 ``override=True`` 见
    :func:`register_provider`；与 provider **条目名**是不同概念
    （条目名是身份标识，adapter 名是类型标识）。
    """
    api_format: ClassVar[str]
    """API 格式标识（如 ``"openai_completions"`` / ``"anthropic_messages"``），
    由框架基类决定。功能与动机：让格式差异在类型层可见；观测层可据此
    分组。行为边界：禁止在业务代码中对它做 ``if`` 分支（反模式）。
    """
    known_model_fields: ClassVar[frozenset[str]]
    """本 adapter 认识的模型元数据字段集合。功能与动机：加载模型条目时
    据此分流——认识的字段提升为 :class:`ModelConfig` 实例属性（可含
    Parsable），不认识的静默进 ``ModelConfig._extra``。行为边界：只
    声明不消费；基类缺省为空 frozenset。参见 :attr:`ModelConfig._extra`。
    """
    config: ProviderConfig
    """构造时绑定的条目配置（含凭证）。行为边界：按只读对待；凭证禁止
    外泄（不进消息 / ``_provided`` / 落盘）。参见 :class:`ProviderConfig`。
    """

    def __init__(self, config: ProviderConfig) -> None:
        """以条目配置构造 Provider 实例。

        .. rubric:: 功能介绍

        懒创建路径上的唯一构造入口：Runtime 的 provider 候选清单在首次
        按条目名获取时调用 ``adapter_cls(config)``。

        .. rubric:: 设计动机

        config 收敛为初始化属性后，``generate()`` 签名只剩
        ``(context, model)``——一次调用所需的全部静态信息在构造期已齐。

        .. rubric:: 使用示例

        .. code-block:: python

            provider = DeepSeekProvider(ProviderConfig({"api_key": "sk-..."}))

        .. rubric:: 行为规约

        - 前置条件：``config`` 已完成 ``{{env.VAR}}`` 替换（加载器保证）。
        - 后置条件：``self.config is config``；不建立网络连接、不发起
          任何 I/O（零启动成本不变量）。
        - 边缘情况：缺 ``api_key`` 不在构造期报错——凭证缺失在首次
          ``generate()`` 时以 ``AuthenticationError`` 报出（条目可能
          走 ``base_url`` 本地代理等无 key 形态）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：:class:`ProviderRegistry` ``.get()`` 懒创建（时机：首次
          按条目名获取，``adapter_cls(config)``，每条目一次）

        .. seealso::

            :class:`ProviderConfig` 配置容器。
        """
        self.config = config  # self.config is config；不建立网络连接（零启动成本不变量）

    @abstractmethod
    async def generate(
        self, context: Context, model: ModelConfig
    ) -> ProviderResponse:
        """非流式单次生成：一次请求，返回完整响应。

        .. rubric:: 功能介绍

        Provider 的核心契约方法。接收组装好的上下文与**已解析**的模型
        规格，发起一次 API 调用，返回完整 :class:`ProviderResponse`。

        .. rubric:: 设计动机

        「一次调用、完整响应」是把流式挡在逻辑 Turn 循环外的契约：Turn
        循环不需要知道底层是 SSE 还是一次性 JSON。无 ``config`` 参数
        （初始化属性），无 ``model: str`` 裸字符串（完整规格结构体）。

        .. rubric:: 使用示例

        .. code-block:: python

            response = await provider.generate(context, model)
            if response.finish:
                ...  # 逻辑 Turn 结束

        .. rubric:: 行为规约

        - 前置条件：``model`` 已经过 ``ModelConfig.resolve()``（全部字段
          静态化）；``context`` 由 ``_assemble_context()`` 现场组装。
        - 期待行为：把 ``context.system_prompt`` / ``context.tools`` /
          ``context.messages`` 映射为 API 请求；把 ``model`` 的元信息
          字段映射为请求参数（字段含义的唯一解释方）；按
          :class:`ProviderResponse` 的默认准则设置 ``finish``。
        - **usage 附着**：usage **不进入** ``ProviderResponse``——adapter
          把本次用量直接附着到 ``message.usage``（唯一权威落点，
          ``Agent.estimate_context_tokens`` 锚点机制的载体，随消息落盘）；
          provider 未返回用量时 ``message.usage`` 保持 ``None``。
          ``message`` 为 ``None``（abort）时本次用量无载体、不留痕
          （见 :class:`ProviderResponse` 类 docstring 的边缘代价声明）。
        - abort 语义：调用期间检测到 abort（如 HTTP 会话被
          ``Execution.cancel()`` 关闭）时，**返回**
          ``ProviderResponse(message=None, finish=False, cancelled=True)``
          而非抛异常——cancel 是正常终止。
        - 异常：底层错误必须归类为 :mod:`flowing.errors` 的明确类型上抛
          （分类表见模块 docstring）；本方法**不做兜底捕获、不重试**。
        - 非行为：不读写消息树、不落盘、不触发钩子（钩子在 Agent 层）；
          不解释 ``PromptBlock.cache`` 以外的任何框架字段——``cache``
          仅是给 adapter 的意图标记（如 Anthropic 据 ``cache="static"``
          设 ``cache_control``），缓存发生在 provider 侧。
        - 边缘情况：``context.messages`` 为空合法（纯系统提示调用）；
          provider 未返回用量时 ``message.usage`` 为 ``None``。

        :raises flowing.errors.ContextLengthError:
            上下文溢出。不可重试，且不经过 ``on_provider_error``，直接上抛。
        :raises flowing.errors.RateLimitedError:
            429 限流。可重试类（是否重试由 ``use_retry()`` 决定）。
        :raises flowing.errors.ServerError:
            5xx 服务端故障。可重试类。
        :raises flowing.errors.NetworkError:
            网络层失败。可重试类。
        :raises flowing.errors.ProviderTimeoutError:
            超时。可重试类。
        :raises flowing.errors.AuthenticationError:
            401/403 凭证问题。不可重试（不自愈）。
        :raises flowing.errors.InvalidRequestError:
            400 请求本身非法。不可重试。
        :raises flowing.errors.ContentPolicyError:
            内容安全策略拒绝。不可重试。

        .. rubric:: 测试案例

        - 前置：mock transport 返回 429。操作：``generate()``。期望：
          抛 ``RateLimitedError``，不自动重试。
        - 前置：``Execution.cancel`` 在请求途中置位。操作：``generate()``。
          期望：返回 ``cancelled=True`` 响应，不抛异常。

        .. rubric:: 调用关系（审计）

        - 调用：``Provider.get_credential()``（adapter 每次发起请求前
          读取凭证，时机见该方法 docstring）
        - 被调：``flowing.agent.Agent.provider_gen()``（非流式路径，每次
          provider_gen，注册 ``Execution(kind="request")`` 后发起）；
          ``Provider.generate_stream()`` 默认实现（未覆写的 adapter
          走流式时回退调用，每次流式调用）

        .. seealso::

            :meth:`generate_stream` 流式变体。
            :meth:`flowing.agent.Agent.provider_gen` 双模式判定与调用方。
        """
        ...

    def generate_stream(
        self, context: Context, model: ModelConfig
    ) -> AsyncIterator[ProviderDelta]:
        """流式生成：逐 delta 产出，由 ``provider_gen()`` 累积并转发。

        .. rubric:: 功能介绍

        流式变体。异步迭代器逐个产出 :class:`ProviderDelta`；``Agent
        .provider_gen()`` 在 ``stream=True``（默认值）时选择本方法，把 delta
        累积进一条 ``partial=True`` 的消息并逐条 dispatch 钩子，结束
        后返回完整 :class:`ProviderResponse`。

        .. rubric:: 设计动机

        流式是 Provider/provider_gen 层的事，与逻辑 Turn 循环解耦。基类提供
        **默认实现**：回退为调用 :meth:`generate` 并把完整文本作为单个
        delta 产出——任何 adapter 不重写本方法也能在流式模式下工作
        （UI 收到一次完整 delta）；支持真流式的 adapter 覆写本方法。

        .. rubric:: 使用示例

        .. code-block:: python

            async for delta in provider.generate_stream(context, model):
                print(delta.text, end="")

        .. rubric:: 行为规约

        - 期待行为（覆写方）：按到达顺序产出 delta；
          ``content_index`` 单调不减；abort 由 ``provider_gen()`` 在每个 delta
          之间检查，adapter 无需自查（但能提前终止时应终止迭代）。
        - 默认实现行为：等价于 ``generate()`` 成功后把 ``message`` 的
          文本内容包成一条 ``ProviderDelta(kind="text", ...)`` 产出。
        - 非行为：delta 不落盘、不进消息树；本方法不返回
          ``ProviderResponse``（完整响应由 ``provider_gen()`` 组装——组装时
          同样履行 usage 附着契约：adapter 末帧提取的 ``usage`` 只进入
          组装消息的 ``message.usage``，同 :meth:`generate`）。
        - 边缘情况：``side_query`` 初版不调用本方法（副线查询固定非
          流式）；迭代途中底层错误按 :meth:`generate` 的同一异常分类
          上抛。

        :raises flowing.errors.FlowingError:
            与 :meth:`generate` 相同的异常分类。

        .. rubric:: 测试案例

        - 前置：未覆写本方法的 adapter。操作：以流式模式 ``provider_gen()``。
          期望：``on_provider_delta`` 收到一条完整文本 delta，最终
          响应与非流式等价。

        .. rubric:: 调用关系（审计）

        - 调用：``Provider.generate()``（基类默认实现回退——未覆写的
          adapter 每次流式调用时；覆写 adapter 自行实现，不在此列）
        - 被调：``flowing.agent.Agent.provider_gen()``（流式路径：``stream=True``
          （默认值）时，每次 provider_gen；``side_query`` 固定非流式不调用本
          方法）

        .. seealso::

            :class:`ProviderDelta` delta 结构。
            :meth:`flowing.agent.Agent.provider_gen` 流式双模式判定规则。
        """
        # 基类默认实现：回退调用 generate()，把完整文本包成单条 delta 产出
        # （签名为同步 def 返回 AsyncIterator，骨架经内部异步生成器表达）
        async def _default_stream() -> AsyncIterator[ProviderDelta]:
            response: ProviderResponse = await self.generate(context, model)
            message = response.message
            if message is not None:
                text = "".join(
                    getattr(block, "text", "") for block in message.content
                )  # 非文本类 block 允许为空字符串贡献
                yield ProviderDelta(kind="text", text=text, content_index=0)
            # abort 路径（message=None）：不产生 delta，由 provider_gen() 组装完整响应

        return _default_stream()

    def get_credential(self) -> str | None:
        """读取本条目凭证。默认实现返回 ``self.config.get("api_key")``。

        .. rubric:: 功能介绍

        凭证读取的唯一入口，adapter 在每次发起请求前调用。

        .. rubric:: 设计动机

        凭证来源是 adapter 级差异（静态 key / OAuth / 实例元数据服务），
        因此留为可覆写点。初版仅提供 ``api_key`` 读取；OAuth、AWS
        credential chain、GCP ADC 初版不实现，仅约定由具体 Provider
        子类覆写本方法接入，不影响现有签名。

        .. rubric:: 使用示例

        .. code-block:: python

            class BedrockProvider(AnthropicMessagesProvider):
                def get_credential(self) -> str | None:
                    return self.config.get("aws_session_token") or super().get_credential()

        .. rubric:: 行为规约

        - 期待行为：返回当前可用凭证字符串；无凭证返回 ``None``（是否
          报错由请求路径决定，通常映射为
          :class:`flowing.errors.AuthenticationError`）。
        - 安全边界：返回值禁止写入消息、``_provided``、日志脱敏外输出
          与任何落盘文件。
        - 非行为：**不支持凭证刷新**——凭证静态绑定于构造期；过期/失效
          由请求路径报错（通常映射为
          :class:`flowing.errors.AuthenticationError`），框架不自动刷新，
          也不提供刷新钩子。

        .. rubric:: 测试案例

        - 前置：``config = {"api_key": "sk-x"}``。操作：
          ``get_credential()``。期望：返回 ``"sk-x"``；空 config 返回
          ``None``。

        .. rubric:: 调用关系（审计）

        - 调用：``ProviderConfig.get()``（读取 ``"api_key"``，每次调用）
        - 被调：adapter 请求路径（每次发起请求前，docstring 自述「凭证
          读取的唯一入口」；框架核心内未见其他调用方）

        .. seealso::

            :class:`ProviderConfig` 凭证的来源容器。
        """
        return self.config.get("api_key")


class FakeProvider(Provider):
    """内置测试替身：实例化后手动注入 ``generate_fn`` / ``stream_fn``。

    .. rubric:: 功能介绍

    框架内置的最小 Fake 替身（Fake = 带简单逻辑的测试替身，区别于纯
    队列回放的 Stub/Scripted——分类约定见 :class:`Provider` 的
    「测试替身建议模式」）。构造后给实例属性 ``generate_fn``（必需）与
    ``stream_fn``（可选）赋值，``generate()`` / ``generate_stream()``
    委托给注入函数执行；同时内置 Spy 成分：每次调用把收到的
    :class:`Context` 追加进 ``received`` 供断言。

    .. rubric:: 设计动机

    测试替身是**每个**测试文件都要造的东西，而形态高度同构（注入一个
    函数 + 记录输入）——内置一个最小实现消除重复样板，同时保留
    「领域逻辑替身自行子类化 :class:`Provider`」的推荐路径不变。
    注入点做成**实例属性**而非构造参数：测试的 arrange 阶段可以先
    创建替身挂进 Runtime，再按用例逐步换绑函数（同一实例服务多个
    断言阶段）。

    .. rubric:: 使用示例

    .. code-block:: python

        provider = FakeProvider()
        provider.generate_fn = my_generate   # async (context, model) -> ProviderResponse

        # 注入 Runtime（测试专属路径，二选一）：
        # a) 子类化 + register_provider，providers.yaml 条目 adapter 指向它；
        #    注入函数写成类属性（实例属性未设时解析到类属性）；
        # b) 直接把实例写入 runtime.provider_registry 的实例表（内部 API，
        #    仅测试使用，不属稳定契约）。

        await agent.query("你好")
        assert len(provider.received) == 1

    .. rubric:: 行为规约

    - 期待行为：``generate()`` 先把 ``context`` 追加进 ``received``，
      再 ``await self.generate_fn(context, model)`` 并原样返回其结果；
      ``generate_stream()`` 在 ``stream_fn`` 已注入时同样先记录再委托，
      未注入时走基类默认回退（经 ``generate()`` 包单条 delta——此时
      由 ``generate()`` 完成记录，不重复追加）。
    - **契约责任转移**：注入函数即 adapter 本体——异常归类为
      :mod:`flowing.errors` 类型、usage 附着到 ``message.usage``、
      ``finish`` 判定等 :class:`Provider` 契约由测试作者自负，框架不
      校验注入函数的输出合法性。
    - 非行为：``config`` 缺省为空 :class:`ProviderConfig`（测试无凭证
      概念）；不自建网络连接；``received`` 只追加不清理（清理由测试
      自行 ``provider.received.clear()``）。
    - 边缘情况：``generate_fn`` 未注入时调用 ``generate()`` → 抛
      :class:`flowing.errors.FlowingError`（显式失败防漏配；消息指明
      缺失的属性名）。``stream_fn`` 未注入**不**报错（基类回退即合理
      默认）。运行中途换绑 ``generate_fn`` 合法，下一次调用生效。

    .. rubric:: 测试案例

    - 前置：``FakeProvider()`` 未注入任何函数。操作：``await
      provider.generate(context, model)``。期望：抛 ``FlowingError``，
      消息含 ``generate_fn``。
    - 前置：已注入 ``generate_fn``。操作：连续两次 ``generate()``。
      期望：``received`` 长度 2，两次 ``context`` 按调用次序排列。
    - 前置：仅注入 ``generate_fn``，未注入 ``stream_fn``。操作：
      流式 ``provider_gen()``。期望：走基类回退，``on_provider_delta`` 收到
      一条完整文本 delta；``received`` 长度 1（不重复记录）。

    .. rubric:: 调用关系（审计）

    - 调用：注入的 ``generate_fn`` / ``stream_fn``（每次被调时）
    - 被调：同 :class:`Provider` 契约——``Agent.provider_gen()`` 经
      ``provider_registry`` 获取后调用（时机相同）
    - 实例化方：测试代码直接构造（唯一推荐路径；不经过 Runtime
      懒创建链，除非按上例 a) 子类化注册）

    .. seealso::

        :class:`Provider` 契约本体与测试替身分类约定。
        :class:`ProviderRegistry` 注入路径 b) 的宿主。
    """

    name: ClassVar[str]  # = "fake"

    generate_fn: Callable[[Context, ModelConfig], Awaitable[ProviderResponse]] | None
    """注入的非流式生成函数。功能与动机：替身行为的唯一注入点，实例化
    后随时赋值/换绑（arrange 与用例阶段可分）。行为边界：``None`` 时
    调用 ``generate()`` 抛 :class:`flowing.errors.FlowingError`；注入
    函数承担全部 Provider 契约责任（异常归类、usage 附着）。
    """
    stream_fn: Callable[[Context, ModelConfig], AsyncIterator[ProviderDelta]] | None
    """注入的流式生成函数（可选）。行为边界：``None`` 时
    ``generate_stream()`` 走基类默认回退（包 ``generate()`` 结果为单条
    delta），不算漏配。
    """
    received: list[Context]
    """Spy 记录：每次 ``generate()`` / ``generate_stream()`` 收到的
    ``context``，按调用次序追加。功能与动机：替代每个测试文件手写
    的记录样板。行为边界：只追加不清理；断言内容（不丢块、系统提示
    正确等）由测试自负——不断言框架内部调用次序。
    """

    def __init__(self, config: ProviderConfig | None = None) -> None:
        """构造 FakeProvider；``config`` 缺省为空 ``ProviderConfig()``。

        .. rubric:: 行为规约

        - 后置条件：``generate_fn`` / ``stream_fn`` 均为 ``None``，
          ``received`` 为空列表；不建立任何连接。
        - 边缘情况：``config=None`` 与显式传空 ``ProviderConfig()``
          等价（后者共享传入对象，前者自建新对象）。

        .. rubric:: 调用关系（审计）

        - 调用：``Provider.__init__()``（构造期一次）
        - 被调：测试代码直接构造（时机：arrange 阶段）
        """
        super().__init__(config if config is not None else ProviderConfig())
        self.generate_fn = None
        self.stream_fn = None
        self.received = []

    async def generate(
        self, context: Context, model: ModelConfig
    ) -> ProviderResponse:
        """委托注入的 ``generate_fn``；未注入抛 ``FlowingError``。

        .. rubric:: 行为规约

        - 期待行为：先 ``self.received.append(context)``（记录先于委托，
          注入函数抛异常时记录仍在），再 ``await self.generate_fn(...)``
          原样返回。
        - 异常：``generate_fn is None`` →
          :class:`flowing.errors.FlowingError`（消息含属性名）；注入
          函数抛出的异常不包装、直接上抛。

        .. rubric:: 调用关系（审计）

        - 调用：注入的 ``generate_fn``（每次调用）
        - 被调：``Agent.provider_gen()`` 非流式路径；基类
          ``generate_stream()`` 默认回退（``stream_fn`` 未注入的流式
          调用）
        """
        self.received.append(context)  # 记录先于委托：注入函数抛异常时记录仍在
        if self.generate_fn is None:
            from flowing.errors import FlowingError
            raise FlowingError(
                "FakeProvider.generate_fn 未注入（实例属性赋值后再调用 generate()）"
            )
        return await self.generate_fn(context, model)

    def generate_stream(
        self, context: Context, model: ModelConfig
    ) -> AsyncIterator[ProviderDelta]:
        """``stream_fn`` 已注入则委托；否则走基类默认回退。

        .. rubric:: 行为规约

        - 期待行为：``stream_fn is not None`` → 先记录 ``received`` 再
          返回 ``self.stream_fn(context, model)``；``None`` → 直接
          ``super().generate_stream(...)``（记录由回退路径上的
          ``generate()`` 完成，不重复追加）。

        .. rubric:: 调用关系（审计）

        - 调用：注入的 ``stream_fn`` 或 ``Provider.generate_stream()``
          基类回退（按注入与否二选一，每次流式调用）
        - 被调：``Agent.provider_gen()`` 流式路径
        """
        if self.stream_fn is not None:
            self.received.append(context)
            return self.stream_fn(context, model)
        return super().generate_stream(context, model)


_provider_adapters: dict[str, type[Provider]] = {}
"""进程级 adapter 注册表（adapter 名 → Provider 类；S-32 裁决具名）。
写入方仅 :func:`register_provider`（import 期）；读取方仅
:class:`ProviderRegistry` 的懒实例化（按 providers.yaml 条目的
``adapter`` 字段选类）。内部 API，不导出、不属稳定契约——adapter 类
是类型层资产故为进程级；条目实例属 Runtime（见 ``ProviderRegistry``）。
"""


class ProviderRegistry:
    """Runtime 持有的 Provider 懒实例化表（条目名 → Provider 实例）。

    .. rubric:: 功能介绍

    ``providers.yaml`` 的每个**条目**（绑定唯一 API key 身份）对应至多
    一个 Provider 实例：Runtime 初始化（Provider 扫描，懒）时构建候选
    清单（条目名 → ``(adapter 类, ProviderConfig)``——adapter 类已在
    扫描期经 ``_provider_adapters`` 解析，spec-draft 02 §3.6 /
    12 §32.3），本类在首次 :meth:`get` 时才 ``adapter_cls(config)``
    实例化并缓存——一条目一实例，对调用方透明。

    .. rubric:: 设计动机

    - **懒创建**：providers.yaml 可配 20 个条目，运行时只用一两个——
      实例化（含凭证读取）推迟到首次使用，Runtime 初始化零网络/零
      环境变量依赖。
    - **两张表分离**（C-04 / S-32 裁决）：adapter **类**是进程级资产
      （``_provider_adapters``，``register_provider`` 写入）；条目
      **实例**是 Runtime 级资产（本类，``Runtime.provider_registry``
      持有）。一个进程可有多个 Runtime，各自持有本类实例。

    .. rubric:: 使用示例

    .. code-block:: python

        provider = runtime.provider_registry.get("deepseek-main")

    .. rubric:: 行为规约

    - ``get(name)``：已缓存 → 返回缓存实例；未缓存 → 现场实例化并缓存
      后返回；**条目名不在候选清单 → ``KeyError``**（与
      ``Runtime.get_plugin`` 默认形态同口径，dict 语义快速失败，
      C-04 裁决）。注意本方法的 ``get`` 是「``[]`` 式取或建」，不是
      ``dict.get`` 的返回 ``None`` 语义。
    - 非行为：不做 adapter 自动发现（那是 Runtime 初始化触发 import
      的职责，见 :func:`register_provider`）；不校验 config（构造
      ``ProviderConfig`` 时已完成）；实例创建失败（缺凭证等）的异常
      原样上抛、**不缓存失败结果**（下次 ``get`` 重试）。
    - 边缘情况：同一 ``ProviderRegistry`` 上并发首次 ``get`` 同条目 →
      至多一个实例被缓存（重复构造的丢弃，以先缓存者为准）。
    - 测试案例：前置：候选清单含 ``"x"``；操作：两次 ``get("x")`` →
      期望：返回同一实例（``is`` 相等）；``get("ghost")`` 抛
      ``KeyError``。

    .. rubric:: 调用关系（审计）

    - 调用：Provider 子类构造 ``adapter_cls(config)``（时机：``get`` 首次
      命中条目，每条目一次）
    - 被调：``flowing.agent.Agent.provider_gen()``（时机：每次 provider_gen 的
      provider 懒获取，``provider_registry.get(model.provider)``）
    - 实例化方：``flowing.runtime.Runtime``（时机：provider 候选清单
      扫描完成后构造并赋给 ``provider_registry`` 属性）

    .. seealso::

        :func:`register_provider` —— adapter 类的进程级注册入口。
        :class:`Provider` —— 实例的基类与 ``config`` 绑定约定。
    """

    _candidates: dict[str, tuple[type[Provider], ProviderConfig]]
    """候选清单（条目名 → (adapter 类, 配置)），Runtime 扫描
    ``providers.yaml`` 的产物；adapter 类在扫描期经 ``_provider_adapters``
    解析（未知 adapter 名在扫描期报错，非本类职责）。
    """
    _instances: dict[str, Provider]
    """实例缓存（条目名 → 实例）；``get`` 的写入目标。
    """

    def __init__(self, candidates: dict[str, tuple[type[Provider], ProviderConfig]]) -> None:
        """
        .. rubric:: 调用关系（审计）

        - 调用：无（仅平行字段赋值）
        - 被调：``flowing.runtime.Runtime``（时机：候选清单扫描完成）
        """
        self._candidates = candidates
        self._instances = {}

    def get(self, name: str) -> Provider:
        """取条目实例（首次懒创建并缓存）；未知条目名抛 ``KeyError``。

        .. rubric:: 调用关系（审计）

        - 调用：Provider 子类构造 ``adapter_cls(config)``（时机：``get``
          首次命中该条目）；``KeyError`` raise（时机：条目名不在候选清单）
        - 被调：``flowing.agent.Agent.provider_gen()``（时机：每次 provider_gen）

        .. seealso:: :class:`ProviderRegistry` 类 docstring 的完整行为规约
        """
        if name in self._instances:
            return self._instances[name]
        adapter_cls, config = self._candidates[name]   # 未知条目名 → KeyError（C-04 裁决口径）；adapter 类已在扫描期解析
        instance = adapter_cls(config)   # 懒创建；失败不缓存
        self._instances[name] = instance
        return instance


def load_provider_candidates(
    path: Path,
) -> dict[str, tuple[type[Provider], ProviderConfig]]:
    """读 ``providers.yaml`` 构建 `ProviderRegistry` 候选清单（S-03 裁决具名）。

    .. rubric:: 功能介绍

    providers.yaml 加载器——此前多处 docstring 把职责归于一个无符号的
    「加载器」，本条将其具名。职责四件：读 yaml；``{{env.VAR}}`` 凭证
    替换（**非** Jinja2/Parsable——凭证不经模板引擎，见模块 docstring
    凭证口径）；每条目构造 `ProviderConfig`；条目的 ``adapter`` 名经
    ``_provider_adapters`` **扫描期**解析为 adapter 类（未知名 → 启动期
    报错，C-04 口径：不是 ProviderRegistry 的职责）。

    .. rubric:: 设计动机

    函数本身**不碰环境变量与默认路径**（``FLOWING_PROVIDERS_PATH`` /
    ``$FLOWING_CONFIG_HOME`` 的解析在调用方 `Runtime` 侧）——保持纯
    「路径 → 清单」，测试与 CI 诊断可直接调。

    :param path: providers.yaml 的已解析路径。
    :return: 条目名 → ``(adapter 类, ProviderConfig)``，即
      ``ProviderRegistry(candidates)`` 的构造实参。
    :raises KeyError: 条目的 ``adapter`` 名未注册（扫描期快速失败）。

    .. rubric:: 调用关系（审计）

    - 调用：``ProviderConfig`` 构造（每条目）；``_provider_adapters``
      查表（每条目 adapter 名解析）；yaml 读取与 ``{{env.VAR}}`` 替换
      （具体库/函数符号未见规约，属实现细节）
    - 被调：``flowing.runtime.Runtime.__init__``（时机：构造期 provider
      扫描，产物构造 ``ProviderRegistry``）

    .. seealso:: :class:`ProviderRegistry`、:func:`register_provider`
    """
    candidates: dict[str, tuple[type[Provider], ProviderConfig]] = {}
    # 逐条目：adapter_cls = _provider_adapters[条目 adapter 名]（未知名
    # 启动期报错）；config = ProviderConfig(...)（{{env.VAR}} 替换后的
    # 条目 dict）；candidates[条目名] = (adapter_cls, config)
    return candidates


def register_provider(
    cls: type[Provider] | None = None,
    *,
    override: bool = False,
) -> type[Provider] | Callable[[type[Provider]], type[Provider]]:
    """注册 Provider adapter 类的装饰器（进程级全局注册表）。

    .. rubric:: 功能介绍

    把 adapter 类登记进全局注册表，键为类的 ``name`` 类属性（如
    ``"deepseek"``）。Runtime 初始化 provider 候选清单时按
    ``providers.yaml`` 条目的 ``adapter`` 字段查本注册表选类。

    .. rubric:: 设计动机

    - **注册全局（进程级）**：与「一个进程可有多个 Runtime」兼容——
      adapter 类是类型层资产，条目实例才属于 Runtime。
    - **自动发现，无需第三方显式 import**：Runtime 初始化时 ① 检索
      ``FLOWING_PROVIDER_MODULES``（``:``/``;`` 分隔的模块路径）并
      import，触发其中的 ``@register_provider``；② 用
      ``pkgutil.iter_modules()`` 扫描 ``sys.path`` 上所有
      ``flowing_provider_*`` 式包名并 import——基于 ``sys.path`` 枚举，
      本地目录包与 editable 安装（``pip install -e``）同样覆盖，不
      要求 dist-info 发行版元数据。
    - **同名唯一，显式覆盖**：「同名不同类」无需求；确需替换内置
      adapter 时用 ``override=True``，语义明确且可告警。

    .. rubric:: 使用示例

    .. code-block:: python

        # my_provider_pkg/__init__.py（包名 flowing_provider_my 可被自动发现，
        # 或由 FLOWING_PROVIDER_MODULES=my_provider_pkg 显式引入）
        from flowing.providers import OpenAICompletionsProvider, register_provider

        @register_provider
        class MyProvider(OpenAICompletionsProvider):
            name = "my"

        @register_provider(override=True)   # 全局替换内置 "deepseek"
        class HardenedDeepSeek(DeepSeekProvider):
            name = "deepseek"

    .. rubric:: 行为规约

    - 期待行为：装饰器原样返回被装饰类（不包装、不子类化）；支持
      ``@register_provider`` 与 ``@register_provider(override=True)``
      两种形态。
    - 注册时机：全部注册发生在 import 期（装饰器执行）；自动发现在
      Runtime 首次构建 provider 候选清单前完成，此后**注册表运行时
      不可变**——运行期再调用本装饰器不改变任何已建 Runtime 的行为。
    - 自动发现隔离降级：``FLOWING_PROVIDER_MODULES`` 或
      ``flowing_provider_*`` 扫描命中的包 **import 抛错时警告并跳过**，
      不中断 Runtime 初始化（与插件加载的隔离降级哲学一致；
      M-53 裁决）。
    - 同名冲突：键已存在且 ``override=False`` → 报错；``override=True``
      → 后 import 者覆盖并产生警告；多个 override 按 import 顺序后者
      胜出（每次覆盖均警告）。
    - 前置条件：被装饰类必须定义非空的 ``name`` 类属性，且为
      :class:`Provider` 的子类。
    - 非行为：不实例化任何 Provider（实例化只发生在懒创建路径）；
      不读取 ``providers.yaml``。

    :raises ProviderNameConflictError:
        键已存在且未指定 ``override=True``（P3-14 裁决：冲突具名化，
        可精确 ``except``）。
    :raises ValueError:
        被装饰对象不是 ``Provider`` 子类 / 缺少非空 ``name``——import
        期作者笔误刻意用内置异常（豁免声明见 :mod:`flowing.errors`
        模块 docstring）。

    .. rubric:: 测试案例

    - 前置：已注册 ``name="deepseek"``。操作：``@register_provider``
      注册另一个 ``name="deepseek"`` 的类。期望：抛
      ``ProviderNameConflictError`` 且 ``e.name == "deepseek"``。
    - 前置：同上。操作：``@register_provider(override=True)``。期望：
      注册表键指向新类，并捕获到一条警告。
    - 前置：``FLOWING_PROVIDER_MODULES="pkg_a:pkg_b"``。操作：Runtime
      初始化。期望：两包被 import，其中的 adapter 均可在 providers.yaml
      中按 ``adapter`` 名引用。

    .. rubric:: 调用关系（审计）

    - 调用：无（仅写进程级全局注册表）
    - 被调：内置 adapter 类定义处（``flowing.providers.openai`` 与
      ``flowing.providers.anthropic`` 模块 import 期，
      随框架发布注册）；第三方扩展包 import 期（由
      ``flowing.runtime.Runtime`` 初始化时的自动发现触发——
      ``FLOWING_PROVIDER_MODULES`` 与 ``flowing_provider_*`` 扫描，
      时机：Runtime 首次构建 provider 候选清单前）；测试替身注册
      （conftest.py 惯例，仅测试）

    .. seealso::

        :class:`Provider` 被注册类的基类与 ``name`` 约定。
        :class:`flowing.runtime.Runtime` 自动发现与候选清单懒创建的宿主。
    """
    # 写入目标：进程级 adapter 注册表 _provider_adapters（S-32 裁决具名，见上）
    def _register(cls: type[Provider]) -> type[Provider]:
        # 前置条件：Provider 子类且定义非空 name 类属性，否则 ValueError
        if not (isinstance(cls, type) and issubclass(cls, Provider)):
            raise ValueError("...")
        if not cls.name:
            raise ValueError("...")
        existing = _provider_adapters.get(cls.name)
        if existing is not None and existing is not cls:
            # 同名冲突：override=False -> 具名报错（P3-14）；override=True -> 覆盖并产生警告
            if not override:
                raise ProviderNameConflictError(cls.name)
            import warnings

            warnings.warn("...")  # 每次覆盖均警告；多个 override 按 import 顺序后者胜出
        _provider_adapters[cls.name] = cls  # 登记进进程级注册表
        return cls  # 原样返回被装饰类（不包装、不子类化）

    if cls is None:
        return _register  # @register_provider(override=True) 形态
    return _register(cls)  # @register_provider 形态
