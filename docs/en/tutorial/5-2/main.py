"""Entry point of the plugin-mechanics example project (5-2): launch imports this file and awaits main()."""

from flowing import Runtime
from flowing.plugins.cron import CronPlugin
from flowing.plugins.skills import SkillPlugin


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Phase one: install registers globally (tool bodies / provide values / config namespace) — must precede the first mount
    runtime.install(SkillPlugin(), CronPlugin())
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
