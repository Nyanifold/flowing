# Example: 5-3 Orchestrator Design

This example separates task coordination from implementation and review. The
orchestrator dispatches code-writing work to a coder, then sends the result to
a read-only reviewer. A third subagent is hidden from the model catalog but
remains callable through the Python API.

## Run

Save the inline contents using the relative filenames shown. Set
`DEEPSEEK_API_KEY` in the environment; the provider configuration contains
only a placeholder. Start the conversation from the project root:

~~~console
$ uv run flowing repl .
~~~

The visibility demo is a separate command:

~~~console
$ uv run python demo_enabled.py
~~~

## Runtime entry and model configuration

Save as `main.py`:

~~~python
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

## Orchestrator and subagent prompts

Save these declarations as `root.fya`,
`agents/coder/agent.fya`, `agents/reviewer/agent.fya`, and
`agents/auditor/agent.fya`:

~~~yaml
# root.fya
description: "Orchestrator: dispatches coding tasks to coder and review tasks to reviewer."
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
You are the task orchestrator.
For development requests: first call subagent-invoke to dispatch the task to coder
for code writing, then dispatch the delivered code to reviewer for review, and
finally summarize the review findings to the user.
Do not write or review code yourself.

# agents/coder/agent.fya
description: "Programmer: writes concise Python code from requirements."
model_tag: default
---
$system_prompt:
You are a programmer. For each requirement, write concise Python code with type
annotations, output only the code itself plus one line of usage notes, and do not
chat.

# agents/reviewer/agent.fya
description: "Code reviewer: reviews code read-only and outputs a list of findings."
model_tag: default
---
$system_prompt:
You are a strict code reviewer. Review the code you receive and output a list of
findings: one "[severity] description" per line; output "LGTM" when there are no
findings. Do not modify the code.

# agents/auditor/agent.fya
description: "Auditor: internal compliance audit (invisible to the LLM by default)."
model_tag: default
---
$system_prompt:
You are an auditor. Upon receiving an instruction, reply only with "Audit channel activated."
~~~

## Conversation input and visible result

Input:

~~~text
I need a Python function for the nth Fibonacci number. Have coder write it, reject negative n with ValueError, then have reviewer review it and summarize the findings.
/exit
~~~

The coder's implementation:

~~~python
def fibonacci(n: int) -> int:
    """Return the nth Fibonacci number, with F(0)=0 and F(1)=1."""
    if n < 0:
        raise ValueError("n must be non-negative")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


if __name__ == "__main__":
    print(fibonacci(10))  # 55
~~~

Visible interaction excerpt:

~~~text
[thinking] (reasoning trace omitted)
[tool_call] coder: write the nth Fibonacci function and reject negative n.
[tool result] fibonacci(10) returns 55.
[tool_call] reviewer: review the implementation for correctness and edge cases.
[tool result] The iteration is correct for non-negative integers; negative values raise ValueError.
~~~

The final response reports that the iterative implementation is correct for
the stated domain. Runtime type validation and an upper bound for very large
inputs remain possible improvements; Python integers do not overflow.

## Visibility and executability demo

Save as `demo_enabled.py`:

~~~python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")
    print("subagent binding table:")
    for alias, entry in root._subagent_entries.items():
        visibility = "in catalog (LLM-visible)" if entry.visible else "not in catalog (LLM-invisible)"
        print(f"  {alias}: visible={entry.visible} -> {visibility}")
    result = await root.invoke_subagent("auditor", prompt="Activate.")
    print(f"programmatically invoking hidden auditor -> "
          f"result={result.result!r} status={result.subagent_status}")
    await runtime.shutdown()


asyncio.run(main())
~~~

Expected output:

~~~text
subagent binding table:
  coder: visible=True -> in catalog (LLM-visible)
  reviewer: visible=True -> in catalog (LLM-visible)
  auditor: visible=False -> not in catalog (LLM-invisible)
programmatically invoking hidden auditor -> result='Audit channel activated.' status=completed
~~~
