# 示例：4-2 声明式工具封装

本例不编写 Flowing 工具子类，而是声明三种工具：运行测试的 CLI 封装、
查询服务状态的 GET 请求，以及创建订单的 POST 请求。本地 HTTP 服务提供
可预测的响应。

## 运行

将本文内联内容按相对文件名保存。通过环境变量设置
`DEEPSEEK_API_KEY`；Provider 配置只含占位符。在一个终端启动本地服务并
保持运行：

~~~console
$ python tools/http_demo_server.py 8641
~~~

在另一个终端的同一项目根目录启动 Agent：

~~~console
$ uv run flowing repl .
~~~

发送下方第一段输入。要运行刻意失败的测试演示，可再启动一个 REPL 并发送
第二段输入。测试非零退出码会作为工具输出返回，不代表框架运行失败。

## 运行时入口与模型配置

将 Python 代码保存为 `main.py`，将 YAML 配置分别保存为所示文件：

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

## Agent 提示词

保存为 `root.fya`：

~~~yaml
description: 零代码工具演示助手：跑测试 + 调本地 REST API。
model_tag: default
tools:
  - ./tools/run-tests
  - ./tools/status-get
  - ./tools/order-create
---
$system_prompt:
你是演示助手。项目根目录绝对路径：{{ env.PWD }}。
用户要求跑测试时用 run-tests；查服务器状态用 status-get；下单用
order-create。工具返回的原始字段如实引用，回答控制在两句话以内。
~~~

## 声明式工具定义

将每段 YAML 保存为标题所列的相对文件名。

### `tools/run-tests/TOOL.fya`

~~~yaml
name: run-tests
type: cli
description: 运行项目测试套件，返回退出码与输出。
command: cd {{ working_dir }} && python -m pytest {{ test_path }}
args:
  working_dir: {type: string, description: 项目根目录的绝对路径}
  test_path: {type: string, default: sample/, description: 相对工作目录的测试路径}
~~~

### `tools/status-get/TOOL.fya`

~~~yaml
name: status-get
type: request
description: 查询演示服务器的健康状态。
url: http://127.0.0.1:8641/api/status
method: GET
args:
  verbose: {type: boolean, default: false, description: 是否返回详细字段}
output: {type: object, properties: {status: {type: string}, uptime: {type: number}}}
~~~

### `tools/order-create/TOOL.fya`

~~~yaml
name: order-create
type: request
description: 创建新订单。
url: http://127.0.0.1:8641/api/orders
method: POST
args:
  item: {type: string, description: 商品名}
  count: {type: integer, default: 1, description: 数量}
output: {type: object, properties: {order_id: {type: string}, total: {type: number}}}
~~~

## 本地 HTTP 服务

保存为 `tools/http_demo_server.py`：

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

## 测试样例

保存为 `sample/test_sample.py`。第二个测试被刻意设置为失败：

~~~python
def test_ok():
    assert 1 + 1 == 2


def test_fail():
    assert "演示" == "非演示", "刻意失败：演示非零退出码不是 error"
~~~

## 输入与预期输出

第一段输入：

~~~text
先查服务器状态（verbose），然后下单 2 件“演示商品”，如实报告两次工具的返回。
/exit
~~~

verbose 状态的 HTTP 响应还包含服务器详情，但工具声明的输出 schema 只向工具
结果暴露 `status` 和 `uptime`。订单工具暴露 `order_id: ord-001` 和
`total: 19.8`。具体 uptime 会随耗时变化。

可见答复示例：

~~~text
verbose 状态调用返回 "status": "ok" 和运行时长；订单调用返回 "order_id": "ord-001" 和 "total": 19.8。
~~~

第二段输入：

~~~text
用 run-tests 跑测试（默认路径即可），如实报告结果（包括失败数与退出码）。
/exit
~~~

预期测试结果为 `1 failed, 1 passed`；刻意失败的是 `test_fail`，工具返回
`exit_code: 1`。

~~~text
测试收集了 2 项，结果为 1 failed, 1 passed。test_fail 抛出
AssertionError: 刻意失败：演示非零退出码不是 error。
exit_code: 1
~~~
