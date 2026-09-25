"""Entry point of the hello subproject: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(user_name: str | None = None, resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)   # recover an existing agent
    else:
        # fixed agent_id -> idempotent mount: the second launch goes through recovery, "the same root is back"
        root = await runtime.mount("@/root.fya", agent_id="agent-main")
        if user_name is not None:
            # runtime assignment: the {{ user_name }} template is evaluated on the spot at the next context assembly
            root.user_name = user_name
    return runtime
