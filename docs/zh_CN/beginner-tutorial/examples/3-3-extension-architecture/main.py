"""扩展示例工程入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime
from flowing.plugins.skills import SkillPlugin


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 阶段一：install 做全局注册（工具本体 / 注册表）——须在首个 mount 之前
    runtime.install(SkillPlugin())
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
