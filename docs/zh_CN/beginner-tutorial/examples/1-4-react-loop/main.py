"""ReAct 循环示例工程入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(strict: str | None = None, resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)   # 恢复既有 agent
    else:
        # --strict done 经 launch 透传 main()，再转发给 mount → setup()
        kwargs = {"strict": strict} if strict else {}
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
