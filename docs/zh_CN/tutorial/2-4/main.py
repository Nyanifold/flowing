"""多智能体应用总装入口（2-4）：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(cwd: str | None = None) -> Runtime:
    runtime = Runtime()
    if cwd:
        # cwd 经 provide 链注入：各 Agent 的 system_prompt 以 {{ cwd }} 消费
        # （刻意截止至 system_prompt——声明处注入是 4-4 的分歧面）
        runtime.provide("cwd", cwd)
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
