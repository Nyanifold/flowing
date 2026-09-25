"""``flowing.providers.moonshot_responses`` provides the built-in Moonshot Open Platform adapter for OpenAI Responses.

.. rubric:: Overview

This module implements the Moonshot Open Platform Responses endpoint at ``api.moonshot.cn``. Importing it registers :class:`MoonshotResponsesProvider` process-wide as ``moonshot-responses``.

.. rubric:: See also

:mod:`flowing.providers.openai_responses` for the Responses format family.
:mod:`flowing.providers.moonshot` for the same platform's Chat Completions adapter.
"""
from __future__ import annotations

from typing import ClassVar
from flowing.providers.openai_responses import OpenAIResponsesProvider
from flowing.providers.provider import register_provider

class MoonshotResponsesProvider(OpenAIResponsesProvider):
    """Built-in Responses adapter for the Moonshot Open Platform (``name="moonshot-responses"``).

    .. rubric:: Overview

    Select this adapter by setting the ``adapter`` field to ``"moonshot-responses"`` in a ``providers.yaml`` entry. Set the model's provider field to the same registry key in ``models.yaml``.

    .. rubric:: Configuration example

    .. code-block:: yaml

        # providers.yaml
        moonshot-responses:
          adapter: moonshot-responses
          api_key: "{{env.MOONSHOT_API_KEY}}"

        # models.yaml
        kimi-k3-responses:
          provider: moonshot-responses
          model: kimi-k3

    .. rubric:: Behavior notes

    - The default base URL is ``https://api.moonshot.cn/v1``; the inherited provider sends requests to the ``/responses`` path. A ``base_url`` in the provider entry overrides this default, for example when using a proxy.
    - The adapter sends credentials in an ``Authorization: Bearer`` header, as defined by the Responses format family.
    - Live checks have passed for text and image multi-turn requests and true SSE streaming with the format family's default request parameters. The endpoint accepts the ``store`` and ``include`` fields and the standard Responses event stream without adapter-specific request or event handling.
    - Request construction, response mapping, usage normalization, streaming event handling, and HTTP error classification follow :class:`~flowing.providers.openai_responses.OpenAIResponsesProvider`.

    .. seealso::

        :class:`~flowing.providers.openai_responses.OpenAIResponsesProvider` for the format-family contract.
        :class:`~flowing.providers.moonshot.MoonshotProvider` for the Chat Completions adapter.
    """
    name: ClassVar[str]
    default_base_url: ClassVar[str | None]
