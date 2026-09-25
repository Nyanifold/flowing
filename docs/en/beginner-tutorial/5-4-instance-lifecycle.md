# 5-4 · Instance Lifecycle: Naming, Resume, and Content/State Separation

> Self-contained example: the complete runtime configuration, orchestrator and memory-agent definitions, input, and representative output appear below. Create the relative files, then run `uv run flowing repl .` from the current directory.
> The conversation example requires the `DEEPSEEK_API_KEY` environment variable. Do not put a real credential in a configuration file.

> Prerequisites: Chapter 5-3 "Orchestrator Design"

## What this chapter covers

The lifecycle of worker instances: the distinction between type binding and
instantiation, naming and named resume (continuous memory), and the separation
of returned content from execution state.

## Background

An orchestrator's catalog declares **types** (which workers may be used);
instances are created only at dispatch time. This distinction replays the
contrast between a class reference and object construction in ordinary OOP:
the declaration phase defines the set of possibilities, and the construction
moment decides the concrete object.

## Core concepts

### Type binding vs. instantiation

```python
team.declare("translator")          # type binding: an option appears in the catalog
worker = team.invoke("translator")  # instantiation: only now is the worker actually created
```

This is conceptual pseudocode showing the two phases, not a standalone runnable program. The complete Flowing configuration and invocation protocol appear below.

The rationale for the two phases: the catalog cares about "who is available",
while an instance cares about "who does the work this time". Mixing the two
makes availability and existence unable to vary independently — disabling a
worker type must not destroy running instances, and named resume must not
depend on the catalog's current state (Chapter 5-3's `auditor` demo shows the
"still invocable from code after being disabled" side).

### Naming and named resume

Each instantiation can register a semantic name; later requests address
**the same instance** by name — its history, state, and memory all continue.
This is the foundation of multi-turn collaboration: the user expects "the
assistant from last time still remembers me"; technically, that expectation
is "instances are addressable".

```python
worker = team.invoke("translator", name="cn")   # create and register
team.invoke("cn", prompt="Continue the previous translation")  # resume the same instance by name
```

Create the files listed later in this chapter and run `uv run flowing repl .`. First invoke the assistant by name to memorize 42; then ask a follow-up that does **not** contain the number itself. Enter these lines in the same session:

```console
Have the assistant memorize the number 42.
[tool_call] subagent-invoke {"agent_type": "assistant", "name": "memo", "prompt": "Please memorize the following: the number is 42. Repeat it back to confirm you have memorized it."}
[tool:completed] subagent-invoke -> 
The assistant has memorized it. Its response:

> Got it—I've memorized that the number is 42.
[thinking] (reasoning trace omitted)
Ask memo: what number did I ask you to remember earlier?
[tool_call] subagent-invoke {"resume": "memo", "prompt": "What number did I ask you to remember earlier?"}
[tool:completed] subagent-invoke -> 
The assistant replied:

> You asked me to remember the number 42.
/exit
```

Line by line: the first dispatch uses `name="memo"` — it creates the
instance and registers the semantic name; the second dispatch uses
`resume="memo"` — it addresses the same instance by name. The parameter
difference between the two calls is directly visible in the transcript. The
follow-up asks only "What number did I ask you to remember earlier?", and the
answer "You asked me to remember the number 42." cannot come from this turn's
input; it can only come from the continued history of the same instance.
Memory continuity is not a prompt trick but an addressing capability: on
named resume, the worker sees its own complete prior history, as if the
conversation had never paused.

### Content / state separation

A worker's return value has two fields:

```python
result = team.invoke("translator", prompt="…")
result.content      # produced content: the final reply or structured data
result.status       # execution state: completed / failed / cancelled / blocked
```

The rationale for the separation: a cancelled worker may already have
produced most of its content — the content is valid, and the cancellation
fact is also valid. Stuffing both facts into one field either pollutes the
content (appending a "cancelled" marker) or loses the state. The caller
handles the two fields separately: use the content as-is, and let the state
decide whether a retry or a fallback is needed.

### The forgetting hierarchy

Instances do not need to exist forever. Forgetting is designed in layers:

| Level | Effect | Applies when |
|---|---|---|
| Stop instance | Runtime resources released; records kept, resumable by name | Idle for a phase |
| Archive | Removed from the registry; files kept as records, no longer addressable | Confirmed no longer needed |
| Physical deletion | Records erased | Compliance requirements; the framework usually does not provide this — it is implemented at the application layer |

The runtime stores one record area per instance under `.flowing/`. These records survive process exit and can be found by name on the next launch. To check cross-process resume, close the REPL and run the same command again from the same practice directory; verify that memo still answers 42.

### Complete example materials

These files form the complete named-resume example. Create them at the relative filenames shown; the input and representative conversation output are given above.

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


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

**`root.fya`**

```yaml
description: "Orchestrator: verifies subagent memory through named invocation and resume."
model_tag: default
tools:
  - subagent-invoke
subagents:
  - ./agents/assistant
---
$system_prompt:
You are the orchestrator. When the user asks the assistant to remember something,
call subagent-invoke with agent_type=assistant and name=memo, and ask it to repeat
the information to confirm. When the user follows up, resume with resume=memo and
relay the answer verbatim. Do not create a new instance unless the user asks.
```

**`agents/assistant/agent.fya`**

```yaml
description: "Memory assistant: remembers information the user provides and repeats it when asked."
model_tag: default
---
$system_prompt:
You are a memory assistant. When the user gives you information, confirm it in one
sentence by restating what you remembered. When asked, accurately repeat the most
recent information. Keep each answer to one sentence.
```

The first call creates an instance named `memo`; the second call uses `resume="memo"` to address that same instance. To check resume across processes, restart the REPL in the same practice directory and ask memo again.

## Common misconceptions

1. **Creating a new instance for every dispatch.** Fine for one-shot
   requests; multi-turn collaboration must use named resume, otherwise
   "memory" does not exist;
2. **Cancellation means discarding the output.** Content and state are
   handled in separate fields; a mostly completed translation is still
   usable;
3. **Instances are never released.** Forgetting is layered; long-idle
   instances should be stopped — recoverability is kept without holding
   resources.

## Exercises

1. Design the instance strategy for a "translation project": which tasks
   create a fresh instance each time, which resume by name, and how semantic
   names should be chosen to support per-project retrieval;
2. Write a handling branch for each of `content` and `status`: how is the
   content delivered, and what follow-up action does each of the four states
   trigger;
3. In this chapter's example, use "Continue the conversation" (run the same
   command again) to restart the process, then ask memo another follow-up:
   verify that named resume still works across processes, and explain which
   persistence layer supports it.

## Summary

1. Type binding defines the set of possibilities; instantiation decides the
   concrete object; the two phases evolve independently;
2. Naming makes instances addressable; named resume is the technical
   foundation of memory continuity;
3. Return values carry content and execution state in separate fields, and
   each is consumed separately;
4. Forgetting is layered: stop (recoverable) → archive (records kept) →
   physical deletion (application layer).
