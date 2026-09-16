"""``flowing.providers.kimi_coding`` —— Kimi Code 内置 adapter。

.. rubric:: 功能介绍

本模块承载 Kimi Code 会员编程权益端点（``https://api.kimi.com/coding``，
Anthropic Messages 协议）的实现 :class:`KimiCodingProvider`，import 期经
:func:`register_provider` 进程级注册（``name="kimi-coding"``）。家族叙述见
:mod:`flowing.providers.anthropic_messages`。

该端点与 Moonshot 开放平台（:class:`MoonshotProvider`，chat/completions）是
两个独立入口：凭证在 Kimi Code 控制台单独签发，模型口径与计费也相互
独立，providers.yaml 条目不可混用。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import register_provider


@register_provider
class KimiCodingProvider(AnthropicMessagesProvider):
    """Kimi Code 端点内置 adapter（``name="kimi-coding"``）。

    .. rubric:: 功能介绍

    Kimi Code 会员编程权益端点的 Anthropic Messages 协议实现；随框架
    发布、import 期经 :func:`register_provider` 进程级注册。
    providers.yaml 条目把 ``adapter`` 字段设为 ``"kimi-coding"`` 即可
    选用。与 Moonshot 开放平台（``name="kimi"``）相互独立——凭证、
    模型口径、计费均不共用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        kimi-code:
          adapter: kimi-coding
          api_key: "{{env.KIMI_CODE_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.kimi.com/coding``；条目配 ``base_url`` 时
      以条目为准（代理场景）。
    - 可用模型随会员档位变化（如 ``k3`` / ``k3-256k`` /
      ``kimi-for-coding``）；API 请求的 model 字段用裸名（``k3``），
      ``k3[1m]`` 写法仅是 Claude Code 环境变量场景的上下文标注，不落
      到 API 请求。
    - 凭证为 Kimi Code 控制台签发的 API key，经基类 ``x-api-key`` 头
      发送；该端点同时接受 ``Authorization: Bearer``。
    - 格式映射、思考块 signature 回放与 Usage 归一继承自
      :class:`AnthropicMessagesProvider`。

    .. seealso::

        :class:`AnthropicMessagesProvider` 格式实现来源。
        :class:`MoonshotProvider` Moonshot 开放平台对照（另一入口）。
    """

    name: ClassVar[str] = "kimi-coding"
    default_base_url: ClassVar[str | None] = "https://api.kimi.com/coding"
