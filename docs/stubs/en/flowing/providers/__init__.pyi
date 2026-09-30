"""``flowing.providers`` provides the contracts for communicating with model APIs.

.. rubric:: Overview

This package contains the Provider-side contracts: the :class:`Provider`
abstract base and the built-in adapter hierarchy (the three format families
in :mod:`flowing.providers.openai_completions`,
:mod:`flowing.providers.openai_responses`, and
:mod:`flowing.providers.anthropic_messages`); the results of a call
(:class:`ProviderResponse`, :class:`ProviderDelta`, and the per-call token
record :class:`Usage`); entry configuration (:class:`ProviderConfig`);
machine-readable field descriptions (:class:`ProviderConfigField`) and
model-option descriptions (:class:`ModelConfigField`); the
adapter registration decorator (:func:`register_provider`) and read-only
enumeration (:func:`provider_adapters`); the Runtime-level lazy instance table
(:class:`ProviderRegistry`); and the built-in test double :class:`FakeProvider`.
The model-side contract (``ModelConfig``,
``models.yaml``, and model-tag mappings) is documented in
:mod:`flowing.model`.

An Agent only holds a ``ModelConfig`` and passes it through mechanically. It
resolves the model immediately before each ``provider_gen()`` call, then passes
the resolved model together with :class:`flowing.context.Context` to
``Provider.generate()``. Only the Provider adapter interprets model fields such
as ``thinking_budget``; the framework core assigns no meaning to them.

.. rubric:: Design

- Adapter differences are represented by an explicit inheritance hierarchy,
  not by configuration flags. Differences between Providers are differences
  between API formats, not merely parameter differences. Each format has one
  framework base class that determines ``api_format``; each vendor has a
  subclass that expresses its differences through overrides. Compatibility
  flags such as ``is_deepseek_compatible=True`` and runtime format checks such
  as ``if api_format == "openai"`` are not used because they reduce format
  differences to parameter differences and weaken type safety.
- Provider instances are created lazily, one per configuration entry. Runtime
  initialization scans the configuration file and builds a candidate table
  from entry names to ``(adapter class, config)`` pairs. An adapter is
  instantiated and cached only when its entry name is first requested. Thus,
  unused entries incur no connection-pool or HTTP-session cost. Each entry
  represents one identity, usually one API key. To use multiple keys with the
  same adapter, define multiple entries, such as ``deepseek-personal`` and
  ``deepseek-team``; the entry name identifies the credential identity.
- Streaming stays outside the Turn loop. ``provider_gen()`` exposes an
  explicit ``stream`` parameter, which defaults to ``True``, and encapsulates
  streaming inside ``generate()`` and ``generate_stream()``. The Turn loop
  sees only a complete :class:`ProviderResponse`. Streaming affects
  ``provider_gen()``, the ``on_provider_delta`` hook (whose value is an
  ephemeral :class:`ProviderDelta` that is not persisted), and
  ``Message.partial``. With ``stream=False``, the Agent still emits one full
  delta after receiving the complete response. Subscribers can therefore
  rely on receiving at least one delta from every ``provider_gen()`` call, and
  both paths use the same delta format. ``side_query()`` always uses
  ``stream=False`` and marks its origin as ``by="_side"``; the delta and
  response both carry this value so hooks can filter by origin.
- A Provider is not declared in ``main()``. ``main.py`` contains reusable
  Agent logic, while users choose the Provider separately. The same ``main``
  can therefore use different Providers in different environments.

.. rubric:: Adapter classes and Provider entries

There are two distinct meanings of “Provider” in this package:

- An adapter class is a concrete subclass of :class:`Provider`, such as
  ``DeepSeekProvider``. It defines how to communicate with an API: request and
  response formats, field interpretation, and error classification. Its
  ``name`` class attribute (for example, ``"deepseek"``) is registered in a
  process-wide registry by :func:`register_provider`. An adapter name is a
  globally unique type identifier.
- A Provider entry is a key in ``providers.yaml``, such as
  ``deepseek-team``. It selects the identity used for a call by binding one
  API key and optional ``base_url``. Runtime looks up the class named by the
  entry's ``adapter`` field and lazily creates and caches one instance for
  that entry. The entry name identifies the credential identity; multiple
  keys for one adapter require multiple entries.

The mapping is entry name → adapter name → adapter class → instance.
``ModelConfig.provider`` refers to the entry name (the identity), while the
``adapter`` field in ``providers.yaml`` refers to the adapter name (the type).
``Provider.name`` is the latter. These two names belong to different layers.

.. rubric:: Configuration resolution: load time and runtime

.. list-table::
   :header-rows: 1
   :widths: 30 32 38

   * - Item
     - When it is resolved
     - Mechanism
   * - Provider entry in ``providers.yaml``
     - Once, when the file is loaded
     - Plain-string replacement of ``{{env.VAR}}``; this is not Jinja2 or Parsable.
   * - ``ModelConfig`` fields
     - At runtime, before every ``provider_gen()`` call
     - Parsable (Jinja2), which can reference environment variables, configuration, and instance attributes.

For Provider and model-entry field values, ``{{env.VAR}}`` is replaced with
``os.environ["VAR"]``. A ``{{...}}`` expression that does not start with
``{{env.`` is left unchanged without an error. If an environment variable is
missing while a Provider entry is loaded, the loader substitutes an empty
string and emits ``warnings.warn`` with the variable and entry names; loading
continues. This lets a file contain several entries when only some of their
credentials are available. The missing credential becomes observable on the
entry's first call, through ``on_provider_error`` (for example, as a 401).
:class:`flowing.errors.MissingEnvironmentVariableError` remains a public error
type for strict validation implemented by applications. Missing variables
encountered while resolving model fields at runtime follow Parsable's
evaluation-error path. This substitution is separate from Parsable's Jinja2
rendering: credential references are static strings replaced once at load
time.

.. rubric:: ``providers.yaml`` schema

The default file is ``$FLOWING_CONFIG_HOME/providers.yaml``. It is private to
the user and contains credentials, so its permissions must be restricted with
``chmod 600``. The model-side schema is documented in :mod:`flowing.model`::

    deepseek-personal:          # entry name identifies the credential identity
      adapter: deepseek         # required; registration key used by register_provider
      api_key: sk-...           # optional; supports {{env.VAR}}
      base_url: https://...     # optional
      # Other fields are read by the adapter; the framework core does not interpret them.

.. rubric:: Environment variables

- ``FLOWING_CONFIG_HOME`` selects the user-level configuration directory. It
  defaults to ``~/.flowing`` and is shared with model configuration.
- ``FLOWING_PROVIDERS_PATH`` overrides the path to ``providers.yaml``.

.. rubric:: Provider error classification

The adapter must raise a specific error type from :mod:`flowing.errors` for
each underlying failure. ``generate()`` does not catch errors as a fallback.

- Retryable errors are ``RateLimitedError``, ``ServerError``, ``NetworkError``,
  and ``ProviderTimeoutError``. A policy layer, such as ``use_retry()``,
  decides whether to retry and how to back off.
- Non-retryable errors include ``AuthenticationError``,
  ``InvalidRequestError``, ``ContentPolicyError``, and
  ``RequestTooLargeError`` (an HTTP 413 byte-size limit; stripping media and
  resending is the handler's responsibility). ``QuotaExhaustedError`` means
  that a quota or balance is exhausted and is distinct from transient
  ``RateLimitedError``. The three built-in format families classify every
  HTTP 429 as ``RateLimitedError`` and do not distinguish exhausted quota.
- ``ContextLengthError`` means the token limit was exceeded. It is also
  dispatched through ``on_provider_error``. Resending the same context will
  reproduce the failure, so the default ``use_retry()`` policy does not retry
  it. A handler is responsible for shortening the history or selecting a
  model with a larger context window.
- ``MissingEnvironmentVariableError`` is retained as a load-time error type,
  but the built-in loader does not raise it: it substitutes an empty string
  and emits a warning. Applications may raise it from their own strict checks.
- Provider-call errors are caught at the logical Turn layer. The Turn ends
  with an error result instead of propagating the error to the ``query()``
  caller. See :mod:`flowing.errors` for details.

.. rubric:: Mapping tool results to APIs

The ``content`` of a TOOL or EVENT message contains content blocks only; it
does not contain protocol blocks. Pairing metadata is stored on the message
as ``tool_call_id`` and ``tool_status`` (see :mod:`flowing.message`). Adapters
map these messages to each API's tool-result representation as follows:

- ``TextBlock`` maps to the API's text field, and ``MediaBlock`` subclasses
  map to its media fields. ``StructBlock`` is always serialized as text with
  ``json.dumps(ensure_ascii=False)``; no adapter maps it to a native
  structured payload.
- The message-level ``tool_call_id`` maps to the API's result ID field:
  ``tool_use_id`` for Anthropic and ``tool_call_id`` for OpenAI.
  ``tool_status="error"`` maps to the API's error marker, such as Anthropic's
  ``is_error``.
- Anthropic embeds media blocks directly in ``tool_result``. OpenAI Chat
  Completions tool messages accept text only, so media blocks are moved into
  a synthetic user message immediately following the tool message, with a
  fixed prompt text.
- An empty TOOL message can occur for a pending receipt returned by the Task
  path (``output=None`` and therefore ``content=[]``). Each adapter chooses
  an API-compatible fallback because APIs differ in whether they accept an
  empty tool result. A pending receipt from the async-generator path has
  content (the first yield and a “background task ID” block) and is mapped
  as an ordinary tool result.
- Adapters build API schemas from an allowlist of fields in
  ``llm_definition()`` (such as ``name``, ``description``, and ``parameters``).
  Other fields carried by that declaration, including ``output_schema``, are
  not forwarded.

.. rubric:: Security boundary

Credentials such as API keys exist only in :class:`ProviderConfig` and the
Provider instance. They must not enter messages or ``_provided``, be
persisted in ``tree.jsonl`` or ``state.jsonl``, or appear in logs or error
text. A configuration file containing credentials must have ``chmod 600``
permissions.

.. rubric:: Example

.. code-block:: python

    import flowing

    async def main() -> flowing.Runtime:
        runtime = await flowing.launch("my-agent")  # A subproject root containing main.py.
        # This entry must exist in providers.yaml. The Provider is created
        # only when the entry is first requested.
        runtime.provider_registry.get("deepseek-team")
        return runtime

.. seealso::

    :mod:`flowing.model`
        The model-side contract: ``ModelConfig``, ``models.yaml``, and model tags.
    :class:`flowing.agent.Agent`
        The caller that resolves model configuration immediately before ``provider_gen()``.
    :mod:`flowing.errors`
        The unified error hierarchy and Provider error classifications.
    :class:`flowing.runtime.Runtime`
        The owner of ``provider_registry`` and its lazy candidate table.
"""
from __future__ import annotations

from flowing.providers.anthropic import AnthropicProvider
from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.bedrock import BedrockProvider
from flowing.providers.deepseek import DeepSeekProvider
from flowing.providers.deepseek_anthropic import DeepSeekAnthropicProvider
from flowing.providers.deepseek_responses import DeepSeekResponsesProvider
from flowing.providers.groq import GroqProvider
from flowing.providers.kimi_coding import KimiCodingProvider
from flowing.providers.kimi_coding_anthropic import KimiCodingAnthropicProvider
from flowing.providers.moonshot import MoonshotProvider
from flowing.providers.moonshot_anthropic import MoonshotAnthropicProvider
from flowing.providers.moonshot_responses import MoonshotResponsesProvider
from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.openai_responses import OpenAIResponsesProvider
from flowing.providers.openrouter import OpenRouterProvider
from flowing.providers.provider import FakeProvider, ModelConfigField, Provider, ProviderConfig, ProviderConfigField, ProviderDelta, ProviderRegistry, ProviderResponse, Usage, load_provider_candidates, provider_adapters, register_provider
__all__: object
