# 4-2 · 声明式工具封装：命令模板与 HTTP 请求

> 将下方文件按相对路径重建在同一个项目根目录中。
> 在第二个终端中用 `export DEEPSEEK_API_KEY="<your-deepseek-api-key>"` 设置自己的凭证。
> 在项目根目录启动本地服务：`python tools/http_demo_server.py 8641`。
> 在另一个终端从同一项目根目录启动 Agent：`uv run flowing repl .`。
> 要看 CLI 演示，另开一个 REPL 并输入下方第二段输入。

> 前置：第 1-2 篇“工具调用”。

## 这篇讲什么

本篇把命令行命令与 HTTP 请求声明为工具，不用 Python 编写工具逻辑。重点是命令注入防护，以及退出码和 HTTP 状态码的含义。

## 背景知识

许多有用能力已经以命令或 HTTP 接口的形式存在：`pytest` 用于运行测试，`/api/status` 用于查询服务状态，`POST /api/orders` 用于创建订单。为每种能力单独编写工具函数，会重复参数映射与调用逻辑。声明式封装描述如何调用现有能力，再由 Flowing 按描述执行。

声明式封装适用于参数映射清楚、行为可预期的调用。包含分支业务逻辑的工具仍需编程实现。

## 核心概念

### 映射 REST 调用语义

HTTP 工具声明描述方法、URL、参数位置、认证方式与期望状态码。

- POST、PUT、PATCH 的参数默认进入请求体；GET、DELETE 的参数默认进入查询串。显式声明可以覆盖默认行为。
- 调用方用 `expected_status` 声明哪些响应码视为成功，缺省集合为 200 与 201。超出集合的响应属于调用失败；应用可以据此决定是否重试或换一条路径。
- Basic、bearer 与 API key 认证可以用简写声明，再展开为请求头。

本例使用两个 request 工具。第一个发送 GET 请求，只向工具结果暴露 `status` 与 `uptime` 字段：

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

第二个发送 POST 请求。POST 参数默认进入 JSON 请求体，输出声明只暴露订单 ID 与总价：

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

下面给出完整的运行时入口和 Provider 配置。Provider 凭证使用环境变量占位符。

### `main.py`

~~~python
"""参数化与 setup() 示例的入口。"""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 影响装配的值要在挂载 Agent 之前提供。
    runtime.provide("timezone", "Asia/Shanghai")
    # 只把显式提供的 setup 参数传给 Agent。
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
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

Agent 提示词说明工具用途，并要求用中文作答：

### `root.fya`

~~~yaml
description: "零代码工具演示助手：运行测试并调用本地 REST API。"
model_tag: default
tools:
  - ./tools/run-tests
  - ./tools/status-get
  - ./tools/order-create
---
$system_prompt:
你是中文演示助手。项目根目录为 {{ env.PWD }}。
请用中文回复。用户要求跑测试时使用 run-tests；查服务器状态时使用 status-get；下单时使用 order-create。如实引用工具返回的原始字段，回答不超过两句话。
~~~

### 命令模板与注入防护

命令模板把参数插入命令字符串。如果参数值未经引用，模型提供的值就可能越出参数位置并改写命令。这与 SQL 注入属于同类问题。

~~~bash
# 不安全的模板思路：python -m pytest {{ test_path }}
# 原样传入 "x; rm -rf ~" 这样的值，可能让一个参数变成另一条命令。
~~~

Flowing 默认对插入值做 shell 引用。声明必须显式标记原始拼接，且会产生警告。模板变量采用严格模式：只有被引用的值才会替换，未定义的变量会报错。

下面的 CLI 工具把测试路径作为路径参数传入，并返回进程退出码、标准输出与标准错误。非零退出码是正常工具结果，不会把已完成的进程调用变成工具错误。

### `tools/run-tests/TOOL.fya`

~~~yaml
name: run-tests
type: cli
description: 运行项目测试套件并返回退出码与输出。
command: python -m pytest {{ working_dir }}/{{ test_path }}
args:
  working_dir: {type: string, description: 项目根目录的绝对路径}
  test_path: {type: string, default: sample/, description: 相对工作目录的测试路径}
~~~

### 退出码是结果数据

测试以退出码 1 结束，是测试进程的正常结果。模型需要看到失败详情，才能报告或处理结果。进程无法启动才属于调用失败；进程正常运行后返回非零码，仍然是一次已完成的调用，其退出码和输出会作为结果返回。

下面的本地服务提供 HTTP 响应，只监听回环地址。状态接口的 uptime 随经过时间变化；订单记录只在服务进程存续期间保存在内存中。

### `tools/http_demo_server.py`

~~~python
"""request 工具示例的本地 HTTP 服务；只监听回环地址。"""

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

完整的 fixture 测试包含一个通过用例和一个失败用例，用于展示 CLI 工具如何返回两种进程结果：

### `sample/test_sample.py`

~~~python
def test_ok():
    assert 1 + 1 == 2


def test_fail():
    assert "演示" == "非演示", "刻意失败：演示非零退出码不是错误"
~~~

按篇首信息框启动服务和 Agent。每段输入都在新的 REPL 会话中输入。`/exit` 会关闭当前会话。

第一段输入：

~~~text
先查服务器状态（verbose），然后下单 2 件“演示商品”，如实报告两次工具的返回。
/exit
~~~

记录的可见输出：

~~~text
(agent-main)>>> 先查服务器状态（verbose），然后下单 2 件“演示商品”，如实报告两次工具的返回。
[thinking] (reasoning trace omitted)
[tool_call] status-get {"verbose": true}
[tool_call] order-create {"item": "演示商品", "count": 2}
[tool:completed] status-get ->
[tool:completed] order-create ->
status-get 返回 `{"status": "ok", "uptime": 2.9}`，服务器正常；order-create 返回 `{"order_id": "ord-001", "total": 19.8}`，2 件“演示商品”已下单。
(agent-main)>>>
~~~

响应声明只暴露 `status` 与 `uptime`，不会把 verbose 请求返回的额外 `detail` 对象传给工具结果。订单声明暴露 `order_id` 与 `total`；服务端还会保存商品名与数量。

第二段输入：

~~~text
用 run-tests 跑测试（默认路径即可），如实报告结果（包括失败数与退出码）。
/exit
~~~

记录的可见输出：

~~~text
(agent-main)>>> 用 run-tests 跑测试（默认路径即可），如实报告结果（包括失败数与退出码）。
[thinking] (reasoning trace omitted)
[tool_call] run-tests {"working_dir": "<项目根目录>", "test_path": "sample/"}
[tool:completed] run-tests ->
测试结果：1 failed, 1 passed（0.03s），退出码 exit_code 为 1。失败的是 sample/test_sample.py::test_fail，断言 '演示' == '非演示' 报 AssertionError: 刻意失败：演示非零退出码不是错误。
(agent-main)>>> 
~~~

这些记录是一次捕获，不保证数值或事件顺序可重复。`Handler.do_GET` 根据 `_STARTED` 计算 `uptime`；`Handler.do_POST` 根据当前内存订单数生成 ID，因此 `ord-001` 以全新的服务进程为前提。pytest 显示的 `0.03s` 会变化。两次 HTTP 调用在同一轮中发出，英文记录中的完成事件顺序与这里不同；输入中的“先……然后……”并不能保证工具按顺序完成。需要严格顺序时，应分成不同轮次。模型生成的措辞也可能变化。

### 使用期望状态码集合

下单服务在 `Handler.do_POST` 中返回 HTTP 201。如果订单声明把 `expected_status` 设为 `[200]`，那么 201 不在成功集合中，会被判定为调用失败。将集合设为 `[201]` 才会接受本例服务端的响应。这与将进程退出码当作结果数据的做法相对应：调用方声明哪些状态码属于成功。

## 常见误区

1. **认为参数直接拼进命令是安全的。** 插入值默认经过 shell 引用；原始拼接必须显式声明。
2. **认为非零退出码代表工具调用失败。** 进程可以正常完成并返回非零码及有用输出。
3. **认为所有 HTTP 状态码都应视为成功。** 应声明期望集合；集合之外的响应走失败通道。

## 练习

1. 将 `git log --oneline -n <N>` 声明为工具。写出安全插入 `N` 的命令模板，说明返回值，并给出 Agent 可能使用它的两个场景。
2. 声明 `GET /api/users/{id}`。指定路径参数、期望状态码、404 的处理方式以及认证请求头。
3. 将下单声明的 `expected_status` 改为 `[200]` 并创建订单。本地服务返回 201，说明工具调用会如何分类；再改为 `[201]` 并说明差异。

## 小结

1. 声明式封装描述如何调用现有能力，适合参数映射清楚的调用。
2. 命令模板默认引用插入值；原始拼接必须显式声明。
3. 退出码与 HTTP 状态码是结果值；期望状态码集合决定哪些 HTTP 响应算作成功调用。
4. 有分支业务逻辑的工具应采用编程实现。
