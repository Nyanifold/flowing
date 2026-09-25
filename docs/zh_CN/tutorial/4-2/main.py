"""状态命名空间演示子项目入口（4-2）：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Runtime 级全局命名袋：跨 Agent 共享的持久化状态（挂持久化根 app.jsonl）
    app = runtime.register_state("app")
    app["boots"] = app.get("boots", 0) + 1   # 每次进程启动 +1（恢复语义：持久值优先）
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
