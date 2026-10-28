# Guessing Game: two Agents and peer messaging

This example builds a two-Agent guessing game. The `oracle` privately keeps an answer, while the `guesser` asks yes-or-no questions and tries to infer it. The Agents communicate through the Peers plugin rather than sharing one conversation history.

You will learn how to:

- host multiple independent Agents in one Runtime;
- install a plugin and explicitly bind its tools to selected Agents;
- describe allowed peers with a per-Agent catalog;
- use hooks to finish a turn after a successful tool call;
- inspect peer messages and recover both Agents after a restart.

## 1. Prerequisites

Use Python 3.13 or later and install `flowing-agent`:

```console
pip install flowing-agent
```

Run the example commands from this directory. In a source checkout, prefix the CLIs with `uv run`; with an installed CLI, use `flowing` and `flowing-config` directly.

```console
cd examples/guessing-game
```

### Configure the `luna` Model

Both Agents use the same Model entry. These instructions configure `luna` with OpenRouter and the API model ID `openai/gpt-6-luna`. If your shared configuration already contains `luna`, skip the two `flowing-config` commands and only ensure that its Provider credential is set.

**Step 1: make the credential available.** Set the environment variable in the shell before starting the REPL:

```console
export OPENROUTER_API_KEY='<your OpenRouter API key>'
```

Keep the real key out of the project files.

**Step 2: add the OpenRouter Provider.** Run the interactive command and answer the prompts as shown:

```console
$ uv run flowing-config providers add
# Press Enter at the configuration path prompt to use the Runtime default.
Provider entry name (identity): openrouter
Provider adapter: openrouter
API endpoint (base_url; leave blank to use 'https://openrouter.ai/api/v1'): [Enter]
API key (api_key; leave blank to skip): env.OPENROUTER_API_KEY
```

This creates a Provider entry named `openrouter` in shared `providers.yaml`. By default, shared configuration lives under `~/.flowing`; set `FLOWING_CONFIG_HOME` if you keep it elsewhere. The `env.OPENROUTER_API_KEY` value stores an environment-variable reference, so the credential remains outside the example.

**Step 3: add the `luna` Model entry.** Run:

```console
$ uv run flowing-config models add
# Press Enter at the configuration path prompt to use the Runtime default.
Model entry name (identity): luna
Known Provider entries: openrouter
Provider entry name (identity): openrouter
API model ID: openai/gpt-6-luna
```

Accept the defaults for the remaining optional parameters unless you intentionally want different model settings. The resulting entry in shared `models.yaml` is equivalent to:

```yaml
luna:
  provider: openrouter
  model: openai/gpt-6-luna
```

**Step 4: check the project tag mapping.** The included `model-tags.yaml` contains:

```yaml
tags:
  default: luna
```

Both `.fya` files declare `model_tag: default`. Each model call therefore follows this lookup:

```text
model_tag: default
    -> project model-tags.yaml: Model entry luna
    -> shared models.yaml: Provider openrouter,
       API model ID openai/gpt-6-luna
    -> shared providers.yaml: OPENROUTER_API_KEY
```

The two Agents share this model configuration, but their peer catalogs, tools, prompts, and histories remain independent.

## 2. Assemble two Agents in `main.py`

```python
from flowing import Runtime
from flowing.plugins.peers import PeersPlugin


async def main() -> Runtime:
    runtime = Runtime()
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.install(PeersPlugin())
    await runtime.mount("@/oracle.fya", agent_id="oracle")
    await runtime.mount("@/guesser.fya", agent_id="guesser")
    return runtime
```

`runtime.install(PeersPlugin())` registers the Peers tools and provides the router that resolves a peer ID through the Runtime. Installing the plugin does not make those tools visible to every Agent: each `.fya` declaration must still list `message-peer` under `tools:`.

The two fixed IDs are important. Peer declarations refer to `oracle` and `guesser` exactly, and the same IDs let later launches recover the corresponding histories.

## 3. Declare the referee in `oracle.fya`

```python
description: "Keeps the answer private and evaluates the guesser's questions."
model_tag: default
tools:
  - message-peer
peers:
  guesser: "The player who asks you questions privately. Reply with your answer."
```

The `tools:` and `peers:` fields have different jobs:

- `tools:` binds the executable `message-peer` tool and makes its declaration visible to the model.
- `peers:` is an allowlist and prompt catalog. The oracle may send only to the exact ID `guesser`.

The system prompt tells the oracle to keep the user's answer private, answer questions only through `message-peer`, and avoid extra explanation. Its `$script` attaches two composables and one hook:

```python
async def setup(self):
    use_peers(self)
    use_retry(self)

    def _end_turn(agent, result):
        if result.status == "completed" and agent.current_turn is not None:
            agent.current_turn.finish = True
        return result

    self.hooks.after_tool_call["message-peer"](_end_turn, by="guessing-game")
```

`use_peers()` injects the catalog into the dynamic system prompt; it does not bind tools. `use_retry(self)` enables the default Provider retry policy. The hook ends the turn after a message is queued successfully, so the oracle does not make another model call merely to comment on its own tool result.

## 4. Declare the player in `guesser.fya`

The guesser has the mirror-image binding:

```python
tools:
  - message-peer
peers:
  oracle: "The referee who knows the answer and replies yes, no, or uncertain. Ask it questions."
```

Its prompt requires one yes-or-no question at a time, at most five questions, and a final guess in assistant text. Its setup uses `use_peers(self)`, `use_retry(self, max_retries=5)`, and the same successful-send hook. The retry parameter belongs to Provider-call retries; it does not limit questions or peer messages.

## 5. Understand one question-and-answer cycle

`message-peer` queues a `PEER` message and returns its message ID. It does not wait for the target Agent's answer.

One cycle therefore looks like this:

```text
guesser --message-peer--> oracle
   |                         |
   | finish current turn     | process PEER message
   |                         | answer with message-peer
   |                         v
   +---- process PEER <------+
   |
   +---- ask again or give the final guess
```

Each Agent has its own message history. The answer given privately to the oracle is not inserted into the guesser's history.

## 6. Run the game

Start the REPL:

```console
uv run flowing repl .
```

At the initial prompt, switch to the oracle and privately set the answer:

```text
(new agent)>>> /agent oracle
(oracle)>>> The answer is pear. Remember it, but do not tell the guesser.
```

Then switch to the guesser and start the game:

```text
(oracle)>>> /agent guesser
(guesser)>>> Start the game. The answer is a fruit. Ask at most five yes-or-no questions, then make your guess.
```

The guesser should use `message-peer` to ask the oracle questions. The oracle replies asynchronously, and each reply starts another guesser turn. A reasonable session might ask whether the fruit is citrus, whether it is typically red, whether it has a stone, whether it is tropical, and whether it is an apple before guessing **pear**. The exact questions and wording depend on the model.

The REPL progress display renders the foreground Agent's own Provider activity. A `PEER` message delivered dynamically by the other Agent does not automatically appear as a transcript line. Use this command to inspect the current Agent's complete message chain:

```text
(guesser)>>> /messages
```

Add `verbose` to see full message content:

```text
(guesser)>>> /messages verbose
```

You can switch back to `oracle` with `/agent oracle` and inspect its side independently.

## 7. Restart or reset

Both Agents persist under the project-local `.flowing/` directory. Exit with `/exit`, run `uv run flowing repl .` again, and switch to either fixed Agent ID to continue with its recovered history.

To start a completely new game with no previous messages, stop the REPL and remove only this example's persistence directory:

```console
rm -rf .flowing
```

Then set a new private answer before starting the guesser again.
