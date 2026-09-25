"""hello 子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)   # 恢复既有 agent（含休眠记录）
    else:
        # 固定 agent_id → 幂等挂载：第二次启动走恢复，「同一个根回来了」
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
