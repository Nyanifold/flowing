"""运维演示（6-3）：配置链 / 来源路径覆盖 / 池与归档 / 快照审计。

运行：uv run python demo_ops.py
"""
import asyncio
import os

from flowing import launch


async def main() -> None:
    # ① 配置链：框架默认(60) < 项目级 config.yaml(45) < set_config(90)
    print("== ① 配置链三层 ==")
    runtime = await launch(".")
    print(f"   项目级 @/config.yaml 覆盖框架默认: agent.timeout = "
          f"{runtime.get_config('agent.timeout')}（框架默认 60）")
    runtime.set_config("agent.timeout", 90)
    print(f"   set_config 覆盖层最优先: agent.timeout = "
          f"{runtime.get_config('agent.timeout')}")
    print(f"   FLOWING_CONFIG_HOME 用户级: "
          f"{os.environ.get('FLOWING_CONFIG_HOME', '~/.flowing（本机未设置）')}")

    # ② 模型来源路径优先级：set_providers 编程覆盖（mount 前调用）
    print("== ② 模型接入来源覆盖 ==")
    print(f"   默认 provider 候选: "
          f"{sorted(runtime.provider_registry._candidates)}")
    print("   优先级：FLOWING_PROVIDERS_PATH 环境变量 > 默认路径；"
          "set_providers 在 mount 前编程覆盖")
    print("   （本工程 main() 已 set_providers('@/providers.yaml')；"
          "改用 alt-providers.yaml 即得 deepseek-alt 条目——见篇内示例代码）")

    # ③ 智能体池与遗忘三档
    print("== ③ 池 / 归档（遗忘三档之二）==")
    root = await runtime.get_agent("agent-main")
    child = await root.create_subagent("@/agents/explore-agent" if False else
                                       "builtin::explore-agent", name="exp-1")
    pool = runtime.snapshot().agents if hasattr(runtime.snapshot(), "agents") else None
    print(f"   池名录（含子 Agent）: {sorted(runtime._agent_pool)}")
    await runtime.archive_agent(child.node_id)
    print(f"   archive_agent 后 get_agent → "
          f"{await runtime.get_agent(child.node_id)}（运行时遗忘，文件留档）")
    print(f"   destroy 档（丢实例留记录）→ archive 档（名录移除）；"
          f"物理删除框架不提供，应用层自做")

    # ④ 快照审计
    print("== ④ snapshot 审计 ==")
    snap = runtime.snapshot()
    print(f"   nodes={sorted(snap.nodes)}")
    print(f"   config_overrides={snap.config_overrides}")
    print("   崩溃演练（kill -9 → 重启恢复）见 4-1；"
          "日志落盘插件在 flowing._unstable.logging（格式不冻结，勿依赖）")
    await runtime.shutdown()


asyncio.run(main())
