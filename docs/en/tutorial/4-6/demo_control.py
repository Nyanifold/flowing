"""Errors and control demo (4-6): rate-limit retry decision + the full cancellation semantics.

① FlakyProvider: the first two calls raise RateLimitedError (retryable class); the third
  delegates to the real DeepSeek. use_retry is a policy attached to on_provider_error —
  the mechanism only dispatches and supplies the can_continue field.
② Intercepted from before_cancel prevents cancellation; abort_turn terminates only the
  current turn.
Run: uv run python demo_control.py
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
    """Test adapter: the first two generate calls raise RateLimitedError, then it delegates to the real DeepSeek."""

    name = "flaky"
    calls = 0

    async def generate(self, context, model):
        type(self).calls += 1
        if type(self).calls <= 2:
            raise RateLimitedError(retry_after=0)
        real = DeepSeekProvider(self.config)
        response = await real.generate(context, model)
        # Normalization in the test adapter: a response with only thinking blocks
        # (no body text / no tool calls) gets a placeholder text block appended —
        # empty text is dropped by the accumulator while composing streaming deltas
        # (delta.text="" is not committed), and the next request would be rejected
        # by DeepSeek for "an assistant message with neither content nor tool_calls"
        # (framework-level empty-body handling: see PROGRESS "findings during writing").
        msg = response.message
        if msg is not None and not any(isinstance(b, ToolCallBlock)
                                       for b in msg.content) and not any(
                isinstance(b, TextBlock) and b.text.strip()
                for b in msg.content):
            msg.content.append(TextBlock("(thinking only, no body text)"))
        return response


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    agent.model = ModelConfig(model="deepseek-v4-flash", provider="flaky")

    # ① Rate-limit retry: mechanism (on_provider_error dispatch) + policy (use_retry decision)
    retries: list[dict] = []
    use_retry(agent, max_retries=3, base_delay=0.1)

    async def observe(agent_, snap):   # on_retry dispatches a snapshot dict (pure observation: a crash here does not affect retry)
        retries.append({"attempt": snap["attempt"],
                        "error": type(snap["error"]).__name__})
        return snap
    agent.hooks.on_retry(observe, by="demo")

    r = await agent.query("Introduce yourself in one sentence.")
    print(f"① Rate-limit retry: status={r.status}")
    print(f"   on_retry observed: {retries}")
    print(f"   FlakyProvider actual calls: {FlakyProvider.calls} (2 failures + 1 success)")

    # ② before_cancel prevents cancellation (signal semantics for non-interruptible scenarios)
    async def guard(agent_, ctx):
        raise Intercepted("payment already committed: this turn cannot be cancelled")
    agent.hooks.before_cancel(guard, by="guard")
    try:
        await agent.cancel()
        print("② cancel was NOT blocked (unexpected!)")
    except Intercepted as exc:
        print(f"② cancel intercepted by before_cancel: Intercepted({exc})")
        print("   signal not set — the cancellation never happened (standard usage for non-interruptible scenarios)")
    agent.hooks.before_cancel.remove_by_owner("guard")   # remove the guard (removal per hook point)

    # ③ abort_turn: terminates only the current turn; the agent survives
    t = asyncio.create_task(agent.query("Run bash sleep 10 once more, then answer."))
    while agent.current_turn is None:
        await asyncio.sleep(0.05)
    await asyncio.sleep(3.0)
    agent.abort_turn()
    r = await t
    print(f"③ abort_turn: status={r.status} (turn terminated, agent alive)")
    r2 = await agent.query("Still there?")
    print(f"   next turn as usual: status={r2.status} final={r2.final_text[:12]!r}")
    await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
