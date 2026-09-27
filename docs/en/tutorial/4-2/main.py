"""Entry point of the state-namespace demo subproject (4-2): launch imports this module and awaits main()."""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Runtime-global named bag: persistent state shared across Agents (backed by app.jsonl)
    app = runtime.register_state("app")
    app["boots"] = app.get("boots", 0) + 1   # +1 on every process launch (recovery semantics: persisted value wins)
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
