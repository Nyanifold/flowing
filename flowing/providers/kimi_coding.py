"""``flowing.providers.kimi_coding`` —— Kimi Code 内置 adapter。

.. rubric:: 功能介绍

本模块承载 Kimi Code 会员编程权益端点（``https://api.kimi.com/coding``）
的默认形态实现 :class:`KimiCodingProvider`（OpenAI chat/completions
协议），import 期经 :func:`register_provider` 进程级注册
（``name="kimi-coding"``）。家族叙述见
:mod:`flowing.providers.openai_completions`。

该端点**三协议并存**：``/v1/chat/completions``、``/v1/responses`` 与
``/v1/messages``（Anthropic）。本 adapter 取 completions 为默认形态
（Kimi Code CLI 及第三方工具的主流接入面）；Anthropic 形态可按
:class:`~flowing.providers.anthropic_messages.AnthropicMessagesProvider`
子类同款方式另行接入，Responses 形态经
:class:`~flowing.providers.openai_responses.OpenAIResponsesProvider`
配 ``base_url`` 即可。该端点与 Moonshot 开放平台
（:mod:`flowing.providers.moonshot`）是两个独立入口：凭证在 Kimi Code
控制台单独签发，模型口径与计费也相互独立，providers.yaml 条目不可
混用。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider


@register_provider
class KimiCodingProvider(OpenAICompletionsProvider):
    """Kimi Code 端点内置 adapter（``name="kimi-coding"``，chat/completions）。

    .. rubric:: 功能介绍

    Kimi Code 会员编程权益端点的 OpenAI chat/completions 实现；随框架
    发布、import 期经 :func:`register_provider` 进程级注册。
    providers.yaml 条目把 ``adapter`` 字段设为 ``"kimi-coding"`` 即可
    选用。与 Moonshot 开放平台（``name="moonshot"``）相互独立——凭证、
    模型口径、计费均不共用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        kimi-code:
          adapter: kimi-coding
          api_key: "{{env.KIMI_CODE_API_KEY}}"

    .. rubric:: 行为要点

    - 默认端点 ``https://api.kimi.com/coding/v1``；条目配 ``base_url``
      时以条目为准（代理场景）。
    - 端点同时提供 ``/v1/responses`` 与 ``/v1/messages``（Anthropic）
      协议；本 adapter 为 completions 默认形态，其余形态按模块
      docstring 说明另行接入。
    - 可用模型（/v1/models 实测）：``k3`` / ``k3-256k`` /
      ``kimi-for-coding`` / ``kimi-for-coding-highspeed``；模型 ID 原样
      透传，adapter 不做存在性校验。
    - 思考内容经各家方言键（实测 ``reasoning_content``）随响应返回，
      由基类方言扫描归一为思考块；``thinking_budget`` 的内建预算语义
      仅 Anthropic 家族使用，本 adapter 不消费。
    - 凭证经 ``Authorization: Bearer`` 发送（openai 家族统一形态）。

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
        :class:`MoonshotProvider` Moonshot 开放平台对照（另一入口）。
    """

    name: ClassVar[str] = "kimi-coding"
    default_base_url: ClassVar[str | None] = "https://api.kimi.com/coding/v1"
