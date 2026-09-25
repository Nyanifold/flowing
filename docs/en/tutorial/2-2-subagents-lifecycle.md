# 2-2 · Subagent Creation and Resume

## Prerequisites

[2-1 Declarative Team Assembly](2-1-subagents-declarative.md) (type binding
and catalog). This chapter includes the complete parent and assistant
declarations, prompts, inputs, and representative output.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| creation-resume | The fixed two-part lifecycle of a subagent instance: `agent_type=` (or catalog invocation) creates a new instance; `resume=` (or the semantic name registered via `name=`) resumes an existing instance |
| `name=` | The semantic name registered for a new instance at invocation time (it enters the parent Agent's `child_ids` table); later resumes address the instance by this name |
| `child_ids` | The parent Agent's "semantic name → agent_id" translation table; the addressing basis for memory resume |
| `SubagentResult` | The structure returned by invocation: `result` (final output: structured finish dict or last reply text) and `subagent_status` (four-state turn outcome) are **separated** |

## Goals

Master the two operations on a subagent instance: **creation** and **resume**
— and verify that resume targets the same instance by checking that its
memory is still there.

## Main text

### Invocation in two phases

`invoke_subagent` runs in two phases:

1. **Preparation phase** (synchronous failures propagate): resolve the type,
   aggregate parameters (specified / alias mapping), run the
   `on_subagent_invoke` hook — a failure propagates directly to the caller,
   and no instance is created;
2. **Run phase**: instance creation (or resume) → run turns → construct
   `SubagentResult` → `on_subagent_returns` hook (before delivery) →
   delivery. The turn outcome of the run phase is the `TurnResult` four-state
   model from 1-1, passed through as `subagent_status`.

The LLM path is isomorphic: `subagent-invoke(agent_type=..., name=...,
prompt=...)` creates, and `subagent-invoke(resume=..., prompt=...)` resumes
(`agent_type` and `resume` are mutually exclusive, and one of the two is
required).

### Creation: name= registers the semantic name

Pass `name=` at creation time, and the instance id is registered in the
parent's `child_ids` — the semantic name lives only in the invoker's table;
the child instance does not carry a name itself. Afterwards,
`resume="memo"` addresses it by name: **the same instance, the same tree,
the same memory** (demonstrated in the main example).

### SubagentResult: content separated from outcome

```python
result = await parent.invoke_subagent("assistant", prompt="remember 42", name="memo")
result.result            # final output: structured finish dict > last reply text > None
result.subagent_status   # "completed" / "blocked" / "error" / "cancelled"
result.subagent_id       # instance id (recover the live instance on demand via runtime.get_agent)
```

When a subagent is cancelled, content already produced is still returned (in
`result`); the cancellation fact is visible only in `subagent_status` — the
content is not tainted by a "cancelled" label. This design follows the same
line as `TurnResult`.

## Out of scope

- Handler details of `on_subagent_invoke` / `on_subagent_returns` — see Chapter 4-6;
- The three forgetting levels (destroy / archive / physical deletion) and the agent pool — Chapter 6-3;
- Programmatic parallel invocation (`create_subagent` + `asyncio.gather`) — Chapter 2-4.

## Main example

Two questions in the REPL: first have the assistant remember a number (named
invocation), then follow up (resume). The second user input contains **the
number itself nowhere** — if 42 appears in the reply, it comes from the resumed
instance's context:

```console
$ uv run flowing repl .
(agent-main)>>> Have assistant remember the number 42.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "assistant", "name": "memo", "prompt": "Please remember this piece of information: the number is 42.\n\nAfter storing it, repeat the content back to me to confirm you have it memorized. State clearly: \"I will remember: the number 42.\""}
[tool:completed] subagent-invoke -> 
The assistant has stored the information and confirmed:

**"I will remember: the number 42."**
(agent-main)>>> Ask memo: what number did I ask you to remember earlier?
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"resume": "memo", "prompt": "Earlier I asked you to remember a number. What number was it? Please answer with the number you stored."}
[tool:completed] subagent-invoke -> 
I asked the assistant (memo) — here's its answer verbatim:

**"The number you asked me to remember was 42."**
(agent-main)>>> /exit
```

Reading this session: the first `subagent-invoke` registers the instance
with `name="memo"`; the second call with `resume="memo"` invokes **the same
instance** — the parameter difference between the two calls is directly
visible in the transcript. Its message tree still holds the first turn's
"remember 42", so it can repeat the number accurately. In the orchestrator
prompt, "do not create a new instance unless the user explicitly asks for
one" is routing discipline; memory continuity is guaranteed by the `resume`
mechanism, not by the model's own initiative.

## Complete example materials

Create these relative files in a Flowing-enabled project and set
`DEEPSEEK_API_KEY` in the environment. The exact assistant wording is
model-generated; the persistent instance name and the resume prompt are fixed.

`main.py`:

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

`providers.yaml`:

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

`models.yaml`:

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

`model-tags.yaml`:

```yaml
tags:
  default: deepseek-flash
```

`root.fya`:

```yaml
description: "Orchestrator: verifies subagent memory via named invocation and resume."
model_tag: default
tools:
  - subagent-invoke
subagents:
  - ./agents/assistant
---
$system_prompt:
The assistant subordinate has memory when the same instance is resumed.
When asked to remember something, invoke it as agent_type="assistant" with
name="memo" and ask it to repeat the information. For a follow-up, use
resume="memo" and relay the answer. Do not create a new instance unless the
user explicitly requests one.
```

`agents/assistant/agent.fya`:

```yaml
description: "Memory assistant: remembers information and repeats it when asked."
model_tag: default
---
$system_prompt:
When the user gives you information, confirm it in one sentence. When asked
about it, repeat the most recently remembered content accurately. Keep each
answer to one sentence.
```

Complete input and run command:

```text
Have assistant remember the number 42.
Ask memo: what number did I ask you to remember earlier?
/exit
```

```console
$ uv run flowing repl .
```

Expected tool interaction: the first call uses
`{"agent_type":"assistant","name":"memo"}` and a prompt that includes 42;
the second uses `{"resume":"memo"}` and asks for the earlier number without
including 42. The resumed assistant should answer 42. Exact natural-language
wrapping varies by model.

## Summary

1. Invocation in two phases: the preparation phase propagates failures synchronously; the run phase ends in one of the four states;
2. `name=` registers the semantic name (`child_ids`), and `resume=` continues the same instance — memory is continuous;
3. `SubagentResult` separates content from outcome: cancellation does not taint the already-produced `result`;
4. Memory resume is a mechanism guarantee (instance addressing), not the model's own discipline.
