"""Self-written Composable example (5-3): use_rate_limit — tool-call rate limiting.

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
    """Tool calls beyond the window limit are blocked by Intercepted (blocked result).

    Declares the observable hook point ``on_rate_limited`` (match_on="name",
    filtered by tool name); the main logic attaches to ``before_tool_call``
    (manageable as a group via by="rate-limit").
    """
    agent.hooks.declare("on_rate_limited", by="rate-limit", match_on="name")

    calls: list[float] = []

    async def _gate(agent_, tool_call):
        now = asyncio.get_event_loop().time()
        calls[:] = [t for t in calls if now - t < window_seconds]   # sliding-window cleanup
        if len(calls) >= max_calls:
            await agent_.hooks.on_rate_limited.dispatch(agent_, tool_call)  # observation channel
            raise Intercepted(
                f"Rate limit exceeded: {max_calls} calls / {window_seconds:.0f} seconds. Please try again later.")
        calls.append(now)
        return tool_call

    agent.hooks.before_tool_call(_gate, by="rate-limit", tags=["rate-limit"])


def remove_rate_limit(agent) -> int:
    """Remove the whole group (the standard way to manage by-grouped handlers)."""
    return agent.hooks.before_tool_call.remove_by_owner("rate-limit")
