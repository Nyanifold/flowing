"""Final-assembly entry for the multi-agent application: launch imports this module and awaits main()."""

from flowing import Runtime


async def main(cwd: str | None = None, resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if cwd:
        # cwd is injected through the provide chain: each Agent's system_prompt
        # consumes it via {{ cwd }} (fed to every level of Agent prompt templates)
        runtime.provide("cwd", cwd)
    if resume is not None:
        await runtime.recover_agent(resume)   # recovery entry: fresh start or resume is the entry's strategy decision
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
