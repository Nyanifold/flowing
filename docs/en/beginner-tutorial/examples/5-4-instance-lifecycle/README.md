# Example: 5-4 Instance Lifecycle

This example demonstrates named subagent instances. The orchestrator creates
an assistant named `memo`, asks it to remember a value, and resumes that same
instance for a follow-up question.

## Run

Save the inline contents under the relative filenames shown. Set
`DEEPSEEK_API_KEY` in the environment; the configuration contains only a
placeholder. From the project root, run:

~~~console
$ uv run flowing repl .
~~~

## Runtime entry and model configuration

Save as `main.py`:

~~~python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        kwargs: dict = {}
        if user_name is not None:
            kwargs["user_name"] = user_name
        if locale != "zh":
            kwargs["locale"] = locale
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
~~~

~~~yaml
# providers.yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"

# models.yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash

# model-tags.yaml
tags:
  default: deepseek-flash
~~~

## Agent prompts

Save the following declarations as `root.fya` and
`agents/assistant/agent.fya`:

~~~yaml
# root.fya
description: "Orchestrator: verifies subagent memory via named invocation and resume."
model_tag: default
tools:
  - subagent-invoke
subagents:
  - ./agents/assistant
---
$system_prompt:
You are the orchestrator. The assistant subagent has memory: when you
resume the same instance, it remembers the earlier conversation.
- When the user asks to "have the assistant memorize something": call
  subagent-invoke with agent_type "assistant", name "memo", and a prompt that
  states clearly what to memorize; ask it to repeat the content back to confirm.
- When the user follows up with the assistant: use resume="memo" to resume the
  same instance and relay its answer verbatim; do not create a new instance
  unless the user explicitly asks for one.

# agents/assistant/agent.fya
description: "Memory assistant: memorizes what the user tells it and repeats it back when asked."
model_tag: default
---
$system_prompt:
You are a memory assistant. When the user tells you a piece of information,
confirm it with a one-sentence restatement of what you remembered; when the
user asks about it, accurately repeat the most recently memorized content.
Keep your answer to one sentence.
~~~

## Input and visible tool interaction

Input:

~~~text
Have the assistant memorize the number 42.
Ask memo: what number did I ask you to remember earlier?
/exit
~~~

Visible interaction excerpt:

~~~text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"assistant","name":"memo","prompt":"Please memorize the following: the number is 42. Repeat it back to confirm you have memorized it."}
[tool result] Got it—I've memorized that the number is 42.
[tool_call] subagent-invoke {"resume":"memo","prompt":"What number did I ask you to remember earlier?"}
[tool result] You asked me to remember the number 42.
~~~

The orchestrator first confirms the stored value, then resumes the named
instance and relays its answer. Model wording may vary, but both responses
should identify 42.
