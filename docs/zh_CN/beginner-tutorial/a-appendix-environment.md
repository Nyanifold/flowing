# 附录 A · 环境准备

本附录说明运行教程中 Flowing 对话示例所需的 Python 环境、模型服务配置与凭证设置。每篇示例的完整应用代码、输入和代表性行为均在该篇正文中。

## 环境要求

- Python 3.13 或更高版本。
- uv，用于创建虚拟环境、安装依赖并运行 CLI。
- 可用的 Flowing `flowing-agent` 包分发版本。
- 对话示例需要模型服务凭证；代码演示示例如未调用模型则不需要凭证。

下面的项目清单可在一个新建的练习目录中直接使用。它只声明包名，不依赖本机仓库位置。

**`pyproject.toml`**

```toml
[project]
name = "flowing-tutorial-example"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flowing-agent>=0.1.0"]

[tool.uv]
package = false
```

在该目录执行：

```console
uv sync
```

如果配置的 Python 包索引尚未提供 `flowing-agent`，请先通过你使用的包分发渠道安装该包。

## 模型服务配置

示例使用 DeepSeek API。`provider`、模型名称与标签映射是三个独立配置层；API 凭证只从环境变量读取。

**`providers.yaml`**

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

**`models.yaml`**

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

**`model-tags.yaml`**

```yaml
tags:
  default: deepseek-flash
```

在运行对话示例的终端中设置自己的凭证占位值：

```console
export DEEPSEEK_API_KEY=sk-your-key-here
```

将 `sk-your-key-here` 替换为你从服务商取得的凭证。不要把真实凭证复制进文档、代码、配置文件或命令历史。环境变量仅对当前 shell 生效；新终端需要重新设置。

## 执行篇内示例

每篇都给出相对文件名、完整文件内容、应输入的内容、运行命令及代表性输出。将该篇列出的文件创建在同一个练习目录中，然后在该目录运行篇内命令；无需执行 `cd` 到其他目录，也无需另找输入或输出文件。示例输出中的自然语言可能因模型版本、采样和服务端状态而变化；工具调用参数、固定脚本输出与章节要点用于核对流程。

命令行输入可直接通过交互提示符键入。需要一次性提供输入时，使用篇内给出的 here-document 命令；输入文本完整写在命令中，不从外部文件重定向。

## 凭证与运行边界

1. 只使用环境变量传递凭证；不要将凭证写入任何文件或粘贴到示例正文。
2. 模型服务调用可能产生费用，并受服务商的速率与配额限制。
3. 不要向示例提交真实个人数据、访问令牌或不应发送给模型服务的内容。
4. 文档中的模型回复是用于解释控制流的代表性记录，不保证逐字复现。
