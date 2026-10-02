# 1-6 · MCP 工具

## 前置阅读

[0-2 使用内置工具](0-2-use-builtin-tools.md)（`tools:` 声明、注册 ≠ 可见）、
[1-4 钩子基础](1-4-hooks-basics.md)（`setup()` 内做装配）。本篇的示例工程
使用本地 stdio 服务端和两个服务端工具。完整声明、服务端代码、
提示词、输入与预期输出都列在下文。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| MCP | Model Context Protocol——把外部工具服务器的一组工具标准化接入的协议 |
| `type: mcp` | `.fya` 工具声明的 MCP 形态：`command`（本地 stdio 子进程）与 `url`（远程端点）互斥 |
| 组代理 | MCP 声明实例的本质：它代理一组服务端工具，本身不可执行 |
| 合成名 | 展开后每个服务端工具的注册名 = `<声明名>--<server 工具名>`（如 `demo--list-prs`） |
| 装配期 fail fast | `env:` / `headers:` 的 `{{ env.X }}` 在声明加载期渲染，缺失变量立即 `FormatError` |

## 目标

零代码接入外部 MCP 能力：写一个 `TOOL.fya` 声明、拉起本地 MCP server、
让 Agent 按合成名调用服务端工具。

## 正文

### TOOL.fya：一个声明 = 一组工具的组代理

```yaml
# tools/demo/TOOL.fya —— MCP 工具声明（stdio 本地子进程形态）
name: demo
type: mcp
description: 演示用的 GitHub 风格操作工具组。
command: uv
# stdio 子进程以 Agent 进程的工作目录为 cwd（即本工程根），相对路径即可
args: ["run", "python", "tools/demo_server.py"]
tools: [list-prs, create-issue]   # 服务端工具子集（demo_server 恰好暴露这两个）
```

两种来源互斥：`command`（stdio 进程）或 `url`（远程端点，`/sse` 结尾走
SSE、其余走 streamable HTTP）；两者都有 → `AmbiguousMcpSourceError`、
都无 → `MissingMcpSourceError`。凭证经 `env:` / `headers:` 的
`{{ env.X }}` 模板注入——**装配期 fail fast**：缺失变量在声明加载期就抛
`FormatError`（主线示例演示 2），不硬编码、不进消息、不落盘。

### 合成名与组代理展开

MCP 声明实例只是**组代理**：`list_tools()` 连接服务器拉取 schema，每个
服务端工具产一个独立代理，注册名 = `<声明名>--<server 工具名>`
（`demo` + `list-prs` → `demo--list-prs`）。不同 MCP 来源的同名服务端
工具因此天然不撞名。

Agent 侧按合成名使用。声明式写法只剩一行——`.fya` 的 `tools:` 引用
组声明，绑定期自动展开为逐工具条目：

```yaml
# root.fya
tools:
  - ./tools/demo     # 组声明 → 等价于逐个声明 demo--list-prs / demo--create-issue
```

组内全量也可以写成名字 glob（`tools: [demo--*]`）；组内单个工具直接
写合成名（`tools: [demo--list-prs]`）——这两种形态经
`ToolRegistry.expand_mcp` 按需展开（机制见 4-5）。组骨架本身不可执行
（无参数 schema），不进 LLM 可见面。

`tools:` 子集过滤（`tools: [list-prs, create-issue]`）决定哪些服务端工具
进入展开；服务端 `outputSchema` 自动随声明携带（存储不校验，4-5）。

## 本篇不覆盖

- 组代理直接执行的报错形态、每次调用惰性连接的细节、inputSchema
  required 口径对齐——4-5；
- `overrides:` 覆写（description / args 稀疏补丁）——4-4；
- 远程 `url` 形态的 SSE / streamable HTTP 传输细节；
- 下文两种本地工具之外的 MCP 服务端功能。

## 主线示例

**演示 1：Agent 调用 MCP 工具**（示意会话）：

```console
$ DEEPSEEK_API_KEY="<your API key>" uv run flowing repl .
(agent-main)>>> 现在有哪些 PR？然后帮我创建一个标题为“演示 Issue”的 Issue。
[tool_call] demo--list-prs {"state": "open"}
[tool_call] demo--create-issue {"title": "演示 Issue"}
[tool:completed] demo--list-prs -> [{"number": 1, "title": "fix typo", "state": "open"}, {"number": 2, "title": "add feature", "state": "open"}]
[tool:completed] demo--create-issue -> {
  "issue_id": 42,
  "title": "演示 Issue",
  "body": "",
  "url": "https://example.test/issues/42"
}
当前有 2 个 open PR（#1 fix typo、#2 add feature），并已创建“演示 Issue”（#42）。
(agent-main)>>> /exit
```

读这段会话：Agent 经合成名并行调用两个服务端工具（每个调用独立成行，
`create-issue` 的参数可见）；其 JSON 文本结果（包含 `issue_id: 42`）
回到回合并用于组织回答。对 Agent 而言，MCP 工具与内置工具的使用体验
完全一致——差别只在声明与装配。

**演示 2：凭证模板装配期 fail fast**。完整离线程序与预期输出见下文。

```console
$ uv run python demo_env_failfast.py
凭证缺失在装配期 fail fast → FormatError: environment variable referenced by template '{{ env.NO_SUCH_VAR }}' is missing: 'mappingproxy object' has no attribute 'NO_SUCH_VAR'
```

## 完整示例材料

在已安装 Flowing 的项目中按相对文件名创建以下内容，并安装兼容的官方
MCP Python SDK：

```console
$ uv add 'mcp<2'
```

从项目根目录运行命令。本例中 `create-issue` 返回 JSON 文本；`list-prs`
返回带 output schema 的结构化内容。

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
description: MCP 演示助手：经 demo 工具组查 PR / 建 Issue。
model_tag: default
tools:
  - ./tools/demo
---
$system_prompt:
你是演示助手。用户问 PR 列表时用 demo--list-prs；用户要求创建 Issue 时用
demo--create-issue。回答控制在一句话以内。
```

`tools/demo/TOOL.fya`：

```yaml
name: demo
type: mcp
description: 演示用的 GitHub 风格操作工具组。
command: uv
args: ["run", "python", "tools/demo_server.py"]
tools: [list-prs, create-issue]
```

`tools/demo_server.py`：

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

运行并输入：

```console
$ DEEPSEEK_API_KEY="<your API key>" uv run flowing repl .
(agent-main)>>> 现在有哪些 PR？然后帮我创建一个标题为“演示 Issue”的 Issue。
```

确定性的工具数据为：PR 工具返回 #1 `fix typo` 与 #2 `add feature`；Issue
工具返回 `issue_id: 42`、空 body 和
`https://example.test/issues/42`。最终自然语言句子由模型生成，措辞可能变化。

演示 2 的 `demo_env_failfast.py` 完整内容：

```python
import asyncio
import pathlib
import tempfile

from flowing.errors import FormatError
from flowing.tool.registry import ToolRegistry

BOGUS = """name: bogus
type: mcp
description: 凭证模板缺失的声明。
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
        print(f"凭证缺失在装配期 fail fast → FormatError: {exc}")

asyncio.run(main())
```

预期输出：

```text
凭证缺失在装配期 fail fast → FormatError: environment variable referenced by template '{{ env.NO_SUCH_VAR }}' is missing: 'mappingproxy object' has no attribute 'NO_SUCH_VAR'
```

## 小结

1. `type: mcp` 声明 = 组代理：`command` / `url` 互斥，凭证模板装配期 fail fast；
2. `list_tools()` 按合成名 `<声明名>--<server 名>` 展开服务端工具；
3. `.fya` 的 `tools:` 直接引用组声明（或写组内模式 `demo--*` / 单个
   合成名），绑定期自动展开注册；
4. 对 Agent 而言 MCP 工具与内置工具使用体验一致。
