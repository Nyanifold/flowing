# 6-1 · 暴露方式选择

## 前置阅读

[0-1 第一个智能体](0-1-hello-agent.md)（cli / repl / web / serve 四种
方式的首次运行）。本篇内联完整最小配置、声明式输入、命令与相关输出，
演示其余四个子命令：`test` / `repl-debug` / `compile`（`run` 与
`web` / `serve` 同机制，不重复演示）。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 封闭子命令集 | CLI 的 8 个子命令（run / repl / cli / repl-debug / serve / web / test / compile），8 名字 8 实命令、无别名 |
| `test` | 冒烟子命令：launch + 快照断言 + shutdown，拉起即停（CI 友好） |
| `repl-debug` | repl 的调试扩展版：多 `/eval` / `/watch` 等调试命令（Jinja2 表达式现场求值） |
| `compile` | `.fya` 显式编译为 `.py` 产物（hash 防覆盖闸；产物可被静态分析） |

## 目标

依据接口选择标准，在剩余子命令中做出选择。

## 正文

### 封闭子命令集

| 子命令 | 一句话职责 | 形态 |
|---|---|---|
| `repl` | 用于直接对话与探索的交互式 REPL | 交互 |
| `cli` | 一次性对话：单条 INPUT 投递即退出 | 脚本 / 管道 |
| `repl-debug` | repl + `/eval` `/watch` 等调试命令 | 交互 / 调试 |
| `run` | launch 后长驻进程（`await runtime`） | 服务 |
| `test` | 冒烟：launch + snapshot 断言 + shutdown | CI |
| `serve` | 纯 HTTP API（24 条封闭端点） | 服务 |
| `web` | serve + 内置默认前端 | 服务 |
| `compile` | `.fya` → `.py` 显式编译 | 构建 |

八者共用 `launch(path)`；interfaces 层只做“launch 拿 Runtime”，不解
析配置、不认识插件、不感知 `@`。退出码 0 / 1 / 2 = 正常 / 运行时错误 /
用法错误。

### 选择依据

- **人要交互** → `repl`（开发调试）/ `repl-debug`（要看运行时求值）；
- **脚本 / CI 要一个结果** → `cli`（一条消息）/ `test`（拉起即停）；
- **别的程序要对接** → `serve`（自建 UI）/ `web`（浏览器即用）；
- **宿主进程自己持有 Runtime** → 不用子命令，直接编程嵌入（6-2）；
- **要静态产物** → `compile`（团队约定 / mypy 友好的场景）。

## 本篇不覆盖

- serve 的 24 条端点逐条说明；
- 嵌入宿主编程的细节——6-2；
- WebSocket / SSE 的服务端实现——6-2 的嵌入示例用 HTTP 轮询同源思想。

## 主线示例

```console
$ uv run flowing test .
exit=0                    ← 冒烟：launch + snapshot + shutdown

$ echo "/eval agent.node_id" | uv run flowing repl-debug .
(agent-main)>>>agent-main ← /eval 在 Agent 上下文求 Jinja2 表达式

$ uv run flowing compile .
exit=0
```

读这段留档：`test` 是 CI 的拉起即停；`repl-debug` 的 `/eval` 把
`agent.node_id` 在 Agent 渲染上下文里求值（4-9 的上下文即此入口）；
`compile` 产出的 `.py` 带 hash 防覆盖闸头注释——产物被外部修改后
compile 会拒绝覆盖（`ArtifactModifiedError`）。

**完整内联材料。**

将每个代码块按标题保存到同一个 Flowing 项目中。配置保留环境变量凭证
占位符。编译输入文件位于项目根级；生成产物摘录仅省略与保存位置相关
的元数据。

### Runtime 入口：`main.py`

```python
from flowing import Runtime


async def main(locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs = {} if locale == "zh" else {"locale": locale}
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

### Provider 配置：`providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### 模型配置：`models.yaml`

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### 模型标签：`model-tags.yaml`

```yaml
tags:
  default: deepseek-flash
```

### Agent 定义：`root.fya`

```yaml
description: 接口层演示助手：最小问答。
model_tag: default
---
$system_prompt:
你是简洁的中文助手。
```

### 编译输入：`scratch.fya`

```yaml
description: 待编译的样例 Agent。
model_tag: default
---
$system_prompt:
你是编译样例。
```

### 命令与留档输出

```console
$ uv run flowing test .
exit=0

$ echo "/eval agent.node_id" | uv run flowing repl-debug .
(agent-main)>>>agent-main
(agent-main)>>>
exit=0

$ uv run flowing compile .
exit=0
```

下方展示生成类的相关定义，其中包含可静态分析的描述、系统提示词与模型
标签。

```python
from flowing import Agent, PENDING, Parsable


class ScratchAgent(Agent):
    description = Parsable("待编译的样例 Agent。")
    system_prompt = Parsable("你是编译样例。")
    model_tag = "default"
```

在保存这些内联文件的工作目录运行命令。最后一条命令会生成编译产物。

## 小结

1. 八子命令封闭集，共用 launch，按“谁在用”选型；
2. `test` 拉起即停、`repl-debug` 运行时求值、`compile` 静态产物；
3. serve / web 是程序对接口，嵌入是宿主自己持有（6-2）。
