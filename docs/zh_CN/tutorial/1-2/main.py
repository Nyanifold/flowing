"""内置工具演示子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime()
    # 固定 agent_id → 幂等挂载：第二次启动走恢复，“同一个根回来了”
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
