# 示例：4-1 协议化工具接入

本例通过 stdio 将 Agent 连接到本地 MCP 服务。服务提供两个工具：列出
Pull Request，以及创建 Issue。

## 运行

将本文内联内容按所示相对文件名保存。通过环境变量提供
`DEEPSEEK_API_KEY`；配置中只保留环境变量占位符。从项目根目录运行：

~~~console
$ uv run flowing repl .
~~~

MCP 服务会作为本地子进程启动，不会访问真实 GitHub 服务。

## 运行时入口

保存为 `main.py`：

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

## 模型配置

将以下三个 YAML 文档分别保存为 `providers.yaml`、`models.yaml` 和
`model-tags.yaml`：

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

## Agent 与 MCP 工具声明

将以下内容分别保存为 `root.fya` 和 `tools/demo/TOOL.fya`：

~~~yaml
# root.fya
description: MCP 演示助手：经 demo 工具组查 PR / 建 Issue。
model_tag: default
tools:
  - ./tools/demo
---
$system_prompt:
你是演示助手。用户问 PR 列表时用 demo--list-prs；用户要求创建 Issue 时用
demo--create-issue。回答控制在一句话以内。

# tools/demo/TOOL.fya
name: demo
type: mcp
description: 演示用的 GitHub 风格操作工具组。
command: uv
args: ["run", "python", "tools/demo_server.py"]
tools: [list-prs, create-issue]
~~~

## 本地 MCP 服务

保存为 `tools/demo_server.py`：

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

## 输入与预期输出

输入：

~~~text
现在有哪些 PR？然后帮我创建一个标题为“演示 Issue”的 Issue。
/exit
~~~

服务返回两个开放 PR（编号 1、2），并创建 Issue #42，标题为“演示
Issue”、正文为空，URL 为 `https://example.test/issues/42`。Agent 用一句话
报告两项结果。结果由本地服务实现确定。

示例最终答复：

~~~text
开放 PR 包括 #1“fix typo”和 #2“add feature”；我已创建标题为“演示 Issue”的 Issue #42（https://example.test/issues/42）。
~~~

## 缺失环境变量检查

以下独立检查演示：解析缺失的凭证占位符时会触发 `FormatError`。保存为
`demo_env_failfast.py`：

~~~python
import asyncio
import pathlib
import tempfile

from flowing.errors import FormatError
from flowing.tool.registry import ToolRegistry

declaration = """name: bogus
type: mcp
description: 凭证缺失示例。
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
        print(f"凭证缺失在装配期 fail fast → FormatError: {exc}")


asyncio.run(main())
~~~

预期结果：`FormatError` 会指出环境变量 `NO_SUCH_VAR` 缺失。此检查不包含
真实凭证，也不需要真实凭证。

~~~text
凭证缺失在装配期 fail fast → FormatError: environment variable referenced by template '{{ env.NO_SUCH_VAR }}' is missing: 'mappingproxy object' has no attribute 'NO_SUCH_VAR'
~~~
