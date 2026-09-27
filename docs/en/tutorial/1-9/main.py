"""Entry point of the plugins-and-Composables demo subproject: launch imports this file and awaits main()."""

from flowing import Runtime
from flowing.plugins.skills import SkillPlugin


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Plugin enablement: install inside main(), before the first mount (mechanics covered in 5-2)
    runtime.install(SkillPlugin())
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
