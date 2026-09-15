"""``flowing.providers.anthropic`` —— Anthropic 官方端点内置 adapter。

.. rubric:: 功能介绍

本模块承载 Anthropic 官方 API 的实现 :class:`AnthropicProvider`，import
期经 :func:`register_provider` 进程级注册（``name="anthropic"``）。
家族叙述见 :mod:`flowing.providers.anthropic_messages`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import register_provider


@register_provider
class AnthropicProvider(AnthropicMessagesProvider):
    """Anthropic 官方端点内置 adapter（``name="anthropic"``）。

    .. rubric:: 功能介绍

    Anthropic 官方 API 的实现；随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"anthropic"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        anthropic:
          adapter: anthropic
          api_key: "{{env.ANTHROPIC_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.anthropic.com``；条目配 ``base_url`` 时以
      条目为准（代理场景）。
    - 凭证取条目 ``api_key``；手动前缀缓存按基类规约设置
      （``cache="static"`` 段标记 ``cache_control``）。

    .. seealso::

        :class:`AnthropicMessagesProvider` 格式实现来源。
    """

    name: ClassVar[str] = "anthropic"
    default_base_url: ClassVar[str | None] = "https://api.anthropic.com"
