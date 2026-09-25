# Example: 4-1 Protocol-based Tool Access

This example connects an agent to a local MCP server through stdio. The server
provides one tool for listing pull requests and one for creating an issue.

## Run

Save the inline contents under the relative filenames shown below. Provide
`DEEPSEEK_API_KEY` through the environment; the configuration uses only an
environment-variable placeholder. From the project root, run:

~~~console
$ uv run flowing repl .
~~~

The MCP server is started as a local subprocess. It does not contact a real
GitHub service.

## Runtime entry

Save as `main.py`:

~~~python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        kwargs: dict = {}
        if user_name is not None:
            kwargs["user_name"] = user_name
        if locale != "zh":
            kwargs["locale"] = locale
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
~~~

## Model configuration

Save these three YAML documents as `providers.yaml`, `models.yaml`, and
`model-tags.yaml`, respectively:

~~~yaml
# providers.yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"

# models.yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash

# model-tags.yaml
tags:
  default: deepseek-flash
~~~

## Agent and MCP tool declaration

Save the following as `root.fya` and `tools/demo/TOOL.fya`:

~~~yaml
# root.fya
description: "MCP demo assistant: check PRs and create Issues via the demo tool group."
model_tag: default
tools:
  - ./tools/demo
---
$system_prompt:
You are a demo assistant. When the user asks for the list of PRs, use
demo--list-prs; when the user asks to create an Issue, use
demo--create-issue. Keep the answer to one sentence.

# tools/demo/TOOL.fya
name: demo
type: mcp
description: A GitHub-style demo tool group.
command: uv
args: ["run", "python", "tools/demo_server.py"]
tools: [list-prs, create-issue]
~~~

## Local MCP server

Save as `tools/demo_server.py`:

~~~python
import sys
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("demo-github")
port = int(sys.argv[2]) if len(sys.argv) > 2 else 0


@mcp.tool(name="create-issue")
def create_issue(title: str, body: str = "") -> dict:
    return {"issue_id": 42, "title": title, "body": body,
            "url": "https://example.test/issues/42"}


@mcp.tool(name="list-prs")
def list_prs(state: str = "open") -> list[dict]:
    return [{"number": 1, "title": "fix typo", "state": state},
            {"number": 2, "title": "add feature", "state": state}]


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    transport = {"http": "streamable-http"}.get(mode, mode)
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport, host="127.0.0.1", port=port)
~~~

## Input and expected output

Input:

~~~text
Which PRs are open right now? Then create an Issue titled "Demo Issue" for me.
/exit
~~~

The server returns two open PRs, numbered 1 and 2, and creates issue 42 with
title `Demo Issue`, an empty body, and URL
`https://example.test/issues/42`. The agent reports both results in one
sentence. These results are determined by the local server implementation.

Example final answer:

~~~text
The open PRs are #1 "fix typo" and #2 "add feature", and I've created Issue #42 titled "Demo Issue" (https://example.test/issues/42).
~~~

## Missing-environment-variable check

This standalone check demonstrates that resolving an absent credential
placeholder raises `FormatError`. Save as `demo_env_failfast.py`:

~~~python
import asyncio
import pathlib
import tempfile

from flowing.errors import FormatError
from flowing.tool.registry import ToolRegistry

declaration = """name: bogus
type: mcp
description: Missing credential example.
command: python
args: ["server.py"]
env:
  API_KEY: "{{ env.NO_SUCH_VAR }}"
"""


async def main() -> None:
    temp = pathlib.Path(tempfile.mkdtemp())
    (temp / "bogus").mkdir()
    (temp / "bogus" / "TOOL.fya").write_text(declaration, encoding="utf-8")
    registry = ToolRegistry(project_root=pathlib.Path.cwd())
    try:
        registry.get("./bogus", source_dir=temp)
    except FormatError as exc:
        print(f"Missing credentials fail fast at assembly time → FormatError: {exc}")


asyncio.run(main())
~~~

Expected result: a `FormatError` identifies the absent `NO_SUCH_VAR`
environment value. This check uses no real credential.

~~~text
Missing credentials fail fast at assembly time → FormatError: environment variable referenced by template '{{ env.NO_SUCH_VAR }}' is missing: 'mappingproxy object' has no attribute 'NO_SUCH_VAR'
~~~
