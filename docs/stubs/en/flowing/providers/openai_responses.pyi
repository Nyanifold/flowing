"""The OpenAI Responses API format family.

.. rubric:: Overview

The :class:`OpenAIResponsesProvider` base class implements request and
response mapping for the OpenAI Responses API (``/responses``), including
input-item sequences, flat function declarations, and reasoning-item replay.
Built-in vendor adapters for Moonshot's Kimi endpoints subclass it and
override only vendor-specific details, such as the default endpoint,
credential source, and vendor-specific fields. See :mod:`flowing.providers`
for the explicit inheritance design and the reasons compatibility flags are
not used.

For tool results, content blocks from a TOOL message map to a top-level
``function_call_output`` item. The message-level ``tool_call_id`` maps to
``call_id``. A text-only result is one concatenated string. If the result
contains media, it becomes a mixed list of ``input_text`` and ``input_image``
items; the media is embedded directly and is not moved elsewhere.
``StructBlock`` is always serialized as text with
``json.dumps(ensure_ascii=False)``.

Assistant output is a top-level sequence of items (reasoning, message, and
function call). When replaying history, PROVIDER messages are also flattened
into top-level items. With ``store=False``, the server retains no conversation
state, so the full history is replayed on each call. Reasoning items are
returned with encrypted content through
``include=["reasoning.encrypted_content"]``; their JSON is carried in
``ThinkingBlock.signature`` and replayed unchanged. Assistant text is replayed
as an ``output_text`` message item whose ID comes from a per-request
incrementing counter (``msg_{n}``). A ``function_call`` item's ``arguments``
is a JSON string; parsing failure raises
:class:`flowing.errors.InvalidRequestError`.

.. seealso::

    :mod:`flowing.providers.openai_completions` and
    :mod:`flowing.providers.anthropic_messages` are the other format families.
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
from flowing.providers.provider import ModelConfigField, Provider, ProviderConfigField, ProviderDelta, ProviderResponse, Usage

class _HttpResponseError(Exception):
    """Internal carrier for the raw facts of a non-2xx transport response.

    This carrier is not part of the framework error hierarchy. ``_post``
    raises it for non-2xx responses; ``generate()`` catches it and maps it
    through ``_classify_error`` to a type from :mod:`flowing.errors`. Mock
    transport tests raise this exception to represent different status codes.
    """

    def __init__(self, status_code: int, body: Any=None, headers: dict[str, str] | None=None) -> None:
        ...

class OpenAIResponsesProvider(Provider):
    """Framework base class for the OpenAI Responses format family.

    .. rubric:: Overview

    This is the adapter base for OpenAI Responses endpoints. It maps the three
    fields of :class:`flowing.context.Context` to a ``/responses`` request
    body containing a system message, an input-item sequence, and flat
    function declarations. It maps the response's ``output[]`` items to
    :class:`ProviderResponse` and normalizes raw usage into :class:`Usage`
    (``input_tokens`` becomes ``input`` and
    ``fresh_input = input_tokens - cached_tokens``). Subclasses override the
    default endpoint (``default_base_url``), credential source, and
    vendor-specific fields.

    Do not instantiate this class directly. Runtime creates concrete vendor
    subclasses lazily, with one instance per ``providers.yaml`` entry; see
    :class:`Provider`.

    .. rubric:: Example

    .. code-block:: python

        from flowing.providers.openai_responses import OpenAIResponsesProvider
        from flowing.providers import register_provider

        @register_provider
        class MyEndpointProvider(OpenAIResponsesProvider):
            name = "my-endpoint"
            default_base_url = "https://llm.example.com/v1"

    .. rubric:: Behavioral notes

    - Request mapping: each ``Context.system_prompt`` segment becomes a
      system message. Each ``Context.messages`` item is mapped by kind into
      ``input[]``. PROVIDER messages are flattened into top-level reasoning,
      message, and function-call items rather than wrapped in an assistant
      message. ``Context.tools`` is assembled from an allowlist into flat
      function declarations, not the nested ``function`` shape used by Chat
      Completions. If ``model.max_output_tokens`` is not ``None``, it is sent
      as ``max_output_tokens``. Every request includes ``store: False`` so
      the server keeps no conversation state and the full history is replayed,
      and ``include: ["reasoning.encrypted_content"]`` so reasoning items
      include encrypted content needed for multi-turn replay with storage off.
    - Message metadata: USER, EVENT, PEER, and SUBAGENT map to ordinary
      ``user`` messages. The request contains only the role and content; it
      does not carry the original kind or source. The server cannot distinguish
      these message kinds or determine their source. SYSTEM and TOOL use their
      respective API mappings.
    - Generic passthrough: if the ``extra_body`` field from a ``models.yaml``
      entry (stored in ``ModelConfig._extra`` and not evaluated by Parsable)
      is a dictionary, merge it unchanged into the request body for
      vendor-specific parameters. A non-dictionary value raises
      :class:`flowing.errors.InvalidRequestError`. Merge order is base-class
      fields, then ``extra_body``, then adapter-specific fields; later values
      replace earlier values with the same key.
    - Reasoning replay: store each reasoning item's complete JSON in
      ``ThinkingBlock.signature``. On a later request, parse that JSON with
      ``json.loads`` and append it unchanged to ``input[]`` only if it is a
      dictionary. Skip the reasoning block if parsing fails, the value is not
      a dictionary, or no signature is present; without a signature the item
      cannot be replayed validly.
    - Preserve the order of ``output[]`` items when creating content blocks:
      reasoning becomes ``ThinkingBlock``, message becomes ``TextBlock``,
      and function call becomes ``ToolCallBlock`` with its ID from
      ``call_id``. If any ``function_call`` is present, set ``finish=False``;
      otherwise set ``finish=True``. Preserve the raw ``status`` as
      ``provider_data["stop_reason"]``.
    - Normalize usage as follows: ``input = input_tokens`` (including cached
      input); ``fresh_input = input_tokens - cached_tokens``;
      ``cache_read = input_tokens_details.cached_tokens``;
      ``cache_write = 0``; ``output = output_tokens``;
      ``reasoning = output_tokens_details.reasoning_tokens``; and
      ``total_tokens = total_tokens or input + output``. Preserve all raw
      usage fields in ``Usage.raw``.
    - Do not infer vendor capabilities from ``base_url``. Vendor differences
      belong in subclass overrides.
    - The service manages prefix caching automatically. The adapter sends no
      cache markers. For this format, ``PromptBlock.cache`` only indicates a
      byte-stability preference and has no request-level effect.

    .. seealso::

        :class:`flowing.providers.Provider` defines the abstract contract.
        :class:`flowing.providers.openai_completions.OpenAICompletionsProvider`
            implements the Chat Completions format family for the same vendor.
        :class:`flowing.providers.anthropic_messages.AnthropicMessagesProvider`
            implements another format family.
    """
    api_format: ClassVar[str]
    known_model_fields: ClassVar[frozenset[str]]
    model_fields: ClassVar[tuple[ModelConfigField, ...]]
    """Model-option descriptions for additional request-body fields."""
    default_base_url: ClassVar[str | None]
    """Subclass override for the vendor's official endpoint. An entry-level
    ``base_url`` takes precedence, for example when using a proxy."""
    config_fields: ClassVar[tuple[ProviderConfigField, ...]]
    """Fields for configuration tools; the endpoint default follows the adapter class."""
    async def _post(self, path: str, body: dict) -> dict:
        """POST once and return parsed JSON; this is the only network point and may be overridden.

        A non-2xx response raises :class:`_HttpResponseError` for the caller
        to classify. A timeout raises :class:`ProviderTimeoutError`, and a
        transport failure raises :class:`NetworkError`. Each call creates a
        new ``httpx.AsyncClient``; the connection pool is not reused across
        calls.
        """
        ...
    def _build_request(self, context: Context, model: ModelConfig) -> dict:
        """Map the three ``Context`` fields and model settings to a ``/responses`` request body."""
        ...
    def _map_tool(self, definition) -> dict:
        """Build a flat function declaration from the allowlisted ``name``, ``description``, and ``parameters`` fields."""
        ...
    def _map_content_blocks(self, msg: Message) -> tuple[str, list[MediaBlock]]:
        """Preprocess plain content blocks into concatenated text and media blocks, shared by text and tool-result content."""
        ...
    def _map_user_content(self, msg: Message) -> list[dict[str, Any]]:
        """Map user-side content to mixed ``input_text`` and ``input_image`` parts."""
        ...
    def _map_message(self, msg: Message, msg_n: dict) -> list[dict[str, Any]]:
        """Map one message to input items; a PROVIDER message is flattened into multiple top-level items."""
        ...
    def _map_response(self, resp: dict) -> ProviderResponse:
        """Map a recorded or live response dictionary to ``ProviderResponse`` and attach usage to its message."""
        ...
    def _classify_error(self, exc: '_HttpResponseError') -> ProviderError:
        """Classify an HTTP status code using the package's error table; this method neither retries nor catches a fallback error."""
        ...
    async def generate(self, context: Context, model: ModelConfig) -> ProviderResponse:
        """Make one non-streaming request; see the ``_*`` methods for mapping and error classification.

        .. rubric:: Behavioral notes

        - Map the assembled context to a ``/responses`` request body and POST
          it to ``/responses``. A non-2xx response is classified as a
          specific :mod:`flowing.errors` type and raised; this method neither
          retries nor catches it as a fallback.
        - Map a 2xx response to :class:`ProviderResponse` and attach usage to
          ``message.usage``.

        .. seealso:: This follows the contract of :meth:`flowing.providers.Provider.generate`.
        """
        ...
    def _usage_from_raw(self, raw_usage: dict) -> 'Usage | None':
        """Normalize raw usage into :class:`Usage` with the same accounting in streaming and non-streaming modes.

        ``input_tokens`` includes cached input. Read ``cached_tokens`` from
        ``input_tokens_details.cached_tokens`` and reasoning tokens from
        ``output_tokens_details.reasoning_tokens``.
        """
        ...
    def _stream_headers(self) -> dict[str, str]:
        """Return the streaming request headers, including ``Accept: text/event-stream``."""
        ...
    def _parse_tool_args(self, raw: str) -> dict:
        """Parse accumulated tool arguments with tolerant JSON repair.

        Following the same approach as pi-ai's ``parseStreamingJson``, first
        try strict parsing. If that fails, apply shallow repairs such as
        removing a trailing comma and adding missing closing delimiters. If
        parsing still fails, return ``{}`` instead of raising: a tool payload
        may be truncated when a stream finishes, and that should not crash
        the Turn. The non-streaming path in ``_map_response`` remains strict
        and reports incomplete arguments as an upstream failure.
        """
        ...
    async def generate_stream(self, context: Context, model: ModelConfig) -> Any:
        """Yield text, reasoning, and tool-call deltas from a true SSE stream.

        This overrides the base fallback, which calls ``generate()`` and
        wraps the complete response in one text delta. It parses each event
        from ``stream=true`` ``/responses`` responses. Events arrive as pairs
        of ``event: <type>`` and ``data: <json>`` lines. There is no
        ``[DONE]`` marker; the stream ends at ``response.completed``.

        - ``response.output_item.added`` opens a block for each reasoning,
          message, or function-call item. Assign ``content_index`` in
          first-seen order, following the OpenAI-family convention; all
          deltas for one logical block share one index.
        - Reasoning: ``response.reasoning_summary_text.delta`` and
          ``response.reasoning_text.delta`` produce ``kind="thinking"``
          deltas. ``response.reasoning_summary_part.done`` adds ``"\\n\\n"``
          between segments, matching the non-streaming summary concatenation.
        - Text: ``response.output_text.delta`` and
          ``response.refusal.delta`` produce ``kind="text"`` deltas.
        - Tools: append each ``response.function_call_arguments.delta`` to
          the tool's argument buffer without yielding a delta. Use the full
          string from ``response.function_call_arguments.done`` as the final
          value. When ``response.output_item.done`` arrives, yield a
          ``block`` delta with the complete :class:`ToolCallBlock`; its ID is
          ``call_id``, and its arguments are parsed with the tolerant parser.
        - When a reasoning item ends at ``response.output_item.done``, yield
          an empty-text thinking delta carrying
          ``signature=json.dumps(item)``. The Agent preserves the first
          signature while accumulating the message; this is required to
          replay reasoning unchanged on a later request. A message item needs
          no closing delta because its text has already been accumulated from
          the text deltas.
        - On ``response.completed`` (and the corresponding incomplete
          outcome), record usage and status, then expose them through the
          final delta's ``usage`` and ``provider_data["stop_reason"]``.
          Classify ``response.failed`` and ``error`` events through
          :meth:`_classify_error`. Read the status from the event's ``status``
          field, defaulting to 400 when it is absent.
        """
        ...
