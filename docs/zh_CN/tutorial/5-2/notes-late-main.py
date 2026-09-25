"""迟装负例的入口（5-2 演示用）：mount 前不 install 插件。"""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing-late")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 故意不 install(SkillPlugin()) —— root.fya 的 setup 里 use_skill 将无注册表可注入
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
