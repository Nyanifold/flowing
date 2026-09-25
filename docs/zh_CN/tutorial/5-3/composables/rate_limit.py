"""自写 Composable 示例（5-3）：use_rate_limit —— 工具调用频率限制。

示例结构（与内置 Composable 的钩子挂载方式同构）：
  use_xxx(agent, ...) → 可选 declare 扩展钩子点 → 可选挂核心钩子点 handler。
同一个函数可用不同参数多次调用；调用效果由函数实现决定。本例的限流
计数只需在当前运行期使用，因此存在闭包中；持久化状态可通过
agent.state.register() 或应用自有的持久化与恢复机制管理。
"""
import asyncio

from flowing.errors import Intercepted


def use_rate_limit(agent, *, max_calls: int = 5, window_seconds: float = 60.0) -> None:
    """窗口内工具调用超额的调用被 Intercepted 阻断（blocked 结果）。

    声明观测钩子点 ``on_rate_limited``（match_on="name"，按工具名过滤）；
    主逻辑挂在 ``before_tool_call`` 上（by="rate-limit" 整组可管理）。
    """
    agent.hooks.declare("on_rate_limited", by="rate-limit", match_on="name")

    calls: list[float] = []

    async def _gate(agent_, tool_call):
        now = asyncio.get_event_loop().time()
        calls[:] = [t for t in calls if now - t < window_seconds]   # 滑窗清理
        if len(calls) >= max_calls:
            await agent_.hooks.on_rate_limited.dispatch(agent_, tool_call)  # 观测通道
            raise Intercepted(
                f"调用频率超限：{max_calls} 次 / {window_seconds:.0f} 秒，请稍后再试")
        calls.append(now)
        return tool_call

    agent.hooks.before_tool_call(_gate, by="rate-limit", tags=["rate-limit"])


def remove_rate_limit(agent) -> int:
    """整组移除（by 归组管理的标准姿势）。"""
    return agent.hooks.before_tool_call.remove_by_owner("rate-limit")
