"""复杂装配示例子项目入口（5-1）：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(user_id: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    kwargs = {"user_id": user_id} if user_id else {}
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
