"""hello 子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(user_name: str | None = None) -> Runtime:
    runtime = Runtime()
    # 固定 agent_id → 幂等挂载：第二次启动走恢复，“同一个根回来了”
    root = await runtime.mount("@/root.fya", agent_id="agent-main")
    if user_name is not None:
        # 运行期赋值：模板 {{ user_name }} 在下次组装上下文时现场求值
        root.user_name = user_name
    return runtime
