"""``flowing.providers.bedrock`` — Anthropic Messages adapter for AWS Bedrock.

.. rubric:: Overview

This module defines :class:`BedrockProvider`, which sends Anthropic Messages
requests through an AWS Bedrock endpoint. Importing the module registers the
adapter process-wide under the name ``bedrock``.

.. seealso::

    :mod:`flowing.providers.anthropic_messages` implements the shared message
        mapping.
"""

from __future__ import annotations

from typing import ClassVar
from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.provider import ProviderConfigField, register_provider

class BedrockProvider(AnthropicMessagesProvider):
    """Anthropic Messages adapter that sends requests to AWS Bedrock.

    Select this built-in adapter with ``adapter: bedrock`` in a provider entry.
    The inherited Anthropic Messages provider handles format mapping; this
    adapter supplies the endpoint and credential source.

    .. rubric:: Example

    .. code-block:: yaml

        # providers.yaml
        bedrock:
          adapter: bedrock
          base_url: https://bedrock.example.com
          aws_session_token: "{{env.AWS_SESSION_TOKEN}}"

    .. rubric:: Behavioral notes

    - This adapter has no default endpoint. Each provider entry must set
      ``base_url``; otherwise the inherited transport raises
      :class:`flowing.errors.FlowingError` when it sends a request.
    - A non-empty ``aws_session_token`` takes precedence over ``api_key`` as the
      credential returned by :meth:`get_credential`.
    - The inherited Anthropic transport sends that credential in the
      ``x-api-key`` header together with ``anthropic-version``. This adapter
      does not implement the full AWS credential chain, including credential
      discovery through environment variables or instance metadata. It also
      does not sign requests with AWS SigV4.
    - The credential is used for requests only; it must not be copied into
      messages, provided values, logs, or persisted data.

    .. seealso::

        :meth:`flowing.providers.Provider.get_credential` is the credential
            override point.
        :class:`flowing.providers.anthropic_messages.AnthropicMessagesProvider`
            implements the shared message mapping and HTTP transport.
    """

    name: ClassVar[str]
    config_fields: ClassVar[tuple[ProviderConfigField, ...]]
    """Required endpoint and optional AWS Session Token fields."""

    def get_credential(self) -> str | None:
        """Return the configured Bedrock credential, falling back to ``api_key``.

        :return: The non-empty ``aws_session_token`` value when configured;
            otherwise the inherited ``api_key`` value, or ``None`` if neither
            is available.

        .. rubric:: Behavioral notes

        The returned credential is sent by the inherited Anthropic transport
        in the ``x-api-key`` header. This method does not resolve credentials
        from AWS environment variables or instance metadata. It also does not
        sign requests with AWS SigV4. The credential must not be exposed in
        messages, provided values, logs, or persisted data.
        """
        ...
