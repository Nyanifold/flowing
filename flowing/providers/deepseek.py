"""``flowing.providers.deepseek`` —— DeepSeek 内置 adapter（OpenAI 系）。

.. rubric:: 功能介绍

本模块承载 DeepSeek 官方端点的 OpenAI 兼容实现 :class:`DeepSeekProvider`，
import 期经 :func:`register_provider` 进程级注册（``name="deepseek"``）。
家族叙述见 :mod:`flowing.providers.openai_completions`。
"""

from __future__ import annotations

from typing import ClassVar

from flowing.context import Context
from flowing.errors import InvalidRequestError
from flowing.model import ModelConfig
from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider


@register_provider
class DeepSeekProvider(OpenAICompletionsProvider):
    """DeepSeek 内置 adapter（``name="deepseek"``）。

    .. rubric:: 功能介绍

    DeepSeek 官方端点的 OpenAI 兼容实现；随框架发布、import 期经
    :func:`register_provider` 进程级注册。providers.yaml 条目把
    ``adapter`` 字段设为 ``"deepseek"`` 即可选用。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # providers.yaml
        deepseek-personal:
          adapter: deepseek
          api_key: "{{env.DEEPSEEK_API_KEY}}"

        # models.yaml
        deepseek-v4:
          provider: deepseek-personal
          model: deepseek-v4-pro
          thinking: enabled          # 思考开关（或 disabled，或 dict 原样透传）
          reasoning_effort: high     # 思考强度（low/high/max）

    .. rubric:: 行为要点

    - 默认端点 ``https://api.deepseek.com``；条目配 ``base_url`` 时以
      条目为准（代理场景）。
    - 凭证取条目 ``api_key``；DeepSeek 的前缀缓存由服务端自动处理
      （要求前缀字节稳定），adapter 不发送缓存标记；缓存命中统计
      （如 ``prompt_cache_hit_tokens``）保留在 ``Usage.raw``。
    - 思考开关：models.yaml 条目中的 ``thinking`` 字段（进
      ``ModelConfig._extra``）按官方形态映射——字符串
      （``"enabled"`` / ``"disabled"``）包装为 ``{"type": ...}``，
      dict 原样透传；缺省不发送 ``thinking`` 字段（取服务端缺省）。
      adapter 不校验取值、由服务端校验。DeepSeek 端点没有数值预算
      概念，本 adapter **不消费** ``ModelConfig.thinking_budget``
      （该内建字段的预算语义仅 Anthropic adapter 使用）。
    - 思考强度：models.yaml 条目中的 ``reasoning_effort`` 字段进
      ``ModelConfig._extra``，本 adapter 原样透传为请求体顶层
      ``reasoning_effort``；官方取值为 ``"low"`` / ``"high"`` /
      ``"max"``，adapter 不校验枚举、由服务端校验；缺省不发送。
    - 其余厂商私有参数走基类 ``extra_body`` 通用透传（见
      :class:`OpenAICompletionsProvider`）；本 adapter 的思考字段在
      ``extra_body`` 合入之后写入，同名键以本 adapter 为准。
    - 本 adapter 只覆盖 DeepSeek 的 OpenAI 兼容端点；DeepSeek 另提供
      Anthropic 格式端点（强度参数为 ``output_config.effort``，与本
      框架 Anthropic 原生 adapter 的 ``budget_tokens`` 形态不同），
      接入时需另写子类，本 adapter 不涉及。

    .. seealso::

        :class:`OpenAICompletionsProvider` 格式实现来源。
    """

    name: ClassVar[str] = "deepseek"
    known_model_fields: ClassVar[frozenset[str]] = frozenset(
        {"thinking", "reasoning_effort"})
    default_base_url: ClassVar[str | None] = "https://api.deepseek.com"

    def _build_request(self, context: Context, model: ModelConfig) -> dict:
        """在基类请求体上补 DeepSeek 思考参数（见类 docstring 行为要点）。"""
        body = super()._build_request(context, model)
        thinking = model._extra.get("thinking")
        if thinking is not None:
            if isinstance(thinking, str):
                body["thinking"] = {"type": thinking}
            elif isinstance(thinking, dict):
                body["thinking"] = thinking
            else:
                raise InvalidRequestError(
                    f"models.yaml thinking must be a string or mapping, "
                    f"got {type(thinking).__name__}")
        effort = model._extra.get("reasoning_effort")
        if effort is not None:
            body["reasoning_effort"] = effort
        return body
