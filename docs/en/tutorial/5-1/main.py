"""Entry point of the complex assembly demo subproject (5-1): launch imports this file and awaits main()."""

from flowing import Runtime


async def main(user_id: str | None = None, resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        kwargs = {"user_id": user_id} if user_id else {}
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
