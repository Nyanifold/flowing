# 4-2 · Declarative tool wrappers: command templates and HTTP requests

> Recreate the files shown below in one project root.
> In the second terminal, set your credential with `export DEEPSEEK_API_KEY="<your-deepseek-api-key>"`.
> Start the local server from the project root: `python tools/http_demo_server.py 8641`.
> In a second terminal, start the agent from the same project root: `uv run flowing repl .`.
> To see the CLI example, start another REPL and enter its second input below.

> Prerequisite: Chapter 1-2, “Tool Calling.”

## What this chapter covers

This chapter declares command-line commands and HTTP requests as tools without writing tool logic in Python. It focuses on command-injection protection and the meaning of exit codes and HTTP status codes.

## Background

Many useful capabilities already exist as commands or HTTP endpoints: `pytest` runs tests, `/api/status` reports service state, and `POST /api/orders` creates an order. Implementing a separate tool function for every such capability repeats the same parameter mapping and invocation work. A declarative wrapper describes how to call an existing capability and lets Flowing execute that description.

Declarative wrappers fit calls with clear parameter mappings and predictable behavior. A tool with branching business logic still needs a programmatic implementation.

## Core concepts

### Map REST semantics explicitly

An HTTP tool declaration describes the method, URL, parameter placement, authentication, and expected status codes.

- Parameters map to the request body by default for POST, PUT, and PATCH, and to the query string by default for GET and DELETE. An explicit declaration can override the default.
- `expected_status` declares which response codes count as success. The default set is 200 and 201. A response outside that set is a call failure; the application can then decide whether to retry or choose another route.
- Basic, bearer, and API-key authentication can be declared as shorthand that expands into request headers.

The example uses two request tools. The first sends a GET request and exposes the `status` and `uptime` fields:

### `tools/status-get/TOOL.fya`

~~~yaml
name: status-get
type: request
description: Query the demo server's health status.
url: http://127.0.0.1:8641/api/status
method: GET
args:
  verbose: {type: boolean, default: false, description: whether to return detailed fields}
output: {type: object, properties: {status: {type: string}, uptime: {type: number}}}
~~~

The second sends a POST request. POST arguments go in the JSON request body by default, and the output declaration exposes only the order ID and total:

### `tools/order-create/TOOL.fya`

~~~yaml
name: order-create
type: request
description: Create a new order.
url: http://127.0.0.1:8641/api/orders
method: POST
args:
  item: {type: string, description: item name}
  count: {type: integer, default: 1, description: quantity}
output: {type: object, properties: {order_id: {type: string}, total: {type: number}}}
~~~

The complete runtime entry point and provider configuration follow. The provider credential remains an environment-variable placeholder.

### `main.py`

~~~python
"""Entry point for the parameterization and setup() example."""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "en") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Provide values needed during assembly before mounting the agent.
    runtime.provide("timezone", "Asia/Shanghai")
    # Pass only explicitly supplied setup parameters to the agent.
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "en":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
~~~

### `providers.yaml`

~~~yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
~~~

### `models.yaml`

~~~yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
~~~

### `model-tags.yaml`

~~~yaml
tags:
  default: deepseek-flash
~~~

The agent prompt names each tool’s purpose and asks for an English response:

### `root.fya`

~~~yaml
description: "Zero-code tool demo assistant: run tests and call the local REST API."
model_tag: default
tools:
  - ./tools/run-tests
  - ./tools/status-get
  - ./tools/order-create
---
$system_prompt:
You are an English demo assistant. The project root is {{ env.PWD }}.
Reply in English. When the user asks to run tests, use run-tests; to check the server status, use status-get; to place an order, use order-create. Quote the raw fields returned by the tools exactly, and keep the answer within two sentences.
~~~

### Command templates and injection guarding

A command template inserts parameters into a command string. If a value is concatenated without quoting, a value supplied by the model can break out of its argument position and change the command. This is the same general class of mistake as SQL injection.

~~~bash
# Unsafe template idea: python -m pytest {{ test_path }}
# A raw value such as "x; rm -rf ~" can turn one argument into another command.
~~~

Flowing shell-quotes inserted values by default. A declaration must mark raw concatenation explicitly, which produces a warning. Template variables are strict: only referenced values are substituted, and an undefined variable is an error.

The CLI tool below passes the test path as a path argument and returns the process exit code, standard output, and standard error. A non-zero exit code is normal tool output; it does not turn a completed process into a tool-call error.

### `tools/run-tests/TOOL.fya`

~~~yaml
name: run-tests
type: cli
description: Run the project test suite and return the exit code plus output.
command: python -m pytest {{ working_dir }}/{{ test_path }}
args:
  working_dir: {type: string, description: absolute path of the project root}
  test_path: {type: string, default: sample/, description: test path relative to the working directory}
~~~

### Exit codes are result data

A test failure with exit code 1 is a normal result from the test process. The model needs the failure details to report or address the result. A process that cannot start is a call failure; a process that runs and returns a non-zero code has completed and returns its exit code and output.

The local server below supplies the HTTP responses. It listens only on the loopback interface. The status uptime changes with elapsed time; order records are kept in memory for the lifetime of the server process.

### `tools/http_demo_server.py`

~~~python
"""Local HTTP server for the request-tool example; it listens on loopback only."""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_STARTED = time.time()
_ORDERS: dict[str, dict] = {}


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
            payload = {"status": "ok", "uptime": round(time.time() - _STARTED, 1)}
            if "verbose=true" in self.path:
                payload["detail"] = {"orders": len(_ORDERS), "version": "1.7-demo"}
            self._send(payload)
        elif self.path.startswith("/api/orders/"):
            order_id = self.path.rsplit("/", 1)[-1]
            if order_id in _ORDERS:
                self._send(_ORDERS[order_id])
            else:
                self._send({"error": "not found"}, 404)
        else:
            self._send({"error": "unknown path"}, 404)

    def do_POST(self):
        if self.path == "/api/orders":
            data = json.loads(self.rfile.read(
                int(self.headers.get("Content-Length", 0))) or b"{}")
            order_id = f"ord-{len(_ORDERS) + 1:03d}"
            count = data.get("count", 1)
            _ORDERS[order_id] = {
                "order_id": order_id,
                "item": data.get("item"),
                "count": count,
                "total": count * 9.9,
            }
            self._send(_ORDERS[order_id], 201)
        else:
            self._send({"error": "unknown path"}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8641
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
~~~

The complete fixture test is intentionally split between one passing case and one failing case so the CLI wrapper can demonstrate both process results:

### `sample/test_sample.py`

~~~python
def test_ok():
    assert 1 + 1 == 2


def test_fail():
    assert "demo" == "not-demo", "Deliberate failure: a non-zero exit code is not an error"
~~~

Start the server and agent as described in the info box. Enter each input in a fresh REPL session. `/exit` closes that session.

First input:

~~~text
First check the server status (verbose), then place an order for 2 "demo items", and report exactly what each tool returned.
/exit
~~~

Recorded visible output:

~~~text
(agent-main)>>> First check the server status (verbose), then place an order for 2 "demo items", and report exactly what each tool returned.
[thinking] (reasoning trace omitted)
[tool_call] status-get {"verbose": true}
[tool_call] order-create {"item": "demo items", "count": 2}
[tool:completed] order-create ->
[tool:completed] status-get ->
The verbose status call returned "status": "ok" and "uptime": 10.5, and the order call returned "order_id": "ord-001" and "total": 19.8 for 2 demo items.
(agent-main)>>>
~~~

The response declaration exposes `status` and `uptime`; it omits the extra `detail` object returned by a verbose server request. The order declaration exposes `order_id` and `total`, while the server also stores the item and count.

Second input:

~~~text
Use run-tests to run the tests (the default path is fine) and report the results exactly (including the failure count and the exit code).
/exit
~~~

Recorded visible output:

~~~text
(agent-main)>>> Use run-tests to run the tests (the default path is fine) and report the results exactly (including the failure count and the exit code).
[thinking] (reasoning trace omitted)
[tool_call] run-tests {"working_dir": "<project-root>", "test_path": "sample/"}
[tool:completed] run-tests ->
The suite collected 2 items and reported `1 failed, 1 passed in 0.03s`, with `test_fail` raising `AssertionError: Deliberate failure: a non-zero exit code is not an error`, and the run returned `exit_code: 1`.
(agent-main)>>>
~~~

The transcript is a capture, not a promise that these values or event ordering will repeat. `Handler.do_GET` computes `uptime` from `_STARTED`; `Handler.do_POST` assigns IDs from the current in-memory order count, so `ord-001` assumes a fresh server. The captured `0.03s` pytest duration varies. The two HTTP calls were issued in one turn, and the captured completion events appear in a different order in the Chinese transcript; the wording “First … then” does not guarantee sequential tool completion. Use separate turns when the order of operations matters. Model-generated wording can also vary.

### Use the expected status set

The order server responds with HTTP 201 in `Handler.do_POST`. If the order declaration sets `expected_status: [200]`, that 201 response falls outside the declared success set and is treated as a call failure. Set `[201]` to accept this server response. This distinction is the HTTP counterpart of treating a process exit code as result data: the caller defines which status codes count as success.

## Common misconceptions

1. **Concatenating parameters directly into commands is safe.** Shell quoting is applied by default, and raw concatenation requires an explicit declaration.
2. **A non-zero exit code means that the tool call failed.** A process can complete normally and return a non-zero code with useful output.
3. **Every HTTP status code should count as success.** Declare the expected set; a response outside that set follows the failure channel.

## Exercises

1. Declare `git log --oneline -n <N>` as a tool. Write its command template with `N` inserted safely, describe its return value, and give two situations in which an agent could use it.
2. Declare `GET /api/users/{id}`. Specify the path parameter, expected status codes, the handling of 404, and an authentication header.
3. Change the order declaration to `expected_status: [200]` and place an order. The local server responds with 201, so explain how the tool call is classified. Then change the expected set to `[201]` and explain the difference.

## Summary

1. Declarative wrappers describe how to invoke a capability and fit calls with clear parameter mappings.
2. Command templates quote inserted values by default; raw concatenation must be explicit.
3. Exit codes and HTTP status codes are result values, while the declared expected status set determines which HTTP responses count as successful calls.
4. Tools with branching business logic should use a programmatic implementation.
