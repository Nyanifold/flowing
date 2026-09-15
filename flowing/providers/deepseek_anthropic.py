"""``flowing.providers.deepseek_anthropic`` —— DeepSeek 的 Anthropic 兼容端点 adapter。

.. rubric:: 功能介绍

本模块承载 DeepSeek Anthropic Messages 兼容端点的实现
:class:`DeepSeekAnthropicProvider`，import 期经
:func:`register_provider` 进程级注册（``name="deepseek-anthropic"``）。
家族叙述见 :mod:`flowing.providers.anthropic_messages`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import register_provider


@register_provider
class DeepSeekAnthropicProvider(AnthropicMessagesProvider):
    """DeepSeek 的 Anthropic Messages 兼容端点 adapter（``name="deepseek-anthropic"``）。

    .. rubric:: 功能介绍

    DeepSeek 提供 Anthropic Messages 兼容 API，可用 Anthropic 协议调用
    DeepSeek 模型。本 adapter 复用 :class:`AnthropicMessagesProvider` 的
    全部格式映射与真 SSE 流式，仅改默认端点与凭证口径——同一 DeepSeek
    key 既可走 OpenAI 系（``name="deepseek"``）也可走 Anthropic 系
    （``name="deepseek-anthropic"``）。流式正文 / 思考 / 工具调用与
    usage 均经本协议真机验证。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        deepseek-anthropic:
          adapter: deepseek-anthropic
          api_key: "{{env.DEEPSEEK_API_KEY}}"
        # model 配置里指定模型（如 deepseek-v4-flash / deepseek-chat）

    .. rubric:: 行为要点

    - 默认端点 ``https://api.deepseek.com/anthropic``；条目配 ``base_url``
      时以条目为准（代理场景）。
    - 凭证取条目 ``api_key``（与 OpenAI 系 deepseek adapter 同 key）；
      DeepSeek 端点接受 Anthropic ``x-api-key`` + ``anthropic-version``
      头（由基类设置）。
    - Anthropic 端点在流式下思考块可能给空 ``signature``（非流式给全量）——
      多轮把思考写回时以实际返回为准。

    .. seealso::

        :class:`AnthropicMessagesProvider` 格式与流式实现来源。
        :class:`DeepSeekProvider` OpenAI 系对照。
    """

    name: ClassVar[str] = "deepseek-anthropic"
    default_base_url: ClassVar[str | None] = "https://api.deepseek.com/anthropic"
