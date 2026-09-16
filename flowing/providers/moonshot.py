"""``flowing.providers.moonshot`` —— Moonshot 开放平台内置 adapter。

.. rubric:: 功能介绍

本模块承载 Moonshot 开放平台（``api.moonshot.cn``）的 OpenAI 兼容实现
:class:`MoonshotProvider`，import 期经 :func:`register_provider` 进程级
注册（``name="moonshot"``）。家族叙述见
:mod:`flowing.providers.openai_completions`。

该平台与 Kimi Code 会员端点（:mod:`flowing.providers.kimi_coding`）是
两个独立入口：凭证在开放平台单独签发，模型口径与计费也相互独立，
providers.yaml 条目不可混用。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider


@register_provider
class MoonshotProvider(OpenAICompletionsProvider):
    """Moonshot 开放平台内置 adapter（``name="moonshot"``）。

    .. rubric:: 功能介绍

    Moonshot 开放平台（api.moonshot.cn）的 OpenAI 兼容实现；随框架
    发布、import 期经 :func:`register_provider` 进程级注册。
    providers.yaml 条目把 ``adapter`` 字段设为 ``"moonshot"`` 即可
    选用。与 Kimi Code 会员端点（``name="kimi-coding"``）相互独立——
    凭证、模型口径、计费均不共用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        moonshot:
          adapter: moonshot
          api_key: "{{env.MOONSHOT_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.moonshot.cn/v1``；条目配 ``base_url`` 时
      以条目为准（代理场景）。
    - 平台模型以 ``kimi-`` 前缀命名（如 ``kimi-k3`` / ``kimi-k2.7-code``）；
      模型 ID 原样透传，adapter 不做存在性校验（调用时报错）。
    - 格式映射与 Usage 归一继承自 :class:`OpenAICompletionsProvider`。

    .. seealso::

        :class:`OpenAIResponsesProvider` 同端点的 Responses 格式家族。
        :class:`KimiCodingProvider` Kimi Code 会员端点对照（另一入口）。
    """

    name: ClassVar[str] = "moonshot"
    default_base_url: ClassVar[str | None] = "https://api.moonshot.cn/v1"
