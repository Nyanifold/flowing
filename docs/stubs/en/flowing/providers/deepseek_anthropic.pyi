"""``flowing.providers.deepseek_anthropic`` — DeepSeek's Anthropic-compatible adapter.

.. rubric:: Overview

This module defines :class:`DeepSeekAnthropicProvider` for DeepSeek's
Anthropic Messages-compatible endpoint. Importing the module registers the
adapter process-wide under the name ``deepseek-anthropic``.

.. seealso::

    :mod:`flowing.providers.anthropic_messages` implements the shared message
        mapping and streaming behavior.
"""

from __future__ import annotations

from typing import ClassVar
from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import register_provider

class DeepSeekAnthropicProvider(AnthropicMessagesProvider):
    """Built-in adapter for DeepSeek's Anthropic Messages-compatible endpoint.

    Select it with ``adapter: deepseek-anthropic`` in a provider entry. It
    reuses the inherited Anthropic request/response mapping and server-sent
    events (SSE) streaming. The adapter supplies DeepSeek's endpoint and uses
    the same API key as the OpenAI-compatible DeepSeek adapter.

    .. rubric:: Example

    .. code-block:: yaml

        # providers.yaml
        deepseek-anthropic:
          adapter: deepseek-anthropic
          api_key: "{{env.DEEPSEEK_API_KEY}}"

        # Set the model in models.yaml, for example deepseek-v4-flash or deepseek-chat.

    .. rubric:: Behavioral notes

    - The default endpoint is ``https://api.deepseek.com/anthropic``. A
      provider entry's ``base_url`` overrides it.
    - The credential comes from ``api_key``. The inherited transport sends it
      in ``x-api-key`` and also sends the ``anthropic-version`` header.
    - A streamed thinking block may have an empty ``signature`` even though a
      non-streaming response includes the full signature. A later turn can
      replay only the signature actually returned by the endpoint.
    - Streaming text, thinking, tool calls, and usage have been verified
      against the live protocol.

    .. seealso::

        :class:`flowing.providers.anthropic_messages.AnthropicMessagesProvider`
            implements the shared Anthropic mapping and streaming behavior.
        :class:`flowing.providers.deepseek.DeepSeekProvider` uses DeepSeek's
            OpenAI-compatible chat/completions endpoint.
    """

    name: ClassVar[str]

    default_base_url: ClassVar[str | None]
