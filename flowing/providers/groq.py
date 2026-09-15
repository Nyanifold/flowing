"""``flowing.providers.groq`` —— Groq 内置 adapter。

.. rubric:: 功能介绍

本模块承载 Groq 端点的 OpenAI 兼容实现 :class:`GroqProvider`，import
期经 :func:`register_provider` 进程级注册（``name="groq"``）。家族
叙述见 :mod:`flowing.providers.openai_completions`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider


@register_provider
class GroqProvider(OpenAICompletionsProvider):
    """Groq 内置 adapter（``name="groq"``）。

    .. rubric:: 功能介绍

    Groq 端点的 OpenAI 兼容实现；随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"groq"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        groq:
          adapter: groq
          api_key: "{{env.GROQ_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.groq.com/openai/v1``。
    - 不做能力校验：模型是否存在于 Groq 由 API 调用时报错。

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
    """

    name: ClassVar[str] = "groq"
    default_base_url: ClassVar[str | None] = "https://api.groq.com/openai/v1"
