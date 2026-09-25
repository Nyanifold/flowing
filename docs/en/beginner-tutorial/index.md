# Multi-Agent Systems: An Introductory Course

This course is for readers familiar with object-oriented Python but new to building multi-agent systems. It first covers key concepts, design decisions, organizational patterns, and their trade-offs, then uses Flowing examples to build a system-level understanding of agentic systems step by step.

## Contents

### Chapter 1: Runtime and Tool Foundations

- [The Agent Runtime](1-1-agent-runtime.md)
- [Tool Calling](1-2-tool-calling.md)
- [Tool Specialization](1-3-tool-specialization.md)
- [The ReAct Loop](1-4-react-loop.md)

### Chapter 2: Loops, Messages, and Assembly

- [Loop and Concurrency](2-1-loop-and-concurrency.md)
- [The Message Model](2-2-message-model.md)
- [Assembly and Parameterization](2-3-assembly-and-params.md)
- [Interception Points](2-4-interception-points.md)

### Chapter 3: Sharing, Persistence, and Extensions

- [Cross-Layer Sharing](3-1-sharing-and-scopes.md)
- [Persistence](3-2-persistence.md)
- [The Extension Architecture](3-3-extension-architecture.md)

### Chapter 4: Protocols and Tool Wrappers

- [Protocol-Based Tool Access](4-1-protocol-tool-access.md)
- [Declarative Tool Wrappers](4-2-declarative-tool-wrappers.md)

### Chapter 5: Multi-Agent Organization and Final Assembly

- [Motivation and Cost of Splitting](5-1-motivation-and-cost.md)
- [Organization Patterns](5-2-organization-patterns.md)
- [Orchestrator Design](5-3-orchestrator-design.md)
- [Instance Lifecycle](5-4-instance-lifecycle.md)
- [Final Assembly](5-5-final-assembly.md)

### Appendices

- [Appendix A: Environment Setup](a-appendix-environment.md)
- [Appendix B: Quick Reference](b-appendix-glossary.md)

Each chapter contains its complete example code, prompts, configuration, input, and representative output. Model responses may vary with server-side sampling; the output shown in each chapter illustrates the flow and the behavior to verify.

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
