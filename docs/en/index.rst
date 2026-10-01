Flowing
=======

**English** | `中文 <../zh_CN/>`__

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

Quick start
-----------

This example builds a two-agent guessing game: the referee ``oracle`` privately keeps the answer, while the player ``guesser`` asks directed questions and works it out. It shows how one Runtime can host multiple agents, how ``PeersPlugin`` registers Peers tools, and how declarative tool bindings control which tools each agent can see. A peer tool looks up its target agent by ID through the Runtime. The two composables have separate roles: ``use_peers()`` reads the ``peers`` catalog and injects it into the system prompt, while ``use_retry()`` configures model-call retries with the default settings (three retries) for the referee and up to five retries for the guesser.

Create a project directory with the following four files:

``main.py`` installs the Peers plugin and mounts both agents with stable IDs:

.. code-block:: python

   from flowing import Runtime
   from flowing.plugins.peers import PeersPlugin

   async def main() -> Runtime:
       runtime = Runtime()
       runtime.set_model_tags("@/model-tags.yaml")
       # Register Peers tools; each agent still declares which tools it can call.
       runtime.install(PeersPlugin())
       await runtime.mount("@/oracle.fya", agent_id="oracle")
       await runtime.mount("@/guesser.fya", agent_id="guesser")
       return runtime

``oracle.fya`` defines the referee, who privately knows the answer and can message only ``guesser``:

.. code-block:: python

   description: "Keeps the answer private and evaluates the guesser's questions."
   model_tag: default
   tools:
     - message-peer
   peers:
     guesser: "The player who asks you questions privately. Reply with your answer."

   ---
   $system_prompt:
   You are the referee in a guessing game. The user will privately tell you the answer. Remember it, but do not reveal it to the guesser.
   When the guesser sends a question, decide whether the answer is yes, no, or uncertain. You must call
   message-peer with peer_id set to "guesser" and send a message containing the original question followed by exactly one of: yes, no, or uncertain.
   Do not add an explanation or other text, and do not reply to the guesser directly. When the user privately tells you the answer, briefly confirm that it is set and do not contact the guesser.

   ---
   $script:
   from flowing.composables import use_retry
   from flowing.plugins.peers import use_peers


   async def setup(self):
       # Add the peers catalog prompt and enable retries with the default settings.
       use_peers(self)
       use_retry(self)

       # status == "completed" means the send succeeded; this example ends the turn without another LLM request for the result.
       def _end_turn(agent, result):
           if result.status == "completed" and agent.current_turn is not None:
               agent.current_turn.finish = True
           return result

       self.hooks.after_tool_call["message-peer"](_end_turn, by="guessing-game")

``guesser.fya`` defines the player, who infers the answer by asking the referee:

.. code-block:: python

   description: "Guesses the answer by asking the referee questions."
   model_tag: default
   tools:
     - message-peer
   peers:
     oracle: "The referee who knows the answer and replies yes, no, or uncertain. Ask it questions."

   ---
   $system_prompt:
   You are playing a guessing game. When the user starts the game, ask the oracle one yes-or-no question at a time.
   Do not ask the referee to reveal the answer directly. After each reply, continue according to the rules; once you know the answer,
   give your best guess to the user.

   ---
   $script:
   from flowing.composables import use_retry
   from flowing.plugins.peers import use_peers


   async def setup(self):
       # Add the peers catalog prompt and allow up to five retries per turn.
       use_peers(self)
       use_retry(self, max_retries=5)

       # status == "completed" means the send succeeded; this example ends the turn without another LLM request for the result.
       def _end_turn(agent, result):
           if result.status == "completed" and agent.current_turn is not None:
               agent.current_turn.finish = True
           return result

       self.hooks.after_tool_call["message-peer"](_end_turn, by="guessing-game")

``model-tags.yaml`` maps the lookup tag ``default`` to the ``luna`` Model entry:

.. code-block:: yaml

   tags:
     default: luna

To let both agents call a model, first add an OpenRouter Provider with its connection details:

.. code-block:: console

   $ flowing-config providers add
   # Press Enter at the configuration path prompt to use the Runtime default.
   Provider entry name (identity): openrouter
   Provider adapter: openrouter
   API endpoint (base_url; leave blank to use 'https://openrouter.ai/api/v1'): [Enter]
   API key (api_key; leave blank to skip): env.OPENROUTER_API_KEY

Once the Provider is saved, add a Model entry that refers to it. The entry is named ``luna``, with API model ID ``openai/gpt-6-luna``:

.. code-block:: console

   $ flowing-config models add
   # Press Enter at the configuration path prompt to use the Runtime default.
   Model entry name (identity): luna
   Known Provider entries: openrouter
   Provider entry name (identity): openrouter
   API model ID: openai/gpt-6-luna

The ``default`` model tag is a lookup label and carries no Provider information. It resolves to the ``luna`` Model entry, which separately selects the ``openrouter`` Provider and API model ID ``openai/gpt-6-luna``. Adapter parameter suggestions depend on the API model ID and do not guarantee support by the remote model.

These declarations serve different purposes: ``tools:`` explicitly binds ``message-peer`` for an agent to call, while ``peers:`` lists the allowed peer IDs and their descriptions. ``PeersPlugin`` registers the Peers tools; when called, a peer tool looks up the target agent by ID through the Runtime. The ``use_peers(self)`` composable only reads the current agent's ``peers`` field and injects the catalog into its dynamic system prompt; it does not bind tools. Both agents enable Provider call retries with ``use_retry()``; the referee uses the default settings, while the guesser sets ``max_retries=5``.

Run:

.. code-block:: console

   $ export OPENROUTER_API_KEY='<your key>'
   $ flowing repl .
   (new agent)>>> /agent oracle
   (oracle)>>> The answer is pear. Remember it, but do not tell the guesser.
   (oracle)>>> /agent guesser
   (guesser)>>> Start the game. The answer is a fruit. Ask at most five yes-or-no questions, then make your guess.

The ``guesser`` sends questions to ``oracle`` through ``message-peer``; the referee replies only yes, no, or uncertain, and keeps the answer in its own conversation. Both agents' messages are persisted under ``.flowing/`` in the project directory, and their fixed ``agent_id`` values let a later run restore each history.

.. note::

   The REPL's progress display only renders messages produced by the bound foreground agent's provider (streaming text, thinking, tool calls, and tool results) plus your own input; messages injected dynamically at run time are not rendered through this channel — so PEER messages delivered by ``message-peer`` do not appear automatically. Type ``/messages`` to see the full message list of the current chain (``/messages verbose`` for full content).

Going further
-------------

- **Capability access**: declare built-in tools, MCP servers, or shell commands and HTTP endpoints as tools via ``tools:``; implement custom logic with ``ScriptTool``.
- **Multi-agent**: declare subagent types under ``subagents:`` and the orchestrator routes work using the auto-generated catalog; you can also create subagents programmatically and run them in parallel. ``PeersPlugin`` enables directed message-queue interactions between agents in one Runtime through a ``peers:`` catalog.
- **Intervention**: attach handlers at hook points — intercept a tool call pending approval, audit at turn completion, switch models and retry on provider errors. The built-in ``use_retry`` / ``use_compact`` are implemented in exactly this way and can serve as references.
- **Run modes**: eight subcommands — REPL, one-shot CLI, plain HTTP API, a built-in web frontend, CI smoke tests, and more — run the same project unchanged in any form.
- **Embedding**: the host application holds the Runtime returned by ``launch()``; input goes through ``query`` / ``message`` / ``steer``, output through hook subscriptions (streaming output, completion notices, call interception).

Built-in plugins and composables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Plugins and composables are the two reusable units of extension, both shipped with the package and enabled on demand. A plugin is installed with ``runtime.install()`` and then enabled per agent by calling the matching ``use_xxx(self)`` in ``setup()``. A composable is an ordinary function ``use_xxx(agent, ...)`` that injects one piece of policy into a single agent in ``setup()``; composables stack and can be copied and rewritten.

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
     - Rule-based orchestration: subclass ``Workflow`` to write branching, loops, and parallel or sequential steps in Python, then start it by path with the ``run-workflow`` tool.
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
