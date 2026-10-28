from flowing import Runtime


async def main(name: str) -> Runtime:
    runtime = Runtime()
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id=f"agent-{name.lower()}", name=name)
    return runtime
