"""``flowing.providers.kimi_coding_anthropic`` —— Kimi Code Anthropic 内置 adapter。

.. rubric:: 功能介绍

本模块承载 Kimi Code 会员编程权益端点（``https://api.kimi.com/coding``）
Anthropic Messages 协议形态的实现 :class:`KimiCodingAnthropicProvider`，
import 期经 :func:`register_provider` 进程级注册
（``name="kimi-coding-anthropic"``）。家族叙述见
:mod:`flowing.providers.anthropic_messages`。

该端点三协议并存（completions / responses / anthropic）；completions
是默认形态（:mod:`flowing.providers.kimi_coding`），本模块为 Anthropic
形态（Claude Code 等工具的接入面）。凭证与 Kimi Code 控制台签发，
与 Moonshot 开放平台（:mod:`flowing.providers.moonshot`）不混用。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import register_provider


@register_provider
class KimiCodingAnthropicProvider(AnthropicMessagesProvider):
    """Kimi Code 端点 Anthropic 形态内置 adapter（``name="kimi-coding-anthropic"``）。

    .. rubric:: 功能介绍

    Kimi Code 会员端点 Anthropic Messages 协议的实现；随框架发布、
    import 期经 :func:`register_provider` 进程级注册。providers.yaml
    条目把 ``adapter`` 字段设为 ``"kimi-coding-anthropic"`` 即可选用。
    与同端点 completions 形态（``name="kimi-coding"``）共用同一凭证与
    模型口径，只是 wire 协议不同。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml（Claude Code 类工具的接入形态）
        kimi-code-anthropic:
          adapter: kimi-coding-anthropic
          api_key: "{{env.KIMI_CODE_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.kimi.com/coding``（路径 ``/v1/messages``
      由基类拼接）；条目配 ``base_url`` 时以条目为准（代理场景）。
    - 凭证经 ``x-api-key`` 头发送（基类默认形态）；端点同时接受
      ``Authorization: Bearer``。
    - 可用模型（/v1/models 实测）：``k3`` / ``k3-256k`` /
      ``kimi-for-coding`` / ``kimi-for-coding-highspeed``；模型 ID 原样
      透传。
    - 端点线形实测：tool_use 的 ``content_block_start`` 显式携带
      ``input: {}``，参数走 ``input_json_delta`` 累积（基类已兼容）。
    - 格式映射、思考块 signature 回放与 Usage 归一继承自
      :class:`AnthropicMessagesProvider`。

    .. seealso::

        :class:`AnthropicMessagesProvider` 格式实现来源。
        :class:`KimiCodingProvider` 同端点的 completions 默认形态。
        :class:`MoonshotAnthropicProvider` Moonshot 开放平台对照。
    """

    name: ClassVar[str] = "kimi-coding-anthropic"
    default_base_url: ClassVar[str | None] = "https://api.kimi.com/coding"
