"""``flowing.providers.kimi`` —— Kimi（Moonshot）内置 adapter。

.. rubric:: 功能介绍

本模块承载 Kimi 官方端点的 OpenAI 兼容实现 :class:`KimiProvider`，
import 期经 :func:`register_provider` 进程级注册（``name="kimi"``）。
家族叙述见 :mod:`flowing.providers.openai_completions`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider


@register_provider
class KimiProvider(OpenAICompletionsProvider):
    """Kimi（Moonshot）内置 adapter（``name="kimi"``）。

    .. rubric:: 功能介绍

    Kimi 官方端点的 OpenAI 兼容实现；随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"kimi"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        kimi:
          adapter: kimi
          api_key: "{{env.MOONSHOT_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.moonshot.cn/v1``；条目配 ``base_url`` 时
      以条目为准（代理场景）。
    - 格式映射与 Usage 归一继承自 :class:`OpenAICompletionsProvider`。

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
    """

    name: ClassVar[str] = "kimi"
    default_base_url: ClassVar[str | None] = "https://api.moonshot.cn/v1"
