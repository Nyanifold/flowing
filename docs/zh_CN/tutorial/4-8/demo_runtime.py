"""Runtime 机制演示（4-8）：@ 上下文隔离 / 创建管线产物 / snapshot / Resource。

运行：uv run python demo_runtime.py
"""
import asyncio

import flowing
from flowing import launch


class FakeDB:                     # 宿主资源替身（连接池等重资源的演示占位）
    def __init__(self, dsn: str):
        self.dsn = dsn


async def main() -> None:
    # ① 同进程双 Runtime：@ 上下文按 Task 隔离，互不串扰
    print("== ① 双 Runtime 共存与 @ 隔离 ==")
    rt_a = await launch(".")                       # 本工程
    rt_b = await launch("nested/other-project")    # 嵌套第二子项目
    print(f"   A.project_root = {rt_a.project_root.name}"
          f"（{'嵌套目录内' if 'nested' in str(rt_a.project_root) else '外层工程'}）")
    print(f"   B.project_root = {rt_b.project_root.name}"
          f"（{'嵌套目录内' if 'nested' in str(rt_b.project_root) else '外层工程'}）")
    print(f"   B 的 @/root.fya 解析到 B 自己的根："
          f"{rt_b.resolve_path('@/root.fya') == rt_b.project_root / 'root.fya'}")

    # ② 模块级 resolve()：launch 返回后不可用（@ 上下文已复位）
    print("== ② 模块级 resolve() 的可用域 ==")
    try:
        flowing.resolve("@/providers.yaml")
        print("   意外可用（与契约不符）")
    except RuntimeError as exc:
        print(f"   launch 返回后调用 flowing.resolve() → RuntimeError: {exc}")

    # ③ provide 链终点与 Resource
    print("== ③ provide 终点 / Resource（树外直引）==")
    rt_a.provide("app_name", "demo-a")
    rt_a.register_resource("db", FakeDB("sqlite:///demo.db"))
    root_a = await rt_a.get_agent("agent-main")
    print(f"   根 inject('app_name') = {root_a.inject('app_name')!r}（链终点 = Runtime）")
    print(f"   get_resource('db').dsn = {root_a.get_resource('db').dsn!r}"
          f"（不走注入链，直引共享实例）")

    # ④ agent 池与 provider 惰性加载
    print("== ④ 池注册表 / provider 懒加载 ==")
    print(f"   池条目: {sorted(rt_a._agent_pool)}")
    print(f"   provider 候选 {len(rt_a.provider_registry._candidates)} 个 / "
          f"已实例化 {len(rt_a.provider_registry._instances)} 个"
          if hasattr(rt_a.provider_registry, '_instances') else
          f"   provider 候选: {sorted(rt_a.provider_registry._candidates)}")

    # ⑤ snapshot：两级观测
    print("== ⑤ snapshot（拉取通道）==")
    snap = rt_a.snapshot()
    print(f"   runtime.snapshot: nodes={sorted(snap.nodes)} plugins={snap.plugins}")
    asnap = root_a.snapshot()
    print(f"   agent.snapshot: node_id={asnap.node_id} model={asnap.model.model}"
          f" queue={asnap.message_queue}"
          f" tree={type(asnap.messages).__name__}（消息树视图）")
    await rt_a.shutdown()
    await rt_b.shutdown()


asyncio.run(main())
