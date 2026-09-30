"""The OpenAI Completions API format family.

.. rubric:: Overview

The :class:`OpenAICompletionsProvider` base class implements request and
response mapping for the OpenAI-compatible Chat Completions wire format.
Built-in adapters for DeepSeek, Kimi, Groq, and OpenRouter subclass it and
override only vendor-specific details, such as the default endpoint,
credential source, and vendor-specific fields. See :mod:`flowing.providers`
for the explicit inheritance design and the reasons compatibility flags are
not used.

For tool results, content blocks from a TOOL message map directly to the
OpenAI tool message, and the message-level ``tool_call_id`` maps to its
``tool_call_id`` field. ``StructBlock`` is always serialized as text with
``json.dumps(ensure_ascii=False)``. Chat Completions tool messages accept
text only, so media blocks are moved from the message into a synthetic user
message immediately after the tool message, with a fixed explanatory prompt.

There is no official field for reasoning content. Endpoints use four observed
field variants: ``reasoning_content``, ``reasoning_details``, ``reasoning``,
and ``reasoning_text``. This base class remembers which variant the endpoint
actually returned and writes reasoning blocks back under the same key when
replaying multi-turn history. ``tool_calls[].function.arguments`` is a JSON
string; if it cannot be parsed, the adapter raises
:class:`flowing.errors.InvalidRequestError`.

.. seealso::

    :mod:`flowing.providers.anthropic_messages` is another format family.
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

class OpenAICompletionsProvider(Provider):
    """Framework base class for the OpenAI Completions format family.

    .. rubric:: Overview

    This is the adapter base for OpenAI-compatible endpoints. It maps the
    three fields of :class:`flowing.context.Context` to a Chat Completions
    request body containing a system message, a ``messages`` array, and tool
    declarations. It maps the response to :class:`ProviderResponse` and
    normalizes raw usage into :class:`Usage` (``prompt_tokens`` becomes
    ``input`` and ``fresh_input = prompt_tokens - cached_tokens``). Subclasses
    override the default endpoint (``default_base_url``), credential source,
    and vendor-specific fields.

    Do not instantiate this class directly. Runtime creates concrete vendor
    subclasses lazily, with one instance per ``providers.yaml`` entry; see
    :class:`Provider`.

    .. rubric:: Example

    .. code-block:: python

        from flowing.providers import OpenAICompletionsProvider, register_provider

        @register_provider
        class MyEndpointProvider(OpenAICompletionsProvider):
            name = "my-endpoint"
            default_base_url = "https://llm.example.com/v1"

    .. rubric:: Behavioral notes

    - Request mapping: each ``Context.system_prompt`` segment becomes a
      system message. Each ``Context.messages`` item is mapped to a role
      according to its kind. ``Context.tools`` is assembled from an allowlist
      into function declarations. If ``model.max_output_tokens`` is not
      ``None``, it is sent as ``max_tokens``.
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
    - If the response contains ``tool_calls``, set ``finish=False``;
      otherwise set ``finish=True``. Preserve the raw ``finish_reason`` as
      ``provider_data["stop_reason"]``.
    - Normalize usage as follows: ``input = prompt_tokens``;
      ``fresh_input = prompt_tokens - cached_tokens``;
      ``cache_read = cached_tokens``; ``cache_write = 0``;
      ``output = completion_tokens``; and
      ``total_tokens = input + output``. Preserve all raw usage fields in
      ``Usage.raw``.
    - Do not infer vendor capabilities from ``base_url``. Vendor differences
      belong in subclass overrides.
    - The OpenAI service manages prefix caching automatically. The adapter
      sends no cache markers. For this format, ``PromptBlock.cache`` only
      indicates a byte-stability preference and has no request-level effect.

    .. seealso::

        :class:`flowing.providers.Provider` defines the abstract contract.
        :class:`flowing.providers.anthropic_messages.AnthropicMessagesProvider`
            is another format-family base class.
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
    _reasoning_dialect: str | None
    """The reasoning-field variant detected in an incoming response and
    remembered so that reasoning blocks use the same field when replayed."""
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
        """Map the three ``Context`` fields and model settings to a Chat Completions request body."""
        ...
    def _map_tool(self, definition) -> dict:
        """Build a tool schema using only the allowlisted ``name``, ``description``, and ``parameters`` fields."""
        ...
    def _map_content_blocks(self, msg: Message) -> tuple[str, list[MediaBlock]]:
        """Preprocess plain content blocks into concatenated text and a media-block list for text-only TOOL messages."""
        ...
    def _map_user_content(self, msg: Message) -> Any:
        """Map a user-side message's content: use ``image_url`` parts when it contains media, otherwise plain text."""
        ...
    def _map_message(self, msg: Message) -> list[dict[str, Any]]:
        """Map one message to Chat Completions message entries; one input may produce several entries when media is moved."""
        ...
    _REASONING_KEYS: ClassVar[tuple[str, ...]]
    """The four observed online reasoning-field variants, in the order
    checked when scanning an incoming response. The first present variant is
    selected and remembered."""
    def _map_response(self, resp: dict) -> ProviderResponse:
        """Map a recorded or live response dictionary to ``ProviderResponse`` and attach usage to its message."""
        ...
    def _classify_error(self, exc: '_HttpResponseError') -> ProviderError:
        """Classify an HTTP status code using the package's error table; this method neither retries nor catches a fallback error."""
        ...

    def __init__(self, config) -> None:
        ...
    async def generate(self, context: Context, model: ModelConfig) -> ProviderResponse:
        """Make one non-streaming request; see the ``_*`` methods for mapping and error classification.

        .. rubric:: Behavioral notes

        - Map the assembled context to a Chat Completions request body and
          POST it to ``/chat/completions``. A non-2xx response is classified
          as a specific :mod:`flowing.errors` type and raised; this method
          neither retries nor catches it as a fallback.
        - Map a 2xx response to :class:`ProviderResponse` and attach usage to
          ``message.usage``.

        .. seealso:: This follows the contract of :meth:`flowing.providers.Provider.generate`.
        """
        ...
    def _usage_from_raw(self, raw_usage: dict) -> 'Usage | None':
        """Normalize raw usage into :class:`Usage` with the same accounting in streaming and non-streaming modes.

        Select cached-token counts by vendor variant: first use
        ``prompt_tokens_details.cached_tokens``; if absent, fall back to
        DeepSeek's ``prompt_cache_hit_tokens`` and then Kimi's top-level
        ``cached_tokens``. The accounting matches the non-streaming path:
        ``input`` includes cached input and
        ``fresh_input = input - cache_read``.
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
        wraps the complete response in one text delta. It parses each chunk
        from ``stream=true`` Chat Completions responses used by the OpenAI and
        DeepSeek families. Chunks use ``data: <json>`` and end with
        ``[DONE]``.

        - Reasoning: inspect ``choices[0].delta.<variant>`` (for example,
          DeepSeek's ``reasoning_content``). In ``_REASONING_KEYS`` order,
          select the first non-empty value and yield it as a
          ``kind="thinking"`` delta. Remember the selected variant so the
          same field is used when replaying reasoning on a later request.
        - Text: a non-empty ``delta.content`` produces a ``kind="text"``
          delta.
        - Tools: accumulate ``delta.tool_calls[]`` by streaming ``index``,
          falling back to ``id`` when no index is present. The first observed
          ``id`` and ``name`` are used, and successive
          ``function.arguments`` fragments are concatenated. At the end of
          the stream, parse the complete arguments and yield a ``block``
          delta containing the complete :class:`ToolCallBlock`.
        - Usage and finish reason: send
          ``stream_options.include_usage`` in the request. A top-level
          ``usage`` value from any chunk replaces the final usage; it may
          arrive in a separate chunk with empty ``choices`` or in a DeepSeek
          finish chunk. Expose ``finish_reason`` through the final
          ``provider_data["stop_reason"]``. See
          :meth:`flowing.agent.Agent.provider_gen` for the source of
          ``finish_reason`` on a completed outcome.

        Assign ``content_index`` dynamically in first-seen order, matching
        the pi convention and text-block behavior. All deltas for one logical
        block share an index so the Agent can place them correctly while
        accumulating. Classify non-2xx responses and an ``error`` field in a
        chunk through :meth:`_classify_error`. Timeouts and transport
        failures follow :meth:`generate`.
        """
        ...
