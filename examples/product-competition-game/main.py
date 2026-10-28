
"""A two-player Production Competition Game in ordinary Python."""

import asyncio
import json
from pathlib import Path

from flowing import Runtime, launch


def payoff(q_a: int, q_b: int) -> tuple[int, int]:
    price = 12 - q_a - q_b
    return q_a * price, q_b * price


async def main() -> Runtime:
    """Assemble two live Agents from one reusable declaration."""
    runtime = Runtime()
    runtime.set_model_tags("@/model-tags.yaml")

    await runtime.mount("@/player.fya", agent_id="player-a", player="A")
    await runtime.mount("@/player.fya", agent_id="player-b", player="B")
    return runtime


if __name__ == "__main__":
    async def _run():
        runtime = await launch(str(Path(__file__).resolve().parent))
        try:
            a = await runtime.get_agent("player-a", strict=True)
            b = await runtime.get_agent("player-b", strict=True)

            # Both decide before either choice is revealed.
            result_a, result_b = await asyncio.gather(
                a.query("Choose your production quantity: 3 or 4."),
                b.query("Choose your production quantity: 3 or 4."),
            )
            q_a = json.loads(result_a.final_text)["quantity"]
            q_b = json.loads(result_b.final_text)["quantity"]

            profit_a, profit_b = payoff(q_a, q_b)

            # The host can directly update and inspect live Agent objects.
            a.last_profit = profit_a
            b.last_profit = profit_b
            print(f"Player A: quantity={q_a}, profit={a.last_profit}")
            print(f"Player B: quantity={q_b}, profit={b.last_profit}")
            print(f"Joint profit: {profit_a + profit_b}")
        finally:
            await runtime.shutdown()

    asyncio.run(_run())
