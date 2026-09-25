"""Binding-layer mechanics demo (4-4, offline): two LLM views of the same tool + parameter priority + hallucinated parameters.

Run: uv run python demo_entry.py
"""
import asyncio

from flowing import launch
from flowing.tool import ToolCall


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")

    # ① two LLM views (ToolEntry → llm_definition generated on the spot)
    root_entry = root._tool_entries["pay"]
    cashier = await root.create_subagent("@/agents/cashier")
    plain_entry = cashier._tool_entries["make-payment"]
    d_root = root_entry.llm_definition(root.runtime, root)
    d_plain = plain_entry.llm_definition(root.runtime, cashier)
    print("① Two LLM views of the same tool:")
    print(f"  root agent: name={d_root.name!r} params={sorted(d_root.params_schema)}")
    print(f"    amount description: {d_root.params_schema['amount'].get('description')!r}")
    print(f"    hidden params: user_id/currency not in schema (specified moves them out of the LLM view)")
    print(f"  cashier   : name={d_plain.name!r} params={sorted(d_plain.params_schema)}")

    # ② argument aggregation priority: specified > LLM input > schema default
    resolved = root_entry.resolve(root, {"oid": "A-1", "amount": 99.0,
                                         "currency": "EUR"},   # the LLM tries to tamper with the currency
                                  d_plain.params_schema)        # canonical schema
    print("② resolve aggregation (LLM passed currency=EUR and alias oid:")
    print(f"    {resolved}")
    print(f"    → currency overridden by the specified fixed value USD (priority iron rule)")

    # ③ hallucinated parameter: a strict=True tool receives an undefined parameter → LLM-view validation fails
    bad = await root.tool_call(ToolCall(id="call-x", name="pay",
                                        args={"oid": "A-1", "amount": 1,
                                              "hack": "drop table"}))
    print(f"③ strict hallucinated param: status={bad.status} error={bad.error!r}")

    # ④ strict=False: unknown keys pass through verbatim and execute handles them
    ok = await root.tool_call(ToolCall(id="call-y", name="probe",
                                       args={"known": "k", "extra": 42}))
    print(f"④ strict=False undefined param: status={ok.status} output={ok.output}")

    # ⑤ finish structured submission: output override → finish_output dict → SubagentResult.result
    scorer = await root.create_subagent("@/agents/scorer")
    r = await scorer.query("Give 'this code' a score of 90: it's very clean.")
    print(f"⑤ finish submission: final_text={r.final_text!r}")
    print(f"   last_result(dict)={scorer.last_result}")
    await scorer.destroy()
    await cashier.destroy()
    await runtime.shutdown()


asyncio.run(main())
