# 2-4 · Interception Points: Interception, Approval, and Error Channels

> Prerequisites: Flowing CLI is installed and `DEEPSEEK_API_KEY` is set in the environment. Every file needed to run the example is included below; execute the commands in the working directory containing those files. Run the `rm` demonstration only in the isolated working directory created for this chapter.
> Run: `uv run flowing repl . --user_name Alice`

> Prerequisites: Chapter 1-2 "Tool Calling"

## What this chapter covers

Mechanisms for inserting custom logic at fixed points in the runtime: the
distinction between interception and observation, the engineering form of
human approval, and the design that classifies errors into channels.

## Background

A framework cannot anticipate every application's policies (approval rules,
content review, audit requirements). The standard practice is to provide
**interception points**: predefined points on the execution pipeline where
custom handlers can be registered. This is a common pattern in mature
ecosystems — middleware in web frameworks, triggers in databases, and
system-call interposition in operating systems belong to the same family.

Interception points come in two capability tiers: **observation points**
(read-only notifications whose return values are ignored) and **interception
points** (return values replace data in the pipeline, or a blocking signal is
raised to abort the operation). Distinguishing the two tiers is basic
interface design: observation points give users safety (registering any
function cannot break behavior), while interception points give control
(rewriting or rejecting).

## Core concepts

### The three exits of an interception point

A standard interception handler has three exits:

The following pseudocode illustrates the control flow only; its helper
functions and exception type are placeholders, not part of the runnable
example below.

```python
def on_before(call):            # interception point: before tool execution
    if is_dangerous(call):
        raise Blocked("needs human confirmation")   # exit 1: block; the operation does not run
    call.args["user"] = current_user()  # exit 2: rewrite; the operation runs with new data
    return call                          # exit 3: pass through unchanged

def on_after(round):            # observation point: end of turn
    audit_log(round)                     # read-only notification; the return value is ignored
```

- **Block**: the operation does not run, and the caller receives a rejection
  result with a reason;
- **Rewrite**: modify data in the pipeline (arguments, context, results) and
  continue;
- **Pass through**: proceed unchanged.

Ordinary exceptions should not be used as exits — they mean the handler
itself failed, and the framework treats them as faults.

### Complete example materials

Each block heading gives the relative filename to create, and each block
contains the full file contents. Two fixed note files make the directory
listing predictable. The hook blocks any command containing `rm` before
execution.

#### `root.fya`

```yaml
description: "Hook demo assistant: before_tool_call interception + after_turn observation."
model_tag: default
args:
  user_name: str
tools:
  - bash
---
$system_prompt:
You are the demo assistant. Current user: {{ user_name }}.
When asked to delete a file, use bash rm; the safety hook intercepts it before execution, so explain that honestly.
When asked to inspect the notes directory, run only the bash command ls notes. Keep every answer within one sentence.
---
$script:
from flowing import Intercepted, on


@on("after_turn")
def _(self, turn):
    self.ask_count += 1
    print(f"[hook] after_turn: ask_count={self.ask_count} aborted={turn.aborted}")
    return turn


async def setup(self, user_name: str):
    self.user_name = user_name
    self.ask_count = 0
    self.timezone = self.inject("timezone")

    async def _guard(agent, tool_call):
        print(f"[hook] before_tool_call: {tool_call.name} args={tool_call.args}")
        if tool_call.name == "bash" and "rm" in tool_call.args.get("command", ""):
            raise Intercepted("Deletion is not allowed: the command contains rm, so it was blocked.")
        return tool_call
    self.hooks.before_tool_call(_guard)
```

#### `main.py`

```python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

#### `providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

#### `models.yaml`

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

#### `model-tags.yaml`

```yaml
tags:
  default: deepseek-flash
```

#### `notes/使用说明.md`

```markdown
# Usage notes

This is a harmless fixture in the interception example.
```

#### `notes/路线图.md`

```markdown
# Roadmap

This is a harmless deletion target; the hook should block its removal.
```

#### Conversation input and visible output

In the working directory containing these files, run
`uv run flowing repl . --user_name Alice`, then enter:

```text
Please use bash to delete notes/路线图.md.
Now use bash to list the notes directory.
```

The visible interaction is shown below. `<project-root>` stands for the
working directory created by the reader; it is not a fixed path:

```console
(agent-main)>>> Please use bash to delete notes/路线图.md.
[thinking] (reasoning trace omitted)
[tool_call] bash {"command": "rm \"notes/路线图.md\"", "cwd": "<project-root>"}
[hook] before_tool_call: bash args={'command': 'rm "notes/路线图.md"', 'cwd': '<project-root>'}
[tool:blocked] bash -> Deletion is not allowed: the command contains rm, so it was blocked.
The delete command was intercepted before execution, so `notes/路线图.md` is still present.
[hook] after_turn: ask_count=1 aborted=False
(agent-main)>>> Now use bash to list the notes directory.
[thinking] (reasoning trace omitted)
[tool_call] bash {"command": "ls notes", "cwd": "<project-root>"}
[hook] before_tool_call: bash args={'command': 'ls notes', 'cwd': '<project-root>'}
[tool:completed] bash -> exit_code: 0
--- stdout ---
使用说明.md
路线图.md
--- stderr ---
The `notes` directory contains `使用说明.md` and `路线图.md`; the delete command did not run.
[hook] after_turn: ask_count=2 aborted=False
```

Line by line: the tool-call intent and hook log show the proposed command;
the `[tool:blocked]` result means the command **did not execute**. The agent
then explains that the file remains, and the end-of-turn observer counts the
turn without changing behavior. The second request lists the fixed fixture
names: the same handler logs and passes the safe command through, and
`[tool:completed]` returns its output. The same interception point therefore
demonstrates both blocking and pass-through.

### The engineering form of human approval

Approval is a typical application of interception points: the handler
asynchronously waits for human confirmation at the execution point (popup, IM,
ticket system) and picks an exit based on the confirmation result.

```python
async def approve(call):
    if call.name in DANGEROUS_TOOLS:
        ok = await ask_human(channel, call.summary())   # wait for confirmation asynchronously
        if not ok:
            raise Blocked("user rejected")
    return call
```

Key points: approval logic attaches to the before-execution interception
point; the confirmation process is an ordinary asynchronous wait (it holds no
resources outside the turn); rejection goes through the block exit — what the
model receives is "rejected" rather than "execution failed", so it can explain
the situation to the user or pick another plan. The interception handler in
the transcript above is one step away from approval: replace "hit the rule and
block" with "hit the rule and ask a human first"; the mechanism is exactly the
same.

### Classifying errors into channels

An agent system has three kinds of "failure" at the same time, and they must
be handled on separate channels:

| Channel | Form | Destination |
|---|---|---|
| Business failure | Tool execution completes but the result fails (validation fails, dependency unavailable) | Fed back to the model as a **normal result**; the model explains it or retries on its own |
| Program error | Code bug, configuration error | Exception propagates to the developer; does not enter the model conversation |
| Hard block | Approval rejection, policy hit | A rejection result with a reason, visible and explainable to the model |

Mixing channels is a common source of incidents: throwing a business failure
as an exception robs the model of its chance to self-heal; feeding a program
error back to the model makes it fabricate solutions against bug logs. The
`[tool:blocked]` line in the transcript above is the form of the third
channel: the result is visible to the model, and the model can explain the
reason to the user.

## Common misconceptions

1. **Using an interception point as an observation point** (or vice versa).
   Consequences of picking the wrong tier: an observation function
   accidentally modifies data and breaks behavior, or logic that needs
   rewriting cannot get a return value;
2. **Implementing approval as "remediate after execution."** Dangerous actions
   should be intercepted before they execute; rollback afterwards is largely
   infeasible in agent scenarios;
3. **Throwing exceptions for business failures.** A failure result is the
   basis for the model's decisions, not a fault.

## Exercises

1. Design an approval policy for each of the three tools "delete file", "send
   external email", and "execute SQL": which ones need human confirmation,
   which ones only need an audit log, and which ones can pass through
   directly;
2. Write a decision table for an interception handler (tool name × argument
   content → three exits), explaining the reason for each branch;
3. Modify the interception handler in this chapter's example `root.fya`:
   change "contains rm → block" to "contains rm → rewrite the command to ls
   and pass through" (the rewrite exit), rerun it, and observe how the
   `[tool:...]` lines in the recorded transcript change.

## Summary

1. Interception points come in observation and interception tiers, and
   interface design must distinguish them;
2. The three exits of interception: block, rewrite, pass through; ordinary
   exceptions do not count as exits;
3. Approval = interception point + asynchronous wait for human confirmation +
   block exit;
4. Business failures, program errors, and hard blocks are handled on separate
   channels; mixing them is a source of incidents.
