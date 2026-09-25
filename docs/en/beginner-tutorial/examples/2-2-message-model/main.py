"""Entry point of the builtin-tools demo subproject: launch imports this file
and awaits main()."""

from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)   # Recover an existing agent
    else:
        # Fixed agent_id → idempotent mount: the second startup goes through
        # recovery — "the same root is back"
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
