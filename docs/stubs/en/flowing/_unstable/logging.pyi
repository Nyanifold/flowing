"""Experimental hook-chain logging plugin for internal debugging.

The plugin writes hook-trigger facts to one ``logging.jsonl`` file per enabled
Agent. It is installed with ``runtime.install(LoggingPlugin(...))`` and enabled
for an Agent with ``use_logging(self)``. The interface and persistence format
are intentionally not stable.
"""

from typing import ClassVar, Literal

from flowing.agent import Agent
from flowing.plugins import Plugin
from flowing.runtime import Runtime

logging_plugin_key: str
"""Provide key used to retrieve the :class:`LoggingPlugin` instance."""

LogLevel = Literal["OFF", "INFO", "DEBUG"]
"""Supported global logging levels."""


class LoggingPlugin(Plugin):
    """Hook-chain logger with a level switch, plugin detection, and persistence.

    ``install()`` registers the plugin and records which built-in extensions
    are installed. ``use_logging()`` attaches per-Agent observation handlers;
    installing the plugin alone does not attach handlers.
    """

    name: ClassVar[str]
    """Plugin registration name."""

    namespace: ClassVar[str]
    """Namespace prefix used by the plugin's provide key."""

    dependencies: ClassVar[list[str]]
    """Declared plugin dependencies; this plugin has none."""

    detected: frozenset[str]
    """Built-in extension names detected during installation."""

    max_value_repr: int
    """Maximum representation length used for DEBUG-level values."""

    def __init__(self, level: LogLevel = "INFO", *, max_value_repr: int = 500) -> None:
        """Create a plugin.

        :param level: Initial global level.
        :param max_value_repr: Maximum length of DEBUG value representations.
        :raises flowing.errors.FlowingError: If ``level`` is invalid.
        """
        ...

    @property
    def level(self) -> LogLevel:
        """Current global level; handlers read it when each hook fires."""
        ...

    @level.setter
    def level(self, value: LogLevel) -> None:
        """Set the global level and validate it."""
        ...

    def install(self, runtime: Runtime) -> None:
        """Provide this plugin and detect the supported built-in extensions."""
        ...


def use_logging(agent: Agent) -> None:
    """Enable logging handlers for one Agent.

    INFO attaches the key hook points immediately. DEBUG additionally covers
    declared extension hook points. Handlers only observe values, do not alter
    the pipeline, and degrade to a warning if persistence fails.

    :param agent: Agent whose setup is enabling logging.
    :raises flowing.errors.MissingProvideError: If the plugin is not installed.
    """
    ...
