"""Entry point of the ReAct loop example project: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(strict: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # --strict done passes through launch to main(), then forwards to mount → setup()
    kwargs = {"strict": strict} if strict else {}
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
