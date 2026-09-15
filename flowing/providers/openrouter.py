"""``flowing.providers.openrouter`` —— OpenRouter 内置 adapter。

.. rubric:: 功能介绍

本模块承载 OpenRouter 聚合端点的 OpenAI 兼容实现
:class:`OpenRouterProvider`，import 期经 :func:`register_provider`
进程级注册（``name="openrouter"``）。家族叙述见
:mod:`flowing.providers.openai_completions`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider


@register_provider
class OpenRouterProvider(OpenAICompletionsProvider):
    """OpenRouter 内置 adapter（``name="openrouter"``）。

    .. rubric:: 功能介绍

    OpenRouter 聚合端点的 OpenAI 兼容实现；随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"openrouter"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        openrouter:
          adapter: openrouter
          api_key: "{{env.OPENROUTER_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://openrouter.ai/api/v1``。
    - 请求侧模型 ID 与响应侧模型 ID 可以不同（如 ``model="auto"`` 时
      实际由 OpenRouter 路由到具体模型）：``ProviderResponse.model``
      填实际响应的模型 ID；路由信息（如 provider 路由选择）保留在
      ``provider_data``。
    - 模型路由是 OpenRouter 服务端行为，本 adapter 不做本地路由 /
      fallback 链。

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
        :attr:`flowing.providers.ProviderResponse.model`
            响应侧模型 ID 语义。
    """

    name: ClassVar[str] = "openrouter"
    default_base_url: ClassVar[str | None] = "https://openrouter.ai/api/v1"
