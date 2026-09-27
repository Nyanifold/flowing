# Example: 4-2 Declarative Tool Wrappers

This example declares three tools without writing a Flowing tool subclass:
a CLI wrapper for tests, a GET request for server status, and a POST request
for creating an order. A local HTTP server supplies deterministic responses.

## Run

Save the inline files under their relative names. Set `DEEPSEEK_API_KEY` in
the environment; the provider configuration below contains only a placeholder.
Start the local server and keep it running in one terminal:

~~~console
$ python tools/http_demo_server.py 8641
~~~

In another terminal, from the same project root, start the agent:

~~~console
$ uv run flowing repl .
~~~

Send the first input shown below. To run the deliberate test-failure example,
start another REPL and send the second input. A non-zero test exit code is
reported as tool output; it is not a framework failure.

## Runtime entry and model configuration

Save the Python block as `main.py` and the YAML block as the three named
configuration files:

~~~python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
~~~

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

## Agent prompt

Save as `root.fya`:

~~~yaml
description: "Zero-code tool demo assistant: run tests + call the local REST API."
model_tag: default
tools:
  - ./tools/run-tests
  - ./tools/status-get
  - ./tools/order-create
---
$system_prompt:
You are a demo assistant. Absolute path of the project root: {{ env.PWD }}.
When the user asks to run tests, use run-tests; to check the server status, use status-get; to place an order, use order-create. Quote the raw fields returned by the tools exactly, and keep the answer within two sentences.
~~~

## Declarative tool definitions

Save each YAML document under the relative filename in its heading.

### `tools/run-tests/TOOL.fya`

~~~yaml
name: run-tests
type: cli
description: Run the project test suite and return the exit code plus output.
command: cd {{ working_dir }} && python -m pytest {{ test_path }}
args:
  working_dir: {type: string, description: absolute path of the project root}
  test_path: {type: string, default: sample/, description: test path relative to the working directory}
~~~

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

## Local HTTP server

Save as `tools/http_demo_server.py`:

~~~python
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

started = time.time()
orders: dict[str, dict] = {}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
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
            self.send_json(payload)
        elif self.path.startswith("/api/orders/"):
            order_id = self.path.rsplit("/", 1)[-1]
            self.send_json(orders.get(order_id, {"error": "not found"}),
                           200 if order_id in orders else 404)
        else:
            self.send_json({"error": "unknown path"}, 404)

    def do_POST(self):
        if self.path != "/api/orders":
            self.send_json({"error": "unknown path"}, 404)
            return
        data = json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0))) or b"{}")
        order_id = f"ord-{len(orders) + 1:03d}"
        count = data.get("count", 1)
        orders[order_id] = {
            "order_id": order_id, "item": data.get("item"),
            "count": count, "total": count * 9.9,
        }
        self.send_json(orders[order_id], 201)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8641
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
~~~

## Test fixture

Save as `sample/test_sample.py`. The second test intentionally fails:

~~~python
def test_ok():
    assert 1 + 1 == 2


def test_fail():
    assert "demo" == "not-demo", "Deliberate failure: a non-zero exit code is not an error"
~~~

## Inputs and expected outputs

First input:

~~~text
First check the server status (verbose), then place an order for 2 "demo items", and report exactly what each tool returned.
/exit
~~~

The HTTP response for verbose status also contains server details, but the
declared output schema exposes only `status` and `uptime` to the tool result.
The order tool exposes `order_id: ord-001` and `total: 19.8`. The exact
uptime changes with elapsed time.

Example visible answer:

~~~text
The verbose status call returned "status": "ok" and an elapsed uptime; the order call returned "order_id": "ord-001" and "total": 19.8.
~~~

Second input:

~~~text
Use run-tests to run the tests (the default path is fine) and report the results exactly (including the failure count and the exit code).
/exit
~~~

Expected test result: `1 failed, 1 passed`; the deliberate assertion failure
is in `test_fail`, and the tool reports `exit_code: 1`.

~~~text
The suite collected 2 items and reported 1 failed, 1 passed. test_fail raised
AssertionError: Deliberate failure: a non-zero exit code is not an error.
exit_code: 1
~~~
