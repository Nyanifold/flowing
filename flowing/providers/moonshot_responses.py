"""``flowing.providers.moonshot_responses`` —— Moonshot Responses 内置 adapter。

.. rubric:: 功能介绍

本模块承载 Moonshot 开放平台（``api.moonshot.cn``）Responses 端点的实现
:class:`MoonshotResponsesProvider`，import 期经 :func:`register_provider`
进程级注册（``name="moonshot-responses"``）。家族叙述见
:mod:`flowing.providers.openai_responses`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.providers.openai_responses import OpenAIResponsesProvider
from flowing.providers.provider import register_provider


@register_provider
class MoonshotResponsesProvider(OpenAIResponsesProvider):
    """Moonshot Responses 端点内置 adapter（``name="moonshot-responses"``）。

    .. rubric:: 功能介绍

    Moonshot 开放平台 Responses API（``/v1/responses``）的实现；随框架
    发布、import 期经 :func:`register_provider` 进程级注册。
    providers.yaml 条目把 ``adapter`` 字段设为 ``"moonshot-responses"``
    即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        moonshot-responses:
          adapter: moonshot-responses
          api_key: "{{env.MOONSHOT_API_KEY}}"

        # models.yaml
        kimi-k3-responses:
          provider: moonshot-responses
          model: kimi-k3

    .. rubric:: 行为要点

    - 默认端点 ``https://api.moonshot.cn/v1``；条目配 ``base_url`` 时
      以条目为准（代理场景）。
    - 真机验证（T153–T157）：文本 / 图片多轮与真 SSE 流式均按基类
      默认参数直接通过，端点兼容 ``store`` / ``include`` 字段与
      标准事件流，无适配层。
    - 格式映射与 Usage 归一继承自 :class:`OpenAIResponsesProvider`。

    .. seealso::

        :class:`OpenAIResponsesProvider` 格式实现来源。
        :class:`MoonshotProvider` 同端点的 chat/completions 格式 adapter。
    """

    name: ClassVar[str] = "moonshot-responses"
    default_base_url: ClassVar[str | None] = "https://api.moonshot.cn/v1"
