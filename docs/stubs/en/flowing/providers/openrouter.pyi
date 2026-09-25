"""``flowing.providers.openrouter`` — OpenRouter's OpenAI-compatible adapter.

.. rubric:: Overview

This module defines :class:`OpenRouterProvider` for OpenRouter's aggregated
chat/completions endpoint. Importing the module registers the adapter
process-wide under the name ``openrouter``.

.. seealso::

    :mod:`flowing.providers.openai_completions` implements the shared request
        and response mapping.
"""

from __future__ import annotations

from typing import ClassVar
from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider

class OpenRouterProvider(OpenAICompletionsProvider):
    """Built-in adapter for OpenRouter's aggregated chat/completions API.

    Select it with ``adapter: openrouter`` in a provider entry.

    .. rubric:: Example

    .. code-block:: yaml

        # providers.yaml
        openrouter:
          adapter: openrouter
          api_key: "{{env.OPENROUTER_API_KEY}}"

    .. rubric:: Behavioral notes

    - The default endpoint is ``https://openrouter.ai/api/v1``. A configured
      ``base_url`` takes precedence.
    - The inherited transport reads ``api_key`` and sends it with Bearer
      authentication.
    - The requested model ID and the model ID returned by OpenRouter may
      differ. For example, when the request uses ``model="auto"``, OpenRouter
      selects a concrete model. ``ProviderResponse.model`` contains the model
      ID reported in the response, and routing details such as the selected
      provider remain in ``provider_data``.
    - OpenRouter performs model routing on the server. This adapter does not
      implement local routing or a fallback chain.

    .. seealso::

        :class:`flowing.providers.openai_completions.OpenAICompletionsProvider`
            implements the shared chat/completions mapping.
        :attr:`flowing.providers.ProviderResponse.model` contains the
            response-side model ID.
    """

    name: ClassVar[str]

    default_base_url: ClassVar[str | None]
