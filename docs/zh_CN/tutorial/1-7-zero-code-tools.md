# 1-7 · 零代码工具：cli 与 request

## 前置阅读

[0-2 使用内置工具](0-2-use-builtin-tools.md)（工具声明与使用）、[1-6 MCP
工具](1-6-mcp-tools.md)（`type:` 声明的组代理形态）。本篇组合本地 REST API
与 CLI 工具。完整声明、服务端、测试数据、提示词、输入和预期输出
都列在下文。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| cli 型工具 | `type: cli`：Jinja2 命令模板 + shell 执行，插入值自动 `shlex` 转义 |
| request 型工具 | `type: request`：零代码 HTTP/HTTPS 调用，参数自动映射 body/query |
| URL 两阶段模板 | `{{ env.X }}` 装配期渲染、路径参数 `{{ arg }}` 执行期渲染的两段式 URL 模板 |
| `expected_status` | 视为成功的状态码集合（默认 `[200, 201]`）；之外 → error 结果 |
| `output` 字段提取 | request 工具按声明的 schema properties 从 JSON 响应提取字段（无关字段忽略） |

## 目标

掌握另两型零代码工具：把 shell 命令和 HTTP API 声明为工具，无需用 Python
实现工具本身。本文以内联的 Python 代码提供本地演示服务器和测试夹具。

## 正文

### cli：把命令声明为工具

```yaml
# tools/run-tests/TOOL.fya
name: run-tests
type: cli
description: 运行项目测试套件，返回退出码与输出。
command: cd {{ working_dir }} && python -m pytest {{ test_path }}
args:
  working_dir: {type: string, default: ".", description: 相对于 Agent 工作目录的项目根目录}
  test_path: {type: string, default: sample/, description: 测试路径（相对工作目录）}
```

要点：

- **Jinja2 命令模板**：`{{ arg }}` 在执行期由 LLM 传入值填充；
- **插入值自动 `shlex.quote` 转义**——LLM 给的值不会逃逸成 shell 注入；
  确需原始拼接写 `{{ x | raw }}`（每次命中框架告警）；
- **非零退出码 ≠ error**：返回恒为 `{exit_code, stdout, stderr}` 字典——
  退出码是正常输出数据（LLM 可据实汇报失败），只有进程无法启动才 error
  （主线示例演示 2 实证）；
- `args:` 必填（cli 没有自动推断来源）；`shell:` 五选一（`sh` 默认 /
  `bash` / `ps` / `powershell` / `cmd`）。

### request：把 HTTP API 声明为工具

```yaml
# tools/order-create/TOOL.fya
name: order-create
type: request
description: 创建新订单。
url: http://127.0.0.1:8641/api/orders
method: POST
args:
  item: {type: string, description: 商品名}
  count: {type: integer, default: 1, description: 数量}
output: {type: object, properties: {order_id: {type: string}, total: {type: number}}}
```

要点：

- **URL 两阶段模板**：`{{ env.X }}` 装配期渲染（凭证，缺失 fail fast——
  同 1-6）；路径参数 `{{ arg }}` 执行期渲染，并自动从 body/query 排除；
- **参数去向**：POST / PUT / PATCH → JSON body；GET / DELETE → query
  string；`body:` / `query:` 显式声明优先于按 method 的默认；
- **`auth` 语法糖**（`basic` / `bearer` / `api_key`）与 `headers` 冲突时
  auth 优先；
- **状态码不在 `expected_status`**（默认 `[200, 201]`）→ error 结果；
- **`output` 字段提取**：声明了 `output` 时按 schema properties 提取字段
  （无关字段忽略，响应非 JSON 回退文本）。

### 声明的引用方式

cli / request 声明放在 `tools/<名>/TOOL.fya`（目录形态），Agent 的
`tools:` 用**路径形态**引用（`- ./tools/run-tests`）；`builtin::` 裸名那套
是出厂工具的命名空间省略规则，不适用于项目自己的声明文件（glob 一把声明
全集是 5-1 的话题）。

## 本篇不覆盖

- 路径参数的 AST 提取等实现细节；
- `overrides:` / 别名等绑定层覆写——4-4；
- MCP / cli / request 的实例级差异（每声明一实例）——4-5。

## 主线示例

**演示 1：request 工具调本地 REST API**（先按下方完整材料启动服务端）：

```console
$ DEEPSEEK_API_KEY="<your API key>" uv run flowing repl .
(agent-main)>>> 先查服务器状态（verbose），然后下单 2 件“演示商品”，如实报告两次工具的返回。
[tool_call] status-get {"verbose": true}
[tool_call] order-create {"item": "演示商品", "count": 2}
[tool:completed] status-get -> {"status": "ok", "uptime": <运行秒数>}
[tool:completed] order-create -> {"order_id": "ord-001", "total": 19.8}
服务器状态为 ok；下单结果是 {"order_id":"ord-001","total":19.8}。
(agent-main)>>> /exit
```

读这段会话：两个 request 工具并行调用、参数完整可见（`verbose` 开关、
商品名与数量），`order-create` 的 POST 参数自动
进 JSON body、服务端落库后返回的 `order_id` / `total` 被如实引用——
全部零代码，只有两份 `TOOL.fya` 声明。

**演示 2：cli 工具与非零退出码**（完整测试用例见下文）：

```console
$ DEEPSEEK_API_KEY="<your API key>" uv run flowing repl .
(agent-main)>>> 用 run-tests 跑测试（默认路径即可），如实报告结果（包括失败数与退出码）。
[tool_call] run-tests {"working_dir": ".", "test_path": "sample/"}
[tool:completed] run-tests ->
pytest 摘要：1 failed, 1 passed。退出码：1。失败项为 `sample/test_sample.py::test_fail`，错误为 `AssertionError: 刻意失败：演示非零退出码不是 error`。
(agent-main)>>> /exit
```

`exit_code=1` 是 **completed 结果**（不是 `[tool:error]`）——失败明细
照常回到回合，LLM 据实汇报。这正是“非零退出码 ≠ error”的实证。

## 完整示例材料

在已安装 Flowing 的项目中按相对文件名创建以下文件。安装 pytest 以运行 CLI 示例：

```console
$ uv add --dev pytest
```

REST 服务端只监听本机回环地址。CLI 工具使用
`working_dir: "."`，所有路径都相对于项目根目录，无需本机绝对路径。

`main.py`：

```python
from flowing import Runtime

async def main() -> Runtime:
    runtime = Runtime()
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

`root.fya`：

```yaml
description: 零代码工具演示助手：跑测试并调用本地 REST API。
model_tag: default
tools:
  - ./tools/run-tests
  - ./tools/status-get
  - ./tools/order-create
---
$system_prompt:
你是演示助手。跑测试时用 run-tests 并传 working_dir="."；查服务器状态时用
status-get；下单时用 order-create。如实引用工具返回的数据，回答不超过两句。
汇报测试时包含失败数、通过数和退出码。
```

`tools/run-tests/TOOL.fya`：

```yaml
name: run-tests
type: cli
description: 运行示例测试套件并返回退出码与输出。
command: cd {{ working_dir }} && python -m pytest {{ test_path }}
args:
  working_dir: {type: string, default: ".", description: 相对于 Agent 工作目录的项目根目录}
  test_path: {type: string, default: sample/, description: 相对于项目根目录的测试路径}
```

`tools/status-get/TOOL.fya`：

```yaml
name: status-get
type: request
description: 查询演示服务器健康状态。
url: http://127.0.0.1:8641/api/status
method: GET
args:
  verbose: {type: boolean, default: false, description: 是否返回详细字段}
output: {type: object, properties: {status: {type: string}, uptime: {type: number}}}
```

`tools/order-create/TOOL.fya`：

```yaml
name: order-create
type: request
description: 创建新订单。
url: http://127.0.0.1:8641/api/orders
method: POST
args:
  item: {type: string, description: 商品名}
  count: {type: integer, default: 1, description: 数量}
output: {type: object, properties: {order_id: {type: string}, total: {type: number}}}
```

`tools/http_demo_server.py`：

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

`sample/test_sample.py`：

```python
def test_ok():
    assert 1 + 1 == 2

def test_fail():
    assert "演示" == "非演示", "刻意失败：演示非零退出码不是 error"
```

在一个终端启动本地服务器：

```console
$ uv run python tools/http_demo_server.py 8641
```

在同一项目根目录的另一终端启动 REPL，并输入上面的状态查询和下单请求。
状态 uptime 随运行时间变化；首次下单的 id 为 `ord-001`，总价为 `19.8`。
测试示例预期一项通过、一项刻意失败，退出码为 `1`。

## 小结

1. cli 型：Jinja2 命令模板 + 自动转义，返回 exit_code/stdout/stderr 三字段；
2. 非零退出码是正常输出数据，不是 error——失败明细照常回到回合；
3. request 型：URL 两阶段模板、参数按 method 自动映射 body/query、
   `expected_status` 之外才 error、`output` 按 schema 提取字段；
4. 项目自己的工具声明放 `tools/<名>/TOOL.fya`，`tools:` 路径形态引用。
