# 多智能体系统入门教程

本教程面向熟悉 Python 面向对象编程、但没有多智能体开发经验的读者。内容先介绍关键概念、设计决策、组织模式及其权衡，再借助 Flowing 示例逐步建立对智能体系统（agentic systems）的整体认识。

## 目录

### 第 1 章：运行与工具基础

- [智能体运行时](1-1-agent-runtime.md)
- [工具调用](1-2-tool-calling.md)
- [工具定制](1-3-tool-specialization.md)
- [ReAct 循环](1-4-react-loop.md)

### 第 2 章：循环、消息与装配

- [循环与并发](2-1-loop-and-concurrency.md)
- [消息模型](2-2-message-model.md)
- [装配与参数化](2-3-assembly-and-params.md)
- [拦截点](2-4-interception-points.md)

### 第 3 章：共享、持久化与扩展

- [跨层共享](3-1-sharing-and-scopes.md)
- [持久化](3-2-persistence.md)
- [扩展架构](3-3-extension-architecture.md)

### 第 4 章：协议与工具封装

- [基于协议的工具接入](4-1-protocol-tool-access.md)
- [声明式工具封装](4-2-declarative-tool-wrappers.md)

### 第 5 章：多智能体组织与收尾

- [拆分的动因与成本](5-1-motivation-and-cost.md)
- [组织模式](5-2-organization-patterns.md)
- [编排器设计](5-3-orchestrator-design.md)
- [实例生命周期](5-4-instance-lifecycle.md)
- [最终装配](5-5-final-assembly.md)

### 附录

- [A 环境准备](a-appendix-environment.md)
- [附录 B：概念速查表](b-appendix-glossary.md)

每篇的示例代码、提示词、配置、输入与代表性输出均直接写在该篇正文中。各篇使用的模型回答可能因服务端采样而不同，文中的输出用于说明流程与验收关键行为。

```{toctree}
:hidden:
:maxdepth: 1

1-1-agent-runtime
1-2-tool-calling
1-3-tool-specialization
1-4-react-loop
2-1-loop-and-concurrency
2-2-message-model
2-3-assembly-and-params
2-4-interception-points
3-1-sharing-and-scopes
3-2-persistence
3-3-extension-architecture
4-1-protocol-tool-access
4-2-declarative-tool-wrappers
5-1-motivation-and-cost
5-2-organization-patterns
5-3-orchestrator-design
5-4-instance-lifecycle
5-5-final-assembly
a-appendix-environment
b-appendix-glossary
```
