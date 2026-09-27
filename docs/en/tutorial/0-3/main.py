"""Entry point of the multi-agent hello subproject: launch imports this file and awaits main()."""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # fixed agent_id -> idempotent mount: the second launch goes through recovery,
    # so "the same root comes back"
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
