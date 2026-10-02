"""插件与 Composable 初识子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime
from flowing.plugins.skills import SkillPlugin


async def main() -> Runtime:
    runtime = Runtime()
    # 插件启用：在 main() 里、首个 mount 之前 install（原理见 5-2）
    runtime.install(SkillPlugin())
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
