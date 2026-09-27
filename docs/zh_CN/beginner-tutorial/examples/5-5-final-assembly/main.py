"""多智能体应用总装入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(cwd: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if cwd:
        # cwd 经 provide 链注入：各 Agent 的 system_prompt 以 {{ cwd }} 消费
        # （经注入链供给各级 Agent 的提示词模板）
        runtime.provide("cwd", cwd)
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
