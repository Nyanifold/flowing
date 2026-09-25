# 1-9 · Plugins and Composables: A First Look

## Prerequisites

[1-4 Hooks Basics](1-4-hooks-basics.md) and [1-5 provide and inject](1-5-provide-inject.md).
This chapter **only uses** ready-made extensions — one integration call per
capability. Its full setup, skill text, prompts, inputs, and observable outputs
are included below.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| Plugin | Runtime-level capability pack: enabled via `runtime.install(...)`, it registers global capabilities for the Runtime such as tools / registries (this chapter uses SkillPlugin as the example) |
| Composable | Application-logic injection primarily targeting an Agent: call `use_xxx(agent, ...)` in `setup()`; the function may attach handlers, Agent attributes, or register state (this chapter uses `use_system_reminder` / `use_prompt_until`) |
| `use_skill` | Instance-level enablement function of the skills plugin (a module-level function taking the agent) — declares the `.fya` `skills:` list and injects the skill catalog |
| `skill-load` | Tool registered by the skills plugin: the LLM loads a skill by name, and the body enters the tree as an EVENT message |
| `use_retry` | Retry-policy Composable: backs off and retries when an LLM call hits rate limiting or transient failures (its observation hook point is `on_retry`) — it depends on real failures to trigger, so this chapter does not demonstrate it |

## Goals

Use ready-made plugins and Composables: know how to write the one line and
what the phenomenon looks like.

## Main text

### The division of labor in one sentence

**A plugin is a Runtime-level capability pack; a Composable usually targets an
Agent for assembly.** A plugin is enabled with `runtime.install(...)` in
`main()`, before the first `mount()` (registering capabilities for the whole
Runtime); a Composable is called as `use_xxx(self, ...)` in an Agent's
`setup()`. The function primarily targets that Agent, while its application-
layer behavior is determined by its implementation.

### Plugins (use only): the skills example

```python
# main.py
runtime.install(SkillPlugin())   # must come before the first mount (installing late has no effect on already-created agents)
```

```yaml
# root.fya header
tools:
  - skill-load   # install registers the tool body; declaring it makes the tool visible to the LLM (registration != visibility)
skills:
  - ./skills/polite.md   # declare the available skill
```

```python
# root.fya $script
from flowing.plugins.skills import use_skill

async def setup(self):
    use_skill(self)   # instance-level enablement: injects the skill catalog and registers the load entry
```

The agent gains the ability to load a skill by name: the catalog holds a list
of skills, the LLM loads one via `skill-load`, and the body enters the tree as
an EVENT message (visible in the main example, run 1). One practical prompt
cooperation: for load-type requests, call `skill-load` first and end the turn
without answering; answer only after the body arrives — the body is a separate
message, not a tool return value.

### Composables (use only): two reproducible examples

```python
from flowing import MessageKind, TextBlock
from flowing.composables import use_prompt_until, use_system_reminder

async def setup(self):
    use_system_reminder(self, contents=["[Reminder] Follow the current task instructions."])

    def _done(agent, turn) -> bool:
        for mid in reversed(turn.message_ids):
            msg = agent.chain.get(mid)
            if msg.kind is MessageKind.PROVIDER:
                text = "".join(
                    block.text for block in msg.content
                    if isinstance(block, TextBlock)
                )
                return "DONE" in text
        return True

    use_prompt_until(
        self,
        predicate=_done,
        message="Acceptance condition not met: include DONE in your final reply, then answer again.",
    )
```

- `use_system_reminder(agent, contents=[...])` injects a reminder message
  (kind=EVENT) at the start of every turn; templates are evaluated at
  injection time;
- `use_prompt_until(agent, predicate, message)` is an end-of-turn assertion —
  if `predicate(agent, turn)` is false, `message` is enqueued via `steer()`
  and the turn keeps running ("stop only when the final reply contains DONE"
  is implemented exactly this way).

One sentence on `use_retry`: it backs off and retries when an LLM call hits
rate limiting or transient failures; it depends on real failures to trigger,
which this chapter cannot control, so it is not demonstrated (a small
reproduction example using its observation channel `on_retry` is in 5-3).

## Out of scope

- Two-phase enablement, the declare hook point, idempotency, dependency declarations — 5-2;
- The full parameter set and the formula for writing your own Composable — 5-3;
- The mechanics of `use_retry` — 4-6 (error decision surface) and 5-3.

## Main example

**Run 1: skills plus two Composables on the same stage** (representative
message flow):

```console
$ uv run flowing repl .
(agent-main)>>> Greet me politely: good morning, and append DONE at the end of your answer.
[thinking] (reasoning trace omitted)
[tool_call] skill-load {"name": "polite"}
[tool:completed] skill-load ->
The skill text below is now available as a separate event message.
[steer] Acceptance condition not met: include DONE in your final reply, then answer again.
May your day be smooth, and may all things go well with you. Good morning, and thank you for your kind greeting.

DONE
(agent-main)>>> Continue: use the fixed sentence pattern you just learned to wish me a smooth afternoon, and append DONE at the end.
[thinking] (reasoning trace omitted)
May your day be smooth, and may all things go well with you. May your afternoon be smooth as well. Thank you.

DONE
(agent-main)>>> /messages
1  user      Greet me politely: good morning, and append DONE at the end of your answer.
2  event     [Reminder] Follow the current task instructions.
3  provider  The first response lacked DONE.
5  tool
6  provider  The skill text has been loaded and will arrive as a separate message.
7  event     Acceptance condition not met: include DONE in your final reply, then answer again.
4  event     Complete skill text: see the fully inlined Politeness skill below.
8  event     [Reminder] Follow the current task instructions.
9  provider  May your day be smooth, and may all things go well with you. Good morning, and thank you for your kind greeting. DONE
10  user      Continue: use the fixed sentence pattern you just learned to wish me a smooth afternoon, and append DONE at the end.
11  event     [Reminder] Follow the current task instructions.
12  provider  May your day be smooth, and may all things go well with you. May your afternoon be smooth as well. Thank you. DONE
```

Read this transcript (observe the phenomena only): after `skill-load` was
called, the skill body entered the tree as an `event` message (id 4); the
answer written after the body arrived (id 9) opens with the fixed sentence
pattern prescribed by the skill text shown below — "May your day be smooth,
and may all things go well with you."; every turn starts with an
`event` reminder message (`use_system_reminder`); the reply at id 6 carried no
DONE, so `use_prompt_until` steered the turn into a rerun at turn end (the
steer message is id 7). Note that the listing prints messages in chain order,
not id order: the EVENT message carries id 4 but sits between the steer
message id 7 and the reminder id 8, because the body was enqueued into the
tree only when it was delivered.

**Run 2: deliberately omitting DONE, and getting a steered rerun**:

```console
$ uv run flowing repl .
(agent-main)>>> Reply with exactly "OK" and nothing else.
[thinking] (reasoning trace omitted)
OK
[steer] Acceptance condition not met: include DONE in your final reply, then answer again.
OK
(agent-main)>>> /messages
1  user      Reply with exactly "OK" and nothing else.
2  event     [Reminder] Follow the current task instructions.
3  provider  OK
4  event     Acceptance condition not met: include DONE in your final reply, then answer again.
5  event     [Reminder] Follow the current task instructions.
```

In this representative run, the model repeats "OK" after the steer; other
model responses may differ. The stable behavior is that a reply without
`DONE` causes `use_prompt_until` to add a steer message at turn end. Enter
`/exit` to terminate the loop.

## Complete example materials

Create these relative files in a Flowing-enabled project and set
`DEEPSEEK_API_KEY` in the environment. The reminder is intentionally free of
machine paths. The response text is model-generated; the stable expected
behavior is that loading adds the skill body as an event message and a reply
without `DONE` causes a steer message.

`main.py`:

```python
from flowing import Runtime
from flowing.plugins.skills import SkillPlugin


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.install(SkillPlugin())
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
description: "Demo assistant for plugins and Composables."
model_tag: default
tools:
  - skill-load
skills:
  - ./skills/polite.md
---
$system_prompt:
You are a concise demo assistant. For a greeting request, first call
skill-load with name "polite" and end the turn without answering. After the
skill body arrives, follow its rules. Include DONE in every final reply.
---
$script:
from flowing import MessageKind, TextBlock
from flowing.composables import use_prompt_until, use_system_reminder
from flowing.plugins.skills import use_skill


async def setup(self):
    use_skill(self)
    use_system_reminder(
        self,
        contents=["[Reminder] Follow the current task instructions."],
    )

    def _done(agent, turn) -> bool:
        for mid in reversed(turn.message_ids):
            msg = agent.chain.get(mid)
            if msg.kind is MessageKind.PROVIDER:
                text = "".join(
                    block.text for block in msg.content
                    if isinstance(block, TextBlock)
                )
                return "DONE" in text
        return True

    use_prompt_until(
        self,
        predicate=_done,
        message="Acceptance condition not met: include DONE in your final reply, then answer again.",
    )
```

`skills/polite.md` (complete skill content):

```markdown
---
description: "Politeness skill: use a fixed greeting and honorific language in replies."
---
# Politeness rules

- Every greeting must open with: "May your day be smooth, and may all things go well with you."
- Address the user respectfully, using "please" and "thank you" where they sound natural.
- Keep the tone humble and avoid blunt imperatives.

## Example

Input: `Good morning.`

Response: `May your day be smooth, and may all things go well with you. Good morning, and thank you for your kind greeting.`
```

First input:

```text
Greet me politely: good morning, and append DONE at the end of your answer.
```

Second input after the first turn:

```text
Continue: use the fixed sentence pattern you just learned to wish me a smooth afternoon, and append DONE at the end.
```

Expected message flow: `skill-load` receives `{ "name": "polite" }`; its skill
body arrives as an `event` message; `use_system_reminder` adds an `event`
message at each turn start; and `use_prompt_until` adds a `steer` message if
the latest provider reply does not contain `DONE`. The model's exact greeting
wording is not deterministic. You can request `Reply with exactly "OK" and
nothing else.` to try the negative case, but the model may still follow its
system prompt and include `DONE`. Whenever the actual reply lacks `DONE`, a
steer event follows.

## Summary

1. A plugin is a Runtime-level capability pack (`install` before mount); a
   Composable injects application logic primarily targeting an Agent
   (`use_xxx(self, ...)` in setup);
2. A tool whose body a plugin registered must still be declared explicitly by
   the agent (registration != visibility; `skill-load` is the example);
3. `use_system_reminder` injects a reminder at the start of every turn, and
   `use_prompt_until` steers a rerun when its assertion fails — both phenomena
   can be observed reproducibly;
4. `use_retry` depends on real failures to trigger, so this chapter does not
   demonstrate it (mechanics in 4-6 / 5-3).
