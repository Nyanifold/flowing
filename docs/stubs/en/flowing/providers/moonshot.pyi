"""``flowing.providers.moonshot`` provides the built-in Moonshot Open Platform adapter for OpenAI Chat Completions.

.. rubric:: Overview

This module implements the OpenAI-compatible Chat Completions integration for the Moonshot Open Platform at ``api.moonshot.cn``. Importing it registers :class:`MoonshotProvider` process-wide as ``moonshot``.

The Moonshot Open Platform and the Kimi Code membership endpoint are separate services. Their credentials, model catalogs, and billing are independent, so provider entries for the two services are not interchangeable.

.. rubric:: See also

:mod:`flowing.providers.openai_completions` for the Chat Completions format family.
:mod:`flowing.providers.kimi_coding` for the separate Kimi Code integration.
"""
from __future__ import annotations

from typing import ClassVar
from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider

class MoonshotProvider(OpenAICompletionsProvider):
    """Built-in Moonshot Open Platform adapter for OpenAI Chat Completions (``name="moonshot"``).

    .. rubric:: Overview

    Select this adapter by setting the ``adapter`` field to ``"moonshot"`` in a ``providers.yaml`` entry. It uses the Moonshot Open Platform, not the separate Kimi Code membership endpoint.

    .. rubric:: Configuration example

    .. code-block:: yaml

        moonshot:
          adapter: moonshot
          api_key: "{{env.MOONSHOT_API_KEY}}"

    .. rubric:: Behavior notes

    - The default base URL is ``https://api.moonshot.cn/v1``. A ``base_url`` in the provider entry overrides this default, for example when using a proxy.
    - The adapter sends credentials in an ``Authorization: Bearer`` header, as defined by the OpenAI-compatible format family.
    - The documented model IDs use the ``kimi-`` prefix, including ``kimi-k3`` and ``kimi-k2.7-code``. The adapter passes model IDs through without checking whether the endpoint currently offers them; the endpoint reports unsupported models when a request is made.
    - Request construction, response mapping, usage normalization, and HTTP error classification follow :class:`~flowing.providers.openai_completions.OpenAICompletionsProvider`.

    .. seealso::

        :class:`~flowing.providers.openai_completions.OpenAICompletionsProvider` for the format-family contract.
        :class:`~flowing.providers.openai_responses.OpenAIResponsesProvider` for the Responses format family.
        :class:`~flowing.providers.kimi_coding.KimiCodingProvider` for the separate Kimi Code endpoint.
    """
    name: ClassVar[str]
    default_base_url: ClassVar[str | None]
