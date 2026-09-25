# Flowing 详细教程

本教程采用由浅入深的路径讲解 Flowing 的运行机制，从快速上手逐步扩展到多智能体、消息树、上下文、工具、插件、接口和运维。适合已经开始使用 Flowing、希望深入理解框架机制的读者。

## 目录

### 0 快速上手

- [环境安装](0-0-env-setup.md)
- [第一个智能体](0-1-hello-agent.md)
- [使用内置工具](0-2-use-builtin-tools.md)
- [你的第一个多智能体应用](0-3-hello-multi-agent.md)

### 1 核心概念

- [回合与循环](1-1-turn-and-loop.md)
- [消息模型](1-2-message-model.md)
- [参数与 setup()](1-3-agent-args-and-setup.md)
- [钩子基础](1-4-hooks-basics.md)
- [provide 与 inject](1-5-provide-inject.md)
- [MCP 工具](1-6-mcp-tools.md)
- [零代码工具](1-7-zero-code-tools.md)
- [运行时目录](1-8-runtime-and-agent-dirs.md)
- [插件与 Composable 初识](1-9-plugins-and-composables.md)

### 2 多智能体系统

- [声明式组建团队](2-1-subagents-declarative.md)
- [子智能体的创建与生命周期](2-2-subagents-lifecycle.md)
- [编写 ScriptTool](2-3-write-script-tool.md)
- [组装多智能体应用](2-4-multi-agent-app.md)

### 3 消息树、上下文与模型

- [消息树与循环机制](3-1-message-tree-and-loop.md)
- [上下文组装](3-2-context-assembly.md)
- [Provider 基础](3-3-provider-basics.md)
- [模型配置与预算](3-4-model-config-and-budget.md)

### 4 核心机制详解

- [核心状态与消息持久化](4-1-core-state-and-message-persistence.md)
- [状态命名空间](4-2-state-namespaces-and-registration.md)
- [消息树编辑](4-3-message-tree-surgery.md)
- [三层能力模型与绑定](4-4-three-axes-and-tool-entry.md)
- [工具机制](4-5-tool-mechanics.md)
- [错误与控制](4-6-errors-and-control.md)
- [Agent 机制详解](4-7-agent-mechanics.md)
- [Runtime 机制详解](4-8-runtime-mechanics.md)
- [Parsable 模板求值](4-9-parsable.md)

### 5 声明式装配与扩展

- [.fya 全字段参考](5-1-fya-reference.md)
- [插件](5-2-plugins.md)
- [Composable 扩展](5-3-composables.md)

### 6 接口、嵌入与运维

- [接口选择](6-1-interfaces.md)
- [嵌入宿主应用](6-2-embedding.md)
- [运维](6-3-ops.md)

5-2 至 6-3 章依次介绍插件、Composable、接口、嵌入与运维。这些章节
均在正文内给出示例所需的完整材料，包括配置、提示词、代码、输入与留档
输出。

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
