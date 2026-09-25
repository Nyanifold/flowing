"""绑定层机制演示（4-4，离线）：同一工具的两种 LLM 视图 + 参数优先级 + 幻觉参数。

运行：uv run python demo_entry.py
"""
import asyncio

from flowing import launch
from flowing.tool import ToolCall


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")

    # ① 两种 LLM 视图（ToolEntry → llm_definition 现场生成）
    root_entry = root._tool_entries["pay"]
    cashier = await root.create_subagent("@/agents/cashier")
    plain_entry = cashier._tool_entries["make-payment"]
    d_root = root_entry.llm_definition(root.runtime, root)
    d_plain = plain_entry.llm_definition(root.runtime, cashier)
    print("① 同一工具的两个 LLM 视图：")
    print(f"  根 Agent  : name={d_root.name!r} params={sorted(d_root.params_schema)}")
    print(f"    amount 描述: {d_root.params_schema['amount'].get('description')!r}")
    print(f"    隐藏参数: user_id/currency 不在 schema（specified 移出 LLM 视图）")
    print(f"  cashier   : name={d_plain.name!r} params={sorted(d_plain.params_schema)}")

    # ② 参数聚合优先级：specified > LLM 传入 > schema 默认值
    resolved = root_entry.resolve(root, {"oid": "A-1", "amount": 99.0,
                                         "currency": "EUR"},   # LLM 想篡改币种
                                  d_plain.params_schema)        # 规范 schema
    print("② resolve 聚合（LLM 传了 currency=EUR、别名 oid：")
    print(f"    {resolved}")
    print(f"    → currency 被 specified 固定值 USD 覆盖（优先级铁律）")

    # ③ 幻觉参数：strict=True 的工具收到未定义参数 → LLM 视角校验失败
    bad = await root.tool_call(ToolCall(id="call-x", name="pay",
                                        args={"oid": "A-1", "amount": 1,
                                              "hack": "drop table"}))
    print(f"③ strict 幻觉参数: status={bad.status} error={bad.error!r}")

    # ④ strict=False：未知键原样放行，由 execute 自行处置
    ok = await root.tool_call(ToolCall(id="call-y", name="probe",
                                       args={"known": "k", "extra": 42}))
    print(f"④ strict=False 未定义参数: status={ok.status} output={ok.output}")

    # ⑤ finish 结构化交卷：output 覆写 → finish_output dict → SubagentResult.result
    scorer = await root.create_subagent("@/agents/scorer")
    r = await scorer.query("给『这段代码』打 90 分：它很整洁。")
    print(f"⑤ finish 交卷: final_text={r.final_text!r}")
    print(f"   last_result(dict)={scorer.last_result}")
    await scorer.destroy()
    await cashier.destroy()
    await runtime.shutdown()


asyncio.run(main())
