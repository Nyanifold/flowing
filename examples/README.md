# Flowing examples

These examples form a short, progressive path from one parameterized Agent to a multi-Agent conversation and finally to an embedded Python application. Each directory is a complete Flowing project with an English step-by-step README.

## Prerequisites

- Python 3.13 or later
- the `flowing-agent` package
- a Model entry named `luna` in your shared Flowing configuration
- the credential environment variable required by that Model's Provider

Every example maps its `default` model tag to `luna`. If that entry does not exist, configure its Provider and Model with `flowing-config providers add` and `flowing-config models add`. Press Enter at the path prompts to use the shared Runtime configuration.

The README commands use `uv run` inside this source checkout. If you installed the CLI in another environment, run `flowing` or `python` directly as appropriate. Model responses are not fixed, so exact wording and decisions can differ from the sample outputs.

## Follow the examples in order

### 1. [Hello Flowing](hello-flowing/README.md)

Build one chat Agent and pass a name from the CLI into its system prompt. This example introduces project assembly, `.fya` declarations, `setup()`, instance attributes, Jinja interpolation, `use_retry()`, stable Agent IDs, and conversation recovery.

Run it with:

```console
cd hello-flowing
uv run flowing repl . --name Alice
```

### 2. [Guessing Game](guessing-game/README.md)

Host an `oracle` and a `guesser` in one Runtime and let them exchange asynchronous peer messages. This example introduces plugins, explicit tool bindings, peer allowlists, multiple composables, tool-result hooks, and inspection of independent message histories.

Run it with:

```console
cd guessing-game
uv run flowing repl .
```

### 3. [Production Competition Game](product-competition-game/README.md)

Mount two players from one declaration and orchestrate them from ordinary Python. This example introduces structured output validation, instance-local counters, concurrent `query()` calls, live Agent lookup, direct attribute access, and deterministic application-side payoff calculation.

Run it with:

```console
cd product-competition-game
uv run python main.py
```

## Persistence and clean runs

Flowing writes each project's messages and state under its local `.flowing/` directory. Fixed Agent IDs allow a later launch to recover the same history. Exit the REPL with `/exit`; embedded applications should call `await runtime.shutdown()` so pending writes are drained.

To repeat an example from an empty history, stop it first and remove only that example's `.flowing/` directory. Do not remove it while the Runtime is still running.
