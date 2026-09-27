# 示例：1-1 智能体运行时

本篇展示最小 Runtime 如何创建智能体、装配声明式提示词，并把命令行参数传入智能体。下文完整内联了本示例所需的代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可供 Flowing CLI 使用的 Python 环境。调用默认的 DeepSeek 模型前，请在当前 shell 中将自己的凭证设为 `DEEPSEEK_API_KEY`；若将 `model_tag` 改为 `luna` 试用 OpenRouter 的 GPT-6 Luna，还需设置 `OPENROUTER_API_KEY`。本文不含任何真实凭证。请在一个全新的工作目录中按下文相对文件名创建内容，并从该目录执行命令。

## 运行

启动标准对话：

```console
$ uv run flowing repl .
```

将用户名传入提示词并询问智能体，可运行：

```console
$ uv run flowing repl . --user_name 小明 < repl_input_run2.txt
```

按本文内联的输入回放普通对话，可运行：

```console
$ uv run flowing repl . < repl_input.txt
```

模型生成的具体措辞可能因运行而异。

## 完整内联材料

The following blocks contain the complete project-specific files. The transcript blocks show example runs; model responses may vary.

### `main.py`

```python
"""hello 子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(user_name: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 固定 agent_id → 幂等挂载：第二次启动走恢复，“同一个根回来了”
    root = await runtime.mount("@/root.fya", agent_id="agent-main")
    if user_name is not None:
        # 运行期赋值：模板 {{ user_name }} 在下次组装上下文时现场求值
        root.user_name = user_name
    return runtime
```

### `model-tags.yaml`

```yaml
# 标签 → 模型条目名映射（单值：一个标签只映射一个条目）。
tags:
  default: deepseek-flash
  luna: openrouter-gpt-6-luna
```

### `models.yaml`

```yaml
# 模型条目：一个条目 = 一个具体模型（绑定一个 provider 条目）。
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
openrouter-gpt-6-luna:
  provider: openrouter
  model: openai/gpt-6-luna
  "reasoning.effort": high
```

### `providers.yaml`

```yaml
# provider 条目：一个条目 = 一个 API key 身份。
# {{env.VAR}} 在加载期替换；缺失时替换为空串并告警（warnings.warn），加载不中断。
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
openrouter:
  adapter: openrouter
  base_url: https://openrouter.ai/api/v1
  api_key: "{{env.OPENROUTER_API_KEY}}"
```

未设置 `OPENROUTER_API_KEY` 时，配置加载会告警，`default` 仍可使用 DeepSeek；将
`root.fya` 中的 `model_tag` 改为 `luna` 时需要该密钥。

### `repl_input.txt`

```text
用一句话介绍你自己。
我刚才问了你什么？
/exit
```

### `repl_input_run2.txt`

```text
我叫什么名字？请只回答名字本身。
/exit
```

### `repl_output.txt`

```text
(agent-main)>>>我是简洁的中文助手，乐于用简短回答帮你解决问题。
[thinking] (reasoning trace omitted)
我是简洁的中文助手，乐于用简短回答帮你解决问题。
(agent-main)>>>你刚才问的是：“用一句话介绍你自己。”
[thinking] (reasoning trace omitted)
你刚才问的是：“用一句话介绍你自己。”
(agent-main)>>>
```

### `repl_output_run2.txt`

```text
(agent-main)>>>小明
[thinking] (reasoning trace omitted)
小明
(agent-main)>>>
```

### `root.fya`

```text
description: 最小问答助手
model_tag: default
---
$system_prompt:
你是一个简洁的中文助手，回答控制在三句话以内。{% if user_name %}用户叫做 {{ user_name }}，回答时可以直接以名字相称。{% endif %}
```
