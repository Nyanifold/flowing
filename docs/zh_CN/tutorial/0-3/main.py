"""多智能体初识子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 固定 agent_id → 幂等挂载：第二次启动走恢复，“同一个根回来了”
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
