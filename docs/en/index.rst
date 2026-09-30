Flowing
=======

**English** | `中文 <../zh_CN/>`__

Flowing is a lightweight, extensible, descriptive agent runtime framework for complex interactions (Python ≥ 3.13). In Flowing, an agent's entire definition — its role and prompt, the LLM it uses, its tools and subagents, composable extensions, and hook code — lives in a single ``.fya`` file. The framework opens its execution pipeline to extensions at key points, and its runtime can be embedded into any Python host application as an ordinary object. The core does only three things: message flow, error classification, and hook dispatch; policies such as retries, compaction, and approvals are mounted on demand as composables or plugins.

Who it's for
------------

- You want every layer of the framework to be readable, modifiable, and auditable, rather than a black box of policy configuration;
- You need fine-grained control over conversation history — branching off from any message, rewriting or pruning the past, instead of append-only dialogue;
- You need the same capability to present different model views on different agents, with an explicit safety boundary: being registered does not mean the model can see it.

These needs don't point to any single application shape: in Flowing, orchestration logic is plain Python code, with sequencing, branching, and concurrency expressed by the language itself. As a foundational runtime framework, it can be used to build coding, education, e-commerce, or companion agents as well as multi-agent systems, and to run multi-agent interaction experiments.

Highlights
----------

- **Message-driven runtime model**: each agent instance owns a priority message queue and a persistent work loop; user input, model responses, tool results, external events, and subagent receipts are all represented as messages, each driving a turn. ``query()`` waits for the turn result, ``message()`` is fire-and-forget, ``steer()`` redirects an in-flight turn.
- **A message-level tree for history**: conversation history is a forest of messages, not a linear list. ``fork()`` moves a cursor to open a parallel branch while old branches stay intact; history itself supports five surgical operations — insert, branch, remove, update, reparent — all persisted as usual.
- **Three-layer capability description**: the executable object, the LLM-visible declaration, and the agent-level binding evolve independently — the same tool can appear under different names, descriptions, and parameter views on different agents. Built-in tools ship with the runtime, but they must be explicitly declared to become visible to the model.
- **Declarative .fya**: an agent's description, model intent, capability bindings, system prompt, and hook code live in one self-contained file; the compiled output is fully equivalent to a hand-written ``Agent`` subclass.
- **Instance-level hooks**: every instance has its own hook registry, with hook points spanning the lifecycle, turns, messages, model calls, tool execution, and subagents. A handler can rewrite data, asynchronously wait for external confirmation, or raise ``Intercepted`` to hard-block the operation — a natural foundation for human approval gates.
- **Automatic persistence and crash recovery**: messages and state are persisted write-behind; after a crash, replay rebuilds the state. Unpaired tool calls are sealed during recovery, so the model always sees a complete, paired history.
- **Zero-code tool access**: MCP servers (stdio / SSE / HTTP), shell command templates (arguments auto-escaped), and HTTP endpoints can all become tools from a single declaration file.
- **Complete exposure options**: eight subcommands — REPL, one-shot CLI, plain HTTP API, a built-in web frontend, CI smoke tests, and more — run the same project unchanged in any form.

Installation
------------

.. code-block:: bash

   pip install flowing-agent

Quick start
-----------

This example builds a two-agent guessing game: the referee ``oracle`` privately keeps the answer, while the player ``guesser`` asks directed questions and works it out. It shows how one Runtime can host multiple agents, how ``PeersPlugin`` registers Peers tools, and how declarative tool bindings control which tools each agent can see. A peer tool looks up its target agent by ID through the Runtime. The two composables have separate roles: ``use_peers()`` reads the ``peers`` catalog and injects it into the system prompt, while ``use_retry()`` configures model-call retries with the default settings for the referee and up to five retries for the guesser.

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

Going further
-------------

- **Capability access**: declare built-in tools, MCP servers, or shell commands and HTTP endpoints as tools via ``tools:``; implement custom logic with ``ScriptTool``.
- **Multi-agent**: declare subagent types under ``subagents:`` and the orchestrator routes work using the auto-generated catalog; you can also create subagents programmatically and run them in parallel. ``PeersPlugin`` enables directed message-queue interactions between agents in one Runtime through a ``peers:`` catalog.
- **Intervention**: attach handlers at hook points — intercept a tool call pending approval, audit at turn completion, switch models and retry on provider errors. The built-in ``use_retry`` / ``use_compact`` are implemented in exactly this way and can serve as references.
- **Embedding**: the host application holds the Runtime returned by ``launch()``; input goes through ``query`` / ``message`` / ``steer``, output through hook subscriptions (streaming output, completion notices, call interception).

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
