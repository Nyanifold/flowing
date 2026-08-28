"""Anthropic Messages 格式家族（``flowing.providers.anthropic``）。

框架基类 :class:`AnthropicMessagesProvider` 收拢 Anthropic Messages API
的请求/响应映射（system 数组、content blocks、tool_use/tool_result、
手动前缀缓存 ``cache_control``）；内置厂商 adapter（Anthropic 官方端点、
AWS Bedrock）各为一个子类。设计动机见 :mod:`flowing.providers` 包
docstring。

工具结果与媒体映射（详见 :mod:`flowing.providers` 包 docstring「工具
结果映射」rubric）：TOOL/EVENT 消息的 ``content`` 块直接映射进 user
消息内的 ``tool_result`` 块——消息级 ``tool_call_id`` →
``tool_use_id``，``tool_status="error"`` → ``is_error: true``；媒体块
原生内嵌于 ``tool_result.content``（image/document），无需转移；
``StructBlock`` 恒投影为 ``json.dumps(ensure_ascii=False)`` 文本。

响应侧事实：``thinking`` 块的 ``signature`` 为不透明签名字符串，
多轮回放必须原样带回；``tool_use`` 的 ``input`` 为已解析 JSON 对象，
流式以 ``input_json_delta`` 字符串分片传输、按 content block ``index``
路由（并行 tool_use 的分片可跨块交错）；流式参数拼装策略（字符串累积
完成后解析 / partial-json 容错解析 + 截断拒执行）由本 adapter 选定并
写明，属实现细节。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值

from typing import ClassVar

from flowing.providers.provider import Provider




class AnthropicMessagesProvider(Provider):
    """Anthropic Messages 格式的框架基类（``api_format="anthropic_messages"``）。

    .. rubric:: 功能介绍

    Anthropic Messages API 家族 adapter 的基类：实现 system 数组、
    content blocks、tool_use/tool_result 的格式映射与 Usage 映射。

    .. rubric:: 设计动机

    与 :class:`OpenAICompletionsProvider` 并列的第二个格式家族。本格式
    的前缀缓存是**手动**的（``cache_control: {type: "ephemeral"}``，
    不标记 = 不缓存），因此 ``PromptBlock.cache == "static"`` 对
    本基类是设置 ``cache_control`` 的**必要信息**，不是可选优化。

    .. rubric:: 使用示例

    .. code-block:: python

        @register_provider
        class AnthropicProvider(AnthropicMessagesProvider):
            name = "anthropic"

    .. rubric:: 行为规约

    - 期待行为：``cache="static"`` 的 PromptSegment 设置
      ``cache_control``；``cache="dynamic"`` 不标记（动态内容不进入
      缓存断点）；``cache="session"`` 按会话冻结语义处理。
      ``stop_reason == "tool_use"`` → ``finish=False``，其余 →
      ``finish=True``，原始值保留在 ``provider_data``。
    - Usage 映射：``fresh_input`` / ``output`` 直取原生
      ``input_tokens`` / ``output_tokens``（Anthropic 原生 input 不含
      cache）；``input = fresh_input + cache_read + cache_write``
      按恒等式归一组装；``cache_creation_input_tokens`` /
      ``cache_read_input_tokens`` 升入一等字段，同时原样保留在
      ``Usage.raw``。
    - 非行为：不替用户决定缓存断点数量与位置之外的策略；压缩/清理
      策略（如 ``cache_edits``）不进入框架核心设计。

    .. rubric:: 测试案例

    - 前置：Context 含一个 ``cache="static"`` segment。操作：
      ``generate()``。期望：请求体对应 block 带 ``cache_control``；
      ``cache="dynamic"`` segment 不带。

    .. rubric:: 调用关系（审计）

    - 被调：具体子类继承（``AnthropicProvider`` / ``BedrockProvider``，
      import 期经 ``register_provider`` 注册）
    - 实例化方：无（框架基类不直接实例化；实例化经具体子类的懒创建
      链，见 :class:`Provider`）

    .. seealso::

        :class:`OpenAICompletionsProvider` 对照的格式家族。
        :class:`flowing.context.PromptBlock` ``cache`` 三值语义。
    """

    api_format: ClassVar[str]  # = "anthropic_messages"


class AnthropicProvider(AnthropicMessagesProvider):
    """Anthropic 官方端点内置 adapter（``name="anthropic"``）。

    .. rubric:: 功能介绍

    Anthropic 官方 API 的实现；随框架发布、进程级注册。

    .. rubric:: 设计动机

    手动前缀缓存（``cache_control``）的标杆实现：``PromptBlock.cache``
    三值语义主要为本 adapter 服务（静态块标记缓存、动态块不标记）。

    .. rubric:: 使用示例

    .. code-block:: yaml

        anthropic:
          adapter: anthropic
          api_key: "{{env.ANTHROPIC_API_KEY}}"

    .. rubric:: 行为规约

    - 期待行为：按基类规约设置 ``cache_control``；凭证取 ``api_key``。
    - 非行为：不管理 ``cachedContent`` 式显式缓存对象生命周期（那是
      Gemini 式机制，不属于本格式家族）。

    .. rubric:: 调用关系（审计）

    - 被调：``register_provider``（进程级注册，import 期，随框架发布）
    - 实例化方：``flowing.runtime.Runtime`` 懒创建链（providers.yaml
      条目 ``adapter: anthropic`` 首次 ``get`` 时，一条目一实例）

    .. seealso::

        :class:`AnthropicMessagesProvider` 格式实现来源。
    """

    name: ClassVar[str]  # = "anthropic"


class BedrockProvider(AnthropicMessagesProvider):
    """AWS Bedrock 上的 Anthropic 模型 adapter（``name="bedrock"``）。

    .. rubric:: 功能介绍

    经 AWS Bedrock 调用 Anthropic Messages 格式的实现；差异集中在
    凭证与传输层（AWS 签名），消息格式复用基类。

    .. rubric:: 设计动机

    「同格式、不同凭证来源」是覆写 :meth:`Provider.get_credential` 的
    典型场景——AWS 凭证链（环境变量 / 实例元数据 / session
    token）属于子类覆写点，不进框架核心。初版仅约定该覆写点存在；
    完整 AWS credential chain 初版不实现。

    .. rubric:: 使用示例

    .. code-block:: yaml

        bedrock:
          adapter: bedrock
          aws_region: us-east-1        # adapter 自读字段

    .. rubric:: 行为规约

    - 期待行为：凭证优先级为 config 显式字段 > AWS 默认链；
      region 等字段从 :class:`ProviderConfig` 自读。
    - 安全边界：AWS 凭证同样遵守「不进消息 / ``_provided`` / 落盘」。

    .. rubric:: 调用关系（审计）

    - 被调：``register_provider``（进程级注册，import 期，随框架发布）
    - 实例化方：``flowing.runtime.Runtime`` 懒创建链（providers.yaml
      条目 ``adapter: bedrock`` 首次 ``get`` 时，一条目一实例）

    .. seealso::

        :meth:`Provider.get_credential` 凭证覆写点。
    """

    name: ClassVar[str]  # = "bedrock"
