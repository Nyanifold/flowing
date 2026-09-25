"""``flowing`` — the authoritative API contract for Flowing's top-level
exports in a lightweight Agent framework.

.. rubric:: Overview

Flowing follows one core principle: the framework provides mechanisms, not
policies. Its package has three layers:

- **Framework core:** ``runtime``, ``agent``, ``agent_registry``,
  ``subagents``, ``message``, ``context``, ``parsable``, ``params``,
  ``tool`` (a subpackage), ``media``, ``model``, ``hooks``, ``lists``,
  ``errors``, ``snapshot``, ``providers``, ``persistence``, ``parser``,
  ``paths``, ``provide``, and ``compiler``. This module re-exports the
  symbols from these modules that are intended for routine application use.
- **Built-in extensions:** ``flowing.plugins`` provides ``skills``, ``comm``,
  ``cron``, ``workflow``, and ``clipboard``. They ship with the package and
  are enabled explicitly with ``runtime.install(...)``.
- **Application layer:** ``flowing.composables`` provides functional
  ``use_xxx(agent)`` helpers.

The runtime model is based on a message-level tree: ``Message.id`` and
``parent_id`` form the links, and ``current_head_id`` points to a message ID.
A Turn is only a logical execution phase. Its runtime carrier,
:class:`flowing.agent.TurnContext`, is not persisted or added to the tree.
Capability descriptions have three layers: the executable object, its
LLM-visible declaration, and the Agent-level binding. This division applies
to Tools, subagents, and Skills. The provide-inject chain walks upward through
``_parent_id`` links, and hooks are scoped to individual instances.

.. rubric:: Design rationale

The top-level exports are limited to the smallest set an Agent project needs
to import. Other symbols, such as detailed exception types, snapshot views,
and internal containers, are imported explicitly from their submodules.
This keeps the top-level namespace readable and easy to remember.

.. rubric:: Usage example

.. code-block:: python

    # @/main.py — the project entry-point convention
    import flowing
    from flowing import Runtime, on

    async def main(**kwargs) -> Runtime:
        runtime = flowing.Runtime()       # launch binds the @ context
        runtime.install(...)              # phase one: install extensions
        await runtime.mount("@/root.fya") # create the root Agent
        return runtime

.. code-block:: bash

    flowing run <path>   # launch(path) → await runtime → SIGINT → shutdown

.. rubric:: Behavior notes

- ``flowing.launch(path, **kwargs)`` is the only supported Runtime creation
  entry point. Calling ``Runtime()`` directly without an ``@`` context raises
  ``RuntimeError``.
- This module only re-exports symbols; it defines no new ones. The defining
  submodules document each symbol's full contract.
- Every symbol exported here is part of the cross-version stable contract.
  Underscore-prefixed symbols and implementation details of
  ``flowing.interfaces.cli`` and ``flowing.interfaces.web`` are outside that
  stability boundary.
"""
from flowing.agent import (
    Agent,
    CancelContext,
    Execution,
    FieldUpdate,
    ProviderErrorContext,
    TurnContext,
    TurnResult,
)
from flowing.agent_registry import AgentRegistry
from flowing.context import Context, PromptBlock, PromptBlockList, PromptSegment
from flowing.errors import FlowingError, Intercepted
from flowing.hooks import HookRegistry, on
from flowing.lists import ManagedList
from flowing.message import (
    ContentBlock,
    FileBlock,
    ImageBlock,
    MediaBlock,
    Message,
    MessageChain,
    MessageKind,
    MessagePriority,
    MessageQueue,
    StructBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
)
from flowing.model import ModelConfig
from flowing.params import ConfigKey, InjectionKey
from flowing.parsable import PENDING, Parsable
from flowing.plugins import Plugin
from flowing.provide import ProvideNode
from flowing.providers import Provider, ProviderConfig, ProviderDelta, ProviderResponse, Usage
from flowing.builtins import FinishTool, SubagentInvokeTool
from flowing.runtime import Runtime, launch, resolve
from flowing.subagents import SubagentEntry, SubagentInvocation, SubagentResult
from flowing.tool import (
    Audio,
    File,
    Image,
    ScriptTool,
    Tool,
    ToolCall,
    ToolDefinition,
    ToolEntry,
    ToolRegistry,
    ToolResult,
    Video,
    flowing_tool,
    normalize_output,
    output_to_blocks,
    register_media_converter,
)

__all__ = [
    "Agent", "AgentRegistry", "Audio", "CancelContext", "ConfigKey",
    "ContentBlock", "Context", "Execution", "FieldUpdate", "File",
    "FileBlock", "FinishTool", "FlowingError", "ScriptTool", "HookRegistry",
    "Image", "ImageBlock", "InjectionKey", "Intercepted", "ManagedList",
    "MediaBlock", "Message", "MessageChain", "MessageKind", "MessagePriority",
    "MessageQueue", "ModelConfig", "PENDING", "Parsable", "Plugin",
    "PromptBlock", "PromptBlockList", "PromptSegment", "ProvideNode", "Provider",
    "ProviderConfig", "ProviderDelta", "ProviderResponse", "ProviderErrorContext",
    "Runtime", "StructBlock", "SubagentEntry", "SubagentInvocation",
    "SubagentInvokeTool", "SubagentResult", "TextBlock", "ThinkingBlock", "Tool",
    "ToolCall", "ToolCallBlock", "ToolDefinition", "ToolEntry", "ToolRegistry",
    "ToolResult", "TurnContext", "TurnResult", "Usage", "Video", "flowing_tool",
    "launch", "normalize_output", "on", "output_to_blocks", "register_media_converter",
    "resolve",
]
