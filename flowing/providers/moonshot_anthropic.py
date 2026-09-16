"""``flowing.providers.moonshot_anthropic`` —— Moonshot Anthropic 内置 adapter。

.. rubric:: 功能介绍

本模块承载 Moonshot 开放平台 Anthropic 兼容端点
（``https://api.moonshot.cn/anthropic``）的实现
:class:`MoonshotAnthropicProvider`，import 期经
:func:`register_provider` 进程级注册（``name="moonshot-anthropic"``）。
家族叙述见 :mod:`flowing.providers.anthropic_messages`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import register_provider


@register_provider
class MoonshotAnthropicProvider(AnthropicMessagesProvider):
    """Moonshot Anthropic 端点内置 adapter（``name="moonshot-anthropic"``）。

    .. rubric:: 功能介绍

    Moonshot 开放平台 Anthropic Messages 兼容端点（``/anthropic``）的
    实现；随框架发布、import 期经 :func:`register_provider` 进程级注册。
    providers.yaml 条目把 ``adapter`` 字段设为 ``"moonshot-anthropic"``
    即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        moonshot-anthropic:
          adapter: moonshot-anthropic
          api_key: "{{env.MOONSHOT_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.moonshot.cn/anthropic``；条目配 ``base_url``
      时以条目为准（代理场景）。
    - 端点鉴权实测同时接受 ``x-api-key``（基类默认形态）与
      ``Authorization: Bearer``。
    - 平台模型以 ``kimi-`` 前缀命名（如 ``kimi-k3`` / ``kimi-k2.7-code``）；
      模型 ID 原样透传，adapter 不做存在性校验。模型清单侧事实：
      ``kimi-k3`` 支持思考档位 low/high/max，``kimi-k2.7-code`` 的思考
      由服务端按模型自身口径处理（真机默认参数文本/图片多轮通过）。
    - 格式映射、思考块 signature 回放与 Usage 归一继承自
      :class:`AnthropicMessagesProvider`。

    .. seealso::

        :class:`AnthropicMessagesProvider` 格式实现来源。
        :class:`MoonshotProvider` 同端点的 chat/completions 格式 adapter。
        :class:`MoonshotResponsesProvider` 同端点的 Responses 格式 adapter。
    """

    name: ClassVar[str] = "moonshot-anthropic"
    default_base_url: ClassVar[str | None] = "https://api.moonshot.cn/anthropic"
