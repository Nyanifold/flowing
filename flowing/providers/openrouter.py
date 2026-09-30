"""``flowing.providers.openrouter`` —— OpenRouter 内置 adapter。

.. rubric:: 功能介绍

本模块承载 OpenRouter 聚合端点的 OpenAI 兼容实现
:class:`OpenRouterProvider`，import 期经 :func:`register_provider`
进程级注册（``name="openrouter"``）。家族叙述见
:mod:`flowing.providers.openai_completions`。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from flowing.context import Context
from flowing.errors import InvalidRequestError
from flowing.model import ModelConfig
from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import ModelConfigField, ProviderResponse, register_provider


def _parse_reasoning_effort(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("reasoning.effort must be a string")
    return value


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
    - 所有请求携带 ``X-OpenRouter-Metadata: enabled``，响应中的
      ``openrouter_metadata`` 映射保存在 ``provider_data``。
    - models.yaml 可为单个模型声明 ``reasoning.effort``；该字符串按
      OpenRouter 参数形状写入请求体 ``reasoning.effort``。该设置属于模型
      属性，不转换 ``thinking_budget``，也不影响其他 provider。各模型接受
      的强度由服务端决定；不支持的配置由既有 HTTP 错误归类暴露。
    - OpenAI Completions 多模态输入目前仅接受图像；音频、视频和文件块
      在本地以 ``InvalidRequestError`` 拒绝。

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
        :attr:`flowing.providers.ProviderResponse.model`
            响应侧模型 ID 语义。
    """

    name: ClassVar[str] = "openrouter"
    model_fields: ClassVar[tuple[ModelConfigField, ...]] = (
        ModelConfigField(
            name="reasoning.effort",
            prompt="Reasoning effort (support and values vary by model)",
            parser=_parse_reasoning_effort,
        ),
    )
    """配置工具可展示的 OpenRouter 模型参数。"""
    default_base_url: ClassVar[str | None] = "https://openrouter.ai/api/v1"

    def _additional_headers(self) -> dict[str, str]:
        return {"X-OpenRouter-Metadata": "enabled"}

    def _build_request(self, context: Context, model: ModelConfig) -> dict:
        body = super()._build_request(context, model)
        try:
            effort = model["reasoning.effort"]
        except KeyError:
            return body
        if not isinstance(effort, str):
            raise InvalidRequestError(
                "models.yaml reasoning.effort must be a string, "
                f"got {type(effort).__name__}")
        reasoning = body.get("reasoning")
        if reasoning is None:
            reasoning = {}
        elif not isinstance(reasoning, dict):
            raise InvalidRequestError(
                "OpenRouter reasoning request field must be a mapping when "
                "reasoning.effort is configured")
        else:
            reasoning = dict(reasoning)
        reasoning["effort"] = effort
        body["reasoning"] = reasoning
        return body

    def _map_response(self, resp: dict) -> ProviderResponse:
        response = super()._map_response(resp)
        metadata = resp.get("openrouter_metadata")
        if isinstance(metadata, Mapping):
            response.provider_data["openrouter_metadata"] = dict(metadata)
        return response

    def _provider_data_from_chunk(self, chunk: dict) -> dict:
        metadata = chunk.get("openrouter_metadata")
        if isinstance(metadata, Mapping):
            return {"openrouter_metadata": dict(metadata)}
        return {}
