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

Create a directory with five files.

``root.fya``:

.. code-block:: yaml

   description: Minimal Q&A assistant
   model_tag: default
   ---
   $system_prompt:
   You are a concise assistant. Answer in at most three sentences.

``main.py``:

.. code-block:: python

   from flowing import Runtime

   async def main() -> Runtime:
       runtime = Runtime(persist_dir="@/.flowing")
       runtime.set_model_tags("@/model-tags.yaml")
       await runtime.mount("@/root.fya", agent_id="agent-main")
       return runtime

Model access is split into three files, declaring the access identity, the model entries, and the tag mapping:

.. code-block:: yaml

   # providers.yaml
   deepseek:
     adapter: deepseek
     base_url: https://api.deepseek.com
     api_key: "{{env.DEEPSEEK_API_KEY}}"
   openrouter:
     adapter: openrouter
     base_url: https://openrouter.ai/api/v1
     api_key: "{{env.OPENROUTER_API_KEY}}"

.. code-block:: yaml

   # models.yaml
   luna:
     provider: openrouter
     model: openai/gpt-6-luna
     "reasoning.effort": high
   deepseek-flash:
     provider: deepseek
     model: deepseek-v4-flash

.. code-block:: yaml

   # model-tags.yaml
   tags:
     default: luna

Run:

.. code-block:: console

   $ export OPENROUTER_API_KEY='<your key>'
   $ flowing repl .
   (agent-main)>>> Introduce yourself in one sentence.
   I'm a concise assistant, keeping answers to three sentences or fewer.
   (agent-main)>>> /exit

The conversation doesn't vanish on exit: the messages it produced are persisted under ``.flowing/`` in the project directory. A fixed ``agent_id`` means that running ``flowing repl .`` again brings back the same agent with its full history — no recovery code required.

Provider configuration command
-------------------------------

After installing ``flowing-agent``, use the standalone command to manage Provider configuration interactively. It does not start a Runtime or contact a model service::

   $ flowing-config providers add [path]
   $ flowing-config providers list [path]
   $ flowing-config providers delete [path]

``path`` can be a ``providers.yaml`` file or a configuration directory. When omitted, the command asks for a path and defaults to the Runtime priority order: ``FLOWING_PROVIDERS_PATH``, ``FLOWING_CONFIG_HOME/providers.yaml``, then ``~/.flowing/providers.yaml``. The add operation prompts for adapter fields one at a time; ``env.API_KEY`` is saved as ``{{env.API_KEY}}``. The list operation masks credentials while preserving environment-variable template text. Deletion proceeds only after an explicit ``y``. Changes take effect when a new Runtime is constructed.

Going further
-------------

- **Capability access**: declare built-in tools, MCP servers, or shell commands and HTTP endpoints as tools via ``tools:``; implement custom logic with ``ScriptTool``.
- **Multi-agent**: declare subagent types under ``subagents:`` and the orchestrator routes work using the auto-generated catalog; you can also create subagents programmatically and run them in parallel. ``PeersPlugin`` uses a ``peers:`` catalog for directed message-queue interactions between agents in one Runtime.
- **Intervention**: attach handlers at hook points — intercept a tool call pending approval, audit at turn completion, switch models and retry on provider errors. The built-in ``use_retry`` / ``use_compact`` are implemented in exactly this way and can serve as references.
- **Embedding**: the host application holds the Runtime returned by ``launch()``; input goes through ``query`` / ``message`` / ``steer``, output through hook subscriptions (streaming output, completion notices, call interception).

Documentation
-------------

- :doc:`Beginner tutorial <beginner-tutorial/index>`: for readers new to agent systems development;
- :doc:`Detailed tutorial <tutorial/index>`: from quick start to core mechanics and operations;
- :doc:`Concise reference <flowing-ref/index>`: the whole framework in four parts, for quick lookup;
- :doc:`API reference <api>`: the public API organized by module.

Project status
--------------

Current version 0.1.0. The framework core is complete (700+ test cases passing) and the project is in the example-scenarios phase. Breaking changes are still possible during 0.x; every such release ships with a detailed changelog explaining how to migrate.

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
