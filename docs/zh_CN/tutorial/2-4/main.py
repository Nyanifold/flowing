"""多智能体应用总装入口（2-4）：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(cwd: str | None = None, resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if cwd:
        # cwd 经 provide 链注入：各 Agent 的 system_prompt 以 {{ cwd }} 消费
        # （刻意截止至 system_prompt——声明处注入是 4-4 的分歧面）
        runtime.provide("cwd", cwd)
    if resume is not None:
        await runtime.recover_agent(resume)   # 恢复入口：策略层保留，机制见 4-1
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
