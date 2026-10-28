Flowing
=======

**Flowing** is a lightweight, extensible agent runtime framework for complex interactions (Python ≥ 3.13). It consolidates the entire definition of a single agent into one declarative ``.fya`` file, leaves the hosting and organization of multiple agents to an embeddable runtime, and opens behavior up to extension through plugins, composables, and hooks. Messages and state are persisted automatically, and history is organized as a message tree that can be edited at fine granularity. Adoption unfolds on demand: start with a single ``.fya`` file and the REPL, then gradually introduce tools, subagents, plugins, composables, and persistent state, up to embedding the runtime into a host application. The framework core handles only message flow, error classification, and hook dispatch; policies such as retries, compaction, and approvals are mounted on demand as composables or plugins.

Who it's for
------------

- **You review and change agent definitions and logic frequently**: declarations, setup, hooks, and in-class logic all live in the same ``.fya`` file, and the bindings and visibility of tools and subagents are spelled out line by line — a single read shows what the agent can use and what the model can see, with no round-trips between assembly sites;
- **You want business logic inside the agent itself**: an agent can freely define attributes and state, and hooks run your code at each point — beyond accepting, intercepting, or rewriting the passed-in object, they can read and write attributes, update state, record audits, query caches, or ask external systems;
- **You want to decide policies like retries, compaction, and approvals yourself**: the core does only message flow, error classification, and hook dispatch; these policies ship as readable composables or plugins — copy them, modify them, or simply leave them off;
- **You want to build an agent system along a progressive path**: the starting point is a single ``.fya`` file plus one model configuration, and it runs; each step toward complexity — tools and subagents, hook logic, persistent state, multi-agent orchestration, host embedding — accumulates on the same ``.fya`` file and plain Python code, introducing no new mechanism layer and requiring no change of working style;
- **You need to embed agents into an existing process and write the orchestration yourself**: the runtime is an ordinary object holding the object graph; organization, shared dependencies, and downstream orchestration are all done in plain Python code;
- **You need conversations and state to carry across runs, and to edit history after the fact**: messages and state are persisted automatically, and recovery is replay; history is a message tree on which you can branch off, rewrite, or prune at any message.

These needs don't point to any single application shape: in Flowing, orchestration logic is plain Python code, with sequencing, branching, and concurrency expressed by the language itself. As a foundational runtime framework, it can be used to build coding, education, e-commerce, or companion agents as well as multi-agent systems, and to run multi-agent interaction experiments.

Highlights
----------

- **Semantically self-contained agent declarations**: an agent's description, model intent, system prompt, tools and subagents, provided and injected objects, fields required by downstream plugins (such as skill lists and peer catalogs), hooks, and composable calls are all defined in a single ``.fya`` file, with assembly code concentrated in its ``setup()``. A tool, subagent, or skill is defined once and can then be bound by many agents as different model views — and being registered does not mean the model can see it. Reviewing a single agent therefore means reading a single file, with no round-trips between assembly sites.
- **A hosting, embeddable runtime**: the runtime is an ordinary Python object that can be embedded into any host program. It hosts and organizes multiple agents — mounting, lookup by ID, recovery, destruction, and archiving — and the host or any agent can fetch subagents from it for downstream orchestration. Shared dependencies such as database connections and indexes are provided on the runtime and can be injected into agents at any depth.
- **Plugins, composables, and hooks**: a plugin packages a set of extensions into a distributable unit that other projects can install and enable; tools, subagent types, skills, and model adapters can likewise be packaged for reuse by other projects. A composable is a finer-grained unit of reuse: an ordinary function that injects a piece of policy into a given agent, reusable with different parameters and stackable within the same agent. Hooks attach to points across the message loop, spanning lifecycle, turns, messages, model calls, tool execution, and subagents; every agent can freely define its own attributes, with its own state and run logic. A hook function executes your code at those points: beyond accepting, intercepting, or rewriting the passed-in object, it can read and write those attributes, update state, record audits, query caches, ask external systems, and more.
- **Automatic persistence of messages and state**: messages and state are written to disk automatically, and a crash is rebuilt by replay. The default backend is json/jsonl files, and the persistence backend is replaceable. Fields that must survive across runs — todo lists, goals, cron schedules, dangerous-operation counters — are declared as agent state, and the framework manages the rest.
- **A tree-structured message history**: conversation history is organized as a message tree. Moving the cursor opens a parallel branch while old branches stay intact; history supports five operations — insert, branch, remove, update, and reparent — and every change is persisted automatically as well. The structure of the message record can thus be organized and edited freely.
- **Built-in verification entries**: a one-shot CLI, a REPL, an HTTP service, and a web frontend are built in. Even before your own frontend exists, the agents or agent systems you build can be demonstrated, tested, and verified directly through the built-in entries.

Installation
------------

.. code-block:: bash

   pip install flowing-agent

Example
-------

This example embeds two specialized decision Agents in a Python application. Each player returns a structured production choice; the host queries both concurrently and computes the result. The game rules are deliberately small: the focus is on reusable declarations, live Agent objects, instance-local hooks and composables, and an embeddable Runtime orchestrated with ordinary Python.

Create a project directory with ``player.fya``, ``main.py``, and ``model-tags.yaml``.

``player.fya`` defines one reusable player declaration:

.. code-block:: python

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

The declaration keeps the prompt, output contract, setup logic, instance members, hooks, and composable policies together. ``use_retry()`` handles retryable Provider failures; ``use_validate_turn_result()`` can steer an invalid structured response toward repair. The hook runs against each mounted instance and updates its own ``requests`` member.

``main.py`` mounts the declaration twice and embeds the Runtime in a normal Python host:

.. code-block:: python

   import asyncio
   import json
   from pathlib import Path

   from flowing import Runtime, launch


   def payoff(q_a: int, q_b: int) -> tuple[int, int]:
       price = 12 - q_a - q_b
       return q_a * price, q_b * price


   async def main() -> Runtime:
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
               result_a, result_b = await asyncio.gather(
                   a.query("Choose your production quantity: 3 or 4."),
                   b.query("Choose your production quantity: 3 or 4."),
               )
               q_a = json.loads(result_a.final_text)["quantity"]
               q_b = json.loads(result_b.final_text)["quantity"]
               profit_a, profit_b = payoff(q_a, q_b)

               a.last_profit = profit_a
               b.last_profit = profit_b
               print(f"Player A: quantity={q_a}, profit={a.last_profit}")
               print(f"Player B: quantity={q_b}, profit={b.last_profit}")
               print(f"Joint profit: {profit_a + profit_b}")
           finally:
               await runtime.shutdown()

       asyncio.run(_run())

``main()`` is the project assembly entry point: it creates, configures, and **returns a Runtime**. ``launch()`` imports and awaits ``main()``, then returns that Runtime to the external caller. From that point on, the host can use it directly like any other Python object: retrieve live Agents with ``get_agent()``, query or update them, and orchestrate them with ``asyncio`` and application logic. The two mounts have independent attributes, hooks, and histories. This embedding pattern can host domain-specific product Agents or controlled multi-agent research experiments without introducing a workflow DSL.

``model-tags.yaml`` maps the lookup tag ``default`` to the ``luna`` Model entry:

.. code-block:: yaml

   tags:
     default: luna

The tag carries no Provider information: ``default`` resolves to ``luna``, which separately selects the Provider and API model ID. For step-by-step Provider and Model configuration, see `Hello Flowing <https://github.com/Nyanifold/flowing/blob/main/examples/hello-flowing/README.md#configure-the-luna-model>`_ or `Guessing Game <https://github.com/Nyanifold/flowing/blob/main/examples/guessing-game/README.md#configure-the-luna-model>`_.

Run from the project directory:

.. code-block:: console

   $ export OPENROUTER_API_KEY='<your key>'
   $ python main.py
   [player-a] player=A requests=1
   [player-b] player=B requests=1
   Player A: quantity=4, profit=16
   Player B: quantity=4, profit=16
   Joint profit: 32

The model determines the choices, so quantities and profits can vary. See the `complete example <https://github.com/Nyanifold/flowing/tree/main/examples/product-competition-game>`_ for a step-by-step walkthrough.

The `examples directory <https://github.com/Nyanifold/flowing/tree/main/examples>`_ contains three projects in a progressive order: `Hello Flowing <https://github.com/Nyanifold/flowing/tree/main/examples/hello-flowing>`_ introduces project assembly, parameter injection, ``setup()``, and persistence; `Guessing Game <https://github.com/Nyanifold/flowing/tree/main/examples/guessing-game>`_ demonstrates plugins, peer messaging, and independent Agent histories; and `Production Competition Game <https://github.com/Nyanifold/flowing/tree/main/examples/product-competition-game>`_ shows how reusable declarations and live Agent objects support ordinary Python orchestration in specialized products and research projects.

Going further
-------------

- **Capability access**: declare built-in tools, MCP servers, or shell commands and HTTP endpoints as tools via ``tools:``; implement custom logic with ``ScriptTool``.
- **Multi-agent**: declare subagent types under ``subagents:`` and the orchestrator routes work using the auto-generated catalog; you can also create subagents programmatically and run them in parallel. ``PeersPlugin`` enables directed message-queue interactions between agents in one Runtime through a ``peers:`` catalog.
- **Intervention**: attach handlers at hook points — intercept a tool call pending approval, audit at turn completion, switch models and retry on provider errors. The built-in ``use_retry`` / ``use_compact`` are implemented in exactly this way and can serve as references.
- **Run modes**: eight subcommands — REPL, one-shot CLI, plain HTTP API, a built-in web frontend, CI smoke tests, and more — run the same project unchanged in any form.
- **Embedding**: the host application holds the Runtime returned by ``launch()``; input goes through ``query`` / ``message`` / ``steer``, output through hook subscriptions (streaming output, completion notices, call interception).

Built-in plugins and composables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Plugins and composables are reusable extensions shipped with the package; none are enabled by default. A plugin uses ``runtime.install()`` to register Runtime-level capabilities such as tools and injectable services. Complete its per-Agent setup in ``setup()`` with the plugin's ``use_xxx(self)`` when one is provided, and bind each required tool explicitly. A standalone composable is an ordinary ``use_xxx(agent, ...)`` function that attaches behavior to one Agent without Runtime installation. Composables can stack, and their implementations can be copied and adapted as application policies.

.. list-table::
   :header-rows: 1
   :widths: 14 86

   * - Plugin
     - Purpose
   * - ``skills``
     - Load a prompt section by name: declare the catalog under ``skills:``; the model loads detailed guidance with the ``skill-load`` tool.
   * - ``comm``
     - An in-process communication bus with two channels: point-to-point signals (``send`` / ``request`` / ``reply``) and publish-subscribe events (``publish`` / ``subscribe``), for agents, UI components, and application code.
   * - ``peers``
     - Directed interaction between agents in one Runtime: registers ``query-peer`` / ``message-peer`` / ``steer-peer`` and limits reachable targets by the ``peers:`` catalog.
   * - ``cron``
     - Per-agent scheduled tasks: ``schedule`` / ``unschedule`` / ``jobs`` live with each agent, the model schedules through ``schedule-cron`` / ``manage-cron``, and delivery arrives on the ``on_cron_trigger`` hook.
   * - ``workflow``
     - Rule-based orchestration: subclass ``Workflow`` to write branching, loops, and parallel or sequential steps in Python, then bind ``run-workflow`` to start a workflow by path.
   * - ``clipboard``
     - Cut, copy, and paste file ranges (``clipboard-cut`` / ``clipboard-copy`` / ``clipboard-paste``), for moving code sections across files.

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Composable
     - Purpose
   * - ``use_retry``
     - On a retryable LLM call failure, wait by the backoff model and resend, up to the retry limit.
   * - ``use_compact``
     - After a request: once context usage crosses a threshold, have the model compress the conversation into a handover summary and start a new chain from it.
   * - ``use_auto_compact``
     - Before a request: keep the head and tail messages, compress the middle into one message, and send the request with the compacted context.
   * - ``use_system_reminder``
     - Before each logical turn, inject a system reminder (current time, directory, task state) that is persisted with the batch.
   * - ``use_prompt_until``
     - At turn end, run an assertion; if it fails, steer the agent into a further turn.
   * - ``use_validate_turn_result``
     - Validate the final reply as JSON against an explicit schema or the Agent's ``output``; steer same-turn repairs (unlimited by default), warn, or raise. A finite exhausted repair budget ends the turn with an error.
   * - ``use_validate_enqueued_message``
     - Filter inbound messages by kind, source, or predicate and validate their text or structured data against an explicit schema or the Agent's ``input``; on failure, reject the enqueue, warn, or raise.

Documentation
-------------

- :doc:`Beginner tutorial <beginner-tutorial/index>`: for readers new to agent systems development;
- :doc:`Detailed tutorial <tutorial/index>`: from quick start to core mechanics and operations;
- :doc:`Concise reference <flowing-ref/index>`: the whole framework in four parts, for quick lookup;
- :doc:`API reference <api>`: the public API organized by module.

Project notes
-------------

Breaking changes may occur during the 0.x series. Each such release includes a detailed changelog with migration guidance; see the `changelogs/ directory <https://github.com/Nyanifold/flowing/tree/main/changelogs>`__.

This project was developed with heavy reliance on AI assistance, using models from different providers at different capability levels. Limited by the author's available time, not every line of code has been individually reviewed; if you find any divergence between the implementation and the documentation (docstrings, tutorials), an `issue <https://github.com/Nyanifold/flowing/issues>`__ is greatly appreciated.

License
-------

`MIT <https://github.com/Nyanifold/flowing/blob/main/LICENSE>`__ © 2026 Nyanifold

.. toctree::
   :hidden:
   :maxdepth: 2
   :caption: Documentation

   beginner-tutorial/index
   tutorial/index
   flowing-ref/index
   api
