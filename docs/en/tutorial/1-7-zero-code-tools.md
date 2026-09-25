# 1-7 · Zero-Code Tools: cli and request

## Prerequisites

[0-2 Using Builtin Tools](0-2-use-builtin-tools.md) (tool declaration and
usage), [1-6 MCP Tools](1-6-mcp-tools.md) (the group proxy form declared with
`type:`). This chapter combines a local REST API and a CLI tool. The complete
declarations, server, test data, model configuration, prompt, inputs, and
expected outputs are included below.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| cli-type tool | `type: cli`: a Jinja2 command template + shell execution; inserted values are `shlex`-escaped automatically |
| request-type tool | `type: request`: zero-code HTTP/HTTPS calls; parameters map automatically to body/query |
| Two-stage URL template | A two-part URL template: `{{ env.X }}` is rendered at assembly time, path parameters `{{ arg }}` at execution time |
| `expected_status` | The set of status codes considered success (default `[200, 201]`); anything outside it → an error result |
| `output` field extraction | The request tool extracts fields from a JSON response according to the declared schema properties (unrelated fields are ignored) |

## Goals

Master the other two zero-code tool types: declare a shell command and an
HTTP API as tools without implementing those tools in Python. The local
demonstration server and test fixture are included as supporting code.

## Main text

### cli: declaring a command as a tool

```yaml
# tools/run-tests/TOOL.fya
name: run-tests
type: cli
description: Run the project test suite; returns the exit code and output.
command: cd {{ working_dir }} && python -m pytest {{ test_path }}
args:
  working_dir: {type: string, default: ".", description: Project root relative to the agent working directory}
  test_path: {type: string, default: sample/, description: Test path (relative to the working directory)}
```

Key points:

- **Jinja2 command template**: `{{ arg }}` is filled at execution time with
  the values passed by the LLM;
- **Inserted values are `shlex.quote`-escaped automatically** — a value given
  by the LLM cannot break out into shell injection; if raw splicing is truly
  needed, write `{{ x | raw }}` (the framework warns on every hit);
- **A non-zero exit code ≠ error**: the return is always a
  `{exit_code, stdout, stderr}` dictionary — the exit code is normal output
  data (the LLM can report the failure faithfully); only a process that fails
  to start produces an error (demo 2 in the Main example shows this);
- `args:` is required (cli has no source to infer from); `shell:` is one of
  five choices (`sh` by default / `bash` / `ps` / `powershell` / `cmd`).

### request: declaring an HTTP API as a tool

```yaml
# tools/order-create/TOOL.fya
name: order-create
type: request
description: Create a new order.
url: http://127.0.0.1:8641/api/orders
method: POST
args:
  item: {type: string, description: Product name}
  count: {type: integer, default: 1, description: Quantity}
output: {type: object, properties: {order_id: {type: string}, total: {type: number}}}
```

Key points:

- **Two-stage URL template**: `{{ env.X }}` is rendered at assembly time
  (credentials; missing → fail fast — same as 1-6); path parameters
  `{{ arg }}` are rendered at execution time and are automatically excluded
  from body/query;
- **Where parameters go**: POST / PUT / PATCH → JSON body; GET / DELETE →
  query string; explicit `body:` / `query:` declarations take precedence over
  the method-based default;
- **`auth` syntax sugar** (`basic` / `bearer` / `api_key`): when it conflicts
  with `headers`, auth wins;
- **A status code outside `expected_status`** (default `[200, 201]`) → an
  error result;
- **`output` field extraction**: when `output` is declared, fields are
  extracted per the schema properties (unrelated fields ignored; non-JSON
  responses fall back to text).

### How declarations are referenced

cli / request declarations live in `tools/<name>/TOOL.fya` (directory form),
and the agent's `tools:` references them in **path form**
(`- ./tools/run-tests`). The bare-name `builtin::` namespace omission rule
applies to factory builtin tools only; it does not apply to a project's own
declaration files (globbing the whole declaration set is the topic of 5-1).

## Out of scope

- Implementation-specific details such as AST extraction of path parameters;
- Binding-layer overrides such as `overrides:` / aliases — 4-4;
- Instance-level differences among MCP / cli / request (one instance per
  declaration) — 4-5.

## Main example

**Demo 1: a request tool calling a local REST API** (start the server using
the command in the complete materials below):

```console
$ uv run flowing repl .
(agent-main)>>> Check the server status first (verbose), then place an order for 2 "demo product"; report both tool results faithfully.
[tool_call] status-get {"verbose": true}
[tool_call] order-create {"item": "demo product", "count": 2}
[tool:completed] status-get -> {"status": "ok", "uptime": <elapsed seconds>}
[tool:completed] order-create -> {"order_id": "ord-001", "total": 19.8}
The status is ok, and the order result is {"order_id":"ord-001","total":19.8}.
(agent-main)>>> /exit
```

Reading this session: the two request tools are called in parallel with fully
visible parameters (the `verbose` switch, the product name, and the
quantity); the POST parameters of `order-create` automatically go into the
JSON body, and the `order_id` / `total` returned by the server after
persisting the order are quoted faithfully — all zero-code, with only two
`TOOL.fya` declarations.

**Demo 2: a cli tool and a non-zero exit code** (the complete sample test is
included below):

```console
$ uv run flowing repl .
(agent-main)>>> Use run-tests to run the tests (the default path is fine) and report the results faithfully, including the number of failures and the exit code.
[tool_call] run-tests {"working_dir": ".", "test_path": "sample/"}
[tool:completed] run-tests ->
pytest summary: 1 failed, 1 passed. Exit code: 1. The failure is `sample/test_sample.py::test_fail` (`AssertionError: Deliberate failure: a non-zero exit code is not an error`).
(agent-main)>>> /exit
```

`exit_code=1` is a **completed result** (not `[tool:error]`) — the failure
details still return to the turn, and the LLM reports them faithfully. This
is the empirical proof that a non-zero exit code is not an error.

## Complete example materials

Create these files by their relative names in a Flowing-enabled project and
set `DEEPSEEK_API_KEY` in the environment. Install pytest for the CLI demo:

```console
$ uv add --dev pytest
```

The REST server binds only to
loopback. The CLI tool uses `working_dir: "."`, so all paths are relative to
the project root and no machine-specific path is needed.

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
description: "Zero-code tools demo assistant: run tests and call a local REST API."
model_tag: default
tools:
  - ./tools/run-tests
  - ./tools/status-get
  - ./tools/order-create
---
$system_prompt:
You are the demo assistant. For tests, call run-tests with working_dir=".";
for server status, use status-get; for orders, use order-create. Quote returned
tool data accurately and answer in no more than two sentences.
```

`tools/run-tests/TOOL.fya`:

```yaml
name: run-tests
type: cli
description: Run the sample test suite and return the exit code and output.
command: cd {{ working_dir }} && python -m pytest {{ test_path }}
args:
  working_dir: {type: string, default: ".", description: Project root relative to the agent working directory}
  test_path: {type: string, default: sample/, description: Test path relative to the project root}
```

`tools/status-get/TOOL.fya`:

```yaml
name: status-get
type: request
description: Query the demo server's health status.
url: http://127.0.0.1:8641/api/status
method: GET
args:
  verbose: {type: boolean, default: false, description: Whether to return detailed fields}
output: {type: object, properties: {status: {type: string}, uptime: {type: number}}}
```

`tools/order-create/TOOL.fya`:

```yaml
name: order-create
type: request
description: Create a new order.
url: http://127.0.0.1:8641/api/orders
method: POST
args:
  item: {type: string, description: Product name}
  count: {type: integer, default: 1, description: Quantity}
output: {type: object, properties: {order_id: {type: string}, total: {type: number}}}
```

`tools/http_demo_server.py`:

```python
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

started = time.time()
orders: dict[str, dict] = {}


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/api/status"):
            payload = {"status": "ok", "uptime": round(time.time() - started, 1)}
            if "verbose=true" in self.path:
                payload["detail"] = {"orders": len(orders), "version": "1.7-demo"}
            self._send(payload)
        elif self.path.startswith("/api/orders/"):
            order_id = self.path.rsplit("/", 1)[-1]
            if order_id in orders:
                self._send(orders[order_id])
            else:
                self._send({"error": "not found"}, 404)
        else:
            self._send({"error": "unknown path"}, 404)

    def do_POST(self):
        if self.path != "/api/orders":
            self._send({"error": "unknown path"}, 404)
            return
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        order_id = f"ord-{len(orders) + 1:03d}"
        orders[order_id] = {
            "order_id": order_id,
            "item": data.get("item"),
            "count": data.get("count", 1),
            "total": data.get("count", 1) * 9.9,
        }
        self._send(orders[order_id], 201)


port = int(sys.argv[1]) if len(sys.argv) > 1 else 8641
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
```

`sample/test_sample.py`:

```python
def test_ok():
    assert 1 + 1 == 2


def test_fail():
    assert "demo" == "non-demo", "Deliberate failure: a non-zero exit code is not an error"
```

Start the local server in one terminal:

```console
$ uv run python tools/http_demo_server.py 8641
```

In another terminal at the same project root, run the REPL and enter the
request shown above. The status uptime varies; the first order has id
`ord-001` and total `19.8`. The test demonstration returns one passing test,
one intentional failure, and exit code `1`.

## Summary

1. cli type: Jinja2 command template + automatic escaping; returns the three
   fields exit_code/stdout/stderr;
2. A non-zero exit code is normal output data, not an error — the failure
   details still return to the turn;
3. request type: two-stage URL template, parameters map automatically to
   body/query by method, only status codes outside `expected_status`
   produce an error, `output` extracts fields per schema;
4. A project's own tool declarations go in `tools/<name>/TOOL.fya`, referenced
   in path form in `tools:`.
