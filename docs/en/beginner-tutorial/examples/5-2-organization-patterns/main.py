"""Multi-agent application assembly entry: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(cwd: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if cwd:
        # cwd is injected via the provide chain: each agent's system_prompt
        # consumes it as {{ cwd }} (supplied to every level's prompt
        # templates through the injection chain)
        runtime.provide("cwd", cwd)
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
