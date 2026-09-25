"""``flowing.providers.anthropic`` — Built-in adapter for Anthropic's official API.

.. rubric:: Overview

This module defines :class:`AnthropicProvider`, the built-in adapter for
Anthropic's Messages API. Importing the module registers it process-wide under
the name ``anthropic``.

.. seealso::

    :mod:`flowing.providers.anthropic_messages` implements the shared message
        format.
"""

from __future__ import annotations

from typing import ClassVar
from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import register_provider

class AnthropicProvider(AnthropicMessagesProvider):
    """Built-in adapter for Anthropic's official Messages API.

    The module registers this adapter under the name ``anthropic``.
    Select it with ``adapter: anthropic`` in a provider entry.

    .. rubric:: Example

    .. code-block:: yaml

        # providers.yaml
        anthropic:
          adapter: anthropic
          api_key: "{{env.ANTHROPIC_API_KEY}}"

    .. rubric:: Behavioral notes

    - The default endpoint is ``https://api.anthropic.com``. A provider entry's
      ``base_url`` overrides it, for example when using a proxy.
    - The adapter reads its credential from the provider entry's ``api_key``.
      The inherited transport sends it in ``x-api-key`` together with the
      ``anthropic-version`` header.
    - To request manual prompt-prefix caching, mark the applicable prompt
      segments with ``cache="static"``. The inherited format adapter then adds
      ``cache_control: {"type": "ephemeral"}`` to those segments.

    .. seealso::

        :class:`flowing.providers.anthropic_messages.AnthropicMessagesProvider`
            implements the shared Anthropic Messages mapping and transport.
    """

    name: ClassVar[str]

    default_base_url: ClassVar[str | None]
