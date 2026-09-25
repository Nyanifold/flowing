"""``flowing.providers.groq`` — Groq's OpenAI-compatible adapter.

.. rubric:: Overview

This module defines :class:`GroqProvider` for Groq's OpenAI-compatible
chat/completions endpoint. Importing the module registers the adapter
process-wide under the name ``groq``.

.. seealso::

    :mod:`flowing.providers.openai_completions` implements the shared request
        and response mapping.
"""

from __future__ import annotations

from typing import ClassVar
from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider

class GroqProvider(OpenAICompletionsProvider):
    """Built-in adapter for Groq's OpenAI-compatible chat/completions API.

    Select it with ``adapter: groq`` in a provider entry.

    .. rubric:: Example

    .. code-block:: yaml

        # providers.yaml
        groq:
          adapter: groq
          api_key: "{{env.GROQ_API_KEY}}"

    .. rubric:: Behavioral notes

    - The default endpoint is ``https://api.groq.com/openai/v1``. A configured
      ``base_url`` takes precedence.
    - The inherited transport reads ``api_key`` and sends it with Bearer
      authentication.
    - The adapter does not check whether a model is available on Groq. Groq
      reports an unknown or unsupported model when the request is sent.

    .. seealso::

        :class:`flowing.providers.openai_completions.OpenAICompletionsProvider`
            implements the shared chat/completions mapping.
    """

    name: ClassVar[str]

    default_base_url: ClassVar[str | None]
