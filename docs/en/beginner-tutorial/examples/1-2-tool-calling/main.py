"""Entry point of the builtin-tools demo project: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)   # recover the existing agent
    else:
        # fixed agent_id -> idempotent mounting: a second launch goes through recovery, "the same root is back"
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
