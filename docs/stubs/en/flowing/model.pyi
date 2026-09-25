"""``flowing.model`` — model specifications and model-configuration loaders.

.. rubric:: Overview

This module defines the contract between an Agent and the model it uses:
:class:`ModelConfig` holds the complete model specification,
:func:`load_models` loads the ``models.yaml`` entry-name-to-configuration
mapping, and :func:`load_model_tags` loads only the tag-to-entry-name mapping
from ``model-tags.yaml``. Runtime joins the two mappings to resolve a tag to a
``ModelConfig``; ``load_model_tags`` does not create model configurations.
Provider contracts—including adapter inheritance, ``ProviderResponse``,
token-usage records such as :class:`flowing.providers.Usage`, registration,
and lazy instantiation—are defined by :mod:`flowing.providers`.

An Agent holds ``self.model: ModelConfig``. Immediately before each
``provider_gen()`` call, it resolves the specification and passes the result
along with the current ``Context`` to ``Provider.generate()``. The core only
holds and forwards model fields; provider adapters interpret metadata such as
``thinking_budget``.

A model is bound to exactly one provider entry. ``ModelConfig.provider`` is a
single entry name, not a candidate list; each ``models.yaml`` entry names one
specific model, and each ``model-tags.yaml`` tag maps to one model entry. The
core supplies no retries, provider fallback, or capability checks.

.. rubric:: Configuration schema

``models.yaml`` defaults to ``$FLOWING_CONFIG_HOME/models.yaml`` and can be
shared by a team. ``FLOWING_MODELS_PATH`` redirects this file:

.. code-block:: yaml

    sonnet:
      provider: anthropic       # Required; one-to-one binding.
      model: claude-sonnet-4-6  # Required; model ID sent to the API.
      thinking_budget: 32000    # Optional metadata; values may be Parsable.
      # The fixed built-in fields become ModelConfig attributes.
      # Other fields are retained in ModelConfig._extra without interpretation.

``model-tags.yaml`` defaults to ``$FLOWING_CONFIG_HOME/model-tags.yaml``:

.. code-block:: yaml

    tags:
      fast: deepseek-v4         # One tag maps to one model-entry name.
      high: sonnet
      default: fast

Tag-map precedence, from highest to lowest, is an explicit path set through
``Runtime.set_model_tags(path)``, the file named by ``FLOWING_MODEL_TAGS``, and
the default file under ``$FLOWING_CONFIG_HOME``. A missing requested tag falls
back to ``default``; if ``default`` is also absent, resolution raises an error
rather than silently selecting another model.

.. rubric:: Example

.. code-block:: python

    from pathlib import Path
    from flowing.model import load_model_tags, load_models

    tags = load_model_tags(Path("model-tags.yaml"))
    models = load_models(Path("models.yaml"))
    config = models[tags["fast"]]

.. rubric:: Behavior

Provider-entry parsing and model-field parsing are separate stages.
Environment placeholders such as ``{{env.VAR}}`` in ``providers.yaml`` are
replaced with strings while provider entries load. If a credential variable
is missing, it becomes an empty string and emits a warning; the missing
credential is surfaced on the first provider call through
``on_provider_error``. Parsable values in ``ModelConfig`` fields are evaluated
at runtime, and failures follow the ordinary evaluation-error path.

The minimal selection path is: configure a ``model_tag`` and the provider,
model, and tag-map files; resolve the tag to one model entry; load its
credentials; then make one provider call. Success is returned; a failure is
propagated and handled according to ``on_provider_error``. There is no
built-in retry, fallback, or capability validation.

The relevant environment variables are:

* ``FLOWING_CONFIG_HOME`` selects the user-level configuration directory
  (default ``~/.flowing``) and is shared with provider configuration.
* ``FLOWING_MODELS_PATH`` redirects ``models.yaml``.
* ``FLOWING_MODEL_TAGS`` selects the highest-priority tag-map file while
  preserving the fallback chain.

.. seealso::

    :mod:`flowing.providers`
        Provider adapters, ``providers.yaml``, registration, lazy
        instantiation, error classification, and security boundaries.
    :class:`flowing.agent.Agent`
        The owner of ``self.model`` and ``self.model_tag``, and the caller
        that resolves the model before ``provider_gen()``.
    :mod:`flowing.parsable`
        The field-level evaluation mechanism used by ``ModelConfig``.
    :class:`flowing.runtime.Runtime`
        The host of ``set_model_tags()``.
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING, Any
from ruamel.yaml import YAML
from flowing.parsable import LITERAL, Parsable

if TYPE_CHECKING:
    from flowing.agent import Agent

class ModelConfig:
    """A self-contained runtime specification for one model and its parameters.

    The model ID is only one field. Metadata such as ``thinking_budget``,
    ``context_window``, and ``max_output_tokens`` is carried by this object as
    well. An Agent's ``self.model`` is always a ``ModelConfig``; its support for
    deferred values is exposed through :meth:`resolve`.

    Replacing a model means replacing the complete specification. The
    ``provider`` field binds one provider entry; changing providers means
    selecting another complete model or constructing a new specification.
    There is no intermediate state in which only the model ID changes while
    the old model's other metadata is inherited.

    ``load_models`` promotes the fixed fields ``model``, ``provider``,
    ``thinking_budget``, ``context_window``, and
    ``max_output_tokens`` to attributes. Other fields from a
    ``models.yaml`` entry are retained in :attr:`_extra`; Composables may
    read them, while the framework core neither interprets nor discards them.

    .. rubric:: Example

    .. code-block:: python

        from flowing.parsable import Parsable
        from flowing.model import ModelConfig

        config = ModelConfig(
            model="deepseek-chat",
            provider="deepseek-personal",
            thinking_budget=Parsable("{{ config.thinking_budget }}"),
            context_window=64000,
        )

    Declarative Agent definitions specify a tag rather than an inline
    ``ModelConfig``:

    .. code-block:: yaml

        # assistant.fya
        ---
        name: assistant
        model_tag: fast
        system_prompt: "You are an assistant."

    .. code-block:: yaml

        # models.yaml
        sonnet:
          provider: anthropic
          model: claude-sonnet-4-6
          thinking_budget: "{{ config.thinking_budget }}"

    At runtime, either the tag or the complete specification can be changed:

    .. code-block:: python

        from flowing.model import ModelConfig

        # In application code with an initialized Agent instance.
        agent.model_tag = "high"  # Resolve another configured model.
        agent.model = ModelConfig(
            model="my-finetune",
            provider="openrouter",
            max_output_tokens=8192,
        )

    .. rubric:: Behavior

    - The fields are mutable and can be read by Composables and hooks after
      Agent creation. A change to ``agent.model`` or ``agent.model_tag`` takes
      effect on the next ``provider_gen()`` call, including within the same
      logical turn, because the model is resolved again before each call.
    - This class does not probe providers or validate capabilities or model
      identifiers. An incompatible model fails when the API is called.
    - :meth:`resolve` is not cached; it is evaluated immediately before each
      ``provider_gen()`` call.
    - ``extra=None`` is normalized to an empty dictionary. The contents of
      ``_extra`` are not a stable cross-version contract.
    - After resolution, ``provider`` must match an entry in the Runtime's
      provider candidates; otherwise the provider-call path raises an error.

    .. seealso:: :class:`flowing.parsable.Parsable`, :class:`flowing.agent.Agent`.
    """
    model: str | Parsable
    """The model ID passed to the provider API. A Parsable value is evaluated
    at runtime and should resolve to a non-empty string. The framework does not
    check whether a provider recognizes it.
    """
    provider: str | Parsable
    """The name of the single provider entry bound to this model, not a
    candidate list. A Parsable value is evaluated at runtime; the provider
    entry is looked up when the Agent makes a provider call. Lists are not
    supported.
    """
    thinking_budget: int | Parsable | None
    """Optional token budget for model reasoning. ``None`` leaves its
    interpretation to the provider adapter; the framework does not validate
    its range.
    """
    context_window: int | Parsable | None
    """Optional context-window size, commonly consumed by a Composable that
    estimates or limits context usage. ``None`` leaves the value unspecified.
    """
    max_output_tokens: int | Parsable | None
    """Optional maximum number of output tokens for one response. ``None``
    leaves the value unspecified.
    """
    _extra: dict[str, Any]
    """Additional fields from a ``models.yaml`` entry that are not among the
    fixed built-in fields. Composables may read this metadata; the core does not
    interpret it. It is populated at load time and conventionally read-only at
    runtime, but its contents are not a stable contract.
    """

    def __init__(self, model: str | Parsable, provider: str | Parsable, *, thinking_budget: int | Parsable | None = None, context_window: int | Parsable | None = None, max_output_tokens: int | Parsable | None = None, extra: dict[str, Any] | None = None) -> None:
        """Construct a complete model specification.

        The loader and direct runtime assignment
        (``agent.model = ModelConfig(...)``) use this constructor. Metadata
        fields are keyword-only, so callers name them rather than passing them
        positionally.

        .. rubric:: Example

        .. code-block:: python

            from flowing.model import ModelConfig
            from flowing.parsable import Parsable

            config = ModelConfig(
                model="deepseek-chat",
                provider="deepseek-personal",
                thinking_budget=Parsable("{{ config.thinking_budget }}"),
                context_window=64000,
            )

        .. rubric:: Behavior

        - ``model`` and ``provider`` are required positional arguments.
        - ``extra=None`` becomes an empty dictionary. Parsable values are not
          evaluated here; evaluation is deferred to :meth:`resolve`.
        - The constructor does not check whether the provider entry exists or
          whether an adapter recognizes the model ID; those checks occur on the
          provider-call path.
        - If a field name is supplied both explicitly and in ``extra``, the
          explicit argument takes precedence. The loader normally separates
          these fields before construction.

        :param model: Model ID sent to the API, optionally as a Parsable value.
        :param provider: Bound provider-entry name, optionally as a Parsable
            value.
        :param thinking_budget: Optional reasoning token budget; defaults to
            ``None``.
        :param context_window: Optional context-window size; defaults to
            ``None``.
        :param max_output_tokens: Optional per-response output limit; defaults
            to ``None``.
        :param extra: Additional fields outside the fixed built-in field set;
            ``None`` is normalized to an empty dictionary.

        .. seealso:: :meth:`resolve` for field-level evaluation.
        """
        ...

    def resolve(self, agent: Agent) -> ModelConfig:
        """Evaluate Parsable values in the model's fixed fields.

        Parsable fields are evaluated with Jinja2 using the Agent's instance
        attributes as the rendering context, including references to
        environment variables, configuration, and runtime state. The Agent
        calls this method before each Provider call so that a model change
        takes effect on the next call in the same turn.

        .. rubric:: Example

        .. code-block:: python

            # ``agent`` is the initialized Agent whose configuration is used.
            resolved = agent.model.resolve(agent)
            budget = resolved.thinking_budget
            window = resolved.context_window

        .. rubric:: Behavior

        - Parsable values are resolved by their own type: ``EXPRESSION``
          returns its native expression value, ``TEMPLATE`` returns a rendered
          string, and ``LITERAL`` and ``RAW`` are returned unchanged.
          Static fields are retained. The result is a new object when any fixed
          field is Parsable; the original object is not modified.
        - If all fixed fields are static, the method may return ``self`` as an
          idempotent fast path.
        - Resolution does not look up providers, access the network, or cache
          the result.
        - Values in ``_extra`` are not evaluated; the mapping is copied to a
          new object when one is returned.
        - Parsable evaluation errors propagate to the caller and are handled by
          the ``_run_turn`` provider-error path (``on_provider_error``).
          Undefined names in templates follow Jinja2's default behavior and do
          not raise.

        :param agent: ``flowing.agent.Agent`` instance used as the rendering
            context.
        :return: The resolved ``ModelConfig``; it may be ``self`` when all
            fixed fields are static.
        """
        ...

def load_models(path: Path) -> dict[str, ModelConfig]:
    """Load ``models.yaml`` into a mapping of entry names to configurations.

    The fixed fields ``model``, ``provider``, ``thinking_budget``,
    ``context_window``, and ``max_output_tokens`` become
    ``ModelConfig`` attributes. Every other entry field is retained in
    ``_extra``; those values are not evaluated or exposed to the LLM by this
    loader. String values in the fixed fields are wrapped only when they have a
    Parsable form (``FILE_REF``, ``EXPRESSION``, ``TEMPLATE``, or
    ``RAW``). Literal strings remain static, as do non-string values.

    Like :func:`load_model_tags`, this is a pure path-to-mapping loader. It
    does not read environment variables or choose a default path. Runtime
    startup uses this loader and joins its result with the tag mapping to
    resolve tags to ``ModelConfig`` objects.

    :param path: Resolved path to ``models.yaml``.
    :return: Mapping from model-entry names to :class:`ModelConfig` objects.
    :raises TypeError: A model entry omits required ``model`` or ``provider``
        fields.
    :raises FileNotFoundError: The supplied file does not exist. This native
        exception is not wrapped.

    .. seealso:: :class:`ModelConfig`, :func:`load_model_tags`.
    """
    ...

def load_model_tags(path: Path) -> dict[str, str]:
    """Load the ``tags`` mapping from a ``model-tags.yaml`` file.

    This loader returns only a tag-to-entry-name mapping. It does not load
    :class:`ModelConfig` objects, apply the ``default`` tag, or select a file
    path. Runtime performs tag fallback and joins the result with
    :func:`load_models` output.

    :param path: Path to the ``model-tags.yaml`` file.
    :return: A mapping whose tag names and model-entry names are strings.
    :raises KeyError: The YAML mapping does not contain the top-level
        ``tags`` key.
    :raises FileNotFoundError: The supplied file does not exist. This native
        exception is not wrapped.

    .. seealso:: :func:`load_models`.
    """
    ...
