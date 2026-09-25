"""``flowing.providers.deepseek`` — DeepSeek's OpenAI-compatible adapter.

.. rubric:: Overview

This module defines :class:`DeepSeekProvider` for DeepSeek's
chat/completions-compatible endpoint. Importing the module registers the
adapter process-wide under the name ``deepseek``.

.. seealso::

    :mod:`flowing.providers.openai_completions` implements the shared request
        and response mapping.
"""

from __future__ import annotations

from typing import ClassVar
from flowing.context import Context
from flowing.errors import InvalidRequestError
from flowing.model import ModelConfig
from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider

class DeepSeekProvider(OpenAICompletionsProvider):
    """Built-in adapter for DeepSeek's OpenAI-compatible chat/completions API.

    Select this adapter with ``adapter: deepseek`` in a provider entry. It
    handles DeepSeek-specific model options, while shared chat/completions
    mapping comes from
    :class:`flowing.providers.openai_completions.OpenAICompletionsProvider`.

    .. rubric:: Example

    .. code-block:: yaml

        # providers.yaml
        deepseek-personal:
          adapter: deepseek
          api_key: "{{env.DEEPSEEK_API_KEY}}"

        # models.yaml
        deepseek-v4:
          provider: deepseek-personal
          model: deepseek-v4-pro
          thinking: enabled
          reasoning_effort: high

    .. rubric:: Behavioral notes

    - The default endpoint is ``https://api.deepseek.com``. A provider entry's
      ``base_url`` overrides it. Credentials come from ``api_key`` and use the
      inherited OpenAI-compatible Bearer authentication.
    - DeepSeek handles prompt-prefix caching on the server. This adapter sends
      no cache markers; cache prefixes therefore depend on byte-for-byte stable
      prompt prefixes. Raw cache-usage fields, such as
      ``prompt_cache_hit_tokens``, remain available in ``Usage.raw``.
    - The model option ``thinking`` accepts a string such as ``enabled`` or
      ``disabled``, which is sent as ``{"type": value}``, or a dictionary,
      which is sent unchanged. If omitted, the adapter sends no ``thinking``
      field.
      The adapter does not validate accepted values; the service validates
      them.
    - DeepSeek has no numeric thinking-budget parameter. This adapter does not
      consume ``ModelConfig.thinking_budget``; that field is used by the
      Anthropic adapter family.
    - The model option ``reasoning_effort`` is sent unchanged as a top-level
      request field. DeepSeek documents ``low``, ``high``, and ``max``; this
      adapter does not validate that set, and an omitted value is not sent.
    - Other provider-specific request fields can be supplied through
      ``extra_body``. The adapter adds ``thinking`` and ``reasoning_effort``
      after those fields, so its values take precedence over same-named keys
      in ``extra_body``.
    - This adapter covers only DeepSeek's OpenAI-compatible endpoint. The
      separate Anthropic-compatible endpoint uses ``output_config.effort``
      rather than the Anthropic Messages family's ``budget_tokens`` field and
      requires
      :class:`flowing.providers.deepseek_anthropic.DeepSeekAnthropicProvider`.

    .. seealso::

        :class:`flowing.providers.openai_completions.OpenAICompletionsProvider`
            implements the shared chat/completions mapping.
        :class:`flowing.providers.deepseek_anthropic.DeepSeekAnthropicProvider`
            handles DeepSeek's Anthropic-compatible endpoint.
    """

    name: ClassVar[str]

    known_model_fields: ClassVar[frozenset[str]]

    default_base_url: ClassVar[str | None]

    def _build_request(self, context: Context, model: ModelConfig) -> dict:
        """Add DeepSeek's thinking options to the inherited request body.

        :raises InvalidRequestError: If ``thinking`` is neither a string nor a
            dictionary.
        """
        ...
