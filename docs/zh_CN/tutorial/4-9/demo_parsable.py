"""Parsable 求值体系演示（4-9，离线）：五形式 / PENDING 位置分流 / 渲染上下文 / resolved。

运行：uv run python demo_parsable.py
"""
import asyncio
import pathlib

from flowing import Parsable, launch
from flowing.errors import (MissingContextError, MissingFieldError,
                            MissingProvideError, ReservedAttributeError)


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    agent.user_name = "小明"

    print("== ① 五种形式判定（type 自动推断，构造时不求值）==")
    for label, p in [
        ("RAW      ", Parsable('$"含 {{ 花括号 }} 的原文"')),
        ("FILE_REF ", Parsable("$./notes/refund.md")),
        ("EXPRESSION", Parsable("{{ 40 + 2 }}")),
        ("TEMPLATE ", Parsable("你好 {{ user_name }}")),
        ("LITERAL  ", Parsable("普通文本")),
    ]:
        print(f"   {label} type={p.type:<11} → {p.resolve(agent)!r}")

    print("== ② 渲染上下文（实例属性摊平 + env / config / self 入口）==")
    import os
    os.environ["DEMO_VAR"] = "来自环境变量"
    print(f"   {{{{ env.DEMO_VAR }}}}    → {Parsable('{{ env.DEMO_VAR }}').resolve(agent)!r}")
    print(f"   {{{{ config.agent.timeout }}}} → {Parsable('{{ config.agent.timeout }}').resolve(agent)!r}")
    print(f"   {{{{ self.node_id }}}}  → {Parsable('{{ self.node_id }}').resolve(agent)!r}")

    print("== ③ {{ x.resolved }}：模板内引用另一个 Parsable 的渲染结果 ==")
    agent.refund_policy = Parsable("$./notes/refund.md").bind(agent)  # 显式绑定（类属性形态经描述符自动绑定）
    out = Parsable("政策：{{ refund_policy.resolved }}").resolve(agent)
    print(f"   {out!r}")
    raw = Parsable("政策：{{ refund_policy }}").resolve(agent)
    print(f"   对照（不写 .resolved 只显示模板原文）: {raw!r}")

    print("== ④ 保留名冲突 → ReservedAttributeError ==")
    agent.env = "占用了保留名"
    try:
        Parsable("{{ env.DEMO_VAR }}").resolve(agent)
    except ReservedAttributeError as exc:
        print(f"   {exc}")

    print("== ⑤ 未绑定 + 未命中 ==")
    try:
        Parsable("{{ anything }}").resolve()
    except MissingContextError:
        print("   未绑定无上下文 resolve → MissingContextError")
    try:
        agent.inject("no_such_key")
    except MissingProvideError:
        print("   inject 未命中 → MissingProvideError")

    print("== ⑥ PENDING 位置分流：字段位 _ = 必须兑现的承诺 ==")
    pathlib.Path("notes/pending-agent.fya").write_text(
        "description: _\nmodel_tag: default\n---\n$system_prompt:\n占位\n",
        encoding="utf-8")
    try:
        await runtime.create_agent("@/notes/pending-agent.fya")
    except MissingFieldError as exc:
        print(f"   description: _ 未兑现 → MissingFieldError: {exc}")

    await runtime.shutdown()


asyncio.run(main())
