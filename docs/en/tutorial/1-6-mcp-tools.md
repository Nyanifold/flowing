# 1-6 · MCP Tools

## Prerequisites

[0-2 Using Builtin Tools](0-2-use-builtin-tools.md) (`tools:` declarations;
registration ≠ visibility) and [1-4 Hooks Basics](1-4-hooks-basics.md)
(assembly inside `setup()`). This chapter uses a local stdio server and two
server-side tools. The full declarations, server, model configuration, prompt,
input, and expected output are included below.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| MCP | Model Context Protocol — a protocol that integrates a group of tools from an external tool server in a standardized way |
| `type: mcp` | The MCP form of a tool declaration in `.fya`: `command` (local stdio subprocess) and `url` (remote endpoint) are mutually exclusive |
| Group proxy | The nature of an MCP declaration instance: it proxies a group of server-side tools and is not itself executable |
| Synthesized name | After expansion, the registration name of each server-side tool is `<declaration name>--<server tool name>` (e.g. `demo--list-prs`) |
| Fail fast at assembly time | The `{{ env.X }}` templates of `env:` / `headers:` are rendered at declaration loading time; a missing variable raises `FormatError` immediately |

## Goals

Access external MCP capabilities with zero tool code: write one `TOOL.fya`
declaration, launch a local MCP server, and let the agent call server-side
tools by their synthesized names.

## Main text

### TOOL.fya: one declaration = a group proxy for a group of tools

```yaml
# tools/demo/TOOL.fya — MCP tool declaration (local stdio subprocess form)
name: demo
type: mcp
description: Demo GitHub-style operation tool group.
command: uv
# the stdio subprocess uses the agent process working directory as cwd
# (i.e. this project root), so relative paths are enough
args: ["run", "python", "tools/demo_server.py"]
tools: [list-prs, create-issue]   # subset of server tools (demo_server exposes exactly these two)
```

The two sources are mutually exclusive: `command` (stdio process) or `url`
(remote endpoint; a path ending in `/sse` uses SSE, everything else uses
streamable HTTP). Both present → `AmbiguousMcpSourceError`; neither present →
`MissingMcpSourceError`. Credentials are injected via the
`{{ env.X }}` templates of `env:` / `headers:` — **fail fast at assembly
time**: a missing variable raises `FormatError` at declaration loading time
(Main example, demo 2). Credentials are never hardcoded, never enter
messages, and never reach disk.

### Synthesized names and group proxy expansion

An MCP declaration instance is only a **group proxy**: `list_tools()`
connects to the server and pulls the schemas; each server-side tool yields an
independent proxy whose registration name is `<declaration name>--<server
tool name>` (`demo` + `list-prs` → `demo--list-prs`). Server-side tools with
the same name from different MCP sources therefore never collide.

On the agent side, tools are used by synthesized name. The declarative form
is a single line — the `tools:` list of a `.fya` references the group
declaration, and the binding phase expands it into per-tool entries
automatically:

```yaml
# root.fya
tools:
  - ./tools/demo     # group declaration → equivalent to declaring demo--list-prs / demo--create-issue one by one
```

The whole group can also be written as a name glob (`tools: [demo--*]`), and
a single tool inside the group can be written directly by synthesized name
(`tools: [demo--list-prs]`); both forms are expanded on demand by
`ToolRegistry.expand_mcp` (mechanics: chapter 4-5). The group skeleton itself
is not executable (it has no parameter schema) and does not enter the LLM
visible surface.

The `tools:` subset filter (`tools: [list-prs, create-issue]`) decides which
server-side tools enter the expansion; the server-side `outputSchema` is
carried along with the declaration automatically (storage does not validate
it; see chapter 4-5).

## Out of scope

- Error shapes when a group proxy is executed directly, per-call lazy
  connection details, and alignment of `inputSchema` required semantics —
  chapter 4-5;
- `overrides:` patching (sparse patches to description / args) — chapter 4-4;
- Remote SSE / streamable HTTP transport details;
- MCP server features beyond the two local tools shown below.

## Main example

**Demo 1: the agent calls MCP tools** (representative session):

```console
$ uv run flowing repl .
(agent-main)>>> What PRs are open right now? Then create an issue titled "Demo Issue".
[tool_call] demo--list-prs {"state": "open"}
[tool_call] demo--create-issue {"title": "Demo Issue"}
[tool:completed] demo--list-prs -> [{"number": 1, "title": "fix typo", "state": "open"}, {"number": 2, "title": "add feature", "state": "open"}]
[tool:completed] demo--create-issue -> {
  "issue_id": 42,
  "title": "Demo Issue",
  "body": "",
  "url": "https://example.test/issues/42"
}
Two PRs are open — #1 "fix typo" and #2 "add feature" — and I created issue #42 titled "Demo Issue" (https://example.test/issues/42).
(agent-main)>>> /exit
```

Reading this session: the agent calls the two server-side tools in parallel
by synthesized name (each call on its own line, with the arguments of
`create-issue` visible); its JSON-text result (including `issue_id: 42`)
returns to the turn and informs the answer. For the agent, using an MCP tool
feels exactly the same as using a builtin tool — the
difference lies only in declaration and assembly.

**Demo 2: credential templates fail fast at assembly time**. The complete
offline program and its expected output are included below.

```console
$ uv run python demo_env_failfast.py
Missing credentials fail fast at assembly time → FormatError: environment variable referenced by template '{{ env.NO_SUCH_VAR }}' is missing: 'mappingproxy object' has no attribute 'NO_SUCH_VAR'
```

## Complete example materials

Create these files by their relative names in a Flowing-enabled project, set
`DEEPSEEK_API_KEY`, and install the compatible official MCP Python SDK:

```console
$ uv add 'mcp<2'
```

Run commands from the project root. In this example, `create-issue` returns
JSON text; `list-prs` returns structured content with an output schema.

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
description: "MCP demo assistant: look up PRs and create issues via the demo tool group."
model_tag: default
tools:
  - ./tools/demo
---
$system_prompt:
You are a demo assistant. Use demo--list-prs when asked for open PRs and
demo--create-issue when asked to create an issue. Keep the answer to one
sentence.
```

`tools/demo/TOOL.fya`:

```yaml
name: demo
type: mcp
description: Demo GitHub-style operation tool group.
command: uv
args: ["run", "python", "tools/demo_server.py"]
tools: [list-prs, create-issue]
```

`tools/demo_server.py`:

```python
from __future__ import annotations

import sys

from mcp.server.mcpserver import MCPServer

port = int(sys.argv[2]) if len(sys.argv) > 2 else 0
mcp = MCPServer("demo-github")


@mcp.tool(name="create-issue")
def create_issue(title: str, body: str = "") -> dict:
    return {
        "issue_id": 42,
        "title": title,
        "body": body,
        "url": "https://example.test/issues/42",
    }


@mcp.tool(name="list-prs")
def list_prs(state: str = "open") -> list[dict]:
    return [
        {"number": 1, "title": "fix typo", "state": state},
        {"number": 2, "title": "add feature", "state": state},
    ]


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    transport = {"http": "streamable-http"}.get(mode, mode)
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport, host="127.0.0.1", port=port)
```

Run and enter the complete input:

```console
$ uv run flowing repl .
(agent-main)>>> What PRs are open right now? Then create an issue titled "Demo Issue".
```

Expected deterministic tool data: the PR tool returns #1 `fix typo` and #2
`add feature`; the issue tool returns `issue_id: 42`, an empty body, and the
URL `https://example.test/issues/42`. The final natural-language sentence can
vary by model.

The second demonstration uses the complete contents of
`demo_env_failfast.py`:

```python
import asyncio
import pathlib
import tempfile

from flowing.errors import FormatError
from flowing.tool.registry import ToolRegistry

BOGUS = """name: bogus
type: mcp
description: Declaration with a missing credential template.
command: python
args: [\"server.py\"]
env:
  API_KEY: \"{{ env.NO_SUCH_VAR }}\"
"""


async def main() -> None:
    temp_dir = pathlib.Path(tempfile.mkdtemp())
    (temp_dir / "bogus").mkdir()
    (temp_dir / "bogus" / "TOOL.fya").write_text(BOGUS, encoding="utf-8")
    registry = ToolRegistry(project_root=pathlib.Path.cwd())
    try:
        registry.get("./bogus", source_dir=temp_dir)
    except FormatError as exc:
        print(f"Missing credentials fail fast at assembly time → FormatError: {exc}")


asyncio.run(main())
```

Expected output:

```text
Missing credentials fail fast at assembly time → FormatError: environment variable referenced by template '{{ env.NO_SUCH_VAR }}' is missing: 'mappingproxy object' has no attribute 'NO_SUCH_VAR'
```

## Summary

1. A `type: mcp` declaration is a group proxy: `command` and `url` are
   mutually exclusive, and credential templates fail fast at assembly time;
2. `list_tools()` expands server-side tools under synthesized names
   `<declaration name>--<server name>`;
3. The `tools:` list of a `.fya` references the group declaration directly
   (or uses the in-group pattern `demo--*` / a single synthesized name), and
   the binding phase expands and registers them automatically;
4. For the agent, MCP tools and builtin tools feel identical to use.
