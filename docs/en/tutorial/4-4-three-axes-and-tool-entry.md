# 4-4 · Three-layer capability description and the binding layer

## Prerequisites

[0-2 Using builtin tools](0-2-use-builtin-tools.md) (declare-and-use),
[2-1 Declarative team assembly](2-1-subagents-declarative.md) (first sight
of SubagentEntry), [1-5 provide and inject](1-5-provide-inject.md)
(prerequisite for injection expressions). The inline materials below include
the deterministic binding checks and the provider-backed LLM-view transcript.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| three-layer capability description | The three dimensions shared by every capability (Tool / subagent / Skill): executable object / LLM-visible declaration / Agent-level binding |
| divergence surface | The difference in LLM-visible declarations produced by the binding layer: the same capability presents different views on different Agents |
| the override quadruplet | `override_params` (sparse patch) / `specified` (fixed values and injection expressions, invisible to the LLM) / `param_aliases` (rename from the LLM's point of view) / `visible` |
| parameter priority | `specified > LLM input > schema default` — an iron rule |
| per-yield hooks | `on_tool_yields`: fires once for each result the tool body yields (sync / receipt / chunk / final value); it complements the call-level `after_tool_call` |

## Goals

Build the overall frame of the three-layer capability description and fully
master the ToolEntry binding layer; the divergence surface accounts for the
different capability views that 0-2 / 1-5 / 2-1 each produced.

## Main text

### Three dimensions

| Dimension | Tool | Subagent | Skill |
|---|---|---|---|
| Executable object | `Tool` (`execute()`) | Agent class | Skill content |
| LLM-visible declaration | `ToolDefinition` | catalog XML entry | catalog XML entry |
| Agent-level binding | `ToolEntry` | `SubagentEntry` | `SkillEntry` (extension) |

**Why the binding layer exists**: the same capability should present
different LLM views on different Agents — overrides happen at the Agent-level
binding layer, without touching the global registry or the capability itself
(`clone_with_overrides` returns a new declaration object; the original is
never polluted).

### ToolEntry: the override quadruplet and priority

```yaml
# root.fya —— the customized view of make-payment on the root Agent
tools:
  - ./tools/make_payment.py as pay:   # as: the alias the LLM sees
      description: "Initiate a payment (confirmed orders only)"
      args:
        amount: {description: "Payment amount (yuan), capped at 50000"}  # override_params sparse patch
        currency: USD                     # specified: fixed value invisible to the LLM
        user_id: "{{ self.inject('user_id') }}"   # injection expression → specified
        order_id as oid: _                 # param_aliases: rename from the LLM's point of view (_ = empty patch)
```

- **Parameter priority**: `specified > LLM input > schema default` — specified is invisible to the LLM and cannot be overridden (demo ② verifies: the LLM passes `currency=EUR` and the fixed value `USD` wins);
- **Injection expressions**: a specified value can be `{{ self.inject('key') }}` — evaluated at call time with the **calling Agent** as the context, walking up the provide chain (this is the evaluation site for the declaration-site injection of 1-5);
- `_normalize` in three steps: LLM-view validation (strict; hallucinated parameters → an `error` result) → `resolve` aggregation (alias mapping + specified overrides) → schema default filling;
- `visible=False`: excluded from `Context.tools` but still callable programmatically (visibility separated from executability — same family as 2-1);
- `shortcut`: the short-circuit field of before_tool_call — the handler supplies the result and execution is skipped (cache-hit scenario); `after_tool_call` still fires.

### Comparison: SubagentEntry and SkillEntry

Subagent binding is **isomorphic** with ToolEntry (alias / description
override / specified / param_aliases / visible; only two differences: the
evaluation result of specified lands in the subagent's initialization
parameters, and the LLM-visible surface is catalog XML rather than
ToolDefinition). The `visible: false` and `description` override of 2-1
belong to this layer. SkillEntry is isomorphic as well, noted here in one
sentence (details in the skills plugin documentation).

### finish with structured submission

The `finish` skeleton has only `summary`; the Agent entry's `output:`
override **expands dynamic fields** (field name → JSON Schema definition, no
wrapper):

```yaml
  - finish:
      output:
        score: {type: integer, description: score 0-100}
        verdict: {type: string, description: one-sentence comment}
```

The subagent calls `finish(score=90, ...)` → the payload is written into
`turn.finish_output` → the turn's finalization puts it into
`Agent.last_result` (a structured dict) → `SubagentResult.result`.
`final_text` remains the model's normal text for that turn and coexists with
the structured payload (demo ⑤ verifies: payload in `last_result`, turn
text as usual).

### Error channel and validation layering

- **LLM-view validation** (`_normalize`, strict): hallucinated parameters → an `error` result (the LLM can self-correct);
- **Internal validation** (`Tool.__call__`, outside the try block): validates against the creation-time-compiled `_args_model` — configuration errors are programming errors; they go up the framework error channel directly and never become LLM-visible text;
- `strict=False`: undefined parameters pass through and execute handles them (must be declared via the explicit `definition` channel; demo ④ verifies).

## Out of scope

- The three background forms and the execution mechanics of media returns — 4-5;
- Handler details of `on_subagent_invoke` / `on_subagent_returns` — 4-6.

## Main example

**Demo 1: mechanics** (the first four checks are deterministic; check ⑤ calls
the configured model, so its generated text may vary):

```console
$ uv run python demo_entry.py
① Two LLM views of the same tool:
  root agent: name='pay' params=['amount', 'oid']
    amount description: 'Payment amount (yuan), capped at 50000'
    hidden params: user_id/currency not in schema (specified moves them out of the LLM view)
  cashier   : name='make-payment' params=['amount', 'currency', 'order_id', 'user_id']
② resolve aggregation (LLM passed currency=EUR and alias oid:
    {'order_id': 'A-1', 'amount': 99.0, 'currency': 'USD', 'user_id': 'u-10086'}
    → currency overridden by the specified fixed value USD (priority iron rule)
③ strict hallucinated param: status=error error="tool 'pay' received undefined parameters: ['hack']"
④ strict=False undefined param: status=completed output={'known': 'k', 'extra_seen': ['extra']}
⑤ finish submission: final_text="No code or content was included in the request — only an instruction to assign a score of 90. …" (the model's turn text)
   last_result(dict)={'summary': 'The request asked for a score of 90 on "this code," but no code or text was actually included. With nothing to inspect, no basis for judging cleanliness, correctness, or style exists, and assigning a score purely on the requester\'s assertion would be unjustified. Please resubmit with the code to be graded.', 'score': 0, 'verdict': 'Nothing was submitted for review, so no assessment of cleanliness or quality is possible.'}
```

**Demo 2: the LLM's view** (complete input and recorded output):

`repl_input.txt`:

```text
Please pay order A-1 with pay, amount 99 yuan.
/exit
```

Run `uv run flowing repl . < repl_input.txt`. This recorded transcript is
included in full; model-generated wording can vary on another run:

```text
$ uv run flowing repl . < repl_input.txt
(agent-main)>>>[thinking] (reasoning trace omitted)
[tool_call] pay {"oid": "A-1", "amount": 99}
[pay] execute args: order_id=A-1 amount=99 currency=USD user_id='u-10086' caller=agent-main
[tool:completed] pay -> 
Payment completed.

- **Order:** A-1
- **Amount charged:** 99
- **Currency:** USD (the tool returned USD, not yuan — flagging in case that's unexpected)
- **Status:** ✅ ok

If the currency mismatch matters for your order, let me know and we can look into it.
[thinking] (reasoning trace omitted)
Payment completed.

- **Order:** A-1
- **Amount charged:** 99
- **Currency:** USD (the tool returned USD, not yuan — flagging in case that's unexpected)
- **Status:** ✅ ok

If the currency mismatch matters for your order, let me know and we can look into it.
(agent-main)>>> 
```

The LLM has only ever seen `pay` (the alias) and the two parameters
`oid`/`amount` (the `[tool_call]` line is the evidence); `currency=USD` and
`user_id='u-10086'` only materialize at the execute layer — all key points
of the **divergence surface** live in this one line of actual arguments. The
model also flagged that the recorded currency (USD) may not match the yuan
the user mentioned — honest reporting of errors and results is the same
topic as 2-3.

### Complete runnable materials

Save each block below under its displayed filename. These are the complete
files needed by both demonstrations. Set `DEEPSEEK_API_KEY` in the
environment before running a model query; no credential value is included.
The cashier tool deliberately returns `USD`, exposing the currency mismatch.

`main.py`:

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
description: "Checkout orchestrator: pay is the customized view of make-payment."
model_tag: default
tools:
  - ./tools/probe.py
  - ./tools/make_payment.py as pay:
      description: "Initiate a payment (confirmed orders only)"
      args:
        amount: {description: "Payment amount (yuan), capped at 50000}
        currency: USD
        user_id: "{{ self.inject('user_id') }}"
        order_id as oid: _
subagents:
  - ./agents/cashier
---
$system_prompt:
You are the checkout. When the user confirms a payment, use the pay tool to
complete it and report the result truthfully.
---
$script:
async def setup(self):
    self.provide("user_id", "u-10086")
```

`tools/make_payment.py`:

```python
from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class PayArgs(BaseModel):
    order_id: str = Field(description="Order ID")
    amount: float = Field(ge=0.01, description="Payment amount")
    currency: str = Field(default="USD", description="Currency")
    user_id: str = Field(default="", description="Operator injected by host")


class MakePayment(ScriptTool):
    """Pay an order only after the user explicitly confirms the payment."""

    name = "make-payment"
    args_model = PayArgs

    async def execute(self, *, order_id: str, amount: float,
                      currency: str, user_id: str, caller: Agent) -> dict:
        print(f"[pay] execute args: order_id={order_id} amount={amount} "
              f"currency={currency} user_id={user_id!r} caller={caller.node_id}")
        return {"ok": True, "order_id": order_id, "amount": amount,
                "currency": currency, "user_id": user_id}
```

`tools/probe.py`:

```python
from flowing import ScriptTool
from flowing.tool import ToolDefinition


class ProbeTool(ScriptTool):
    """Pass undefined arguments through for the caller to inspect."""

    definition = ToolDefinition(
        name="probe",
        description="strict=False probe: undefined parameters pass through.",
        params_schema={"known": {"type": "string", "description": "Known parameter"}},
        strict=False,
    )

    async def execute(self, *, known: str, **kwargs) -> dict:
        return {"known": known, "extra_seen": sorted(kwargs)}
```

`agents/cashier/agent.fya`:

```yaml
description: "-plain cashier: the bare view of make-payment (control group)."
model_tag: default
tools:
  - "@/tools/make_payment.py"
---
$system_prompt:
You are the cashier. Initiate payments as the user requests.
```

`agents/scorer/agent.fya`:

```yaml
description: "Scorer: submit a structured result with finish."
model_tag: default
tools:
  - finish:
      output:
        score: {type: integer, description: "score 0-100"}
        verdict: {type: string, description: "one-sentence comment"}
---
$system_prompt:
You are the scorer. After receiving content, grade it and submit with finish:
summary holds the comment, score holds the numeric grade, and verdict holds a
one-sentence conclusion.
```

`demo_entry.py`:

```python
import asyncio

from flowing import launch
from flowing.tool import ToolCall


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")

    root_entry = root._tool_entries["pay"]
    cashier = await root.create_subagent("@/agents/cashier")
    plain_entry = cashier._tool_entries["make-payment"]
    d_root = root_entry.llm_definition(root.runtime, root)
    d_plain = plain_entry.llm_definition(root.runtime, cashier)
    print("① Two LLM views of the same tool:")
    print(f"  root agent: name={d_root.name!r} params={sorted(d_root.params_schema)}")
    print(f"    amount description: {d_root.params_schema['amount'].get('description')!r}")
    print("    hidden params: user_id/currency not in schema "
          "(specified moves them out of the LLM view)")
    print(f"  cashier   : name={d_plain.name!r} params={sorted(d_plain.params_schema)}")

    resolved = root_entry.resolve(
        root, {"oid": "A-1", "amount": 99.0, "currency": "EUR"},
        d_plain.params_schema)
    print("② resolve aggregation (LLM passed currency=EUR and alias oid:")
    print(f"    {resolved}")
    print("    → currency overridden by the specified fixed value USD "
          "(priority iron rule)")

    bad = await root.tool_call(ToolCall(
        id="call-x", name="pay",
        args={"oid": "A-1", "amount": 1, "hack": "drop table"}))
    print(f"③ strict hallucinated param: status={bad.status} error={bad.error!r}")

    ok = await root.tool_call(ToolCall(
        id="call-y", name="probe", args={"known": "k", "extra": 42}))
    print(f"④ strict=False undefined param: status={ok.status} output={ok.output}")

    scorer = await root.create_subagent("@/agents/scorer")
    result = await scorer.query("Give 'this code' a score of 90: it's very clean.")
    print(f"⑤ finish submission: final_text={result.final_text!r}")
    print(f"   last_result(dict)={scorer.last_result}")
    await scorer.destroy()
    await cashier.destroy()
    await runtime.shutdown()


asyncio.run(main())
```

For the LLM-view transcript, run `uv run flowing repl . < repl_input.txt`.
The complete `repl_input.txt` content and recorded transcript are shown above.

## Summary

1. Three-layer capability description: executable object / LLM-visible declaration / Agent-level binding — isomorphic across the three capability kinds;
2. The override quadruplet + the priority iron rule (specified > LLM > default); injection expressions are evaluated along the provide chain at call time;
3. finish's `output:` override expands dynamic fields; the structured payload lands in `last_result`;
4. Validation layering: the strict LLM-view gate vs internal validation (programming errors never reach the LLM surface).
