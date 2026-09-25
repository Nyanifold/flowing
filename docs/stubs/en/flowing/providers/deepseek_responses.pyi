"""``flowing.providers.deepseek_responses`` — DeepSeek's OpenAI Responses adapter.

.. rubric:: Overview

This module defines :class:`DeepSeekResponsesProvider` for DeepSeek's
OpenAI Responses API. Importing the module registers the adapter
process-wide under the name ``deepseek-responses``.

.. seealso::

    :mod:`flowing.providers.openai_responses` implements the shared
        ``/responses`` request and response mapping.
"""

from __future__ import annotations

from typing import ClassVar
from flowing.providers.openai_responses import OpenAIResponsesProvider
from flowing.providers.provider import register_provider

class DeepSeekResponsesProvider(OpenAIResponsesProvider):
    """Built-in adapter for DeepSeek's OpenAI Responses API.

    Select this adapter with ``adapter: deepseek-responses`` in a provider
    entry. The inherited class handles the Responses format and usage
    normalization.

    .. rubric:: Example

    .. code-block:: yaml

        # providers.yaml
        deepseek-responses:
          adapter: deepseek-responses
          api_key: "{{env.DEEPSEEK_API_KEY}}"

        # models.yaml
        deepseek-reasoner-responses:
          provider: deepseek-responses
          model: deepseek-reasoner

    .. rubric:: Behavioral notes

    - The default base URL is ``https://api.deepseek.com``. Requests use the
      ``/responses`` path without a ``/v1`` prefix; the inherited class appends
      that path. A provider entry's ``base_url`` overrides the default.
    - The credential comes from ``api_key`` and is sent with Bearer
      authentication by the inherited transport.
    - DeepSeek routes ``deepseek-chat`` to ``deepseek-flash`` on the server.
      The ``deepseek-reasoner`` model can return reasoning items. The inherited
      request sets ``include: ["reasoning.encrypted_content"]`` so the endpoint
      returns the encrypted content needed to replay a reasoning item unchanged
      in a later turn. The item is retained in ``ThinkingBlock.signature``.
    - Reasoning behavior is selected by the model. This adapter does not add a
      reasoning-control field to the request.

    .. seealso::

        :class:`flowing.providers.openai_responses.OpenAIResponsesProvider`
            implements the shared Responses mapping and usage normalization.
        :class:`flowing.providers.deepseek.DeepSeekProvider` uses the same
            DeepSeek service through chat/completions.
        :class:`flowing.providers.deepseek_anthropic.DeepSeekAnthropicProvider`
            uses DeepSeek's Anthropic-compatible endpoint.
    """

    name: ClassVar[str]

    default_base_url: ClassVar[str | None]
