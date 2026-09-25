"""Runtime mechanics demo (4-8): @ context isolation / creation pipeline artifacts / snapshot / Resource.

Run: uv run python demo_runtime.py
"""
import asyncio

import flowing
from flowing import launch


class FakeDB:                     # stand-in for a host resource (demo placeholder for heavy resources such as connection pools)
    def __init__(self, dsn: str):
        self.dsn = dsn


async def main() -> None:
    # ① Two Runtimes in one process: @ contexts are isolated per Task and do not interfere
    print("== ① Two coexisting Runtimes and @ isolation ==")
    rt_a = await launch(".")                       # this project
    rt_b = await launch("nested/other-project")    # nested second subproject
    print(f"   A.project_root = {rt_a.project_root.name}"
          f" ({'nested directory' if 'nested' in str(rt_a.project_root) else 'outer project'})")
    print(f"   B.project_root = {rt_b.project_root.name}"
          f" ({'nested directory' if 'nested' in str(rt_b.project_root) else 'outer project'})")
    print(f"   B's @/root.fya resolves to B's own root: "
          f"{rt_b.resolve_path('@/root.fya') == rt_b.project_root / 'root.fya'}")

    # ② module-level resolve(): unavailable after launch returns (@ context has been reset)
    print("== ② Availability scope of module-level resolve() ==")
    try:
        flowing.resolve("@/providers.yaml")
        print("   unexpectedly available (violates the contract)")
    except RuntimeError as exc:
        print(f"   calling flowing.resolve() after launch returns → RuntimeError: {exc}")

    # ③ provide chain endpoint and Resource
    print("== ③ provide endpoint / Resource (out-of-tree direct reference) ==")
    rt_a.provide("app_name", "demo-a")
    rt_a.register_resource("db", FakeDB("sqlite:///demo.db"))
    root_a = await rt_a.get_agent("agent-main")
    print(f"   root inject('app_name') = {root_a.inject('app_name')!r} (chain endpoint = Runtime)")
    print(f"   get_resource('db').dsn = {root_a.get_resource('db').dsn!r}"
          f" (no injection chain; direct reference to the shared instance)")

    # ④ agent pool and lazy provider instantiation
    print("== ④ Pool registry / lazy providers ==")
    print(f"   pool entries: {sorted(rt_a._agent_pool)}")
    print(f"   provider candidates: {len(rt_a.provider_registry._candidates)} / "
          f"instantiated: {len(rt_a.provider_registry._instances)}"
          if hasattr(rt_a.provider_registry, '_instances') else
          f"   provider candidates: {sorted(rt_a.provider_registry._candidates)}")

    # ⑤ snapshot: two-level observation
    print("== ⑤ snapshot (pull channel) ==")
    snap = rt_a.snapshot()
    print(f"   runtime.snapshot: nodes={sorted(snap.nodes)} plugins={snap.plugins}")
    asnap = root_a.snapshot()
    print(f"   agent.snapshot: node_id={asnap.node_id} model={asnap.model.model}"
          f" queue={asnap.message_queue}"
          f" tree={type(asnap.messages).__name__} (message tree view)")
    await rt_a.shutdown()
    await rt_b.shutdown()


asyncio.run(main())
