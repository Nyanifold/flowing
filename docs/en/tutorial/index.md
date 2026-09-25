# Flowing: A Detailed Tutorial

This tutorial starts with the basics, then explores Flowing's mechanics: multi-agent systems, message trees, context assembly, tools, plugins, interfaces, and operations. It is for readers who already use Flowing and want to understand how the framework works.

## Contents

### 0 Quick Start

- [Environment Setup](0-0-env-setup.md)
- [Your First Agent](0-1-hello-agent.md)
- [Using Built-in Tools](0-2-use-builtin-tools.md)
- [Your First Multi-Agent Application](0-3-hello-multi-agent.md)

### 1 Core Mental Models

- [Turn and Loop](1-1-turn-and-loop.md)
- [Message Model](1-2-message-model.md)
- [Parameters and setup()](1-3-agent-args-and-setup.md)
- [Hook Basics](1-4-hooks-basics.md)
- [Provide and Inject](1-5-provide-inject.md)
- [MCP Tools](1-6-mcp-tools.md)
- [Zero-Code Tools](1-7-zero-code-tools.md)
- [Runtime and Agent Directories](1-8-runtime-and-agent-dirs.md)
- [Plugins and Composables](1-9-plugins-and-composables.md)

### 2 Multi-Agent Systems

- [Declarative Subagents](2-1-subagents-declarative.md)
- [Subagent Lifecycle](2-2-subagents-lifecycle.md)
- [Writing a ScriptTool](2-3-write-script-tool.md)
- [Assembling a Multi-Agent Application](2-4-multi-agent-app.md)

### 3 Message Tree, Context, and Models

- [Message Tree and Loop](3-1-message-tree-and-loop.md)
- [Context Assembly](3-2-context-assembly.md)
- [Provider Basics](3-3-provider-basics.md)
- [Model Configuration and Budgets](3-4-model-config-and-budget.md)

### 4 Framework Mechanics

- [Core State and Message Persistence](4-1-core-state-and-message-persistence.md)
- [State Namespaces and Registration](4-2-state-namespaces-and-registration.md)
- [Editing the Message Tree](4-3-message-tree-surgery.md)
- [The Three-Layer Capability Model and Bindings](4-4-three-axes-and-tool-entry.md)
- [Tool Mechanics](4-5-tool-mechanics.md)
- [Errors and Control Flow](4-6-errors-and-control.md)
- [Agent Mechanics](4-7-agent-mechanics.md)
- [Runtime Mechanics](4-8-runtime-mechanics.md)
- [Parsable Template Evaluation](4-9-parsable.md)

### 5 Declarative Assembly and Extensions

- [Complete .fya Field Reference](5-1-fya-reference.md)
- [Plugins](5-2-plugins.md)
- [Composables](5-3-composables.md)

### 6 Interfaces, Embedding, and Operations

- [Choosing an Interface](6-1-interfaces.md)
- [Embedding Flowing in an Application](6-2-embedding.md)
- [Operations](6-3-ops.md)

Chapters 5-2 through 6-3 cover plugins, composables, interfaces, embedding, and
operations. Each of these chapters contains the complete material needed for
its examples inline, including configuration, prompts, code, inputs, and
recorded outputs.

```{toctree}
:hidden:
:maxdepth: 1

0-0-env-setup
0-1-hello-agent
0-2-use-builtin-tools
0-3-hello-multi-agent
1-1-turn-and-loop
1-2-message-model
1-3-agent-args-and-setup
1-4-hooks-basics
1-5-provide-inject
1-6-mcp-tools
1-7-zero-code-tools
1-8-runtime-and-agent-dirs
1-9-plugins-and-composables
2-1-subagents-declarative
2-2-subagents-lifecycle
2-3-write-script-tool
2-4-multi-agent-app
3-1-message-tree-and-loop
3-2-context-assembly
3-3-provider-basics
3-4-model-config-and-budget
4-1-core-state-and-message-persistence
4-2-state-namespaces-and-registration
4-3-message-tree-surgery
4-4-three-axes-and-tool-entry
4-5-tool-mechanics
4-6-errors-and-control
4-7-agent-mechanics
4-8-runtime-mechanics
4-9-parsable
5-1-fya-reference
5-2-plugins
5-3-composables
6-1-interfaces
6-2-embedding
6-3-ops
```
