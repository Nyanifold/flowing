# 0-0 · 环境安装

## 前置阅读

无（全教程第一篇）。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| flowing 子项目 | 一个用 flowing 编写的智能体应用单元：一个目录 + 入口 `main.py` + 声明式 Agent 定义（`.fya` 文件），详见 0-1 |
| `flowing` CLI | flowing 包安装后获得的命令行工具（封闭子命令集，本篇只用 `--help` 自检） |
| provider | 模型服务的接入方（本篇只负责拿到它的 API key，接入配置见 0-1） |

## 目标

装好 Python 运行环境、flowing 包与模型 API key——0-1 的必备前置。
本篇结束时，你的机器满足两个条件：Python ≥ 3.13，且 `flowing` 命令可用。

## 正文

### 安装 uv 与 Python 环境

flowing 要求 Python ≥ 3.13，并用 uv 管理环境（uv 是 Python 的包与环境
管理器）。安装 uv（已装可跳过）：

```console
$ curl -LsSf https://astral.sh/uv/install.sh | sh
$ uv --version
uv 0.8.11
```

建环境并装依赖：

```console
$ mkdir my-flowing
$ cd my-flowing
$ uv venv --python 3.13
$ uv pip install flowing-agent
```

`flowing-agent` 包同时提供两样东西：`flowing` Python 包（编程接口）与
`flowing` 命令行工具（CLI）。本篇只验证后者；两者的分工在 0-1 展开。

### 准备模型 API key

首次配置全局 Provider 时需要模型服务的 API key。以 DeepSeek 为例，在
DeepSeek 开放平台注册账号并创建 API key。运行需要模型调用的命令时，将凭证
作为环境变量传入；不要把真实值写入项目文件。

### 自检

CLI 是封闭子命令集（`run` / `repl` / `cli` / `repl-debug` / `serve` /
`web` / `test` / `compile`，完整对比见 6-1），没有顶层 `--help` 开关；
逐子命令的 `--help` 可用：

```console
$ uv run flowing repl --help
usage: flowing repl <path> [-f <main file>] [--key value ...]
```

看到用法行即环境就绪。（裸 `flowing` 或 `flowing --help` 会打印用法并
以退出码 2 结束——“用法错误”的CLI 约定，见 6-1。）

## 本篇不覆盖

- 全局 Provider 与 Model 的配置——0-1 首次配置；
- 多 provider 配置与三文件的来源路径优先级——6-3 运维面。

## 主线示例

从空机器到环境就绪的完整命令序列（版本号和安装输出可能因环境而异）：

```console
# 1. 安装 uv（已安装时可跳过）
$ curl -LsSf https://astral.sh/uv/install.sh | sh
$ uv --version
uv 0.8.11

# 2. 创建并进入项目目录，再建 Python 环境
$ mkdir my-flowing
$ cd my-flowing
$ uv venv --python 3.13
$ uv run python --version
Python 3.13.6

# 3. 安装 flowing
$ uv pip install flowing-agent

# 4. 自检：CLI 可用
$ uv run flowing repl --help
usage: flowing repl <path> [-f <main file>] [--key value ...]
```

本篇不创建 Agent 项目；下一篇会在新目录中逐项给出可复现的项目内容。

## 小结

1. 环境由 Python ≥ 3.13、uv 和 `flowing-agent` 组成；
2. 自检用 `flowing repl --help`（封闭子命令集，无顶层 `--help`）。
