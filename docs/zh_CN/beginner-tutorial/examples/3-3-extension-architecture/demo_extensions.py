"""扩展架构演示：两阶段启用 / 迟装无效 / 依赖成环 / 策略注入与卸载。

运行：uv run python demo_extensions.py
"""
import asyncio
import sys

from flowing import Runtime, launch
from flowing.errors import DependencyError, MissingProvideError, ToolNotFoundError
from flowing.plugins import Plugin
from flowing.tool import ToolCall

sys.path.insert(0, "composables")
from rate_limit import remove_rate_limit, use_rate_limit


class _P1(Plugin):
    name = "p1"
    dependencies = ("p2",)

    def install(self, runtime):
        pass


class _P2(Plugin):
    name = "p2"
    dependencies = ("p1",)

    def install(self, runtime):
        pass


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ① 两阶段启用：阶段一 install 注册本体，阶段二 setup 里 use_skill 启用
    print("== ① 两阶段启用的产物检查 ==")
    print(f"   skill-load 在全局注册表: "
          f"{agent.get_tool('skill-load') is not None}")
    print(f"   prompt 块含 skills catalog: "
          f"{any(b.name == 'skills' for b in agent.prompt_blocks)}")

    # ② 迟装无效：mount 在前、install 在后 → 创建期失败
    print("== ② 迟装（install 迟于 mount）→ 创建期失败 ==")
    try:
        await launch(".", main_file="@/notes-late-main.py")
        print("   意外成功（与契约不符）")
    except (ToolNotFoundError, MissingProvideError) as exc:
        print(f"   迟装 launch 失败: {type(exc).__name__}"
              f"（插件注册的工具本体在阶段一；必须先于首个 mount）")

    # ③ 依赖声明：成环在 install 现场抛 DependencyError
    print("== ③ 依赖声明：成环 fail-fast ==")
    try:
        runtime.install(_P1(), _P2())
    except DependencyError:
        print("   p1 ↔ p2 互相依赖 → DependencyError")

    # ④ 策略注入：自写 use_rate_limit（窗口 60s、上限 3 次）
    print("== ④ use_rate_limit(max_calls=3) 注入 ==")
    limited: list[str] = []
    use_rate_limit(agent, max_calls=3, window_seconds=60.0)

    async def observe(agent_, tool_call):
        limited.append(tool_call.name)
        return tool_call
    agent.hooks.on_rate_limited(observe, by="demo")

    results = []
    for i in range(5):   # 连发 5 次：前 3 放行、后 2 阻断
        r = await agent.tool_call(ToolCall(id=f"c{i}", name="echo",
                                           args={"text": f"第{i}发"}))
        results.append(r.status)
    print(f"   5 次调用结果: {results}")
    print(f"   on_rate_limited 观测到 {len(limited)} 次超限（第 4、5 发）")

    # ⑤ 可卸载：by 归组整组移除后行为还原
    print("== ⑤ remove_by_owner 整组移除 ==")
    removed = remove_rate_limit(agent)
    r = await agent.tool_call(ToolCall(id="c9", name="echo", args={"text": "恢复"}))
    print(f"   移除 {removed} 条 handler；再调一次: status={r.status}")

    await runtime.shutdown()


asyncio.run(main())
