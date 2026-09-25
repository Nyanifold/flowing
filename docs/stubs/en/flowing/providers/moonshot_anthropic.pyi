"""``flowing.providers.moonshot_anthropic`` provides the Moonshot Open Platform adapter for Anthropic Messages.

.. rubric:: Overview

This module implements the Anthropic Messages-compatible endpoint rooted at ``https://api.moonshot.cn/anthropic``. Importing it registers :class:`MoonshotAnthropicProvider` process-wide as ``moonshot-anthropic``.

.. rubric:: See also

:mod:`flowing.providers.anthropic_messages` for the Anthropic Messages format family.
:mod:`flowing.providers.moonshot` for the same platform's Chat Completions adapter.
:mod:`flowing.providers.moonshot_responses` for the same platform's Responses adapter.
"""
from __future__ import annotations

from typing import ClassVar
from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import register_provider

class MoonshotAnthropicProvider(AnthropicMessagesProvider):
    """Built-in Anthropic Messages adapter for the Moonshot Open Platform (``name="moonshot-anthropic"``).

    .. rubric:: Overview

    Select this adapter by setting the ``adapter`` field to ``"moonshot-anthropic"`` in a ``providers.yaml`` entry.

    .. rubric:: Configuration example

    .. code-block:: yaml

        moonshot-anthropic:
          adapter: moonshot-anthropic
          api_key: "{{env.MOONSHOT_API_KEY}}"

    .. rubric:: Behavior notes

    - The default base URL is ``https://api.moonshot.cn/anthropic``; the inherited Anthropic Messages provider appends the ``/v1/messages`` path. A ``base_url`` in the provider entry overrides this default, for example when using a proxy.
    - The base sends credentials in the ``x-api-key`` header. The endpoint has also been observed to accept ``Authorization: Bearer``.
    - The documented model IDs use the ``kimi-`` prefix, including ``kimi-k3`` and ``kimi-k2.7-code``. The adapter passes model IDs through without checking whether the endpoint currently offers them.
    - The model catalog describes ``kimi-k3`` as supporting the ``low``, ``high``, and ``max`` thinking levels. For ``kimi-k2.7-code``, the server applies that model's own thinking behavior. Live checks with default parameters have passed for text and image multi-turn requests.
    - Request and response mapping, reasoning-signature replay, usage normalization, and HTTP error classification follow :class:`~flowing.providers.anthropic_messages.AnthropicMessagesProvider`.

    .. seealso::

        :class:`~flowing.providers.anthropic_messages.AnthropicMessagesProvider` for the format-family contract.
        :class:`~flowing.providers.moonshot.MoonshotProvider` for the Chat Completions adapter.
        :class:`~flowing.providers.moonshot_responses.MoonshotResponsesProvider` for the Responses adapter.
    """
    name: ClassVar[str]
    default_base_url: ClassVar[str | None]
