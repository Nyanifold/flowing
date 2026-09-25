"""模型选择演示（3-4，离线）：两跳解析回顾 / 运行期换模型 / 子 Agent 不同标签。

运行：uv run python demo_model.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")

    # ① 两跳解析：model_tag → 标签映射 → 模型条目
    print(f"① 根 Agent：model_tag={root.model_tag!r} → model={root.model.model!r}")
    print(f"   provider 条目绑定：{root.model.provider!r}")

    # ② 运行期换模型（0-1 伏笔回收）：改 model_tag 即重新解析
    root.model_tag = "chat"
    print(f"② 运行期 root.model_tag = 'chat' → model={root.model.model!r}")

    # ③ 声明层差异：子 Agent 在 .fya 里指定 model_tag
    child = await root.create_subagent("@/agents/greeter")
    print(f"③ 子 Agent greeter：声明 model_tag={child.model_tag!r} "
          f"→ model={child.model.model!r}")
    await child.destroy()
    await runtime.shutdown()


asyncio.run(main())
