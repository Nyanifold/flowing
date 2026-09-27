"""错误与控制演示（4-6）：限流重试决策 + 取消语义全集。

① FlakyProvider：前两次调用抛 RateLimitedError（可重试类），第三次放行真实调用；
  use_retry 是挂在 on_provider_error 上的策略——机制只负责分发与 can_continue 字段。
② before_cancel 的 Intercepted 阻止取消；abort_turn 只终止当前回合。
运行：uv run python demo_control.py
"""
import asyncio

from flowing import launch
from flowing.composables import use_retry
from flowing.errors import Intercepted, RateLimitedError
from flowing.message import TextBlock, ToolCallBlock
from flowing.model import ModelConfig
from flowing.providers import Provider, register_provider
from flowing.providers.deepseek import DeepSeekProvider


@register_provider
class FlakyProvider(Provider):
    """测试 adapter：前两次 generate 抛 RateLimitedError，之后委托真实 DeepSeek。"""

    name = "flaky"
    calls = 0

    async def generate(self, context, model):
        type(self).calls += 1
        if type(self).calls <= 2:
            raise RateLimitedError(retry_after=0)
        real = DeepSeekProvider(self.config)
        response = await real.generate(context, model)
        # 测试 adapter 的归一：仅思考块（无正文/无工具调用）的响应补占位文本块——
        # 空文本会在流式合成增量时被累积器丢弃（delta.text="" 不归位），
        # 下一轮请求将因“assistant 消息既无 content 也无 tool_calls”被
        # DeepSeek 拒绝（框架级空正文处理见 PROGRESS“编写期发现”）。
        msg = response.message
        if msg is not None and not any(isinstance(b, ToolCallBlock)
                                       for b in msg.content) and not any(
                isinstance(b, TextBlock) and b.text.strip()
                for b in msg.content):
            msg.content.append(TextBlock("（仅思考过程，无正文）"))
        return response


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    agent.model = ModelConfig(model="deepseek-v4-flash", provider="flaky")

    # ① 限流重试：机制（on_provider_error 分发）+ 策略（use_retry 决策）
    retries: list[dict] = []
    use_retry(agent, max_retries=3, base_delay=0.1)

    async def observe(agent_, snap):   # on_retry 派发快照 dict（纯观测：崩不影响重试）
        retries.append({"attempt": snap["attempt"],
                        "error": type(snap["error"]).__name__})
        return snap
    agent.hooks.on_retry(observe, by="demo")

    r = await agent.query("用一句话介绍你自己。")
    print(f"① 限流重试: status={r.status}")
    print(f"   on_retry 观测: {retries}")
    print(f"   FlakyProvider 实际调用次数: {FlakyProvider.calls}（2 次失败 + 1 次成功）")

    # ② before_cancel 阻止取消（不可中断场景的信号语义）
    async def guard(agent_, ctx):
        raise Intercepted("支付已提交：该回合不可取消")
    agent.hooks.before_cancel(guard, by="guard")
    try:
        await agent.cancel()
        print("② cancel 未被阻止（与预期不符！）")
    except Intercepted as exc:
        print(f"② cancel 被 before_cancel 拦截: Intercepted({exc})")
        print("   信号未置位——取消从未发生（不可中断场景的标准用法）")
    agent.hooks.before_cancel.remove_by_owner("guard")   # 撤掉守卫（按钩子点移除）

    # ③ abort_turn：只终止当前回合，Agent 存活
    t = asyncio.create_task(agent.query("再跑一次 bash sleep 10，然后回答。"))
    while agent.current_turn is None:
        await asyncio.sleep(0.05)
    await asyncio.sleep(3.0)
    agent.abort_turn()
    r = await t
    print(f"③ abort_turn: status={r.status}（回合终止，Agent 存活）")
    r2 = await agent.query("还在吗？")
    print(f"   下一回合照常: status={r2.status} final={r2.final_text[:12]!r}")
    await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
