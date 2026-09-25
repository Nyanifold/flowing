"""``flowing.providers.kimi_coding_anthropic`` provides the Kimi Code adapter for Anthropic Messages.

.. rubric:: Overview

This module implements the Anthropic Messages protocol for the Kimi Code membership coding endpoint at ``https://api.kimi.com/coding``. Importing it registers :class:`KimiCodingAnthropicProvider` process-wide as ``kimi-coding-anthropic``.

The endpoint also supports Chat Completions and Responses. Chat Completions is the default integration in :mod:`flowing.providers.kimi_coding`; this module provides the Anthropic Messages form used by tools that speak that protocol. Kimi Code credentials and model availability are separate from those of the Moonshot Open Platform.

.. rubric:: See also

:mod:`flowing.providers.anthropic_messages` for the Anthropic Messages format family.
:mod:`flowing.providers.kimi_coding` for the Chat Completions integration.
"""
from __future__ import annotations

from typing import ClassVar
from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import register_provider

class KimiCodingAnthropicProvider(AnthropicMessagesProvider):
    """Built-in Anthropic Messages adapter for Kimi Code (``name="kimi-coding-anthropic"``).

    .. rubric:: Overview

    Select this adapter by setting the ``adapter`` field to ``"kimi-coding-anthropic"`` in a ``providers.yaml`` entry. It uses the same Kimi Code credentials and model catalog as :class:`~flowing.providers.kimi_coding.KimiCodingProvider`; only the wire protocol differs.

    .. rubric:: Configuration example

    .. code-block:: yaml

        kimi-code-anthropic:
          adapter: kimi-coding-anthropic
          api_key: "{{env.KIMI_CODE_API_KEY}}"

    .. rubric:: Behavior notes

    - The default base URL is ``https://api.kimi.com/coding``; the inherited Anthropic Messages provider appends the ``/v1/messages`` path. A provider entry's ``base_url`` overrides this default, for example when using a proxy.
    - The base sends credentials in the ``x-api-key`` header. The endpoint has also been observed to accept ``Authorization: Bearer``.
    - The model IDs observed from ``/v1/models`` are ``k3``, ``k3-256k``, ``kimi-for-coding``, and ``kimi-for-coding-highspeed``. The adapter passes model IDs through without checking whether the endpoint currently offers them.
    - In the observed streaming response, a ``tool_use`` block starts with ``input: {}``, and tool arguments arrive in ``input_json_delta`` events. The inherited Anthropic Messages provider handles this event shape.
    - Request and response mapping, reasoning-signature replay, usage normalization, and HTTP error classification follow :class:`~flowing.providers.anthropic_messages.AnthropicMessagesProvider`.

    .. seealso::

        :class:`~flowing.providers.anthropic_messages.AnthropicMessagesProvider` for the format-family contract.
        :class:`~flowing.providers.kimi_coding.KimiCodingProvider` for the Chat Completions integration using the same endpoint.
        :class:`~flowing.providers.moonshot_anthropic.MoonshotAnthropicProvider` for the separate Moonshot Open Platform adapter.
    """
    name: ClassVar[str]
    default_base_url: ClassVar[str | None]
