# Flowing: A Concise Guide

Flowing is a lightweight agent framework built on the principle that **the framework provides mechanisms, not policies**. The core provides the runtime, message flow, context assembly, and extension points; applications compose policies such as retries, compaction, and approval. This concise guide has four chapters on the runtime model, the message tree and context, capability descriptions and multi-agent systems, and assembly, control, and runtime environments.

## Contents

1. [The Runtime Model](01-运行模型.md): Set up model access, launch a Flowing project, and run a conversation.
2. [The Message Tree and Context](02-消息树与上下文.md): Understand the message model, history organization, and context assembly.
3. [The Three-Layer Capability Model and Multi-Agent Systems](03-三层能力描述与多智能体.md): Declare and bind capabilities, implement tools, and organize multi-agent teams.
4. [Assembly, Control, and Runtime Environments](04-装配控制与运行环境.md): Use hooks, dependency injection, state, and cancellation to connect Flowing to an application.

Each chapter includes the example code, configuration, prompts, inputs, and recorded outputs needed for its discussion. Chapter 01 starts with a Q&A agent; Chapter 03 shows an orchestrator working with a translator; Chapters 02 and 04 use interaction examples to explain context observation, hooks, and state recovery.

```{toctree}
:hidden:
:maxdepth: 1

01-运行模型
02-消息树与上下文
03-三层能力描述与多智能体
04-装配控制与运行环境
```
