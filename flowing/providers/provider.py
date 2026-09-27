"""``flowing.providers.provider`` —— Provider 机制层：抽象基类、调用产物、注册与懒实例化。

.. rubric:: 功能介绍

本模块承载 Provider 侧机制层的核心符号：抽象基类 :class:`Provider`、
一次调用的完整产物 :class:`ProviderResponse` / 流式增量
:class:`ProviderDelta` / token 用量记录 :class:`Usage`、条目配置
:class:`ProviderConfig`、内置测试替身 :class:`FakeProvider`、adapter
注册装饰器 :func:`register_provider`、Runtime 级懒实例化表
:class:`ProviderRegistry` 与 providers.yaml 加载器
:func:`load_provider_candidates`。

adapter 继承树的三个格式家族基类与内置厂商 adapter 分别在
:mod:`flowing.providers.openai_completions`、
:mod:`flowing.providers.openai_responses` 与
:mod:`flowing.providers.anthropic_messages`；
包级契约（显式继承树、懒创建、adapter 与条目两层术语、providers.yaml
schema、异常分类、凭证安全边界）见 :mod:`flowing.providers` 包
docstring。
"""

from __future__ import annotations

import os
import re
import warnings

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ruamel.yaml import YAML  # 与 flowing.model 共用同一 yaml 库

from flowing.context import Context
from flowing.errors import ProviderNameConflictError
from flowing.message import ContentBlock, Message
from flowing.model import ModelConfig




class ProviderConfig(dict[str, Any]):
    """单个 provider 条目解析后的配置容器（``dict`` 子类）。

    .. rubric:: 功能介绍

    一个 provider 条目（``providers.yaml`` 中的一个 key）解析后的全部
    字段的容器。作为 :class:`Provider` 的初始化属性在构造时绑定；
    :meth:`Provider.generate` 不再接收 config 参数，adapter 在方法内经
    ``self.config`` 读取。默认凭证读取
    :meth:`Provider.get_credential` 从 ``self.config.get("api_key")``
    取值。

    字段集合是开放的：``adapter`` / ``api_key`` / ``base_url`` 之外的
    字段全部由 adapter 自行读取（如 Bedrock 的 ``aws_region``），框架
    核心不解释任何字段的含义。

    .. rubric:: 使用示例

    .. code-block:: python

        # 通常由 load_provider_candidates 在加载时构造；程序化装配时
        # 也可直接实例化
        config = ProviderConfig({
            "adapter": "deepseek",
            "api_key": "sk-...",        # {{env.VAR}} 已在加载时替换
            "base_url": "https://proxy.company.com/deepseek",
        })

    .. rubric:: 行为要点

    - 前置条件：构造传入的 ``{{env.VAR}}`` 引用必须已在加载期替换完毕；
      环境变量缺失在加载期替换为空串并告警，本类不会见到未替换的
      ``{{env.`` 前缀引用（空串凭证的后果是首次调用期 401 类错误，经
      ``on_provider_error`` 分发）。
    - 实例化 Provider 后按只读对待：运行期修改 config 不属于支持的行为。
    - 不做 schema 校验；``adapter`` 键只由加载器用于选类。
    - 安全边界：本配置含凭证，禁止写入消息、``_provided`` 与任何落盘
      文件（详见包 docstring“安全边界”）。

    .. seealso::

        :class:`Provider` 本配置的唯一消费方。
        :func:`register_provider` adapter 名到类的注册表。
    """
    ...


@dataclass
class ProviderDelta:
    """流式增量：``on_provider_delta`` 钩子收到的 value。

    .. rubric:: 功能介绍

    流式模式下，Provider 每产出一小段输出就产生一个本实例；
    ``Agent.provider_gen()`` 逐条把增量累积进 ``Message.content``，并把
    每条 delta 分发给 ``on_provider_delta`` 钩子——这是 delta 的唯一
    观察点。

    非流式路径同样产生一条：``stream=False`` 时 ``provider_gen()`` 在
    拿到完整响应后合成一条全量 delta 分发（从空到全量即一个增量），
    两种路径的 delta 数据格式完全一致。订阅者因此永远可以依赖“每次
    ``provider_gen()`` 至少收到一条 delta”。

    delta 本身是易失的：不落盘、不进消息树。落盘的是累积完成（或流式
    中断时以 ``partial=True`` 保留）的消息。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing import Agent, on

        class RenderAgent(Agent):
            @on("on_provider_delta")
            def _print_text(self, delta):
                if delta.kind == "text" and delta.by == "_turn":
                    print(delta.text, end="")

    .. rubric:: 行为要点

    - 同一响应内 ``content_index`` 单调不减；同一 content block 的
      delta 按到达顺序排列，按序拼接 ``text`` 即得该 block 的完整文本。
    - ``by`` 是来源标记，由 ``provider_gen()`` 盖写（adapter 不填、无法
      伪造）：主 Turn 内为 ``"_turn"``，副线查询为 ``"_side"``，下划线
      开头为框架保留值。``on_provider_delta`` 钩子点以
      ``match_on="by"`` 声明，handler 可按来源模式过滤注册。
    - 纯观察语义：钩子 handler 对 delta 的改写不影响已累积内容——
      ``provider_gen()`` 丢弃分发返回值、不回写累积。需要改写输出内容
      时走 ``after_provider_gen`` 钩子改整条消息。
    - 流式中断（abort）：已累积内容保留为 ``partial=True`` 的消息落盘；
      取消点起不再分发 delta。
    - ``usage`` / ``provider_data`` 仅末帧携带（其余帧为 ``None``），
      是流式路径下把它们并入组装响应消息的传输通道。

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
    """来源标记（``provider_gen()`` 盖写，adapter 不填）：主 Turn
    ``"_turn"``、副线 ``"_side"``；下划线开头为框架保留值。钩子过滤
    依据（``on_provider_delta`` 以 ``match_on="by"`` 声明）。
    """
    message_id: str | None = None
    """本 delta 所属 assistant 消息的 id（agent 在派发边界盖写，
    adapter 不填、无法伪造）。

    ``provider_gen()`` 在流式开始前预铸该 id，逐条 delta 携带；流
    结束后用同一 id 构造落盘消息——观察者从第一条 delta 起即可用它
    归组，且该 id 就是最终挂树消息的 id。与 :attr:`by` 同属 agent
    装饰字段，provider 层不感知消息 id。
    """
    signature: str | None = None
    """思考块的签名（仅 Anthropic 家族思考 delta 携带，其余恒 ``None``）。

    Anthropic 多轮回放要求 assistant 消息里的 thinking 块连同
    ``signature`` 原样带回，否则 API 拒绝。流式时签名随
    ``kind="thinking"`` delta 携带（content_block_start 即给全量）；
    ``provider_gen`` 累积思考块时把首见签名保留到最终
    ``ThinkingBlock.signature``。
    """
    usage: "Usage | None" = None
    """本次调用的最终用量，仅末帧携带（其余帧为 ``None``）。

    流式路径的完整响应由 ``provider_gen()`` 组装——adapter 在末帧把
    usage 附在本字段上，``provider_gen()`` 将其并入组装消息的
    ``message.usage``。非流式路径不经过本字段（usage 直接附着在
    :meth:`Provider.generate` 的响应消息上）。
    """
    block: ContentBlock | None = None
    """非文本类内容的完整块载体（如流式末端拼装完成的
    ``ToolCallBlock``）。

    文本/思考类 delta 经 ``text`` 逐段累积；工具调用等结构化内容无法
    从文本分片在 ``provider_gen()`` 侧无损重建，adapter 在流式末端以
    完整块形态交付（``provider_gen()`` 按 ``content_index`` 归位进组装
    消息）。携带本字段的 delta 其 ``text`` 为空字符串。
    """
    provider_data: dict[str, Any] | None = None
    """本次响应的 provider 特有元信息（至少含原始 ``stop_reason``），
    仅末帧携带（其余帧为 ``None``）。

    流式路径的完整响应由 ``provider_gen()`` 组装——adapter 在末帧把
    provider_data 附在本字段上，``provider_gen()`` 将其并入组装响应。
    非流式路径不经过本字段（:meth:`Provider.generate` 的响应直接携带
    ``provider_data``）。
    """


@dataclass
class Usage:
    """一次模型调用的 token 用量：七个计数字段 + 原始字段。

    .. rubric:: 功能介绍

    :class:`ProviderResponse` 的组成部分，由各 adapter 从原始响应映射
    产出并归一到统一口径。消费方（成本、预算、统计、UI）只依赖计数
    字段，无需知道 provider。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.providers import Usage

        usage = Usage(input=10, fresh_input=6, output=5,
                      cache_read=4, cache_write=0, reasoning=0,
                      total_tokens=15)
        assert usage.total_tokens == usage.input + usage.output
        assert usage.input == (usage.fresh_input + usage.cache_read
                               + usage.cache_write)

    .. rubric:: 行为要点

    - 七个计数字段为非负 ``int``；provider 无某概念（如无 cache、不
      报告推理 token）时对应字段零填充。“未上报”信号只存在于整体层
      （``message.usage is None``），不做逐字段 ``None`` 区分。
    - 恒等式由 adapter 归一保证（违反属 adapter 缺陷，框架不运行时
      校验）：``input == fresh_input + cache_read + cache_write``；
      ``total_tokens == input + output``。
    - ``reasoning`` 是 ``output`` 的子集标注，不是正交桶——参与聚合
      （“本 turn 累计推理 token”），但不重复计入 ``total_tokens``。
    - provider 未返回用量时 ``message.usage`` 为 ``None``，不产生
      ``Usage`` 实例；``raw`` 为空 dict 合法。
    - 唯一权威：``Message.usage`` （仅 PROVIDER 消息携带，随消息落盘）
      是本结构的唯一权威落点；``ProviderResponse`` 不携带 usage。
      回合层把每次成功调用响应消息上附着的 usage 追加进
      ``TurnContext.usages`` （持有同一对象的引用），收尾时逐字段求和
      为 ``TurnResult.token_usage`` （累加器为空时为 ``None``）——聚合
      总是发生，不因没有消费方而跳过。
    - 框架核心不读 ``raw`` 做任何决策；``raw`` 中未文档化字段不属于
      稳定契约。

    .. seealso::

        :class:`flowing.message.Message` 本结构的宿主（``usage`` 字段，
        唯一权威）。
    """

    input: int
    """全部输入 token。恒等式：``input == fresh_input + cache_read + cache_write``
    （由 adapter 归一保证）。
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
    provider 不报告时零填充。聚合语义为“本 turn 累计推理 token”。
    """
    total_tokens: int
    """总 token 数。恒等式：``total_tokens == input + output``。
    """
    raw: dict[str, Any] = field(default_factory=dict)
    """provider 返回的全部原始用量字段原样保留（未文档化字段不稳定）。
    行为边界：框架核心不依赖其内容；turn 聚合时不求和（聚合体为空
    dict——逐次原始字段的消费方走 ``after_provider_gen``）。参见
    :class:`Usage`。
    """


@dataclass
class ProviderResponse:
    """单次模型调用的完整产物：Turn 循环与 Provider 之间的唯一契约。

    .. rubric:: 功能介绍

    ``Provider.generate()`` 的返回类型，也是 ``Agent.provider_gen()``
    的返回类型。无论底层是否流式，Turn 循环只见完整的本结构——
    “``provider_gen()`` 返回完整响应”是把流式挡在逻辑 Turn 循环外的
    契约。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.message import Message, MessageKind, TextBlock
        from flowing.providers import ProviderResponse, Usage

        # adapter 的 generate() 内：构造消息并附着用量
        msg = Message(kind=MessageKind.PROVIDER,
                      content=[TextBlock(text="你好")])
        msg.usage = Usage(input=12, fresh_input=7, output=5,
                          cache_read=5, cache_write=0, reasoning=0,
                          total_tokens=17)
        response = ProviderResponse(message=msg,
                                    model="claude-sonnet-4-6",
                                    finish=True)
        assert response.message.usage.total_tokens == 17

        # provider_gen() 内 abort 检查命中（无需 adapter 参与）：
        # 不抛异常，返回取消响应
        response = ProviderResponse(message=None, finish=False,
                                    cancelled=True)

    .. rubric:: 行为要点

    - ``message``：模型产生的消息（``kind=MessageKind.PROVIDER``，永远
      回合内产生、不进队列），Turn 循环把它追加进消息级树；abort 路径
      为 ``None``，调用方必须先判 ``response.message is not None`` 再
      append。
    - ``usage``：本结构不携带 usage——usage 的唯一落点是
      ``message.usage`` （adapter 构造时附着，随消息落盘）。钩子 /
      Composable 经 ``response.message.usage`` 读取（先判 ``message``
      非 ``None``）。回合聚合管线不变：消息上同一对象的引用会被追加进
      ``TurnContext.usages``，收尾时求和为 ``TurnResult.token_usage``。
      边缘代价：abort 于消息成形前（``message=None``）的已耗 token
      框架内不留痕——逐次不漏的计费属插件策略，可在 adapter 层自行
      拦截。
    - ``model``：实际响应的模型 ID（响应侧记录），观测与计费用；不
      要求与请求侧 ``ModelConfig.model`` 相同，不回填任何结构体。流式
      路径（``Agent.provider_gen`` 累积 delta 后组装最终响应）由 Agent
      层填请求侧模型 ID；非流式路径填服务端返回的模型 ID。
    - ``finish``：provider 层概念——“provider 完成了本次响应（无待
      执行 tool_call）”。turn 是否关闭由 agent 层判断
      （``finish or cancelled`` → ``Message.turn_end``），本字段不直接
      承担。得出方式框架不强制，adapter 可覆写；推荐默认准则：响应含
      ``tool_call`` block → ``False``，其它（stop / length / error）
      → ``True``。
    - ``cancelled``：Turn 被取消标记。``True`` 时 ``finish`` 保持
      ``False`` （中断的流式没有 finish——provider 从未完成），且
      ``message`` 允许为 ``None``；其余路径 ``message`` 非 ``None``。
      ``after_turn`` handler 据此区分正常结束与取消。
    - ``provider_data``：透明传递 provider 特有元信息（原始
      ``stop_reason`` 等）；框架核心不依赖其内容做任何决策；未文档化
      字段不属于稳定契约。
    - 本结构不携带异常：错误一律以 :mod:`flowing.errors` 中的分类异常
      抛出，不包装进响应。

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
    """provider 层概念：``True`` = provider 完成本次响应（无待执行工具
    调用）；``False`` = 有工具调用（进入工具循环后继续 provider_gen）
    或本次响应被中断（中断的流式没有 finish）。turn 是否关闭由 agent
    层判断（``finish or cancelled`` → ``Message.turn_end``），本字段不
    直接承担。得出方式由 adapter 决定（推荐准则见类 docstring）。
    """
    model: str = ""
    """实际响应的模型 ID（响应侧记录，保持 ``str``）。行为边界：不要求
    与请求侧 ``ModelConfig.model`` 相同；不回填任何结构体。
    """
    cancelled: bool = False
    """Turn 被取消标记。``True`` 时 ``finish`` 保持 ``False`` （provider
    未完成）、``message`` 可为 ``None``；``after_turn`` 据此区分结局。
    缺省 ``False``。
    """
    by: str | None = None
    """来源标记（``provider_gen()`` 盖写，adapter 不填、无法伪造）：主
    Turn ``"_turn"``、副线 ``"_side"``；下划线开头为框架保留值。
    ``after_provider_gen`` 钩子点以 ``match_on="by"`` 声明，handler 可
    按来源模式过滤注册。缺省 ``None``。
    """
    provider_data: dict[str, Any] = field(default_factory=dict)
    """provider 特有元信息（原始 stop_reason 等），透明传递。行为边界：
    框架核心不依赖其内容做决策；未文档化字段不稳定。缺省空 dict。
    """


class Provider(ABC):
    """Provider 抽象基类：一种 API 格式 / 服务商一个 adapter 类。

    .. rubric:: 功能介绍

    模型调用的执行端。每个 provider 条目（``providers.yaml`` 的一个
    key，绑定唯一 API key 身份）懒创建一个本类实例——一条目一实例，
    首次 ``provider_registry.get(条目名)`` 时才实例化并缓存，对调用方
    透明。adapter 类经 :func:`register_provider` 进程级注册，同一
    adapter 类可实例化多个条目实例（不同 config）。

    .. rubric:: 设计要点

    - 显式继承树：格式差异写在子类覆写中，类型层可见，IDE 可推断；
      禁止 compat flags 与运行时格式检测（见包 docstring）。
    - config 是初始化属性：``generate()`` 无 config 参数——配置在进程
      内静态，构造时绑定一次即可。
    - 懒创建：零启动成本——配 20 个条目只用 1 个时，其余 19 个的
      连接池 / HTTP 会话成本完全不发生。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.providers import OpenAICompletionsProvider, register_provider

        @register_provider
        class MyProvider(OpenAICompletionsProvider):
            name = "my"

    .. code-block:: yaml

        # providers.yaml —— 同一 adapter 多个 key = 多个条目
        my-team:
          adapter: my
          api_key: "{{env.MY_TEAM_KEY}}"
          base_url: https://proxy.company.com/my

    .. rubric:: 测试替身建议

    内置最小测试替身 :class:`FakeProvider` （实例化后注入 ``generate_fn``
    / ``stream_fn``，内置 ``received`` 记录）覆盖最简单的同构形态；带
    领域逻辑的替身推荐各测试文件自行子类化本类、经
    :func:`register_provider` 以测试专属 adapter 名注册（import 期
    完成）。替身同样受本类契约约束：不读写消息树、不落盘、异常归类为
    :mod:`flowing.errors` 类型；记录收到的 ``context`` 供断言允许，
    但不要断言框架内部调用次序。

    .. rubric:: 行为要点

    - 懒创建：实例化只发生在首次按条目名获取时；实例化后按条目名缓存，
      同条目后续获取返回同一实例。
    - 子类约束：作为注册 adapter 的子类必须定义非空类属性 ``name``
      （adapter 注册键，全局唯一）；``api_format`` 由框架基类决定，
      子类通常不再改。
    - Provider 不读 ``model_tag``、不感知标签映射；不做重试与 fallback
      （策略属于 ``use_retry()``）；不缓存响应。
    - 构造期不建立网络连接（连接成本推迟到首次 ``generate()``），保证
      懒创建的零启动成本语义成立。

    .. seealso::

        :class:`OpenAICompletionsProvider` / :class:`AnthropicMessagesProvider`
            两个框架基类（决定 ``api_format``）。
        :func:`register_provider` adapter 类注册入口。
        :class:`flowing.runtime.Runtime` ``provider_registry`` 的宿主。
    """

    name: ClassVar[str]
    """adapter 名，:func:`register_provider` 的注册键（如 ``"deepseek"``），
    全局唯一。providers.yaml 条目的 ``adapter`` 字段按它选类。同名冲突
    须 ``override=True`` 见 :func:`register_provider`；与 provider 条目
    名是不同概念（条目名是身份标识，adapter 名是类型标识）。
    """
    api_format: ClassVar[str]
    """API 格式标识（如 ``"openai_completions"`` / ``"anthropic_messages"``），
    由框架基类决定。行为边界：禁止在业务代码中对它做 ``if`` 分支
    （格式差异应经子类覆写表达）。
    """
    known_model_fields: ClassVar[frozenset[str]]
    """本 adapter 声明的模型元数据字段名集合（如 ``thinking_budget``）。

    框架当前不读取本属性：模型条目字段的分流由 :mod:`flowing.model`
    的固定内建字段集决定，其余字段进 ``ModelConfig._extra``。保留为
    声明性扩展点，供未来消费方或第三方工具参考；基类缺省为空
    frozenset。
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

        .. rubric:: 使用示例

        .. code-block:: python

            from flowing.providers import DeepSeekProvider, ProviderConfig

            provider = DeepSeekProvider(ProviderConfig({"api_key": "sk-..."}))

        .. rubric:: 行为要点

        - 前置条件：``config`` 已完成 ``{{env.VAR}}`` 替换（加载器保证）。
        - 后置条件：``self.config is config``；不建立网络连接、不发起
          任何 I/O（零启动成本不变量）。
        - 缺 ``api_key`` 不在构造期报错——凭证缺失在首次 ``generate()``
          时以 ``AuthenticationError`` 报出（条目可能走 ``base_url``
          本地代理等无 key 形态）。

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

        Provider 的核心契约方法。接收组装好的上下文与已解析的模型规格，
        发起一次 API 调用，返回完整 :class:`ProviderResponse`。每次调用
        前由调用方（``Agent.provider_gen()``）负责解析模型字段与组装
        上下文；本方法不解释 ``PromptBlock.cache`` 以外的任何框架字段
        ——``cache`` 仅是给 adapter 的意图标记（如 Anthropic 据
        ``cache="static"`` 设 ``cache_control``），缓存发生在 provider
        侧。

        .. rubric:: 使用示例

        .. code-block:: python

            response = await provider.generate(context, model)
            if response.finish:
                ...   # provider 完成本次响应，逻辑 Turn 可结束

        .. rubric:: 行为要点

        - 前置条件：``model`` 已经过 ``ModelConfig.resolve()`` （全部字段
          静态化）；``context`` 由调用方现场组装。``context.messages``
          为空合法（纯系统提示调用）。
        - 期待行为：把 ``context.system_prompt`` / ``context.tools`` /
          ``context.messages`` 映射为 API 请求；把 ``model`` 的元信息
          字段映射为请求参数（字段含义的唯一解释方）；按
          :class:`ProviderResponse` 的默认准则设置 ``finish``。
        - usage 附着：usage 不进入 ``ProviderResponse``——adapter 把
          本次用量直接附着到 ``message.usage`` （唯一权威落点，随消息
          落盘）；provider 未返回用量时 ``message.usage`` 保持
          ``None``。``message`` 为 ``None`` （abort）时本次用量无载体、
          不留痕。
        - abort 语义：取消信号的检测在调用方
          （``Agent.provider_gen()``）以竞速完成——在途调用被 cancel 时
          ``asyncio.CancelledError`` 注入本方法的 await 点，adapter 不做
          捕获、原样透传（``CancelledError`` 继承 ``BaseException``，
          常规 ``except Exception`` 分类 catch 不会截获）；cancelled
          响应由调用方合成——cancel 是正常终止。
        - 异常：底层错误必须归类为 :mod:`flowing.errors` 的明确类型
          上抛（分类表见包 docstring）；本方法不做兜底捕获、不重试。
        - 不读写消息树、不落盘、不触发钩子（钩子在 Agent 层）。

        :raises flowing.errors.ContextLengthError:
            上下文溢出。与其它调用期异常一样经 ``on_provider_error`` 分发
            （默认策略不重试；压缩 / 换模型属 handler 职责）。
        :raises flowing.errors.RateLimitedError:
            429 限流。可重试类（是否重试由策略层如 ``use_retry()`` 决定）。
        :raises flowing.errors.ServerError:
            5xx 服务端故障。可重试类。
        :raises flowing.errors.NetworkError:
            网络层失败。可重试类。
        :raises flowing.errors.ProviderTimeoutError:
            调用超时。可重试类。
        :raises flowing.errors.AuthenticationError:
            401/403 凭证问题。不可重试（不自愈）。
        :raises flowing.errors.InvalidRequestError:
            请求本身非法。不可重试。
        :raises flowing.errors.ContentPolicyError:
            内容安全策略拒绝。不可重试。
        :raises flowing.errors.RequestTooLargeError:
            请求体字节超限（HTTP 413）。不可重试。
        :raises flowing.errors.QuotaExhaustedError:
            配额 / 余额耗尽。不可重试（内置 adapter 对 429 一律归类为
            ``RateLimitedError``，不区分配额耗尽——本类属分类体系，供
            能区分的 adapter 使用）。

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

        流式变体。异步迭代器逐个产出 :class:`ProviderDelta`；
        ``Agent.provider_gen()`` 在 ``stream=True`` （默认值）时选择本
        方法，把 delta 累积进一条 ``partial=True`` 的消息并逐条分发
        ``on_provider_delta`` 钩子，结束后返回完整
        :class:`ProviderResponse`。

        基类提供默认实现：回退为调用 :meth:`generate` 并把完整文本包成
        单个 delta 产出——任何 adapter 不覆写本方法也能在流式模式下
        工作；支持真流式的 adapter 覆写本方法。

        .. rubric:: 使用示例

        .. code-block:: python

            async for delta in provider.generate_stream(context, model):
                print(delta.text, end="")

        .. rubric:: 行为要点

        - 覆写方职责：按到达顺序产出 delta；``content_index`` 单调不减；
          abort 由 ``provider_gen()`` 在每个 delta 之间检查、并在等待
          下一 delta 期间竞速取消（在途 ``__anext__`` 被 cancel 时
          ``CancelledError`` 注入生成器的 await 点，迭代终止时
          ``provider_gen()`` 调用 ``aclose()``——两条路径都应让底层
          HTTP 流随之关闭），adapter 无需自查信号。
        - 默认实现行为：等价于 ``generate()`` 成功后把消息文本包成一条
          ``ProviderDelta(kind="text", ...)`` 产出；结构化 block（无
          ``text`` 属性者）逐块补发 ``block`` delta。
        - 本方法不返回 ``ProviderResponse`` （完整响应由 ``provider_gen()``
          组装——组装时同样履行 usage 附着契约：adapter 末帧提取的
          ``usage`` 只进入组装消息的 ``message.usage``，同
          :meth:`generate`）；delta 不落盘、不进消息树。
        - 副线查询（``side_query``）固定非流式，不调用本方法；迭代途中
          底层错误按 :meth:`generate` 的同一异常分类上抛。

        :raises flowing.errors.FlowingError:
            与 :meth:`generate` 相同的异常分类。

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
                # usage 与非文本块经末帧字段透传给 provider_gen() 的流式
                # 组装——纯文本响应仍恰好一条 delta（usage 附着其上），含
                # 结构化块时逐块补发 block delta；provider_data（含原始
                # stop_reason）同样经末帧透传，使流式组装响应的
                # provider_data 与非流式一致
                nontext = [b for b in message.content
                           if getattr(b, "text", None) is None]
                if not nontext:
                    yield ProviderDelta(kind="text", text=text, content_index=0,
                                        usage=message.usage,
                                        provider_data=response.provider_data)
                else:
                    yield ProviderDelta(kind="text", text=text, content_index=0)
                    for i, block in enumerate(message.content):
                        if getattr(block, "text", None) is None:
                            yield ProviderDelta(kind=block.type, text="",
                                                content_index=i, block=block)
                    yield ProviderDelta(kind="text", text="",
                                        content_index=len(message.content),
                                        usage=message.usage,
                                        provider_data=response.provider_data)
            # abort 路径（message=None）：不产生 delta，由 provider_gen() 组装完整响应

        return _default_stream()

    def get_credential(self) -> str | None:
        """读取本条目凭证；默认实现返回 ``self.config.get("api_key")``。

        .. rubric:: 功能介绍

        凭证读取的唯一入口，adapter 在每次发起请求前调用。凭证来源是
        adapter 级差异（静态 key / OAuth / 实例元数据服务），因此本
        方法留为可覆写点；默认实现只读 ``api_key``，其他凭证来源由
        具体 Provider 子类覆写本方法接入。

        .. rubric:: 使用示例

        .. code-block:: python

            class BedrockProvider(AnthropicMessagesProvider):
                def get_credential(self) -> str | None:
                    return (self.config.get("aws_session_token")
                            or super().get_credential())

        .. rubric:: 行为要点

        - 返回当前可用凭证字符串；无凭证返回 ``None`` （是否报错由请求
          路径决定，通常映射为
          :class:`flowing.errors.AuthenticationError`）。
        - 安全边界：返回值禁止写入消息、``_provided``、日志与任何落盘
          文件。
        - 不支持凭证刷新——凭证静态绑定于构造期；过期 / 失效由请求路径
          报错（通常映射为
          :class:`flowing.errors.AuthenticationError`），框架不自动
          刷新，也不提供刷新钩子。

        .. seealso::

            :class:`ProviderConfig` 凭证的来源容器。
        """
        return self.config.get("api_key")


class FakeProvider(Provider):
    """内置测试替身：实例化后手动注入 ``generate_fn`` / ``stream_fn``。

    .. rubric:: 功能介绍

    框架内置的最小测试替身（Fake = 带简单逻辑的测试替身，区别于纯队列
    回放的 Stub / Scripted）。构造后给实例属性 ``generate_fn`` （必需）
    与 ``stream_fn`` （可选）赋值，``generate()`` / ``generate_stream()``
    委托给注入函数执行；同时内置 Spy 成分：每次调用把收到的
    :class:`Context` 追加进 ``received`` 供断言。

    注入点做成实例属性而非构造参数：测试的 arrange 阶段可以先创建替身
    挂进 Runtime，再按用例逐步换绑函数（同一实例服务多个断言阶段）。
    带领域逻辑的测试替身推荐子类化 :class:`Provider` 并注册（见
    :class:`Provider`“测试替身建议”）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.context import Context
        from flowing.message import Message, MessageKind, TextBlock
        from flowing.model import ModelConfig
        from flowing.providers import FakeProvider, ProviderResponse

        provider = FakeProvider()

        async def my_generate(context, model):
            return ProviderResponse(
                message=Message(kind=MessageKind.PROVIDER,
                                content=[TextBlock(text="ok")]),
                finish=True, model="fake")

        provider.generate_fn = my_generate
        response = await provider.generate(
            Context(system_prompt=[], tools=[], messages=[]),
            ModelConfig(model="fake-model", provider="fake"))
        assert len(provider.received) == 1

    .. rubric:: 行为要点

    - ``generate()`` 先把 ``context`` 追加进 ``received``，再
      ``await self.generate_fn(context, model)`` 并原样返回其结果；
      ``generate_stream()`` 在 ``stream_fn`` 已注入时同样先记录再委托，
      未注入时走基类默认回退（经 ``generate()`` 包单条 delta——此时由
      ``generate()`` 完成记录，不重复追加）。
    - 契约责任转移：注入函数即 adapter 本体——异常归类为
      :mod:`flowing.errors` 类型、usage 附着到 ``message.usage``、
      ``finish`` 判定等 :class:`Provider` 契约由测试作者自负，框架不
      校验注入函数的输出合法性。
    - ``config`` 缺省为空 :class:`ProviderConfig` （测试无凭证概念）；
      不自建网络连接；``received`` 只追加不清理（清理由测试自行
      ``provider.received.clear()``）。
    - ``generate_fn`` 未注入时调用 ``generate()`` 抛
      :class:`flowing.errors.FlowingError` （显式失败防漏配，消息指明
      缺失的属性名）；``stream_fn`` 未注入不报错（基类回退即合理默认）。
      运行中途换绑 ``generate_fn`` 合法，下一次调用生效。

    .. seealso::

        :class:`Provider` 契约本体与测试替身分类约定。
        :class:`ProviderRegistry` 注入路径 b) 的宿主。
    """

    name: ClassVar[str]

    generate_fn: Callable[[Context, ModelConfig], Awaitable[ProviderResponse]] | None
    """注入的非流式生成函数：替身行为的唯一注入点，实例化后随时赋值 /
    换绑。``None`` 时调用 ``generate()`` 抛
    :class:`flowing.errors.FlowingError`；注入函数承担全部 Provider 契约
    责任（异常归类、usage 附着）。
    """
    stream_fn: Callable[[Context, ModelConfig], AsyncIterator[ProviderDelta]] | None
    """注入的流式生成函数（可选）。``None`` 时 ``generate_stream()`` 走
    基类默认回退（包 ``generate()`` 结果为单条 delta），不算漏配。
    """
    received: list[Context]
    """Spy 记录：每次 ``generate()`` / ``generate_stream()`` 收到的
    ``context``，按调用次序追加。只追加不清理（断言后由测试自行
    ``clear()``）；断言内容（不丢块、系统提示正确等）由测试自负。
    """

    def __init__(self, config: ProviderConfig | None = None) -> None:
        """构造 FakeProvider；``config`` 缺省为空 ``ProviderConfig()``。

        .. rubric:: 行为要点

        - 后置条件：``generate_fn`` / ``stream_fn`` 均为 ``None``，
          ``received`` 为空列表；不建立任何连接。
        - ``config=None`` 与显式传空 ``ProviderConfig()`` 等价（后者
          共享传入对象，前者自建新对象）。
        """
        super().__init__(config if config is not None else ProviderConfig())
        self.generate_fn = None
        self.stream_fn = None
        self.received = []

    async def generate(
        self, context: Context, model: ModelConfig
    ) -> ProviderResponse:
        """委托注入的 ``generate_fn``；未注入抛 ``FlowingError``。

        .. rubric:: 行为要点

        - 先 ``self.received.append(context)`` （记录先于委托，注入函数
          抛异常时记录仍在），再 ``await self.generate_fn(...)`` 原样
          返回。
        - ``generate_fn is None`` → :class:`flowing.errors.FlowingError`
          （消息含属性名）；注入函数抛出的异常不包装、直接上抛。
        """
        self.received.append(context)  # 记录先于委托：注入函数抛异常时记录仍在
        if self.generate_fn is None:
            from flowing.errors import FlowingError
            raise FlowingError(
                "FakeProvider.generate_fn not injected (assign the instance attribute before calling generate())"
            )
        return await self.generate_fn(context, model)

    def generate_stream(
        self, context: Context, model: ModelConfig
    ) -> AsyncIterator[ProviderDelta]:
        """``stream_fn`` 已注入则委托；否则走基类默认回退。

        .. rubric:: 行为要点

        - ``stream_fn is not None`` → 先记录 ``received`` 再返回
          ``self.stream_fn(context, model)``；``None`` → 直接
          ``super().generate_stream(...)`` （记录由回退路径上的
          ``generate()`` 完成，不重复追加）。
        """
        if self.stream_fn is not None:
            self.received.append(context)
            return self.stream_fn(context, model)
        return super().generate_stream(context, model)


_provider_adapters: dict[str, type[Provider]] = {}
"""进程级 adapter 注册表（adapter 名 → Provider 类）。

写入方仅 :func:`register_provider` （import 期）；读取方仅
:class:`ProviderRegistry` 的懒实例化（按 providers.yaml 条目的
``adapter`` 字段选类）。内部 API，不导出、不属稳定契约——adapter 类
是类型层资产故为进程级；条目实例属 Runtime（见
:class:`ProviderRegistry`）。
"""


class ProviderRegistry:
    """Runtime 持有的 Provider 懒实例化表（条目名 → Provider 实例）。

    .. rubric:: 功能介绍

    ``providers.yaml`` 的每个条目（绑定唯一 API key 身份）对应至多一个
    Provider 实例：Runtime 初始化（懒）时构建候选清单（条目名 →
    ``(adapter 类, ProviderConfig)``——adapter 类已在扫描期经
    ``_provider_adapters`` 解析），本类在首次 :meth:`get` 时才
    ``adapter_cls(config)`` 实例化并缓存——一条目一实例，对调用方透明。

    .. rubric:: 设计要点

    - 懒创建：providers.yaml 可配 20 个条目，运行时只用一两个——实例化
      推迟到首次使用，Runtime 初始化零网络成本。条目字段的
      ``{{env.VAR}}`` 在加载期替换，但环境变量缺失只告警降级为空串、
      不阻断加载（未用条目的环境变量不必齐备）。
    - 两张表分离：adapter 类是进程级资产（``_provider_adapters``，
      ``register_provider`` 写入）；条目实例是 Runtime 级资产（本类，
      挂在 ``Runtime.provider_registry``）。一个进程可有多个 Runtime，
      各自持有本类实例。

    .. rubric:: 使用示例

    .. code-block:: python

        provider = runtime.provider_registry.get("deepseek-main")

    .. rubric:: 行为要点

    - ``get(name)``：已缓存 → 返回缓存实例；未缓存 → 现场实例化并缓存
      后返回；条目名不在候选清单 → ``KeyError`` （dict 语义快速失败）。
      注意本方法的 ``get`` 是“取或建”语义，不是 ``dict.get`` 的返回
      ``None`` 语义。
    - 不做 adapter 自动发现（adapter 类由 :func:`register_provider` 在
      import 期登记，本类只查表）；不校验条目配置（配置在加载期解析
      完成）。
    - 实例创建失败（缺凭证等）的异常原样上抛、不缓存失败结果（下次
      ``get`` 重试）。
    - 同一条目重复 ``get`` 返回同一实例（``is`` 相等）。

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
        """构造懒实例化表。

        :param candidates: 候选清单（条目名 → ``(adapter 类, ProviderConfig)``），
            通常为 :func:`load_provider_candidates` 的产物。
        """
        self._candidates = candidates
        self._instances = {}

    def get(self, name: str) -> Provider:
        """取条目实例（首次懒创建并缓存）；未知条目名抛 ``KeyError``。

        :param name: provider 条目名（``providers.yaml`` 的 key）。
        :return: 该条目的 Provider 实例（首次调用时创建并缓存）。

        .. rubric:: 行为要点

        - 已缓存 → 直接返回；未缓存 → ``adapter_cls(config)`` 实例化并
          缓存后返回。
        - 条目名不在候选清单 → ``KeyError`` （不做 ``dict.get`` 式的
          ``None`` 返回）。
        - 实例化失败 → 异常原样上抛，失败结果不缓存（下次调用重试）。

        .. seealso:: :class:`ProviderRegistry` 类 docstring 的完整行为要点
        """
        if name in self._instances:
            return self._instances[name]
        adapter_cls, config = self._candidates[name]   # 未知条目名 → KeyError；adapter 类已在扫描期解析
        instance = adapter_cls(config)   # 懒创建；失败不缓存
        self._instances[name] = instance
        return instance


_ENV_REF_RE = re.compile(r"\{\{env\.([A-Za-z_][A-Za-z0-9_]*)\}\}")
"""``{{env.VAR}}`` 引用正则：仅匹配 ``env.`` 前缀，其余 ``{{...}}``
原样保留。"""


def _substitute_env(value: Any, *, entry: str) -> Any:
    """对字符串值做 ``{{env.VAR}}`` 全局替换；缺失替换为空串并告警。

    仅 ``str`` 类型值参与替换（全局替换）；环境变量缺失时替换为**空串**
    并 ``warnings.warn`` 告警（消息含变量名与条目名）——加载不中断，
    配多个条目只用一个时，其余条目的环境变量不必齐备。凭证不经
    Jinja2 / Parsable（凭证口径见包 docstring）。内部 API，不属稳定契约。
    """
    if not isinstance(value, str):
        return value

    def _sub(m: "re.Match[str]") -> str:
        var = m.group(1)
        if var not in os.environ:
            warnings.warn(
                f"environment variable {var!r} referenced by provider "
                f"entry {entry!r} is not set; substituting empty string",
                UserWarning, stacklevel=2)
            return ""
        return os.environ[var]

    return _ENV_REF_RE.sub(_sub, value)


def load_provider_candidates(
    path: Path,
) -> dict[str, tuple[type[Provider], ProviderConfig]]:
    """读 ``providers.yaml`` 构建 ``ProviderRegistry`` 候选清单。

    .. rubric:: 功能介绍

    providers.yaml 加载器。职责四件：读 yaml；做 ``{{env.VAR}}`` 纯字符串
    替换（非 Jinja2 / Parsable——凭证不经模板引擎，见包 docstring；环境
    变量缺失替换为空串并告警，不中断加载）；每条目构造
    :class:`ProviderConfig`；把条目的 ``adapter`` 名经进程级注册表解析为
    adapter 类（未知 adapter 名在扫描期报错，不是
    :class:`ProviderRegistry` 的职责）。

    本函数不碰环境变量与默认路径（``FLOWING_PROVIDERS_PATH`` /
    ``$FLOWING_CONFIG_HOME`` 的解析在调用方 Runtime 侧）——保持纯
    “路径 → 候选清单”，测试与 CI 诊断可直接调用。

    :param path: providers.yaml 的已解析路径。
    :return: 条目名 → ``(adapter 类, ProviderConfig)``，即
      ``ProviderRegistry(candidates)`` 的构造实参。
    :raises KeyError: 条目的 ``adapter`` 名未注册（扫描期快速失败）。

    .. rubric:: 行为要点

    - 文件不存在 → 空候选清单（Runtime 零配置启动容忍：没有
      providers.yaml 不等于启动失败）；空文件同样返回空清单。
    - 非 ``{{env.`` 前缀的 ``{{...}}`` 保持原样，不报错、不替换。
    - 非字符串字段值（如数字）不参与替换，原样保留。
    - ``{{env.VAR}}`` 引用的环境变量缺失 → 替换为空串并 ``warnings.warn``
      告警（消息含变量名与条目名），加载不中断；缺失凭证的实际后果
      （如 401）在该条目首次调用时经 ``on_provider_error`` 暴露。

    .. seealso:: :class:`ProviderRegistry`、:func:`register_provider`
    """
    candidates: dict[str, tuple[type[Provider], ProviderConfig]] = {}
    # 文件不存在 → 空候选清单（Runtime 零配置启动容忍：懒加载原则下
    # 没有 providers.yaml 不等于启动失败）
    if not Path(path).exists():
        return candidates
    data = YAML(typ="rt").load(Path(path).read_text(encoding="utf-8"))
    if not data:   # 空文件 → 空清单
        return candidates
    for entry_name, fields in dict(data).items():
        fields = dict(fields or {})
        # {{env.VAR}} 纯字符串替换（仅 str 值、全局替换；环境变量缺失
        # → 空串 + warnings.warn 告警，加载不中断）；非 {{env. 前缀的
        # {{...}} 保持原样
        resolved = {k: _substitute_env(v, entry=str(entry_name))
                    for k, v in fields.items()}
        # adapter 名扫描期解析为类（未知名 → KeyError 快速失败）
        adapter_cls = _provider_adapters[resolved["adapter"]]
        candidates[str(entry_name)] = (adapter_cls, ProviderConfig(resolved))
    return candidates


def register_provider(
    cls: type[Provider] | None = None,
    *,
    override: bool = False,
) -> type[Provider] | Callable[[type[Provider]], type[Provider]]:
    """注册 Provider adapter 类的装饰器（进程级全局注册表）。

    .. rubric:: 功能介绍

    把 adapter 类登记进全局注册表，键为类的 ``name`` 类属性（如
    ``"deepseek"``）。Runtime 构建 provider 候选清单时按
    ``providers.yaml`` 条目的 ``adapter`` 字段查本注册表选类。

    .. rubric:: 设计要点

    - 注册全局（进程级）：与“一个进程可有多个 Runtime”兼容——adapter
      类是类型层资产，条目实例才属于 Runtime。
    - 注册时机是 import 期：装饰器在被装饰模块被 import 时执行。要让
      第三方 adapter 可用，嵌入方必须在 Runtime 构建候选清单之前 import
      相应模块——本框架不自动发现扩展 provider 包。
    - 同名唯一，显式覆盖：“同名不同类”无需求；确需替换内置 adapter
      时用 ``override=True``，语义明确且可告警。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.providers import OpenAICompletionsProvider, register_provider

        @register_provider
        class MyProvider(OpenAICompletionsProvider):
            name = "my"

        @register_provider(override=True)   # 全局替换内置 "deepseek"
        class HardenedDeepSeek(DeepSeekProvider):
            name = "deepseek"

    .. rubric:: 行为要点

    - 装饰器原样返回被装饰类（不包装、不子类化）；支持
      ``@register_provider`` 与 ``@register_provider(override=True)``
      两种形态。
    - 注册全部发生在 import 期（装饰器执行时）；此后注册表运行时不可变
      ——运行期再调用本装饰器不改变任何已建 Runtime 的行为。
    - 同名冲突：键已存在且被另一个类占用且 ``override=False`` → 抛
      :class:`flowing.errors.ProviderNameConflictError`；
      ``override=True`` → 后 import 者覆盖并产生警告；多个 override 按
      import 顺序后者胜出（每次覆盖均警告）。同一个类对象重复注册不
      视为冲突。
    - 前置条件：被装饰类必须是 :class:`Provider` 的子类，且定义非空的
      ``name`` 类属性。
    - 不实例化任何 Provider（实例化只发生在懒创建路径）；不读取
      ``providers.yaml``。

    :raises flowing.errors.ProviderNameConflictError:
        同名键已被另一个类占用且未指定 ``override=True``。
    :raises ValueError:
        被装饰对象不是 ``Provider`` 子类 / 缺少非空 ``name``——import
        期作者笔误刻意用内置异常（豁免声明见 :mod:`flowing.errors`
        模块 docstring）。

    .. seealso::

        :class:`Provider` 被注册类的基类与 ``name`` 约定。
        :class:`flowing.runtime.Runtime` 候选清单懒创建的宿主。
    """
    # 写入目标：进程级 adapter 注册表 _provider_adapters（见上）
    def _register(cls: type[Provider]) -> type[Provider]:
        # 前置条件：Provider 子类且定义非空 name 类属性，否则 ValueError
        if not (isinstance(cls, type) and issubclass(cls, Provider)):
            raise ValueError(
                "register_provider: decorated object must be a Provider subclass"
                f" (got {cls!r})")
        if not getattr(cls, "name", None):
            raise ValueError(
                "register_provider: Provider subclass must define a non-empty name class attribute"
                f" ({cls.__qualname__})")
        existing = _provider_adapters.get(cls.name)
        if existing is not None and existing is not cls:
            # 同名冲突：override=False -> 具名报错；override=True -> 覆盖并产生警告
            if not override:
                raise ProviderNameConflictError(cls.name)
            import warnings

            warnings.warn(
                f"register_provider: adapter {cls.name!r} is being overridden: "
                f"{existing.__qualname__} → {cls.__qualname__} (override=True)",
                stacklevel=2)  # 每次覆盖均警告；多个 override 按 import 顺序后者胜出
        _provider_adapters[cls.name] = cls  # 登记进进程级注册表
        return cls  # 原样返回被装饰类（不包装、不子类化）

    if cls is None:
        return _register  # @register_provider(override=True) 形态
    return _register(cls)  # @register_provider 形态
