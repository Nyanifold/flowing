"""The Anthropic Messages API format family.

.. rubric:: Overview

The :class:`AnthropicMessagesProvider` base class maps requests and responses
for the Anthropic Messages API, including the system array, content blocks,
``tool_use`` and ``tool_result``, and manual prefix caching through
``cache_control``. Built-in vendor adapters for the official Anthropic
endpoint and AWS Bedrock subclass it. See :mod:`flowing.providers` for the
family design.

For tool results, content blocks from TOOL messages map directly to
``tool_result`` blocks inside a user message. The message-level
``tool_call_id`` maps to ``tool_use_id``; ``tool_status="error"`` maps to
``is_error: true``. Media blocks are embedded natively in
``tool_result.content`` as images or documents, without being transferred
elsewhere.
``StructBlock`` is always serialized as text with
``json.dumps(ensure_ascii=False)``. Consecutive TOOL messages are combined
into one user message containing multiple ``tool_result`` blocks, as required
to pair parallel ``tool_use`` calls. Any non-TOOL message ends the sequence.

SYSTEM, USER, EVENT, PEER, and SUBAGENT messages mapped to ordinary user
messages carry only the role and content in the request. Their original kind
and source are not sent, so the server cannot distinguish these message kinds
or determine their source. TOOL uses the separate ``tool_result`` mapping.

On the response side, a thinking block's ``signature`` is an opaque string
that must be replayed unchanged on later calls. The ``input`` of a
``tool_use`` block is a parsed JSON object.

.. seealso::

    :mod:`flowing.providers.openai_completions` is another format family.
    :mod:`flowing.providers.openai_responses` implements the Responses format.
    :class:`flowing.providers.Provider` defines the abstract contract.
"""
from __future__ import annotations
from typing import Any, ClassVar
import json
import ssl
from flowing.context import Context
from flowing.errors import AuthenticationError, ContentPolicyError, ContextLengthError, FlowingError, InvalidRequestError, NetworkError, ProviderError, ProviderTimeoutError, RateLimitedError, RequestTooLargeError, ServerError
from flowing.message import MediaBlock, Message, MessageKind, StructBlock, TextBlock, ThinkingBlock, ToolCallBlock
from flowing.model import ModelConfig
from flowing.providers.provider import Provider, ProviderConfigField, ProviderDelta, ProviderResponse, Usage

class _HttpResponseError(Exception):
    """Internal carrier for a non-2xx transport response, before classification.

    This follows the same shape as the OpenAI-family carrier and preserves the
    raw response facts until the caller classifies them.
    """

    def __init__(self, status_code: int, body: Any=None, headers: dict[str, str] | None=None) -> None:
        ...

class AnthropicMessagesProvider(Provider):
    """Framework base class for the Anthropic Messages format family.

    .. rubric:: Overview

    This is the adapter base for the Anthropic Messages API family. It maps
    system arrays, content blocks, ``tool_use`` and ``tool_result`` values,
    and usage. Subclasses override only vendor-specific details such as the
    default endpoint, credential source, and vendor-specific fields.

    Do not instantiate this class directly. Runtime creates concrete vendor
    subclasses lazily, with one instance per ``providers.yaml`` entry; see
    :class:`Provider`.

    .. rubric:: Example

    .. code-block:: python

        from flowing.providers import AnthropicMessagesProvider, register_provider

        @register_provider
        class MyAnthropicProvider(AnthropicMessagesProvider):
            name = "my-anthropic"
            default_base_url = "https://my-proxy.example.com"

    .. rubric:: Behavioral notes

    - Message metadata: SYSTEM, USER, EVENT, PEER, and SUBAGENT map to ordinary
      ``user`` messages. The request contains only the role and content; it
      does not carry the original kind or source. The server cannot distinguish
      these message kinds or determine their source. TOOL maps to
      ``tool_result``, and PROVIDER maps to ``assistant``.
    - Manual prefix caching: only a ``PromptSegment`` with
      ``cache="static"`` receives ``cache_control: {"type": "ephemeral"}``.
      ``cache="dynamic"`` and ``cache="session"`` do not receive a cache
      marker; no marker means no caching.
    - If ``stop_reason == "tool_use"``, the response has ``finish=False``;
      otherwise it has ``finish=True``. The raw ``stop_reason`` is preserved
      in ``provider_data["stop_reason"]``.
    - For usage, ``fresh_input`` and ``output`` come directly from the native
      ``input_tokens`` and ``output_tokens`` fields; Anthropic's native input
      count excludes cached input. The adapter computes
      ``input = fresh_input + cache_read + cache_write``. It maps
      ``cache_creation_input_tokens`` and ``cache_read_input_tokens`` to the
      corresponding first-class counters and also preserves them in
      ``Usage.raw``.
    - Consecutive TOOL messages are combined into one user message containing
      multiple ``tool_result`` blocks. Anthropic requires all results for
      parallel ``tool_use`` calls in one assistant turn to appear in the
      immediately following user message; separate user messages are
      rejected by the API. Pairing IDs remain aligned with their blocks in
      message order. A non-TOOL message ends the sequence, and an isolated
      TOOL message is mapped on its own.

    .. seealso::

        :class:`flowing.providers.openai_completions.OpenAICompletionsProvider`
            is a contrasting format-family base class.
        :class:`flowing.context.PromptSegment` defines the three ``cache`` values.
    """
    api_format: ClassVar[str]
    known_model_fields: ClassVar[frozenset[str]]
    default_base_url: ClassVar[str | None]
    """Subclass override for the vendor's official endpoint. An entry-level
    ``base_url`` takes precedence, for example when using a proxy."""
    config_fields: ClassVar[tuple[ProviderConfigField, ...]]
    """Fields for configuration tools; the endpoint default follows the adapter class."""
    anthropic_version: ClassVar[str]
    """The Anthropic API version sent in the request header."""
    async def _post(self, path: str, body: dict) -> dict:
        """POST once and return the parsed JSON response, the only network point.

        Subclasses and tests may override this method.

        A non-2xx response raises :class:`_HttpResponseError`
        for the caller to classify. A timeout raises :class:`ProviderTimeoutError`,
        and a transport failure raises :class:`NetworkError`. Each call uses
        a new ``httpx.AsyncClient`` with a 120-second timeout. If neither the
        entry nor subclass supplies a ``base_url``, it raises
        :class:`flowing.errors.FlowingError` before making a request.
        """
        ...
    def _build_request(self, context: Context, model: ModelConfig) -> dict:
        """Map the three ``Context`` fields and model specification to an
        Anthropic Messages request body.
        """
        ...
    def _map_tool(self, definition) -> dict:
        """Build a tool schema from the allowlisted ``name``, ``description``, and ``parameters`` fields.

        Use ``params_schema or {}`` as ``input_schema.properties``. A
        property is listed in ``required`` exactly when its parameter
        definition has no ``default`` key.
        """
        ...
    def _map_plain_blocks(self, msg: Message) -> list[dict[str, Any]]:
        """Map text, structured, and media blocks to Anthropic content blocks.

        Media blocks are embedded natively in Anthropic content without being
        transferred elsewhere.
        """
        ...
    def _map_messages(self, messages) -> list[dict[str, Any]]:
        """Map messages to the Anthropic ``messages`` array, combining each consecutive TOOL run into one user message.

        Anthropic requires all ``tool_result`` blocks for parallel
        ``tool_use`` calls from one assistant turn to be in the immediately
        following user message. The framework's parallel-tool batch produces
        consecutive TOOL messages; mapping each to a separate user message
        would be rejected by the API. This method combines each consecutive
        run into one user message containing multiple ``tool_result`` blocks.
        It preserves the pairing between each ``tool_use_id`` and its block
        in message order. A non-TOOL message ends the run, and an isolated
        TOOL message is mapped on its own.
        """
        ...
    def _map_tool_result(self, msg: Message) -> dict[str, Any]:
        """Map one TOOL message to one ``tool_result`` block, for both combined and isolated forms.

        The message-level ``tool_call_id`` maps to ``tool_use_id``;
        ``tool_status="error"`` maps to ``is_error: true``. For an empty
        content value, the adapter supplies one empty text block
        (``{"type": "text", "text": ""}``) for a pending receipt returned by
        the Task path. A pending receipt from the
        async-generator path has content—the first yield and a “background
        task ID” block—and is mapped as an ordinary ``tool_result``.
        """
        ...
    def _map_message(self, msg: Message) -> list[dict[str, Any]]:
        """Map one message to Anthropic ``messages`` array entries.

        PROVIDER messages become assistant entries; TOOL messages become
        user entries containing one ``tool_result`` block. Every other
        message kind becomes a user entry using the plain content-block
        mapping.
        """
        ...
    def _map_response(self, resp: dict) -> ProviderResponse:
        """Map response content blocks and usage into ``ProviderResponse``.

        Recognized response blocks are ``text``, ``thinking``, and
        ``tool_use``; tool input defaults to an empty object. Usage is created
        only for a non-empty raw usage mapping, with cached input added to
        native ``input_tokens``. ``finish`` is false if a tool call exists or
        ``stop_reason`` is ``"tool_use"``. Preserve the model (defaulting to
        an empty string) and raw stop reason in the response.
        """
        ...
    def _classify_error(self, exc: '_HttpResponseError') -> ProviderError:
        """Classify a response status as a framework ``ProviderError`` subtype.

        Statuses 401/403 map to authentication, 429 to rate limiting, and
        413 to request-too-large errors. For 400, inspect the message for
        context-length or content-policy markers; otherwise use
        ``InvalidRequestError``. Statuses at least 500 become ``ServerError``;
        other statuses become ``ProviderError``.
        """
        ...
    async def generate(self, context: Context, model: ModelConfig) -> ProviderResponse:
        """Make one non-streaming request; see the ``_*`` methods for mapping and error classification.

        .. rubric:: Behavioral notes

        - Map the assembled context to an Anthropic Messages request body and
          POST it to ``/v1/messages``. A non-2xx response is classified as a
          specific :mod:`flowing.errors` type and raised; this method neither
          retries nor catches it as a fallback.
        - Map a 2xx response to :class:`ProviderResponse` and attach usage to
          ``message.usage``.

        .. seealso:: This follows the contract of :meth:`flowing.providers.Provider.generate`.
        """
        ...
    def _stream_headers(self) -> dict[str, str]:
        """Return streaming headers with the API version and optional credential.

        The headers include ``Accept: text/event-stream``,
        ``anthropic-version``, and ``Content-Type: application/json``. Add
        ``x-api-key`` only when :meth:`Provider.get_credential` returns a
        truthy value.
        """
        ...
    def _usage_from_anthropic(self, raw: dict) -> 'Usage | None':
        """Normalize non-empty Anthropic usage and return ``None`` when absent.

        Set ``fresh_input`` from ``input_tokens``, add cache-read and
        cache-creation tokens to obtain ``input``, set ``output`` from
        ``output_tokens``, and set ``reasoning`` to zero. Compute
        ``total_tokens`` as normalized input plus output, and preserve the raw
        fields in ``Usage.raw``.
        """
        ...
    def _parse_tool_input(self, raw: str) -> dict:
        """Parse partial JSON fragments for tool arguments into an object
        using tolerant parsing, as in the OpenAI family."""
        ...
    async def generate_stream(self, context: Context, model: ModelConfig) -> Any:
        """Override streaming generation and yield text, reasoning, and tool-call deltas.

        Parse the Anthropic Messages ``stream=true`` response frame by frame.
        Each frame is ``data: <json>``; its type is in the JSON ``type``
        field. The event sequence is ``message_start`` followed by zero or
        more ``content_block_start``, ``content_block_delta``, and
        ``content_block_stop`` events, then ``message_delta`` and
        ``message_stop``.

        - Text: a ``text_delta`` in ``content_block_delta`` produces a
          ``kind="text"`` delta.
        - Reasoning: a ``thinking_delta`` produces a ``kind="thinking"``
          delta. The signature comes from ``content_block_start`` and is
          attached to the first thinking delta. The Agent preserves it as
          ``ThinkingBlock.signature`` while accumulating the message; this is
          required for Anthropic multi-turn replay.
        - Tools: append each ``input_json_delta`` fragment from a ``tool_use``
          block. At ``content_block_stop``, yield a ``block`` delta containing
          the complete :class:`ToolCallBlock`.
        - Usage and stop reason: combine input and cache counts from
          ``message_start.usage`` with output counts from
          ``message_delta.usage`` into the final usage. Expose
          ``message_delta.delta.stop_reason`` through
          ``provider_data["stop_reason"]``.
        - Classify an ``error`` event in the stream, including an overloaded
          error, through :meth:`_classify_error` and raise it.
        """
        ...
