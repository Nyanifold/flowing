# Production Competition: reusable declarations and Python orchestration

This example moves from interactive Agent conversations to an embedded Python application. Two players independently choose to produce **3 or 4 units**. A host program requests one decision from each player, calculates their profits, and prints the result.

The example demonstrates:

- mounting two independent live Agents from one reusable `.fya` declaration;
- declaring and validating a structured JSON result;
- using an instance-local hook to count and log user requests;
- querying Agents concurrently with `asyncio.gather()`;
- reading and writing ordinary attributes on live Agent objects;
- keeping game rules and orchestration in plain Python rather than a workflow DSL.

## 1. Prerequisites

Use Python 3.13 or later, install `flowing-agent`, and prepare a shared Model entry named `luna`. If necessary, use `flowing-config providers add` and `flowing-config models add` to configure it, then set the Provider's credential environment variable.

Run from this directory:

```console
cd examples/product-competition-game
```

The source files are `player.fya`, `main.py`, and `model-tags.yaml`.

## 2. Understand the game

The selling price falls as combined production increases:

```text
price = 12 - (quantity_a + quantity_b)
profit_a = quantity_a * price
profit_b = quantity_b * price
```

The four possible outcomes are:

| Player A | Player B | Price | Profit A | Profit B | Joint profit |
|---:|---:|---:|---:|---:|---:|
| 3 | 3 | 6 | 18 | 18 | 36 |
| 3 | 4 | 5 | 15 | 20 | 35 |
| 4 | 3 | 5 | 20 | 15 | 35 |
| 4 | 4 | 4 | 16 | 16 | 32 |

Producing 4 is each player's best response to either choice by the other player, so `(4, 4)` is the Nash equilibrium (where both self-interested players would end up). Joint profit is highest at `(3, 3)`. The model is free to choose either allowed quantity; the example does not force an equilibrium.

## 3. Reuse one Agent declaration in `player.fya`

```python
description: "A player in a two-player Production Competition Game."
model_tag: default
output:
  quantity:
    type: integer
    enum: [3, 4]
    description: "The quantity to produce."

---
$system_prompt:
You are Player {{player}} in a two-player Production Competition Game.

The selling price is 12 minus the combined production:
price = 12 - (your quantity + the other player's quantity).
Your profit = your quantity * price.

Choose the quantity that maximizes your own profit.
Return only a JSON object such as {"quantity": 3}.

---
$script:
from flowing.composables import use_retry
from flowing.composables.validate import use_validate_turn_result
from flowing.message import MessageKind


async def setup(self, player: str):
    use_retry(self)
    use_validate_turn_result(self, on_fail="steer", max_retries=2)

    self.player = player
    self.requests = 0

    def log_request(agent, msg):
        if msg.kind is MessageKind.USER:
            agent.requests += 1
            print(
                f"[{agent.node_id}] "
                f"player={agent.player} requests={agent.requests}"
            )
        return msg

    self.hooks.on_enqueue(log_request)
```

A `.fya` file is a reusable declaration, not a running Agent. This one colocates the model tag, output contract, prompt, setup logic, composables, instance attributes, and hook registration.

The `output:` field requires a JSON object whose `quantity` is the integer 3 or 4. `use_validate_turn_result()` validates the final response against that contract. With `on_fail="steer"`, an invalid response sends a correction instruction back into the turn; `max_retries=2` allows two repairs, and another invalid response ends the turn with an error. `use_retry()` separately handles retryable Provider exceptions.

The `on_enqueue` hook runs for each mounted instance. On a user message, it increments that instance's `requests` member and prints its `node_id` and player label. The hook does not share a counter between players.

## 4. Assemble the application in `main.py`

The assembly entry point mounts the declaration twice:

```python
async def main() -> Runtime:
    runtime = Runtime()
    runtime.set_model_tags("@/model-tags.yaml")

    await runtime.mount("@/player.fya", agent_id="player-a", player="A")
    await runtime.mount("@/player.fya", agent_id="player-b", player="B")
    return runtime
```

`player="A"` and `player="B"` flow into `setup()` and then into `{{player}}`. The two mounts have distinct IDs, prompts, instance attributes, hooks, and histories even though they share the same declaration file.

The executable host uses `launch()` to run that assembly entry point:

```python
runtime = await launch(str(Path(__file__).resolve().parent))
a = await runtime.get_agent("player-a", strict=True)
b = await runtime.get_agent("player-b", strict=True)
```

`get_agent()` returns the live Agent objects. The host can call their methods and access their attributes directly; no REPL, HTTP service, or separate orchestration framework is involved.

## 5. Request simultaneous decisions

The host sends both queries concurrently:

```python
result_a, result_b = await asyncio.gather(
    a.query("Choose your production quantity: 3 or 4."),
    b.query("Choose your production quantity: 3 or 4."),
)
q_a = json.loads(result_a.final_text)["quantity"]
q_b = json.loads(result_b.final_text)["quantity"]
```

Neither query contains the other player's current decision, and the Agents have separate histories. `asyncio.gather()` is ordinary Python concurrency, not a Flowing-specific workflow construct.

The payoff calculation is also plain Python:

```python
def payoff(q_a: int, q_b: int) -> tuple[int, int]:
    price = 12 - q_a - q_b
    return q_a * price, q_b * price
```

Finally, the host writes `last_profit` onto each live Agent and reads it back for output. This complements the hook's access to `requests`: application code and hooks interact with the same objects.

## 6. Run the example

From a source checkout, run:

```console
uv run python main.py
```

With Flowing installed in the active environment, `python main.py` is equivalent. One possible result is:

```text
[player-a] player=A requests=1
[player-b] player=B requests=1
Player A: quantity=4, profit=16
Player B: quantity=4, profit=16
Joint profit: 32
```

The model determines both quantities, so another valid run can produce 3 instead of 4. The hook lines can appear in either order because the two queries run concurrently. The host always computes profits from the actual JSON decisions.

The `finally` block calls `await runtime.shutdown()`, which destroys the Agents and drains pending persistence writes. On a later run, the fixed IDs recover the existing Agents and their histories. To repeat the demonstration from a clean state, stop the program and remove this example's `.flowing/` directory:

```console
rm -rf .flowing
```
