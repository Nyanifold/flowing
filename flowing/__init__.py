"""``flowing`` —— 轻量式 Agent 框架：最终 API 规约（顶层导出）。

.. rubric:: 功能介绍

Flowing 的核心立场是「框架只提供机制，不提供策略」。包结构三层：

- **框架核心**：``runtime`` / ``agent`` / ``agent_registry`` /
  ``subagents`` / ``message`` / ``context`` /
  ``parsable`` / ``params`` / ``tool``（子包）/ ``media`` / ``model`` /
  ``hooks`` / ``lists`` / ``errors`` /
  ``snapshot`` / ``providers`` / ``persistence`` / ``parser`` / ``paths`` /
  ``provide`` / ``compiler`` —— 本模块顶层导出其中面向日常使用的符号。
- **内置扩展**：``flowing.plugins``（``skills`` / ``comm`` / ``cron`` /
  ``workflow`` / ``clipboard``）——随包发布、显式 ``runtime.install(...)`` 启用。
- **应用层**：``flowing.composables``（``use_xxx(agent)`` 纯函数式注入）。

运行模型锚点：消息级树（``Message.id`` + ``parent_id`` 链，
``current_head_id`` 指向消息 id）；Turn 仅为逻辑执行阶段（执行期载体
:class:`flowing.agent.TurnContext`，不落盘、不进树）；三层能力描述
（可执行对象 / LLM 可见声明 / Agent 级绑定，推广到 Tool / 子 Agent /
Skill）；provide-inject 沿 ``_parent_id`` 链上溯；实例级钩子系统。

.. rubric:: 设计动机

顶层导出收敛到「写一个 Agent 项目一定会 import」的最小集合；其余符号
（异常明细、快照视图、内部容器）经子模块显式导入，保持顶层命名空间
可读、可记忆。

.. rubric:: 使用示例

.. code-block:: python

    # @/main.py —— 子项目入口约定
    import flowing
    from flowing import Runtime, on

    async def main(**kwargs) -> Runtime:
        runtime = flowing.Runtime()      # @ 由 launch 上下文自动绑定
        runtime.install(...)              # 阶段一：安装扩展
        await runtime.mount("@/root.fya") # 创建根 Agent
        return runtime

.. code-block:: bash

    flowing run <path>   # launch(path) → await runtime → SIGINT → shutdown

.. rubric:: 行为规约

- ``flowing.launch(path, **kwargs)`` 是 Runtime 的**唯一创建入口**；
  绕过它直接 ``Runtime()`` 因无 ``@`` 上下文抛 ``RuntimeError``。
- 本模块只做 re-export，不定义任何新符号；各符号的完整契约见所属
  子模块的规约。
- 稳定性：本模块导出的全部符号属跨版本稳定契约；``_`` 前缀符号与
  ``flowing.interfaces.cli`` / ``flowing.interfaces.web`` 的细节不属稳定边界。
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
from flowing.model import (
    ModelConfig,
)
from flowing.params import ConfigKey, InjectionKey
from flowing.parsable import PENDING, Parsable
from flowing.plugins import Plugin
from flowing.provide import ProvideNode
from flowing.providers import (
    Provider,
    ProviderConfig,
    ProviderDelta,
    ProviderResponse,
    Usage,
)
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
    "Agent",
    "AgentRegistry",
    "Audio",
    "CancelContext",
    "ConfigKey",
    "ContentBlock",
    "Context",
    "Execution",
    "FieldUpdate",
    "File",
    "FileBlock",
    "FinishTool",
    "FlowingError",
    "ScriptTool",
    "HookRegistry",
    "Image",
    "ImageBlock",
    "InjectionKey",
    "Intercepted",
    "ManagedList",
    "MediaBlock",
    "Message",
    "MessageChain",
    "MessageKind",
    "MessagePriority",
    "MessageQueue",
    "ModelConfig",
    "PENDING",
    "Parsable",
    "Plugin",
    "PromptBlock",
    "PromptBlockList",
    "PromptSegment",
    "ProvideNode",
    "Provider",
    "ProviderConfig",
    "ProviderDelta",
    "ProviderResponse",
    "ProviderErrorContext",
    "Runtime",
    "StructBlock",
    "SubagentEntry",
    "SubagentInvocation",
    "SubagentInvokeTool",
    "SubagentResult",
    "TextBlock",
    "ThinkingBlock",
    "Tool",
    "ToolCall",
    "ToolCallBlock",
    "ToolDefinition",
    "ToolEntry",
    "ToolRegistry",
    "ToolResult",
    "TurnContext",
    "TurnResult",
    "Usage",
    "Video",
    "flowing_tool",
    "launch",
    "normalize_output",
    "on",
    "output_to_blocks",
    "register_media_converter",
    "resolve",
]
