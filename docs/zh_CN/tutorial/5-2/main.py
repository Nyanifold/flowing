"""插件机制示例子项目入口（5-2）：launch 会 import 本文件并 await main()。"""

from flowing import Runtime
from flowing.plugins.cron import CronPlugin
from flowing.plugins.skills import SkillPlugin


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 阶段一：install 全局注册（工具本体 / provide 值 / 配置命名空间）——须在首个 mount 前
    runtime.install(SkillPlugin(), CronPlugin())
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
