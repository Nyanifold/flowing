# 1-8 · Runtime Directory and Agent Directory

## Prerequisites

[0-1 First Agent](0-1-hello-agent.md) (`Runtime(persist_dir="@/.flowing")`,
fixed `agent_id` for idempotent mounting). This chapter uses the minimal hello
shape and includes its complete configuration, prompt, inputs, and observable
directory output below.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| persistence root (persist_dir) | Runtime's persistence root directory; defaults to `<cwd>/.flowing` and is created lazily on the first persistence action |
| agent directory | One session directory per Agent (`persist_dir/<agent_id>/`), with a fixed agent_id mapping to a fixed directory |
| identity four keys | The four fields of `meta.json`: `agent_type` / `parent_agent_id` / `created_at` / `args` — the minimal record of an Agent's identity |
| runtime-level state | `core.jsonl` / `default.jsonl` directly under the persistence root: Runtime's own global state namespaces (mechanics in 4-2) |

## Goals

Know where things land: after running a flowing sub-project, what appears on
disk, what each piece is for, and the simplest operations move of all —
deleting the directory means a fresh start.

## Main text

### Persistence root: the default behavior is good enough

`Runtime(persist_dir=...)` sets the persistence root; **by default it is
`<cwd>/.flowing`**, created lazily on the first persistence action — a run
with zero persistence writes nothing and leaves no trace in the working
directory. You can run without configuring anything: the default behavior is
"persist whenever persistence is possible."

### Agent directory: fixed id → fixed directory

Each Agent gets one session directory (`persist_dir/<agent_id>/`). The fixed
`agent_id="agent-main"` from 0-1 means a fixed directory: after a restart,
idempotent mounting recovers the on-disk record of "the same Agent."

### Directory contents at a glance (surface)

After one conversation (see the main example), the directory layout is:

```
.flowing/
├── core.jsonl              # Runtime-level core state (agent pool registry, etc.; 4-1 / 4-2)
└── agent-main/             # session directory of the root Agent
    ├── meta.json           # identity four keys: agent_type / parent_agent_id / created_at / args
    ├── tree.jsonl          # message tree (one message per line; 4-1 expands this)
    ├── core.jsonl          # Agent-level core state (head cursor, child_ids)
    └── state.jsonl         # state bag (4-2)
```

This chapter only requires knowing the names and rough responsibilities;
field-level layout, write-behind, and recovery semantics are the subject of
4-1.

### Deleting the directory means a fresh start

`.flowing/` carries the entire run history: delete it, and the world in the
process's eyes returns to its initial state (run 2 in the main example proves
this — `/messages` shows only the new session's one question and one answer).
The converse also holds: while the directory exists, the memory exists.

## Out of scope

- Field-level layout of the four files, the write-behind persistence
  mechanism, and the recovery pipeline — 4-1;
- State namespaces and self-registration — 4-2;
- Torn last lines / tombstones / compaction timing — 6-3, on demand;
- Directory operations and cleanup (archive / orphans) — 6-3.

## Main example

**run 1**: have one conversation, then inspect the directory:

```console
$ uv run flowing repl .
(agent-main)>>> Introduce yourself in one sentence.
[thinking] (reasoning trace omitted)
I am a concise English-speaking assistant here to help answer your questions and assist with tasks.
(agent-main)>>> /exit

$ find .flowing -type f | sort
.flowing/agent-main/core.jsonl
.flowing/agent-main/meta.json
.flowing/agent-main/state.jsonl
.flowing/agent-main/tree.jsonl
.flowing/core.jsonl

$ cat .flowing/agent-main/meta.json
{
  "agent_type": "@/root.fya",
  "parent_agent_id": "runtime-0",
  "created_at": "<run-generated timestamp>",
  "args": {}
}
```

`meta.json` is exactly the identity four keys: `agent_type` records the source
declaration (`@/root.fya`), `parent_agent_id` is `"runtime-0"` (the root's
parent node is the Runtime — the multi-agent topology is a tree, expanded in
3-1), and `args` holds the creation kwargs (1-3 stated that they must be
"JSON-serializable and part of the identity"; here it is, persisted on disk).

The post-reset check below renames the old state directory to a backup, then
confirms that a new run creates a separate, fresh `.flowing/` directory.

**run 2**: move the old directory aside and start over:

```console
$ mv .flowing .flowing.saved
$ uv run flowing repl .
(agent-main)>>> Introduce yourself in one sentence.
[thinking] (reasoning trace omitted)
I'm a concise English-speaking AI assistant here to help you with questions and tasks.
(agent-main)>>> /messages
1  user      Introduce yourself in one sentence.
2  provider  I'm a concise English-speaking AI assistant here to help you with questions and tasks.
(agent-main)>>> /exit
```

The `/messages` chain contains only this session's one question and one
answer — the previous session's history disappeared together with the
directory.

## Complete example materials

Create these relative files in a Flowing-enabled project and set
`DEEPSEEK_API_KEY` in the environment. Run every command from the project root;
no directory-change command is needed.

`main.py`:

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

`providers.yaml`:

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

`models.yaml`:

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

`model-tags.yaml`:

```yaml
tags:
  default: deepseek-flash
```

`root.fya`:

```yaml
description: "Directory-observation assistant: minimal Q&A."
model_tag: default
---
$system_prompt:
You are a concise English-speaking assistant. Keep your answers within
three sentences.
```

Run 1 input:

```text
Introduce yourself in one sentence.
/exit
```

Run 1 commands and representative output:

```console
$ uv run flowing repl .
(agent-main)>>> Introduce yourself in one sentence.
I am a concise English-speaking assistant here to help answer your questions and assist with tasks.
(agent-main)>>> /exit
$ find .flowing -type f | sort
.flowing/agent-main/core.jsonl
.flowing/agent-main/meta.json
.flowing/agent-main/state.jsonl
.flowing/agent-main/tree.jsonl
.flowing/core.jsonl
$ cat .flowing/agent-main/meta.json
{
  "agent_type": "@/root.fya",
  "parent_agent_id": "runtime-0",
  "created_at": "<run-generated timestamp>",
  "args": {}
}
```

`created_at` is generated at runtime. The JSONL state files are append-only
runtime data, so their exact message IDs and timestamps vary; the tree above
shows the complete expected file layout.

For a reversible fresh-start demonstration, move the existing directory aside:

```text
Introduce yourself in one sentence.
/messages
/exit
```

```console
$ mv .flowing .flowing.saved
$ uv run flowing repl .
```

The new `.flowing/` contains only the second run's conversation, while the
previous state remains recoverable in `.flowing.saved/`.

## Summary

1. The default persistence root is `<cwd>/.flowing`: created lazily, runnable
   with zero configuration;
2. A fixed agent_id maps to a fixed agent directory; while the directory
   exists, the memory exists;
3. The four files at a glance: `meta.json` (identity four keys) /
   `tree.jsonl` (message tree) / two `core.jsonl` + `state.jsonl` (state, 4-2);
4. Renaming `.flowing/` preserves a recoverable backup while allowing a fresh run.
