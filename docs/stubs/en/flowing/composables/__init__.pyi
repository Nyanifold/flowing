"""Functional Composable extensions in the application layer.

In the three-layer architecture, a Composable is an ordinary Python function
following the ``use_xxx(agent, ...)`` naming convention. Called from an
Agent's ``setup()``, it registers hook handlers or binds instance attributes
for that Agent alone. The built-in Composables are:

* :func:`use_retry <flowing.composables.retry.use_retry>` retries failed LLM
  calls with a backoff policy.
* :func:`use_compact <flowing.composables.compact.use_compact>` compacts after
  a request by replacing the full message chain. In contrast,
  :func:`use_auto_compact <flowing.composables.compact.use_auto_compact>`
  compacts before a request while retaining the head and tail. Choose one of
  these two approaches.
* :func:`use_system_reminder <flowing.composables.reminder.use_system_reminder>`
  injects system reminders at the start of turns.
* :func:`use_prompt_until <flowing.composables.prompt_until.use_prompt_until>`
  steers another run when a turn-end assertion is false.

Scenario-specific Composables such as ``use_logging`` and ``use_guardrail``
belong to application code; the framework does not reserve symbols for them.

A Composable only mounts behavior by registering handlers or binding
instance attributes; it does not change core framework state. The package is
not a phase-one plugin: it does not register globally, does not go through
``runtime.install()``, and has no ``install()`` method of its own. Calling a
Composable takes effect immediately and its scope is limited to the supplied
Agent instance.

.. rubric:: Example

Combine the policies you need in a hand-written Agent's ``setup()`` method:

.. code-block:: python

    from flowing import Agent
    from flowing.composables import (
        use_compact, use_prompt_until, use_retry, use_system_reminder,
    )

    class MyAgent(Agent):
        async def setup(self) -> None:
            use_retry(self, max_retries=3)        # Back off and retry failed LLM calls.
            use_compact(self, threshold=0.8)      # Compact context after a request.
            use_system_reminder(self, contents=[
                lambda agent: f"Current node: {agent.node_id}",
            ])                                    # Add a system reminder each turn.
            use_prompt_until(self, self._done, "The task is incomplete; continue.")
            #                                        # Steer another run if the assertion fails.

.. rubric:: Behavior notes

- A Composable is enabled explicitly for an Agent instance by calling
  ``use_xxx(self)`` from ``setup()``. During recovery, ``setup()`` runs on a
  new instance and its hook registry is rebuilt, so registrations do not
  accumulate. An Agent that does not call a Composable has none of its
  handlers or state; the disabled code path does not exist and incurs no
  overhead.
- Whether a Composable function is synchronous or asynchronous depends on
  whether its implementation needs to ``await`` anything. Pure registration
  functions are synchronous; all five built-in Composables in this package
  are synchronous. A handler is asynchronous only when it needs to wait, for
  example for a backoff sleep or a side query.
- The order in which Composables are called from ``setup()`` determines their
  registration order. A handler registered later runs later in the chain.
- The five built-in Composables do not deduplicate repeated calls. Each call
  adds another handler set, allowing different settings to be enabled
  independently. To replace a set, use ``remove_by_owner()`` with the
  ``by`` value documented in that Composable's registration surface, then
  register the desired handler.
- Each submodule documents its registered resources, declared hook points,
  and attached handlers in its registration-surface list.

.. seealso:: :mod:`flowing.plugins` provides phase-one plugins for capabilities
    shared across Agents or at process scope. :class:`flowing.hooks.HookRegistry`
    is the primary attachment point for Composables.
"""

from .compact import DEFAULT_COMPACT_PROMPT, DEFAULT_COMPACT_TEMPLATE, use_auto_compact, use_compact
from .prompt_until import use_prompt_until
from .reminder import use_system_reminder
from .retry import MAX_RETRY_DELAY, NON_RETRYABLE_ERRORS, RETRYABLE_ERRORS, use_retry

__all__: list[str]
