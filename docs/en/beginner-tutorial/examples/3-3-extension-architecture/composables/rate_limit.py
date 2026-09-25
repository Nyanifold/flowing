"""Self-written Composable example: use_rate_limit -- rate limiting for tool calls.

Example structure (isomorphic to the hook-mounting form of built-in Composables):
  use_xxx(agent, ...) -> optionally declare an extension hook point
  -> optionally attach a handler to a core hook point.
The same function can be called with different parameters; its implementation
determines the effect of repeated calls. This example's rate-limit counter is
needed only during the current runtime, so it lives in a closure. Persistent
state can use agent.state.register() or an application-managed persistence and
recovery mechanism.
"""
import asyncio

from flowing.errors import Intercepted


def use_rate_limit(agent, *, max_calls: int = 5, window_seconds: float = 60.0) -> None:
    """Tool calls beyond the limit within the window are blocked with an Intercepted (blocked) result.

    Declares the observation hook point ``on_rate_limited`` (match_on="name", filtered by tool name);
    the main logic attaches to ``before_tool_call`` (managed as one group via by="rate-limit").
    """
    agent.hooks.declare("on_rate_limited", by="rate-limit", match_on="name")

    calls: list[float] = []

    async def _gate(agent_, tool_call):
        now = asyncio.get_event_loop().time()
        calls[:] = [t for t in calls if now - t < window_seconds]   # sliding-window cleanup
        if len(calls) >= max_calls:
            await agent_.hooks.on_rate_limited.dispatch(agent_, tool_call)  # observation channel
            raise Intercepted(
                f"Rate limit exceeded: {max_calls} calls / {window_seconds:.0f} seconds,"
                f" please try again later")
        calls.append(now)
        return tool_call

    agent.hooks.before_tool_call(_gate, by="rate-limit", tags=["rate-limit"])


def remove_rate_limit(agent) -> int:
    """Remove the whole group (the standard pattern for by-group management)."""
    return agent.hooks.before_tool_call.remove_by_owner("rate-limit")
