# 5-3 · Orchestrator Design: Catalog Routing and the Dispatch Protocol

> Self-contained example: the complete runtime configuration, orchestrator and worker definitions, input, programmatic demo code, and representative output appear below. Create the relative files, then run `uv run flowing repl .` from the current directory; run `uv run python demo_enabled.py` for the visibility demo.
> The conversation example requires the `DEEPSEEK_API_KEY` environment variable. Do not put a real credential in a configuration file.

> Prerequisites: Chapter 5-2 "Organization Patterns"

## What this chapter covers

The core design of the orchestrator-worker pattern: how the worker catalog
supports routing decisions, how the dispatch protocol is structured, and the
discipline the orchestrator itself must follow.

## Background

The orchestrator's scheduling decisions are made by the model ("which worker
should take this task" is a judgment expressed in language), and the input the
model needs is a structured list of workers. The quality of that list therefore
determines the quality of routing — consistent with traditional scheduling
systems: the input surface of the scheduler determines scheduling accuracy.

## Core concepts

### The catalog: the input surface of routing

The catalog describes three elements for each dispatchable worker:

```
- alias (referenced when invoking)
- responsibility description (model-facing natural language: what it does, when to use it)
- visible parameters (name, type, description; sensitive or fixed parameters are not shown)
```

How the description field is written directly determines routing accuracy. A
"universal assistant" description is equivalent to no description; an effective
description states the types of tasks it fits and the boundaries where it does
not.

```yaml
# conceptual shape of a catalog entry
- alias: greeter
  description: greeting requests; outputs only greetings, does not handle information queries
  parameters:
    style: { type: string, description: tone of voice }
```

The catalog is **rendered on the spot**: it is regenerated from the current
binding state on every context assembly. Enabling or disabling a worker at
runtime is reflected in the next turn; the catalog is always consistent with
the binding state. The orchestrator in this chapter's example declares three
workers, one of which is explicitly disabled. Run `uv run python demo_enabled.py`:

```console
subagent binding table:
  coder: visible=True -> in catalog (LLM-visible)
  reviewer: visible=True -> in catalog (LLM-visible)
  auditor: visible=False -> not in catalog (LLM-invisible)
programmatically invoking visible=False auditor -> result='Audit channel activated.' status=completed
```

Line by line: of the three records in the binding table, the first two enter
the catalog (the model can see them and dispatch to them); `auditor` is
disabled and therefore stays out of the catalog — the model has no idea it
exists. The last line proves that it **can still be invoked directly from
code** (it returned a normal result). Visibility and executability are two
independent switches: the catalog controls "whether the model can see it,"
not "whether the capability exists."

### The dispatch protocol: the structure of the task package

The complete information structure of one dispatch:

```python
dispatch = {
    "to": "greeter",                                   # catalog alias
    "task": "Greet the user in a formal tone",         # natural-language task description
    "args": {"style": "formal"},                       # structured arguments (per the catalog's visible parameters)
    "context": "The user just completed registration", # necessary background (optional)
}
```

The quality of a dispatch depends on whether the task description is
self-sufficient: a worker cannot see the orchestrator's full history and
receives only the information explicitly carried in the dispatch package.
Insufficient context is the leading cause of dispatch failure — "just fix
that thing from before" means nothing to the worker.

After creating the files listed later in this chapter and running `uv run flowing repl .`, enter this compound task:

**Input**

```text
I need a Python function that returns the nth Fibonacci number. Define f(0)=0 and f(1)=1. Have coder write it, then have reviewer review it and summarize the findings for me.
/exit
```

**Representative output**

```text
(agent-main)>>> I need a Python function that returns the nth Fibonacci number. Define f(0)=0 and f(1)=1. Have coder write it, then have reviewer review it and summarize the findings for me.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"coder","name":"fib-coder","prompt":"Write a Python function named fibonacci that accepts integer n and returns the nth Fibonacci number, with f(0)=0 and f(1)=1. Raise ValueError when n is negative. Keep the code concise and add one usage note. Return complete, runnable Python code."}
[tool:completed] subagent-invoke ->
def fibonacci(n: int) -> int:
    """Return the nth Fibonacci number, with f(0)=0 and f(1)=1."""
    if n < 0:
        raise ValueError("n must not be negative")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"reviewer","name":"fib-reviewer","prompt":"Review this code read-only and list findings by severity. Do not modify it:\ndef fibonacci(n: int) -> int:\n    \"\"\"Return the nth Fibonacci number, with f(0)=0 and f(1)=1.\"\"\"\n    if n < 0:\n        raise ValueError(\"n must not be negative\")\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n\nCheck correctness, boundaries, runtime type validation, and performance."}
[tool:completed] subagent-invoke ->
Low priority: the function does not validate types at runtime; because bool subclasses int, True is accepted as 1; the iterative algorithm may be slow for very large n; and there is no input limit. Informational: the docstring does not mention the ValueError for negative input. Python integers have arbitrary precision, so integer overflow is not a concern.
Summary: the iterative logic is correct; n=0 and n=1 are handled correctly, and negative n raises ValueError. No high-severity issue was found.
[thinking] (reasoning trace omitted)
(agent-main)>>>
```

Model wording may vary. Verify that the second delegation carries the complete code from the first and that the reviewer performs a read-only review.

Line by line on the dispatch chain: the two `subagent-invoke` calls are
strictly sequential — the second dispatch package must carry the output of
the first (the code), because reviewer cannot see coder's history. This is
directly visible in the recorded transcript: reviewer's `prompt` carries the
full text of the code delivered by coder. What the orchestrator does between
the two rounds is exactly "pack the previous worker's output into the next
dispatch package."

### Three disciplines for the orchestrator

1. **Minimize its own tool table**: the orchestrator holds no execution tools;
   otherwise the model tends to complete tasks itself instead of delegating,
   and the routing structure becomes a sham. The orchestrator in this chapter's
   example has exactly one tool: invoking workers;
2. **Summarize results without reworking them**: the worker's output is relayed
   verbatim; second-hand rewriting by the orchestrator makes error attribution
   impossible (whose words is the user seeing?);
3. **Dispatch on demand, no chatter**: each turn dispatches only to the workers
   necessary for the task; every description in the catalog occupies the
   orchestrator's context.

### Complete example materials

The following are all files needed for the conversation and visibility demonstrations. Create them at the relative filenames shown. The prompts, configuration, and fixed program output are reproduced here in full.

```console
export DEEPSEEK_API_KEY=sk-your-key-here
```

**`providers.yaml`**

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

**`models.yaml`**

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

**`model-tags.yaml`**

```yaml
tags:
  default: deepseek-flash
```

**`main.py`**

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

**`root.fya`**

```yaml
description: "Orchestrator: delegates coding tasks to coder and review tasks to reviewer."
model_tag: default
tools:
  - subagent-invoke
subagents:
  - ./agents/coder
  - ./agents/reviewer
  - ./agents/auditor:
      visible: false
---
$system_prompt:
You are the task orchestrator. For development requests, dispatch coding to coder,
then send the complete code to reviewer, and summarize the review findings.
Do not write or review code yourself. The auditor is hidden from the model catalog,
but it remains callable from code.
```

**`agents/coder/agent.fya`**

```yaml
description: "Programmer: writes concise Python code from requirements."
model_tag: default
---
$system_prompt:
You are a programmer. Write concise, type-annotated Python code for each requirement.
Return only the code and one usage note. Do not add chatter.
```

**`agents/reviewer/agent.fya`**

```yaml
description: "Code reviewer: reviews code read-only and outputs a list of findings."
model_tag: default
---
$system_prompt:
You are a strict code reviewer. Review the code you receive and output one
"[severity] description" per line, ordered by severity. Output LGTM if there are
no findings. Do not modify the code.
```

**`agents/auditor/agent.fya`**

```yaml
description: "Auditor: internal compliance audit."
model_tag: default
---
$system_prompt:
You are an auditor. Upon receiving an instruction, reply only: "Audit channel activated."
```

**`demo_enabled.py`**

```python
import asyncio
from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")
    print("subagent binding table:")
    for alias, entry in root._subagent_entries.items():
        state = "in catalog (LLM-visible)" if entry.visible else "not in catalog (LLM-invisible)"
        print(f"  {alias}: visible={entry.visible} -> {state}")
    result = await root.invoke_subagent("auditor", prompt="Activate.")
    print(
        "programmatically invoking visible=False auditor -> "
        f"result={result.result!r} status={result.subagent_status}"
    )
    await runtime.shutdown()


asyncio.run(main())
```

The complete expected output of the visibility demonstration is:

```text
subagent binding table:
  coder: visible=True -> in catalog (LLM-visible)
  reviewer: visible=True -> in catalog (LLM-visible)
  auditor: visible=False -> not in catalog (LLM-invisible)
programmatically invoking visible=False auditor -> result='Audit channel activated.' status=completed
```

## Common misconceptions

1. **Writing catalog descriptions too generically**. Routing accuracy is
   directly affected by description quality; state the boundaries of the task
   types;
2. **Dispatching without context**. Workers have no shared memory (except named
   resume, discussed in Chapter 5-4); a self-sufficient task description is the
   baseline requirement for dispatch;
3. **The orchestrator doing the work itself**. When the orchestrator holds
   execution tools, the dispatch structure degrades into decoration.

## Exercises

1. Write three catalog entries for a "document processing team" (format check /
   content summary / translation), each including one sentence of "when to
   dispatch to me";
2. Review this dispatch package and fill in the missing information:
   `{"to": "translator", "task": "translate this"}`;
3. In this chapter's example `root.fya`, delete `visible: false` from
   `auditor`, rerun the conversation, and directly ask "have auditor run an
   audit"; observe how the routing result differs from before.

## Summary

1. The catalog is the input surface of routing: alias, responsibility
   description, visible parameters; description quality determines routing
   accuracy;
2. The dispatch package must be self-sufficient: a worker receives only
   explicitly carried information;
3. Orchestrator discipline: minimal tool table, summarize-only results,
   on-demand dispatch;
4. The catalog is rendered on the spot and is always consistent with the
   binding state.
