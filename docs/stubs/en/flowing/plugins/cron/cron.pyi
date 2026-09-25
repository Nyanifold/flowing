"""Plugin registration and per-agent enablement for scheduled jobs.

Installing :class:`CronPlugin` registers the ``schedule-cron`` and
``manage-cron`` tools in the runtime's global tool registry. It does not create
a runtime-wide scheduler or provide cron state. Calling :func:`use_cron` from
an agent's ``setup()`` declares that agent's state key and hook point, creates
its timer runtime, and attaches recovery and destruction handlers. The module
API remains usable without installing the plugin; without the plugin, an LLM
simply has no cron tools to call.

Task registration is separate from enablement. Applications can register
tasks in an ``after_create`` handler, while an LLM can do so through the
``schedule-cron`` tool when the agent exposes it.

.. rubric:: Per-agent resources

- ``cron_jobs`` is registered with a default empty list; registration writes
  the default immediately, is idempotent, and keeps the key present in the
  agent's persisted state.
- ``on_cron_trigger`` is declared with ``by="cron"`` and
  ``match_on="source"``. Pattern handlers match against the hook value's
  top-level ``source`` field, and the delivery routine dispatches the hook for
  each due job.
- ``after_recover`` rebuilds timers and delivers missed work; ``before_destroy``
  cancels timer handles without deleting task records.
- An agent that has not called ``use_cron`` has none of these resources, and
  module API calls for it raise ``ValueError``.

.. rubric:: Example

.. code-block:: python

    from flowing.plugins.cron import CronFireContext, schedule, use_cron

    async def setup(self, data_dir: str):
        use_cron(self)
        self.add_tool("schedule-cron")
        self.add_tool("manage-cron")

        @self.hooks.after_create
        def _(agent, _value=None):
            schedule(
                agent,
                "3 2 * * *",
                "Please perform the daily reflection. The current time is "
                "{{current_time:%Y-%m-%d %H:%M}}",
                source="daily",
            )

        @self.hooks.on_cron_trigger["daily-*"]
        def _(agent, fire: CronFireContext):
            # Placeholder: replace this with an application-defined condition.
            if some_condition:
                fire.shortcut = True  # Skip this delivery without advancing the cursor.
            return fire

.. seealso:: :mod:`flowing.plugins.cron.jobs` (runtime and module API),
    :mod:`flowing.plugins.cron.models` (data objects),
    :mod:`flowing.plugins.cron.tools` (LLM tools)
"""

from typing import ClassVar

from flowing.agent import Agent
from flowing.plugins import Plugin
from flowing.runtime import Runtime


class CronPlugin(Plugin):
    """Plugin that registers the two LLM tools for scheduling.

    Installing it registers ``schedule-cron`` and ``manage-cron`` with the
    runtime. It does not provide dependencies, create a scheduler, or persist
    agent state. Per-agent state and timer handles are established by
    :func:`use_cron`.

    Installing or omitting this plugin does not change whether an enabled
    agent can use the module-level scheduling API. The tools become available
    to an LLM only when the agent also adds them. The plugin owns no shutdown
    resources; agent destruction cancels its timers.
    """

    name: ClassVar[str] = "cron"
    """Registration name used by ``runtime.get_plugin("cron")``."""

    dependencies: ClassVar[list[str]] = []
    """This plugin declares no dependencies."""

    def install(self, runtime: Runtime) -> None:
        """Register only the two LLM tools; do not read persisted state."""
        ...


def use_cron(agent: Agent) -> None:
    """Enable scheduled jobs for one agent instance.

    Call this exactly once from the agent's ``setup()``. It registers the
    ``cron_jobs`` state key with an empty-list default. Registration immediately
    persists that default, is idempotent, and keeps the key present thereafter.
    It declares the ``on_cron_trigger`` hook point with ``by="cron"`` and
    ``match_on="source"``; pattern handlers match the hook value's top-level
    ``source`` field. It creates the per-agent timer runtime in ``agent._cron``
    and attaches lifecycle handlers.

    The ``after_recover`` handler first delivers any overdue jobs using the
    same coalescing delivery path as a timer fire, then arms timers for the jobs
    that remain. The ``before_destroy`` handler cancels timer handles only;
    persisted task records remain in the state table so overdue jobs can be
    delivered after recovery.

    Calling this more than once on the same instance is a programming error:
    it attaches duplicate handlers and is not idempotent. It does not register
    any jobs and does not require :class:`CronPlugin` to be installed. An agent
    that never calls it has no cron hook, runtime slot, timers, or state key.
    Recovery runs ``setup()`` on a new instance, so declarations and handlers
    are attached to that instance rather than accumulated across instances.

    :param agent: The agent instance to enable.

    .. seealso:: :class:`CronPlugin`, :mod:`flowing.plugins.cron.jobs`,
        :class:`flowing.plugins.cron.models.CronJob`,
        :class:`flowing.plugins.cron.models.CronFireContext`
    """
    ...
