"""Built-in extension packages and the common plugin interface.

This package contains the common :class:`Plugin` base class and the built-in
``skills``, ``comm``, ``peers``, ``cron``, ``workflow``, and ``clipboard`` extensions.
Built-in extensions ship with Flowing but are not installed automatically.
The core consumes the plugin interface through
``flowing.runtime.Runtime.install()`` and does not depend on any particular
extension.

Enablement has two stages. ``runtime.install(plugin)`` registers Runtime-wide
capabilities such as tools, provided values, configuration namespaces, Agent
types, resources, or global state namespaces. Each Agent that needs
per-instance capabilities enables them separately from ``setup()`` (usually
with a ``use_xxx(self)`` function). A capability from an extension that was
not enabled is absent; the framework does not install it and then skip it at
runtime.

Flowing calls only ``install()`` and ``shutdown()`` on a plugin instance.
Runtime collaboration after installation uses the plugin's own interfaces,
``provide``/``inject``, or the message queue; the framework does not call
arbitrary plugin methods during normal operation.

Dependencies are declarations, not plugin-managed checks. After each call to
``Runtime.install()``, Flowing incrementally checks the installed plugin set,
warns about dependencies that are still missing, and raises
``DependencyError`` if the graph contains a cycle. A missing dependency does
not prevent installation. Plugin names are unique within one Runtime, not
globally across the ecosystem. Re-providing the same key replaces its value
and makes the new value immediately visible through injection.

The plugin conventions are author responsibilities rather than enforced
checks: ``install()`` only registers capabilities; plugins do not perform
business work, query other plugins, or modify unrelated state there. Runtime
registration state is not added or removed during normal operation, and
plugins declare dependencies without checking them themselves. Plugins
collaborate through provided/injected values or messages, so their
installation order does not determine collaboration results. When a plugin
binds a function onto an Agent or Runtime, it first checks with ``hasattr()``
and leaves an existing member (including a class method) untouched. A common
registration-name convention is the class name without ``Plugin`` converted
to kebab-case; Agent-bound member names use the registration name in
underscore form as their prefix. ``namespace`` is optional metadata for a
plugin's own keys and is not read by Flowing.

An exception from ``install()`` propagates directly from
``Runtime.install()``; Flowing does not isolate a failing plugin, and that
plugin is not added to the installed set.

.. rubric:: Usage example

.. code-block:: python

    from flowing.plugins import Plugin
    from flowing.runtime import Runtime


    class MyPlugin(Plugin):
        name = "myplugin"
        dependencies: list[str] = []

        def install(self, runtime: Runtime) -> None:
            runtime.register_tool(MyTool())  # MyTool is defined by the application.
            runtime.provide("myplugin:service", service)  # service is application-owned.

        async def shutdown(self) -> None:
            await service.close()

    runtime.install(MyPlugin())
"""
from pathlib import Path
from typing import ClassVar
from flowing.runtime import Runtime

__all__: list[str] = ["Plugin"]

class Plugin:
    """Common base class for built-in and application-defined plugins.

    Construct plugin instances in application code and pass them to
    ``Runtime.install()``; Flowing does not instantiate plugins. Subclasses
    must declare ``name`` and ``dependencies`` and may declare ``namespace``.
    Override ``install()`` to register global capabilities and ``shutdown()``
    to release runtime resources.

    The Runtime calls ``install()`` once per plugin instance, in installation
    order. It calls ``shutdown()`` once during shutdown, after recursively
    destroying Agents. Install plugins before creating Agents; a plugin
    installed later does not retrofit its capabilities onto existing Agents.
    The framework does not enforce this timing, so late installation itself
    is not an error.
    An exception from ``install()`` propagates and the failed plugin is not
    recorded as installed. An exception from one ``shutdown()`` is logged
    without preventing later plugins from shutting down.

    ``install()`` should only register capabilities. It should not perform
    business work, query another plugin, modify unrelated state, or retain the
    Runtime for runtime callbacks. It runs again after each process restart,
    so registration decisions should not depend on persisted state. Rebuild
    runtime structures from persisted values later, for example in an Agent's
    ``after_recover`` handler or through plugin-managed lazy rebuilding.
    Per-Agent state keys belong in Agent ``setup()``, not in Runtime-level
    registration.

    .. rubric:: Usage example

    .. code-block:: python

        from flowing.plugins import Plugin
        from flowing.runtime import Runtime


        class GuardrailPlugin(Plugin):
            name = "guardrail"
            dependencies: list[str] = ["comm"]

            def install(self, runtime: Runtime) -> None:
                runtime.register_tool(ApprovalTool())
                runtime.provide("guardrail:policy", policy)
                runtime.register_config_namespace("guardrail", {
                    "type": "object",
                    "properties": {},
                })
    """
    name: ClassVar[str]
    """Required registration name, used for dependency matching, lookup,
    snapshots, logs, and duplicate detection. Flowing does not derive a
    default. Names are unique only within one Runtime; kebab-case based on the
    class name is a convention, not a validation rule.
    """
    namespace: ClassVar[str]
    """Optional prefix for keys and configuration owned by the plugin.
    Flowing does not read or enforce it; plugins use it by convention to avoid
    collisions. Public tool names need not include the prefix.
    """
    dependencies: ClassVar[list[str]]
    """Names of plugins declared as dependencies. Flowing checks the
    installed plugin set after ``Runtime.install()``: a missing dependency
    emits a warning without aborting installation, while a cycle among
    installed plugins raises ``DependencyError``. The plugin itself does not
    perform this check.
    """
    @property
    def plugin_dir(self) -> Path:
        """Directory containing the file that defines this plugin class.

        Use this as the base for resources shipped beside the plugin, such as
        ``.fya`` tool or subagent definitions and templates. These resources
        may be outside the project ``@/`` root and outside every Agent's
        ``source_dir`` lookup chain, so build their paths from this property
        and pass them to the file-path form of ``runtime.register_tool()``.
        Supply ``namespace`` explicitly when needed; namespace selection is
        independent of the file path.

        .. code-block:: python

            def install(self, runtime: Runtime) -> None:
                runtime.register_tool(
                    str(self.plugin_dir / "tools" / "pay.fya"),
                    namespace="myplugin",
                )

        This read-only property has no side effects.
        """
        ...

    def install(self, runtime: Runtime) -> None:
        """Register this plugin's global capabilities during installation.

        The base implementation does nothing. An override may use Runtime
        registration APIs for tools, provide values, configuration namespaces,
        Agent types, resources, or global state. Per-Agent state belongs in
        Agent setup instead. Installation should only register capabilities;
        it should not perform business work, query another plugin, or modify
        unrelated state. For resources shipped beside a plugin, construct the
        definition-file path from ``plugin_dir`` and pass it to the
        file-path form of ``register_tool()`` with an explicit namespace when
        needed. Runtime registration state is established here rather than
        added or removed during normal operation. Do not retain the Runtime
        for runtime callbacks; ordinary collaboration uses ``provide`` /
        ``inject`` or messages. Persisted values should not determine
        registration decisions because this method runs again on process
        startup; rebuild runtime structures later, such as in
        ``after_recover`` or through plugin-managed lazy rebuilding.

        Re-providing an existing key replaces its value. If this method
        raises, the exception propagates from ``Runtime.install()`` and the
        plugin is not added to the installed set.

        .. rubric:: Usage example

        .. code-block:: python

            from flowing.plugins.skills import SkillLoadTool, SkillRegistry, skill_registry_key


            def install(self, runtime: Runtime) -> None:
                runtime.register_tool(SkillLoadTool())
                runtime.provide(skill_registry_key, SkillRegistry())

        :param runtime: Runtime currently installing this plugin.
        """
        ...

    async def shutdown(self) -> None:
        """Release resources owned by this plugin during Runtime shutdown.

        The base implementation is a no-op for subclasses to override. The
        plugin is responsible for cleaning up resources it installed, such as
        stopping timers, closing buses, and releasing runtime-only resources.
        After ``install()``, this is the only method the framework calls on a
        plugin instance.

        It runs during ``Runtime.shutdown()``, after recursive destruction of
        all Agents and before global state views are closed or the exit event
        is set. The Runtime awaits plugins in installation order. A failure
        from one plugin is logged and does not prevent later cleanup. This
        method does not delete persisted data: session directories and global
        state files remain with their directories; only runtime resources are
        released.

        .. seealso:: :meth:`install` and
            :meth:`flowing.runtime.Runtime.shutdown`
        """
        ...
