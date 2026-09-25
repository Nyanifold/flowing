"""``flowing.providers.kimi_coding`` provides the built-in Kimi Code adapter for OpenAI Chat Completions.

.. rubric:: Overview

This module implements the default OpenAI Chat Completions adapter for the Kimi Code membership coding endpoint at ``https://api.kimi.com/coding``. Importing the module registers :class:`KimiCodingProvider` process-wide as ``kimi-coding``.

The endpoint exposes three protocols: ``/v1/chat/completions``, ``/v1/responses``, and ``/v1/messages`` (Anthropic). This adapter uses Chat Completions as the default integration surface and is the mainstream interface for the Kimi Code CLI and third-party tools. The Anthropic form can be integrated like a subclass of :class:`~flowing.providers.anthropic_messages.AnthropicMessagesProvider`; the Responses form can use :class:`~flowing.providers.openai_responses.OpenAIResponsesProvider` with ``base_url``. This endpoint is separate from the Moonshot Open Platform: credentials are issued separately through the Kimi Code console, and model catalogs and billing are independent, so provider entries are not interchangeable.

.. rubric:: See also

:mod:`flowing.providers.openai_completions` for the Chat Completions format family.
:mod:`flowing.providers.moonshot` for the separate Moonshot Open Platform integration.
"""
from __future__ import annotations

from typing import ClassVar
from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.provider import register_provider

class KimiCodingProvider(OpenAICompletionsProvider):
    """Built-in Kimi Code adapter for OpenAI Chat Completions (``name="kimi-coding"``).

    .. rubric:: Overview

    Select this adapter by setting the ``adapter`` field to ``"kimi-coding"`` in a ``providers.yaml`` entry. The module registers the adapter at import time. It uses the Kimi Code credential and model catalog, which are separate from those of the Moonshot Open Platform.

    .. rubric:: Configuration example

    .. code-block:: yaml

        kimi-code:
          adapter: kimi-coding
          api_key: "{{env.KIMI_CODE_API_KEY}}"

    .. rubric:: Behavior notes

    - The default base URL is ``https://api.kimi.com/coding/v1``. A ``base_url`` in the provider entry overrides this default, for example when using a proxy.
    - The endpoint also supports Responses and Anthropic Messages. This adapter uses Chat Completions; use the corresponding provider integration for either other protocol.
    - The model IDs observed from ``/v1/models`` are ``k3``, ``k3-256k``, ``kimi-for-coding``, and ``kimi-for-coding-highspeed``. The adapter passes model IDs through without checking whether the endpoint currently offers them.
    - The endpoint returns reasoning content in its response dialect, observed as ``reasoning_content``. The inherited provider recognizes supported reasoning fields and converts them to Flowing thinking blocks. ``thinking_budget`` is handled by the Anthropic Messages family and has no effect on this adapter.
    - The adapter sends credentials in an ``Authorization: Bearer`` header.
    - Request construction, response mapping, usage normalization, and HTTP error classification follow :class:`~flowing.providers.openai_completions.OpenAICompletionsProvider`.

    .. seealso::

        :class:`~flowing.providers.openai_completions.OpenAICompletionsProvider` for the format-family contract.
        :class:`~flowing.providers.moonshot.MoonshotProvider` for the separate Moonshot Open Platform adapter.
    """
    name: ClassVar[str]
    default_base_url: ClassVar[str | None]
