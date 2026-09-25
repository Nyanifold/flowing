"""The Provider mechanism layer: contracts, call results, registration, and lazy instances.

.. rubric:: Overview

This module contains the core Provider-side mechanisms: the :class:`Provider`
abstract base; complete call results (:class:`ProviderResponse`), streaming
deltas (:class:`ProviderDelta`), and token-usage records (:class:`Usage`);
entry configuration (:class:`ProviderConfig`); the built-in test double
:class:`FakeProvider`; the adapter registration decorator
(:func:`register_provider`); the Runtime-level lazy instance table
(:class:`ProviderRegistry`); and the ``providers.yaml`` loader
(:func:`load_provider_candidates`).

The three adapter-family base classes and built-in vendor adapters are in
:mod:`flowing.providers.openai_completions`,
:mod:`flowing.providers.openai_responses`, and
:mod:`flowing.providers.anthropic_messages`. Package-wide contracts—explicit
inheritance, lazy creation, the distinction between adapter classes and
Provider entries, the ``providers.yaml`` schema, error classification, and
credential security—are documented in :mod:`flowing.providers`.
"""
from __future__ import annotations
import os
import re
import warnings
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from ruamel.yaml import YAML
from flowing.context import Context
from flowing.errors import ProviderNameConflictError
from flowing.message import ContentBlock, Message
from flowing.model import ModelConfig

class ProviderConfig(dict[str, Any]):
    """The resolved configuration container for one Provider entry.

    .. rubric:: Overview

    This mapping contains all resolved fields for one entry (a key in
    ``providers.yaml``). It is bound to a :class:`Provider` instance during
    construction. ``Provider.generate()`` does not accept a configuration
    argument; the adapter reads its configuration from ``self.config``.
    By default, :meth:`Provider.get_credential` reads
    ``self.config.get("api_key")``.

    The set of fields is open. The adapter reads every field other than the
    common ``adapter``, ``api_key``, and ``base_url`` fields itself; for
    example, a Bedrock adapter may read ``aws_region``. The framework core
    does not interpret adapter-specific fields.

    .. rubric:: Example

    .. code-block:: python

        # Usually created by load_provider_candidates while loading the file.
        # Applications may also construct it directly.
        config = ProviderConfig({
            "adapter": "deepseek",
            "api_key": "sk-...",  # {{env.VAR}} was replaced during loading.
            "base_url": "https://proxy.company.com/deepseek",
        })

    .. rubric:: Behavioral notes

    - Any ``{{env.VAR}}`` reference must already have been replaced when this
      object is constructed. The loader replaces a missing variable with an
      empty string and emits a warning, so this object never receives an
      unresolved ``{{env.`` reference. An empty credential may cause a 401 on
      the entry's first call, which is dispatched through
      ``on_provider_error``.
    - Treat the configuration as read-only after constructing the Provider.
      Mutating it at runtime is not a supported operation.
    - This class does not validate a schema. The loader uses the ``adapter``
      key only to select the Provider class.
    - This configuration can contain credentials. Do not write it to
      messages, ``_provided``, or persisted files. See the security section
      in the :mod:`flowing.providers` package documentation.

    .. seealso::

        :class:`Provider` is the only consumer of this configuration.
        :func:`register_provider` maps adapter names to classes.
    """
    ...

class ProviderDelta:
    """One streaming increment delivered as the value of ``on_provider_delta``.

    .. rubric:: Overview

    In streaming mode, the Provider creates one instance for each output
    increment. ``Agent.provider_gen()`` appends each increment to
    ``Message.content`` and dispatches it to the ``on_provider_delta`` hook.
    This hook is the only observation point for deltas.

    The non-streaming path also produces one delta. With ``stream=False``,
    ``provider_gen()`` creates a single full-response delta after receiving
    the complete response. Its content represents one increment from empty
    content to the full result. Both paths use the same delta format, so a
    subscriber can rely on receiving at least one delta per
    ``provider_gen()`` call.

    Deltas are ephemeral: they are neither persisted nor added to the message
    tree. The accumulated message is persisted, including with
    ``partial=True`` if streaming is interrupted.

    .. rubric:: Example

    .. code-block:: python

        from flowing import Agent, on

        class RenderAgent(Agent):
            @on("on_provider_delta")
            def _print_text(self, delta):
                if delta.kind == "text" and delta.by == "_turn":
                    print(delta.text, end="")

    .. rubric:: Behavioral notes

    - Within one response, ``content_index`` never decreases. Deltas for one
      content block arrive in order; concatenating their ``text`` values in
      that order gives the block's complete text.
    - ``provider_gen()`` overwrites ``by`` with the origin marker, which an
      adapter neither supplies nor can forge. A main Turn uses ``"_turn"``;
      a side query uses ``"_side"``. Values beginning with an underscore
      are reserved by the framework. The ``on_provider_delta`` hook declares
      ``match_on="by"`` so handlers can filter registrations by origin
      pattern.
    - The hook is observational. A handler's edits to a delta do not change
      the content already accumulated because ``provider_gen()`` discards
      the dispatch return value and does not write it back. To change the
      generated content, use ``after_provider_gen`` to edit the complete
      message.
    - If streaming is aborted, accumulated content is persisted as a message
      with ``partial=True``. No further deltas are dispatched after the
      cancellation point.
    - Only the final delta carries ``usage`` and ``provider_data``; earlier
      deltas carry ``None``. These fields transport the final values so
      ``provider_gen()`` can add them to the assembled response message.

    .. seealso::

        :meth:`Provider.generate_stream` produces deltas.
        :class:`flowing.message.Message` documents the meaning of ``partial``.
    """
    kind: str
    """The delta kind, represented by an open string. Built-in kinds are
    ``"text"`` and ``"thinking"``. An adapter may emit another kind, such as
    ``"tool_use"``; consumers can filter by kind."""
    text: str
    """The incremental text carried by this delta. A non-text delta may carry
    an empty string."""
    content_index: int
    """The index of this delta's content block in the message's ``content``
    list. This identifies blocks in responses containing, for example, text,
    reasoning, and tool calls. Indices start at zero and never decrease."""
    by: str | None
    """The origin marker, overwritten by ``provider_gen()`` and not supplied
    by the adapter: ``"_turn"`` for a main Turn and ``"_side"`` for a side
    query. Values beginning with an underscore are reserved by the framework.
    The ``on_provider_delta`` hook filters on this field with
    ``match_on="by"``."""
    message_id: str | None
    """The ID of the assistant message to which this delta belongs. The Agent
    assigns it at the dispatch boundary; an adapter neither supplies nor can
    forge it.

    ``provider_gen()`` creates the ID before streaming starts and attaches it
    to every delta. It uses that same ID for the persisted message when the
    stream ends. Observers can therefore group deltas by this ID from the
    first delta, and the ID is also the ID of the final message in the tree.
    Like ``by``, this is an Agent-added field; the Provider layer does not
    manage message IDs."""
    signature: str | None
    """A thinking-block signature carried only by thinking deltas from the
    Anthropic family; all other deltas use ``None``.

    Anthropic requires the thinking block's ``signature`` to be replayed
    unchanged in assistant messages on later requests. Otherwise, the API
    rejects the request. In streaming mode, the signature arrives with the
    ``kind="thinking"`` delta (the ``content_block_start`` event contains the
    full value). While accumulating reasoning blocks,
    ``provider_gen()`` preserves the first signature it sees as the final
    ``ThinkingBlock.signature``."""
    usage: 'Usage | None'
    """The final usage for this call. Only the final delta carries a value;
    all earlier deltas carry ``None``.

    In streaming mode, ``provider_gen()`` assembles the complete response.
    The adapter places usage on this field in the final delta, and
    ``provider_gen()`` copies it to ``message.usage`` on the assembled
    message. The non-streaming path does not use this field; its usage is
    attached directly to the message returned by :meth:`Provider.generate`."""
    block: ContentBlock | None
    """A complete content block for non-text content, such as a
    ``ToolCallBlock`` assembled at the end of a stream.

    Text and reasoning deltas are accumulated piece by piece from ``text``.
    Structured content such as a tool call cannot be reconstructed losslessly
    from text fragments by ``provider_gen()``, so the adapter delivers it as a
    complete block at the end of streaming. ``provider_gen()`` places the
    block in the assembled message at ``content_index``. A delta carrying
    this field has an empty ``text`` value."""
    provider_data: dict[str, Any] | None
    """Provider-specific metadata for the response, including at least the
    raw ``stop_reason``. Only the final delta carries a value; all earlier
    deltas carry ``None``.

    In streaming mode, the adapter places this metadata on the final delta,
    and ``provider_gen()`` copies it to the assembled response. The
    non-streaming path does not use this field; the response returned by
    :meth:`Provider.generate` carries ``provider_data`` directly."""

class Usage:
    """Token usage for one model call: seven counters and the raw fields.

    .. rubric:: Overview

    Each adapter maps the Provider's raw response to this normalized record,
    which is part of :class:`ProviderResponse`'s result message. Consumers
    such as cost, budget, statistics, and UI code can use the counters without
    knowing which Provider produced them.

    .. rubric:: Example

    .. code-block:: python

        from flowing.providers import Usage

        usage = Usage(input=10, fresh_input=6, output=5,
                      cache_read=4, cache_write=0, reasoning=0,
                      total_tokens=15)
        assert usage.total_tokens == usage.input + usage.output
        assert usage.input == (usage.fresh_input + usage.cache_read
                               + usage.cache_write)

    .. rubric:: Behavioral notes

    - Each of the seven counters is a non-negative ``int``. An adapter fills
      a counter with zero when the Provider has no corresponding concept,
      such as caching or reported reasoning tokens. “Not reported” is
      represented only at the whole-record level: ``message.usage is None``.
      Individual counters are not ``None``.
    - Adapters are responsible for maintaining these equalities; the
      framework does not validate them at runtime:
      ``input == fresh_input + cache_read + cache_write`` and
      ``total_tokens == input + output``.
    - ``reasoning`` is a subset annotation on ``output``, not a separate
      additive category. It can be aggregated to report reasoning tokens for
      a Turn, but it is not counted again in ``total_tokens``.
    - If the Provider returns no usage, ``message.usage`` is ``None`` and no
      ``Usage`` instance is created. An empty ``raw`` dictionary is valid.
    - ``Message.usage`` is the sole authoritative location for this record.
      Only PROVIDER messages carry it, and it is persisted with the message.
      The Turn layer appends references to the same usage objects from each
      successful response message to ``TurnContext.usages`` and sums each
      counter into ``TurnResult.token_usage`` when the Turn ends. The result
      is ``None`` if the accumulator is empty. Aggregation always occurs,
      even if no consumer uses the result.
    - The framework core does not use ``raw`` to make decisions. Undocumented
      fields in ``raw`` are not part of the stable contract.

    .. seealso::

        :class:`flowing.message.Message` owns this record in its authoritative
        ``usage`` field.
    """
    input: int
    """All input tokens. Adapters maintain
    ``input == fresh_input + cache_read + cache_write``."""
    fresh_input: int
    """New input tokens excluding cached input; the primary measure for
    billing and cache analysis."""
    output: int
    """Output tokens. ``reasoning`` is a subset annotation on this value."""
    cache_read: int
    """Input tokens served from cache. An adapter fills this with zero when
    its Provider has no cache concept."""
    cache_write: int
    """Input tokens written to cache. The same zero-filling rule applies."""
    reasoning: int
    """Reasoning tokens, annotated as a subset of ``output`` and not counted
    again in ``total_tokens``. An adapter fills this with zero if the Provider
    does not report reasoning tokens. The aggregate represents reasoning
    tokens across the current Turn."""
    total_tokens: int
    """Total token count, equal to ``input + output``."""
    raw: dict[str, Any]
    """All raw usage fields returned by the Provider, preserved unchanged.
    Undocumented fields are not stable. The framework core does not depend on
    this mapping, and Turn aggregation does not sum it; the aggregate's
    ``raw`` value is an empty dictionary. Consumers that need per-call raw
    fields can read them through ``after_provider_gen``."""

class ProviderResponse:
    """The complete result of one model call and the sole Provider/Turn-loop contract.

    .. rubric:: Overview

    This is the return type of ``Provider.generate()`` and
    ``Agent.provider_gen()``. The Turn loop sees a complete response whether
    or not the underlying Provider streamed. Keeping a complete response at
    this boundary keeps streaming outside the logical Turn loop.

    .. rubric:: Example

    .. code-block:: python

        from flowing.message import Message, MessageKind, TextBlock
        from flowing.providers import ProviderResponse, Usage

        # Inside an adapter's generate() method, attach usage to the message.
        msg = Message(kind=MessageKind.PROVIDER,
                      content=[TextBlock(text="Hello")])
        msg.usage = Usage(input=12, fresh_input=7, output=5,
                          cache_read=5, cache_write=0, reasoning=0,
                          total_tokens=17)
        response = ProviderResponse(message=msg,
                                    model="claude-sonnet-4-6",
                                    finish=True)
        assert response.message.usage.total_tokens == 17

        # When provider_gen() detects an abort, it returns a cancellation
        # response without raising or requiring the adapter to participate.
        response = ProviderResponse(message=None, finish=False,
                                    cancelled=True)

    .. rubric:: Behavioral notes

    - ``message`` is the model-produced message. Its kind is
      ``MessageKind.PROVIDER``; it is created inside the Turn and is not put
      in the queue. The Turn loop appends it to the message-level tree. On
      the abort path it is ``None``, so callers must check that it is not
      ``None`` before appending it.
    - This object does not carry usage. The sole authoritative location is
      ``message.usage``, attached by the adapter and persisted with the
      message. Hooks and Composables read it through
      ``response.message.usage`` after checking that ``message`` is not
      ``None``. The Turn layer appends references to the same objects to
      ``TurnContext.usages`` and sums them into ``TurnResult.token_usage`` at
      the end of the Turn. If an abort occurs before a message is assembled,
      ``message`` is ``None`` and the framework does not retain tokens
      already spent. A plugin that requires complete per-call billing can
      capture usage in the adapter layer.
    - ``model`` is the model ID reported for the response and is used for
      observation and billing. It need not match the requested
      ``ModelConfig.model`` and is not copied back into another structure. In
      streaming mode, the Agent fills it with the requested model ID after
      accumulating deltas. In non-streaming mode, it contains the model ID
      returned by the server.
    - ``finish`` is a Provider-level value meaning that the Provider has
      completed this response with no pending tool call. The Agent decides
      whether the Turn closes (``finish or cancelled`` leads to
      ``Message.turn_end``); this field does not close the Turn by itself.
      The framework does not enforce how adapters determine it. A recommended
      default is ``False`` when the response contains a ``tool_call`` block
      and ``True`` for other outcomes such as stop, length, or error.
    - ``cancelled`` marks a canceled Turn. When it is ``True``, ``finish``
      remains ``False`` because the Provider did not complete an interrupted
      stream, and ``message`` may be ``None``. On other paths, ``message`` is
      not ``None``. ``after_turn`` handlers can use this field to distinguish
      normal completion from cancellation.
    - ``provider_data`` transparently carries Provider-specific metadata such
      as the raw ``stop_reason``. The framework core does not use its contents
      to make decisions; undocumented fields are not stable.
    - This object does not carry errors. Failures are raised as classified
      exceptions from :mod:`flowing.errors`, not wrapped in a response.

    .. seealso::

        :meth:`Provider.generate` produces this response.
        :meth:`flowing.agent.Agent.provider_gen` consumes it and selects the
        streaming mode.
        :class:`flowing.agent.TurnContext` is the execution-time carrier for
        one logical Turn.
    """
    message: Message | None
    """The model-produced message (``kind=MessageKind.PROVIDER``), created
    inside the Turn and not put in the queue. On abort this is ``None``; check
    ``if response.message is not None`` before appending it. See
    :class:`flowing.message.Message`."""
    finish: bool
    """A Provider-level value: ``True`` means the Provider completed this
    response with no pending tool call; ``False`` means it returned a tool
    call or the response was interrupted. The Agent decides whether the Turn
    closes (``finish or cancelled`` leads to ``Message.turn_end``). The
    adapter determines this value; see the class documentation for the
    recommended default."""
    model: str
    """The actual response model ID, recorded as a ``str``. It need not match
    the requested ``ModelConfig.model`` and is not copied back into another
    structure."""
    cancelled: bool
    """Whether the Turn was canceled. When ``True``, ``finish`` remains
    ``False`` because the Provider did not complete, and ``message`` may be
    ``None``. ``after_turn`` can use this to distinguish outcomes. Defaults
    to ``False``."""
    by: str | None
    """The origin marker overwritten by ``provider_gen()`` and not supplied
    by the adapter: ``"_turn"`` for the main Turn and ``"_side"`` for a side
    query. Values beginning with an underscore are reserved by the framework.
    The ``after_provider_gen`` hook declares ``match_on="by"`` so handlers
    can filter registrations by origin pattern. Defaults to ``None``."""
    provider_data: dict[str, Any]
    """Provider-specific metadata, such as the raw ``stop_reason``, passed
    through transparently. The framework core does not use its contents to
    make decisions; undocumented fields are unstable. Defaults to an empty
    dictionary."""

class Provider(ABC):
    """Abstract base for Provider adapters; define one adapter class per API format and vendor.

    .. rubric:: Overview

    A Provider is the execution endpoint for a model call. Runtime lazily
    creates at most one instance for each ``providers.yaml`` entry, when that
    entry is first requested through ``provider_registry.get(entry_name)``.
    It then caches the instance by entry name. The adapter class is registered
    process-wide with :func:`register_provider`, and one adapter class may be
    instantiated for multiple entries with different configurations.

    .. rubric:: Design

    - Format differences are expressed through subclass overrides, making
      them visible to the type system and IDEs. The framework does not use
      compatibility flags or runtime format detection; see the package
      documentation.
    - Configuration is bound to the instance at initialization.
      ``generate()`` has no configuration parameter because the configuration
      is static within the process after construction.
    - Lazy creation avoids startup costs. If 20 entries are configured but
      only one is used, the other 19 incur no connection-pool or HTTP-session
      cost.

    .. rubric:: Example

    .. code-block:: python

        from flowing.providers import OpenAICompletionsProvider, register_provider

        @register_provider
        class MyProvider(OpenAICompletionsProvider):
            name = "my"

    .. code-block:: yaml

        # providers.yaml: separate entries bind separate credentials for one adapter.
        my-team:
          adapter: my
          api_key: "{{env.MY_TEAM_KEY}}"
          base_url: https://proxy.company.com/my

    .. rubric:: Testing guidance

    The built-in :class:`FakeProvider` is a minimal test double. Assign
    ``generate_fn`` and optionally ``stream_fn`` after construction; it also
    records received contexts in ``received``. For test doubles with domain
    logic, subclass this class in the test module and register it under a
    test-specific adapter name with :func:`register_provider` during import.
    Such a test double remains subject to this contract: it must not read or
    write the message tree or persist data, and it must classify exceptions
    with :mod:`flowing.errors`. Recording received contexts for assertions is
    allowed, but tests should not assert the framework's internal call order.

    .. rubric:: Behavioral notes

    - Runtime creates an instance only when its entry name is first
      requested, then returns the same instance for later requests of that
      entry.
    - A registered Provider subclass must define a non-empty class attribute
      ``name``, which is its globally unique adapter registration key. The
      framework base class determines ``api_format``; subclasses normally do
      not override it.
    - A Provider does not read ``model_tag`` or interpret model-tag mappings.
      It does not retry or fall back to another Provider; retry policy belongs
      to a Composable such as ``use_retry()``. It does not cache responses.
    - Construction does not establish a network connection. Connection costs
      are deferred until the first ``generate()`` call, preserving the
      zero-startup-cost behavior of lazy creation.

    .. seealso::

        :class:`OpenAICompletionsProvider` and
        :class:`AnthropicMessagesProvider` determine two API-format families.
        :func:`register_provider` registers adapter classes.
        :class:`flowing.runtime.Runtime` owns ``provider_registry``.
    """
    name: ClassVar[str]
    """The adapter name and :func:`register_provider` key, such as
    ``"deepseek"``. It must be globally unique. The ``adapter`` field in a
    ``providers.yaml`` entry uses this name to select the class. A conflict
    requires ``override=True``; this type-level name is distinct from the
    Provider entry name, which identifies a credential identity."""
    api_format: ClassVar[str]
    """The API format identifier, such as ``"openai_completions"`` or
    ``"anthropic_messages"``. The framework family base class determines
    this value. Business code must not branch on it; subclasses express
    format differences through overrides."""
    known_model_fields: ClassVar[frozenset[str]]
    """A declarative set of model-metadata field names, such as
    ``thinking_budget``. The framework currently does not read this
    attribute: :mod:`flowing.model` uses a fixed built-in field set when
    separating ``ModelConfig`` fields, and stores other fields in
    ``ModelConfig._extra``. This attribute remains available to future
    consumers and third-party tools; the base class defaults it to an empty
    ``frozenset``."""
    config: ProviderConfig
    """The entry configuration, including credentials, bound to this
    instance at construction. Treat it as read-only and do not expose its
    credentials in messages, ``_provided``, or persisted data. See
    :class:`ProviderConfig`."""
    def __init__(self, config: ProviderConfig) -> None:
        """Construct a Provider instance from one entry's configuration.

        .. rubric:: Overview

        This is the only construction entry point in the lazy-creation path:
        Runtime calls ``adapter_cls(config)`` when an entry is requested for
        the first time.

        .. rubric:: Example

        .. code-block:: python

            from flowing.providers import DeepSeekProvider, ProviderConfig

            provider = DeepSeekProvider(ProviderConfig({"api_key": "sk-..."}))

        .. rubric:: Behavioral notes

        - The loader has already replaced every ``{{env.VAR}}`` reference in
          ``config`` before construction.
        - After construction, ``self.config is config``. Construction does
          not establish a network connection or perform I/O; this is required
          for lazy creation to have no startup cost.
        - A missing ``api_key`` does not cause a construction-time error.
          The first ``generate()`` call reports missing credentials as an
          ``AuthenticationError``. Some entries, such as local proxies using
          ``base_url``, may not require a key.

        .. seealso:: :class:`ProviderConfig` is the configuration container.
        """
        ...
    async def generate(self, context: Context, model: ModelConfig) -> ProviderResponse:
        """Make one non-streaming request and return its complete response.

        .. rubric:: Overview

        This is the core Provider contract. It accepts an assembled context
        and resolved model specification, makes one API call, and returns a
        complete :class:`ProviderResponse`. Before each call,
        ``Agent.provider_gen()`` resolves model fields and assembles the
        context. This method interprets no framework fields other than
        ``PromptBlock.cache``, which is an intent marker for the adapter (for
        example, Anthropic uses ``cache="static"`` to set
        ``cache_control``). Caching itself occurs on the Provider side.

        .. rubric:: Example

        .. code-block:: python

            response = await provider.generate(context, model)
            if response.finish:
                ...  # The Provider completed this response; the logical Turn can end.

        .. rubric:: Behavioral notes

        - ``model`` must already have been resolved with
          ``ModelConfig.resolve()`` so that all of its fields are static.
          The caller assembles ``context`` immediately before the call.
          ``context.messages`` may be empty for a call with only a system prompt.
        - The implementation maps ``context.system_prompt``,
          ``context.tools``, and ``context.messages`` to the API request, maps
          model metadata to request parameters, and sets ``finish`` according
          to the :class:`ProviderResponse` guidance. The adapter is the only
          layer that interprets model-field meanings.
        - Usage is not stored on ``ProviderResponse`` itself. The adapter
          attaches it to ``message.usage``, the authoritative location that
          is persisted with the message. If the Provider returns no usage,
          ``message.usage`` remains ``None``. If ``message`` is ``None`` on an
          abort path, there is no carrier for usage and it is not retained.
        - The caller detects aborts in ``Agent.provider_gen()`` by racing the
          call against cancellation. If an in-flight call is canceled,
          ``asyncio.CancelledError`` is injected at an await point in this
          method. The adapter must not catch it and must let it propagate
          unchanged. It inherits from ``BaseException``, so a normal
          ``except Exception`` used for error classification does not catch
          it. The caller synthesizes the canceled response; cancellation is a
          normal termination path.
        - The adapter must raise a specific error from
          :mod:`flowing.errors` for an underlying failure. This method does
          not catch errors as a fallback or retry the request; see the package
          documentation for classifications.
        - This method does not read or write the message tree, persist data,
          or trigger hooks. Those responsibilities belong to the Agent layer.

        :raises flowing.errors.ContextLengthError:
            The context exceeds the model's limit. Like other call-time
            errors, it is dispatched through ``on_provider_error``. The
            default policy does not retry it; a handler is responsible for
            shortening the context or selecting another model.
        :raises flowing.errors.RateLimitedError:
            The Provider is rate limited (HTTP 429). This is retryable if the
            policy layer, such as ``use_retry()``, chooses to retry.
        :raises flowing.errors.ServerError:
            The server failed with a 5xx response. This is retryable.
        :raises flowing.errors.NetworkError:
            A network-layer failure occurred. This is retryable.
        :raises flowing.errors.ProviderTimeoutError:
            The call timed out. This is retryable.
        :raises flowing.errors.AuthenticationError:
            Credentials were rejected (HTTP 401 or 403). Retrying does not
            repair the credentials.
        :raises flowing.errors.InvalidRequestError:
            The request is invalid and is not retryable.
        :raises flowing.errors.ContentPolicyError:
            The Provider rejected the content under its safety policy. This
            is not retryable.
        :raises flowing.errors.RequestTooLargeError:
            The request body exceeded the byte-size limit (HTTP 413). This is
            not retryable.
        :raises flowing.errors.QuotaExhaustedError:
            The quota or balance is exhausted and is not retryable. Built-in
            adapters classify every HTTP 429 as ``RateLimitedError`` rather
            than distinguishing exhausted quota; this error type is available
            to adapters that can make that distinction.

        .. seealso::

            :meth:`generate_stream` is the streaming variant.
            :meth:`flowing.agent.Agent.provider_gen` selects the mode and calls
            this method.
        """
        ...
    def generate_stream(self, context: Context, model: ModelConfig) -> AsyncIterator[ProviderDelta]:
        """Yield deltas for streaming; ``provider_gen()`` accumulates and forwards them.

        .. rubric:: Overview

        This is the streaming variant. The async iterator yields
        :class:`ProviderDelta` objects. When ``stream=True`` (the default),
        ``Agent.provider_gen()`` selects this method, accumulates the deltas
        into one message with ``partial=True``, dispatches each one through
        ``on_provider_delta``, and returns a complete :class:`ProviderResponse`
        after the stream ends.

        The base implementation calls :meth:`generate` and yields the
        complete response text as one delta. Thus an adapter can support
        streaming mode without overriding this method; an adapter that
        supports true streaming overrides it.

        .. rubric:: Example

        .. code-block:: python

            async for delta in provider.generate_stream(context, model):
                print(delta.text, end="")

        .. rubric:: Behavioral notes

        - An override must yield deltas in arrival order, with a
          non-decreasing ``content_index``. ``provider_gen()`` checks for
          cancellation between deltas and races cancellation against waiting
          for the next delta. If the in-flight ``__anext__`` is canceled,
          ``CancelledError`` is injected at an await point in the generator.
          When iteration ends, ``provider_gen()`` calls ``aclose()``. Both
          paths should close the underlying HTTP stream; the adapter does not
          need to check the cancellation signal itself.
        - The default implementation calls ``generate()`` and yields the
          complete message text as one delta. Structured blocks without a
          ``text`` attribute are emitted as individual ``block`` deltas.
        - This method does not return a ``ProviderResponse``.
          ``provider_gen()`` assembles the complete response and preserves the
          usage contract: usage extracted by the adapter for the final delta
          is attached only to ``message.usage`` on the assembled message, as
          in :meth:`generate`. Deltas are not persisted or added to the
          message tree.
        - ``side_query()`` always uses non-streaming mode and does not call
          this method. An error raised while iterating is classified the same
          way as in :meth:`generate`.

        :raises flowing.errors.FlowingError:
            The same classified errors as :meth:`generate`.

        .. seealso::

            :class:`ProviderDelta` defines the delta structure.
            :meth:`flowing.agent.Agent.provider_gen` defines streaming-mode
            selection and accumulation.
        """
        ...
    def get_credential(self) -> str | None:
        """Read this entry's credential; by default, return ``self.config.get("api_key")``.

        .. rubric:: Overview

        This is the single entry point for reading credentials, called by an
        adapter before each request. Credential sources differ by adapter
        (static API keys, OAuth, or instance metadata services), so subclasses
        may override this method. The default reads only ``api_key``.

        .. rubric:: Example

        .. code-block:: python

            class BedrockProvider(AnthropicMessagesProvider):
                def get_credential(self) -> str | None:
                    return (self.config.get("aws_session_token")
                            or super().get_credential())

        .. rubric:: Behavioral notes

        - Return the currently available credential as a string, or ``None``
          when no credential is available. The request path decides whether
          that is an error; it usually becomes
          :class:`flowing.errors.AuthenticationError`.
        - Do not write the returned value to messages, ``_provided``, logs,
          or persisted files.
        - Credential refresh is not supported. Credentials are bound
          statically at construction; the request path reports expired or
          invalid credentials, usually as
          :class:`flowing.errors.AuthenticationError`. The framework does
          not refresh them automatically or provide a refresh hook.

        .. seealso:: :class:`ProviderConfig` stores the credential source.
        """
        ...

class FakeProvider(Provider):
    """Built-in test double with injectable ``generate_fn`` and ``stream_fn``.

    .. rubric:: Overview

    This is the framework's minimal Fake: a test double with simple behavior,
    unlike a Stub or Scripted double that only replays a queue. After
    construction, assign the required ``generate_fn`` and optional
    ``stream_fn`` instance attributes. ``generate()`` and
    ``generate_stream()`` delegate to those functions. It also acts as a Spy:
    each call appends the received :class:`Context` to ``received`` for
    assertions.

    The injection points are instance attributes rather than constructor
    arguments. A test can create the double and install it in Runtime during
    arrange, then rebind its functions for individual assertion phases while
    retaining the same instance. For test doubles with domain logic, subclass
    :class:`Provider` and register the subclass instead; see the testing
    guidance in :class:`Provider`.

    .. rubric:: Example

    .. code-block:: python

        from flowing.context import Context
        from flowing.message import Message, MessageKind, TextBlock
        from flowing.model import ModelConfig
        from flowing.providers import FakeProvider, ProviderResponse

        provider = FakeProvider()

        async def my_generate(context, model):
            return ProviderResponse(
                message=Message(kind=MessageKind.PROVIDER,
                                content=[TextBlock(text="ok")]),
                finish=True, model="fake")

        provider.generate_fn = my_generate
        response = await provider.generate(
            Context(system_prompt=[], tools=[], messages=[]),
            ModelConfig(model="fake-model", provider="fake"))
        assert len(provider.received) == 1

    .. rubric:: Behavioral notes

    - ``generate()`` appends ``context`` to ``received`` before awaiting
      ``generate_fn(context, model)``, then returns its result unchanged. If
      ``stream_fn`` is set, ``generate_stream()`` records the context before
      delegating to it. If it is not set, the base fallback calls
      ``generate()`` and emits one delta; that call records the context, so it
      is not appended twice.
    - The injected functions are the adapter implementation. The test author
      is responsible for satisfying the Provider contract, including
      classifying errors with :mod:`flowing.errors`, attaching usage to
      ``message.usage``, and setting ``finish`` correctly. The framework does
      not validate the injected functions' results.
    - If ``config`` is omitted, it defaults to an empty
      :class:`ProviderConfig`; the test double has no credential concept and
      creates no network connections. ``received`` only grows. Tests can
      clear it themselves with ``provider.received.clear()``.
    - Calling ``generate()`` before assigning ``generate_fn`` raises
      :class:`flowing.errors.FlowingError` with the missing attribute name in
      its message. An unset ``stream_fn`` is valid and uses the base fallback.
      Rebinding ``generate_fn`` during a test is supported and affects the
      next call.

    .. seealso::

        :class:`Provider` defines the contract and test-double guidance.
        :class:`ProviderRegistry` hosts this double through injection path b).
    """
    name: ClassVar[str]
    generate_fn: Callable[[Context, ModelConfig], Awaitable[ProviderResponse]] | None
    """The async function delegated to by ``generate()``. It is assigned
    after construction and is required before the first call."""
    stream_fn: Callable[[Context, ModelConfig], AsyncIterator[ProviderDelta]] | None
    """The async iterator function delegated to by ``generate_stream()``.
    It is assigned after construction and is optional; if unset, the base
    implementation is used."""
    received: list[Context]
    """Contexts passed to calls on this instance, appended in call order and
    retained until the test clears the list."""
    def __init__(self, config: ProviderConfig | None=None) -> None:
        """Construct the fake Provider; ``config`` defaults to an empty ``ProviderConfig``.

        .. rubric:: Behavioral notes

        - After construction, ``generate_fn`` and ``stream_fn`` are ``None``
          and ``received`` is an empty list. Construction opens no connection.
        - Passing ``config=None`` is equivalent to passing an empty
          ``ProviderConfig``, except that an explicitly passed object is
          shared with this instance while ``None`` causes a new object to be
          created.
        """
        ...
    async def generate(self, context: Context, model: ModelConfig) -> ProviderResponse:
        """Delegate to ``generate_fn``; raise ``FlowingError`` if it is unset.

        .. rubric:: Behavioral notes

        - Append ``context`` to ``self.received`` before delegating. The
          context remains recorded even if the injected function raises.
          Then await ``self.generate_fn(...)`` and return its result unchanged.
        - If ``generate_fn is None``, raise
          :class:`flowing.errors.FlowingError` whose message names the missing
          attribute. Do not wrap exceptions raised by the injected function.
        """
        ...
    def generate_stream(self, context: Context, model: ModelConfig) -> AsyncIterator[ProviderDelta]:
        """Delegate to ``stream_fn`` when set; otherwise use the base fallback.

        .. rubric:: Behavioral notes

        - If ``stream_fn is not None``, append the context to ``received`` and
          return ``self.stream_fn(context, model)``. If it is ``None``, call
          ``super().generate_stream(...)`` directly. The fallback records the
          context through ``generate()`` when the returned async iterator is
          consumed, without appending it a second time. With an injected
          ``stream_fn``, the context is recorded immediately when
          ``generate_stream()`` is called, before the iterator is consumed.
        """
        ...

_provider_adapters: dict[str, type[Provider]]
"""The process-wide adapter registry, mapping adapter names to Provider
classes.

Only :func:`register_provider` writes to it, during import. Only
:class:`ProviderRegistry` reads it when resolving an entry's ``adapter``
field during lazy instantiation. This is an internal, non-stable API. Adapter
classes are process-level assets; entry instances belong to a Runtime."""

class ProviderRegistry:
    """Runtime-owned lazy instance table mapping entry names to Providers.

    .. rubric:: Overview

    Each ``providers.yaml`` entry, which binds one credential identity, maps
    to at most one Provider instance. During Runtime initialization, the
    loader builds a candidate table from entry names to
    ``(adapter class, ProviderConfig)`` pairs; it resolves adapter classes
    through the process-wide registry. This class instantiates and caches an
    adapter with ``adapter_cls(config)`` only on the first :meth:`get` call
    for an entry.

    .. rubric:: Design

    - Lazy creation supports files with many entries when only one or two
      are used. Instantiation waits until an entry is first requested, so
      Runtime initialization has no network cost. Missing environment
      variables in entry fields produce a warning and an empty string rather
      than blocking loading; unused entries do not need available credentials.
    - The two registries have different lifetimes. Adapter classes are
      process-level assets stored in ``_provider_adapters`` and written by
      ``register_provider``. Entry instances are Runtime-level assets stored
      here on ``Runtime.provider_registry``. Multiple Runtime instances in
      one process each have their own ``ProviderRegistry``.

    .. rubric:: Example

    .. code-block:: python

        provider = runtime.provider_registry.get("deepseek-main")

    .. rubric:: Behavioral notes

    - ``get(name)`` returns the cached instance when present. Otherwise, it
      creates and caches an instance before returning it. An entry name
      absent from the candidate table raises ``KeyError``. This ``get`` means
      “retrieve or create”; it does not have ``dict.get``'s ``None`` result.
    - This class does not discover adapters. Adapter classes must have been
      registered during import with :func:`register_provider`. It does not
      validate entry configuration, which the loader has already resolved.
    - If instance creation fails, for example because credentials are
      missing, the exception propagates unchanged and the failed result is not
      cached; a later ``get`` tries again.
    - Repeated ``get`` calls for one entry return the same instance, as
      confirmed by ``is``.

    .. seealso::

        :func:`register_provider` registers adapter classes process-wide.
        :class:`Provider` defines the instance base and configuration binding.
    """
    _candidates: dict[str, tuple[type[Provider], ProviderConfig]]
    """Candidate adapter class and configuration pairs keyed by entry name."""
    _instances: dict[str, Provider]
    """Provider instances already created for entries, keyed by entry name."""
    def __init__(self, candidates: dict[str, tuple[type[Provider], ProviderConfig]]) -> None:
        """Construct the lazy instance table.

        :param candidates: Candidate mapping from entry name to
            ``(adapter class, ProviderConfig)``. This is usually returned by
            :func:`load_provider_candidates`.
        """
        ...
    def get(self, name: str) -> Provider:
        """Get a Provider entry, lazily creating and caching it; raise ``KeyError`` if unknown.

        :param name: Provider entry name, corresponding to a key in ``providers.yaml``.
        :return: The Provider for this entry, created and cached on the first call.

        .. rubric:: Behavioral notes

        - Return the cached instance when present. Otherwise, instantiate
          ``adapter_cls(config)``, cache it, and return it.
        - Raise ``KeyError`` if the name is absent from the candidate table;
          this method does not return ``None`` like ``dict.get``.
        - If instantiation fails, propagate the exception unchanged and do
          not cache a failed result. A later call tries again.

        .. seealso:: See :class:`ProviderRegistry` for the complete behavior.
        """
        ...

_ENV_REF_RE: object
"""The regular expression for ``{{env.VAR}}`` references. It matches only
the ``env.`` prefix; other ``{{...}}`` expressions are left unchanged."""
def _substitute_env(value: Any, *, entry: str) -> Any:
    """Replace every ``{{env.VAR}}`` reference in string values; warn and use an empty string when missing.

    Only values of type ``str`` participate in global replacement. A missing
    environment variable is replaced with an empty string, and
    ``warnings.warn`` includes the variable and entry names. Loading continues,
    so entries that are not used do not require all their credentials.
    Credentials do not pass through Jinja2 or Parsable; see the
    :mod:`flowing.providers` package documentation. This is an internal API
    and is not part of the stable contract.
    """
    ...

def load_provider_candidates(path: Path) -> dict[str, tuple[type[Provider], ProviderConfig]]:
    """Load ``providers.yaml`` and build the candidate table for ``ProviderRegistry``.

    .. rubric:: Overview

    This loader reads YAML, performs plain-string replacement of
    ``{{env.VAR}}`` references (not Jinja2 or Parsable), constructs one
    :class:`ProviderConfig` per entry, and resolves each entry's ``adapter``
    name to a class in the process-wide registry. A missing environment
    variable is replaced with an empty string and produces a warning without
    stopping loading. An unregistered adapter name fails during scanning;
    resolving that name is not :class:`ProviderRegistry`'s responsibility.

    The function does not resolve environment variables or choose a default
    path. The Runtime caller resolves ``FLOWING_PROVIDERS_PATH`` and
    ``$FLOWING_CONFIG_HOME``. Keeping this function as a pure
    “path to candidate table” operation allows direct use in tests and CI
    diagnostics.

    :param path: The resolved path to ``providers.yaml``.
    :return: A mapping from entry name to ``(adapter class, ProviderConfig)``,
        suitable as the ``ProviderRegistry(candidates)`` argument.
    :raises KeyError: An entry's ``adapter`` name is not registered.

    .. rubric:: Behavioral notes

    - If the file does not exist, return an empty candidate table so Runtime
      can start without Provider configuration. An empty file also returns an
      empty table.
    - A ``{{...}}`` expression that does not start with ``{{env.`` is left
      unchanged without an error.
    - A non-string field value, such as a number, is not substituted and is
      preserved unchanged.
    - If an environment variable referenced by ``{{env.VAR}}`` is missing,
      replace it with an empty string and issue ``warnings.warn`` containing
      the variable and entry names. Loading continues. The consequence of a
      missing credential, such as a 401, is exposed through
      ``on_provider_error`` on the entry's first call.

    .. seealso:: :class:`ProviderRegistry` and :func:`register_provider`.
    """
    ...

def register_provider(cls: type[Provider] | None=None, *, override: bool=False) -> type[Provider] | Callable[[type[Provider]], type[Provider]]:
    """Register a Provider adapter class in the process-wide registry.

    .. rubric:: Overview

    Register the class under its ``name`` class attribute, such as
    ``"deepseek"``. When Runtime builds the Provider candidate table, it uses
    an entry's ``adapter`` field in ``providers.yaml`` to look up the class in
    this registry.

    .. rubric:: Design

    - Registration is process-wide. An adapter class is a type-level asset;
      entry instances belong to individual Runtime objects. This supports
      multiple Runtime instances in one process.
    - The decorator registers the class when its module is imported. To use a
      third-party adapter, the embedding application must import that module
      before Runtime builds its candidate table. The framework does not
      discover third-party Provider packages automatically.
    - Adapter names are unique by default. To replace a built-in adapter,
      pass ``override=True`` explicitly; this makes the replacement clear
      and emits a warning.

    .. rubric:: Example

    .. code-block:: python

        from flowing.providers import OpenAICompletionsProvider, register_provider

        @register_provider
        class MyProvider(OpenAICompletionsProvider):
            name = "my"

        @register_provider(override=True)  # Replace the built-in "deepseek" adapter globally.
        class HardenedDeepSeek(DeepSeekProvider):
            name = "deepseek"

    .. rubric:: Behavioral notes

    - Return the decorated class unchanged; the decorator does not wrap or
      subclass it. Both ``@register_provider`` and
      ``@register_provider(override=True)`` are supported.
    - Registration occurs at import time, when the decorator runs. The
      registry is immutable at runtime; calling this decorator later does not
      change the behavior of any Runtime that has already been built.
    - If a name is already held by a different class and ``override=False``,
      raise :class:`flowing.errors.ProviderNameConflictError`. With
      ``override=True``, the later import replaces the entry and emits a
      warning. Multiple overrides are resolved in import order, with a
      warning for each replacement. Registering the same class object again
      is not a conflict.
    - The decorated class must subclass :class:`Provider` and define a
      non-empty ``name`` class attribute.
    - This decorator does not instantiate a Provider or read
      ``providers.yaml``. Instances are created only on the lazy-creation
      path.

    :raises flowing.errors.ProviderNameConflictError:
        Another class already holds this name and ``override=True`` was not
        specified.
    :raises ValueError:
        The decorated object is not a ``Provider`` subclass or does not have
        a non-empty ``name``. This built-in exception is intentional for an
        authoring error at import time; see :mod:`flowing.errors`.

    .. seealso::

        :class:`Provider` is the base class and defines the ``name`` contract.
        :class:`flowing.runtime.Runtime` owns the lazy candidate table.
    """
    ...
