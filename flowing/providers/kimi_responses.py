"""``flowing.providers.kimi_responses`` —— Kimi（moonshot）Responses 端点 adapter。

.. rubric:: 功能介绍

本模块承载 moonshot 开放平台 OpenAI Responses 端点的实现
:class:`KimiResponsesProvider`，import 期经 :func:`register_provider`
进程级注册（``name="kimi-responses"``）。家族叙述见
:mod:`flowing.providers.openai_responses`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.openai_responses import OpenAIResponsesProvider
from flowing.providers.provider import register_provider


@register_provider
class KimiResponsesProvider(OpenAIResponsesProvider):
    """Kimi（moonshot）Responses 端点内置 adapter（``name="kimi-responses"``）。

    .. rubric:: 功能介绍

    moonshot 开放平台 ``/v1/responses``（OpenAI Responses API）的实现；
    随框架发布、import 期经 :func:`register_provider` 进程级注册。
    providers.yaml 条目把 ``adapter`` 字段设为 ``"kimi-responses"``
    即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        kimi-responses:
          adapter: kimi-responses
          api_key: "{{env.MOONSHOT_API_KEY}}"

        # models.yaml
        kimi-k3-responses:
          provider: kimi-responses
          model: kimi-k3

    .. rubric:: 行为要点

    - 默认端点 ``https://api.moonshot.cn/v1``；条目配 ``base_url`` 时
      以条目为准（代理场景）。
    - 凭证取条目 ``api_key``（与 chat/completions 的 ``kimi`` adapter
      同 key）；Bearer 头由基类设置。
    - reasoning 是否开启由服务端按模型默认决定，本 adapter 不发送
      reasoning 参数字段；reasoning 项经基类
      ``include=["reasoning.encrypted_content"]`` 带加密内容回传，
      多轮以 ``ThinkingBlock.signature`` 原样回放。
    - 格式映射与 Usage 归一继承自 :class:`OpenAIResponsesProvider`。

    .. seealso::

        :class:`OpenAIResponsesProvider` 格式实现来源。
        :class:`KimiProvider` 同端点的 chat/completions 格式 adapter。
    """

    name: ClassVar[str] = "kimi-responses"
    default_base_url: ClassVar[str | None] = "https://api.moonshot.cn/v1"
