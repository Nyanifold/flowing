"""Multi-agent application final assembly entry point (2-4): launch imports this file and awaits main()."""

from flowing import Runtime


async def main(cwd: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if cwd:
        # cwd is injected through the provide chain: each Agent's system_prompt
        # consumes it as {{ cwd }} (deliberately stopped at system_prompt —
        # declaration-site injection is the divergence surface covered in 4-4)
        runtime.provide("cwd", cwd)
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
