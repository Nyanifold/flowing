"""OpenAI Completions 格式家族（``flowing.providers.openai``）。

框架基类 :class:`OpenAICompletionsProvider` 收拢 OpenAI 兼容 wire format
的请求/响应映射；内置厂商 adapter（DeepSeek / Kimi / Groq / OpenRouter）
各为一个子类，只覆写厂商差异。设计动机（显式继承树、禁止 compat flags）
见 :mod:`flowing.providers` 包 docstring。

工具结果与媒体映射（详见 :mod:`flowing.providers` 包 docstring「工具
结果映射」rubric）：Chat Completions 的 tool 消息为文本-only——消息级
``tool_call_id`` → tool 消息的 ``tool_call_id`` 位；``StructBlock``
恒投影为 ``json.dumps(ensure_ascii=False)`` 文本；媒体块**转移**：攒入
紧随 tool 消息的合成 user 消息（固定措辞提示）。

响应侧事实：思考无官方字段，线上方言四种——``reasoning_content`` /
``reasoning_details`` / ``reasoning`` / ``reasoning_text``，adapter
采「入站扫描 + 出站同方言回写」（记住端点实际说的方言，回放时用同一
key 写回）；``function.arguments`` 为 JSON 字符串，流式按数字 ``index``
分片路由，「参数分片先于 name 到达」的乱序真实存在、缓冲逻辑不可省；
流式参数拼装策略（字符串累积完成后解析 / partial-json 容错解析 + 截断
拒执行）由本 adapter 选定并写明，属实现细节。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值

from typing import ClassVar

from flowing.providers.provider import Provider




class OpenAICompletionsProvider(Provider):
    """OpenAI Completions 格式的框架基类（``api_format="openai_completions"``）。

    .. rubric:: 功能介绍

    所有 OpenAI 兼容端点 adapter 的基类：实现请求/响应的格式映射
    （messages 数组、tool_calls、``prompt_tokens → Usage.input``
    且 ``fresh_input = prompt_tokens - cached`` 的 :class:`Usage`
    归一映射等），子类只覆写差异（base_url 默认、凭证头、
    厂商特有字段）。

    .. rubric:: 设计动机

    OpenAI 兼容是一个**格式级别**的家族（DeepSeek / Kimi / Groq /
    OpenRouter 等共享同一 wire format），用基类收拢格式实现、用子类
    表达厂商差异，差异在类型层可见——而非 compat flags。

    .. rubric:: 使用示例

    .. code-block:: python

        @register_provider
        class GroqProvider(OpenAICompletionsProvider):
            name = "groq"

    .. rubric:: 行为规约

    - 期待行为：把 ``Context`` 三字段映射为 chat/completions 请求体；
      响应 ``finish_reason == "tool_calls"`` → ``finish=False``，其余
      → ``finish=True``；原始 ``finish_reason`` 保留在
      ``provider_data["stop_reason"]``。
    - 非行为：不做厂商探测（如按 base_url 猜测能力）——厂商差异属于
      子类覆写。
    - 边缘情况：前缀缓存为 OpenAI 服务端自动启发式（>1024 token），
      adapter 不发送任何缓存标记；``PromptBlock.cache`` 对本格式仅是
      字节稳定性提示。

    .. rubric:: 测试案例

    - 前置：响应含 ``tool_calls``。操作：``generate()``。期望：
      ``finish is False`` 且 ``provider_data["stop_reason"] ==
      "tool_calls"``。

    .. rubric:: 调用关系（审计）

    - 被调：具体厂商子类继承（``DeepSeekProvider`` / ``KimiProvider``
      / ``GroqProvider`` / ``OpenRouterProvider``，import 期经
      ``register_provider`` 注册）
    - 实例化方：无（框架基类不直接实例化；实例化经具体子类的懒创建
      链，见 :class:`Provider`）

    .. seealso::

        :class:`Provider` 抽象契约。
        :class:`AnthropicMessagesProvider` 另一格式家族基类。
    """

    api_format: ClassVar[str]  # = "openai_completions"


class DeepSeekProvider(OpenAICompletionsProvider):
    """DeepSeek 内置 adapter（``name="deepseek"``）。

    .. rubric:: 功能介绍

    DeepSeek 官方端点的 OpenAI 兼容实现；随框架发布、进程级注册。

    .. rubric:: 设计动机

    DeepSeek 的前缀缓存为自动 128-token 前缀哈希（要求前缀字节稳定），
    adapter 无需发送缓存标记——这印证了「框架核心只保证 append-only
    消息历史的字节稳定性，缓存发生在 provider 侧」的分工。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        deepseek-personal:
          adapter: deepseek
          api_key: sk-...

    .. rubric:: 行为规约

    - 期待行为：默认 base_url 指向 DeepSeek 官方端点；凭证取
      ``api_key``；缓存命中统计（``prompt_cache_hit_tokens`` 等）保留
      在 ``Usage.raw``。
    - 非行为：不注入任何缓存策略；不感知 ``model_tag``。

    .. rubric:: 调用关系（审计）

    - 被调：``register_provider``（进程级注册，import 期，随框架发布）
    - 实例化方：``flowing.runtime.Runtime`` 懒创建链（providers.yaml
      条目 ``adapter: deepseek`` 首次 ``get`` 时，一条目一实例）

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
    """

    name: ClassVar[str]  # = "deepseek"


class KimiProvider(OpenAICompletionsProvider):
    """Kimi（Moonshot）内置 adapter（``name="kimi"``）。

    .. rubric:: 功能介绍

    Kimi 官方端点的 OpenAI 兼容实现；随框架发布、进程级注册。

    .. rubric:: 设计动机

    与 :class:`DeepSeekProvider` 同理：格式继承自基类，厂商差异
    （base_url、凭证、厂商特有字段）写在本子类覆写中。

    .. rubric:: 使用示例

    .. code-block:: yaml

        kimi:
          adapter: kimi
          api_key: "{{env.MOONSHOT_API_KEY}}"

    .. rubric:: 行为规约

    - 期待行为：默认 base_url 指向 Moonshot 官方端点。
    - 边缘情况：条目配 ``base_url`` 时以条目为准（代理场景）。

    .. rubric:: 调用关系（审计）

    - 被调：``register_provider``（进程级注册，import 期，随框架发布）
    - 实例化方：``flowing.runtime.Runtime`` 懒创建链（providers.yaml
      条目 ``adapter: kimi`` 首次 ``get`` 时，一条目一实例）

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
    """

    name: ClassVar[str]  # = "kimi"


class GroqProvider(OpenAICompletionsProvider):
    """Groq 内置 adapter（``name="groq"``）。

    .. rubric:: 功能介绍

    Groq 端点的 OpenAI 兼容实现；随框架发布、进程级注册。

    .. rubric:: 设计动机

    低延迟推理端点的代表；常用于 ``fast`` 标签指向的模型条目（标签
    语义是用户侧约定，adapter 本身不感知标签）。

    .. rubric:: 使用示例

    .. code-block:: yaml

        groq:
          adapter: groq
          api_key: "{{env.GROQ_API_KEY}}"

    .. rubric:: 行为规约

    - 期待行为：默认 base_url 指向 Groq 官方端点。
    - 非行为：不做能力校验（模型是否存在于 Groq 由 API 调用时报错）。

    .. rubric:: 调用关系（审计）

    - 被调：``register_provider``（进程级注册，import 期，随框架发布）
    - 实例化方：``flowing.runtime.Runtime`` 懒创建链（providers.yaml
      条目 ``adapter: groq`` 首次 ``get`` 时，一条目一实例）

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
    """

    name: ClassVar[str]  # = "groq"


class OpenRouterProvider(OpenAICompletionsProvider):
    """OpenRouter 内置 adapter（``name="openrouter"``）。

    .. rubric:: 功能介绍

    OpenRouter 聚合端点的 OpenAI 兼容实现。

    .. rubric:: 设计动机

    OpenRouter 是「请求侧模型 ID 与响应侧模型 ID 可不同」的典型
    （``model="auto"`` → 实际 ``anthropic/claude-sonnet-4-6``），是
    :attr:`ProviderResponse.model` 保持 ``str``、不回填规格结构体
    这一契约的驱动场景。

    .. rubric:: 使用示例

    .. code-block:: yaml

        openrouter:
          adapter: openrouter
          api_key: "{{env.OPENROUTER_API_KEY}}"

    .. rubric:: 行为规约

    - 期待行为：``ProviderResponse.model`` 填实际响应的模型 ID；
      路由信息（如 provider 路由选择）保留在 ``provider_data``。
    - 非行为：不在 adapter 内做模型路由策略（路由是 OpenRouter
      服务端行为；本地侧无 fallback 链）。

    .. rubric:: 调用关系（审计）

    - 被调：``register_provider``（进程级注册，import 期，随框架发布）
    - 实例化方：``flowing.runtime.Runtime`` 懒创建链（providers.yaml
      条目 ``adapter: openrouter`` 首次 ``get`` 时，一条目一实例）

    .. seealso::

        :class:`ProviderResponse` ``model`` 字段语义。
    """

    name: ClassVar[str]  # = "openrouter"
