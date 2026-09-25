"""插件机制演示（5-2）：两阶段启用 / 依赖声明 / 迟装无效 / cron 真机触发。

运行：uv run python demo_plugins.py   （cron 部分最多等待约 70 秒）
"""
import asyncio

from flowing import Runtime, launch
from flowing.errors import (DependencyError, MissingProvideError,
                            ToolNotFoundError)
from flowing.message import MessageKind
from flowing.plugins import Plugin
from flowing.plugins.cron import schedule


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

    # ① 依赖声明：成环抛 DependencyError（install 现场即报错）
    print("== ① 依赖声明：成环 fail-fast ==")
    try:
        runtime.install(_P1(), _P2())
    except DependencyError:
        print("   p1 ↔ p2 互相依赖 → DependencyError")

    # ② 迟装无效：mount 在前、install 在后 → 创建期 use_skill 无注册表可注入
    print("== ② 迟装（install 迟于 mount）→ 创建期失败 ==")
    try:
        await launch(".", main_file="@/notes-late-main.py")
        print("   意外成功（与契约不符）")
    except (ToolNotFoundError, MissingProvideError) as exc:
        print(f"   迟装 launch 失败: {type(exc).__name__}"
              f"（插件注册的工具本体/注册表在阶段一；必须先于首个 mount）")

    # ③ cron 真机触发：编程式登记每分钟任务，等 EVENT 到点投递
    print("== ③ cron 到点触发（编程式登记，最多等 70s）==")
    fired: list[str] = []

    async def on_fire(agent_, ctx):
        fired.append(ctx.content)
        return ctx
    agent.hooks.on_cron_trigger(on_fire, by="demo")

    schedule(agent, "*/1 * * * *", "【提醒】该起来活动一下了。")   # 模块级同步 API
    print("   已登记每分钟任务，等待到点…")
    for _ in range(140):
        await asyncio.sleep(0.5)
        if fired:
            break
    for _ in range(40):   # EVENT 在钩子之后入队并随回合挂树，稍等它落地
        await asyncio.sleep(0.5)
        events = [m for m in agent._messages.values()
                  if m.kind is MessageKind.EVENT and m.source in ("cron", "scheduled_task")]
        if events:
            break
    print(f"   on_cron_trigger 触发 {len(fired)} 次；内容={fired!r}")
    print(f"   EVENT 消息进树 {len(events)} 条（投递 source：'cron' 或自定义）")

    # ④ 两阶段证据：install 注册的本体在注册表、未声明则 LLM 不可见
    print("== ④ 阶段一产物检查 ==")
    print(f"   skill-load 在全局注册表: "
          f"{agent.get_tool('skill-load') is not None}")
    print(f"   prompt 块含 skills catalog: "
          f"{any(b.name == 'skills' for b in agent.prompt_blocks)}")
    await runtime.shutdown()


asyncio.run(main())
