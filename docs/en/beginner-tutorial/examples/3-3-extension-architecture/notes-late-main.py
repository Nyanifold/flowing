"""Entry point for the late-install negative example: no plugin install before mount."""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing-late")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Deliberately no install(SkillPlugin()) -- use_skill in root.fya's setup will find no registry to inject
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
