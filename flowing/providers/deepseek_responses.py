"""``flowing.providers.deepseek_responses`` —— DeepSeek Responses 端点 adapter。

.. rubric:: 功能介绍

本模块承载 DeepSeek OpenAI Responses 端点的实现
:class:`DeepSeekResponsesProvider`，import 期经
:func:`register_provider` 进程级注册（``name="deepseek-responses"``）。
家族叙述见 :mod:`flowing.providers.openai_responses`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.openai_responses import OpenAIResponsesProvider
from flowing.providers.provider import register_provider


@register_provider
class DeepSeekResponsesProvider(OpenAIResponsesProvider):
    """DeepSeek Responses 端点内置 adapter（``name="deepseek-responses"``）。

    .. rubric:: 功能介绍

    DeepSeek 的 OpenAI Responses API（``/responses``）的实现；随框架
    发布、import 期经 :func:`register_provider` 进程级注册。
    providers.yaml 条目把 ``adapter`` 字段设为 ``"deepseek-responses"``
    即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        deepseek-responses:
          adapter: deepseek-responses
          api_key: "{{env.DEEPSEEK_API_KEY}}"

        # models.yaml
        deepseek-reasoner-responses:
          provider: deepseek-responses
          model: deepseek-reasoner

    .. rubric:: 行为要点

    - 默认端点 ``https://api.deepseek.com``，请求路径为 ``/responses``
      （无 ``/v1`` 前缀，路径由基类拼接）；条目配 ``base_url`` 时以
      条目为准（代理场景）。
    - 凭证取条目 ``api_key``（与 OpenAI 系 ``deepseek`` adapter 同
      key）；Bearer 头由基类设置。
    - 端点事实：``deepseek-chat`` 由服务端路由到 ``deepseek-flash``；
      ``deepseek-reasoner`` 的输出含 reasoning item，思考块经基类
      signature 机制（``include: ["reasoning.encrypted_content"]``
      带加密内容回传）在多轮以原样 reasoning item 回放。
    - reasoning 是否开启由模型本身决定，本 adapter 不发送 reasoning
      参数字段。
    - 格式映射与 Usage 归一继承自 :class:`OpenAIResponsesProvider`。

    .. seealso::

        :class:`OpenAIResponsesProvider` 格式实现来源。
        :class:`DeepSeekProvider` 同端点的 chat/completions 格式 adapter。
        :class:`DeepSeekAnthropicProvider` 同端点的 Anthropic 兼容格式
            adapter。
    """

    name: ClassVar[str] = "deepseek-responses"
    default_base_url: ClassVar[str | None] = "https://api.deepseek.com"
