"""Context budget observation demo (3-4): measured anchor + tail estimation of estimate_context_tokens.

Prints one estimate per round and watches the ratio grow across the conversation.
Run: uv run python demo_budget.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    est = agent.estimate_context_tokens()
    print(f"Initial (empty tree): tokens={est.tokens} measured={est.measured} "
          f"estimated={est.estimated} ratio={est.usage_ratio}")
    for i in range(6):
        await agent.query(
            f"Question {i + 1}: from another angle, describe the 'context window' "
            "in one sentence.")
        est = agent.estimate_context_tokens()
        print(f"After round {i + 1}: tokens={est.tokens} measured={est.measured} "
              f"estimated={est.estimated} ratio={est.usage_ratio:.4f}")
    await runtime.shutdown()


asyncio.run(main())
