# 示例：5-1 拆分的动因与成本

本例将检查与文件修改分开：编排者把检查任务委派给只读的 explore-agent，并要求
它如实报告能力边界，而不能声称文件已经创建。

## 运行

将本文内联材料按所示相对文件名保存。通过环境变量设置 DEEPSEEK_API_KEY；
配置只包含占位符。从项目根目录启动：

~~~console
$ uv run flowing repl .
~~~

## 运行时入口与模型配置

保存为 main.py：

~~~python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
~~~

将以下内容分别保存为 providers.yaml、models.yaml 和 model-tags.yaml：

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

## 编排者声明与提示词

保存为 root.fya：

~~~yaml
description: 任务编排者：把查看类任务派给探索智能体，汇总交付。
model_tag: default
tools:
  - subagent-invoke
subagents:
  - explore-agent
---
$system_prompt:
你是任务编排者。项目根目录是 {{ env.PWD }}。
凡是查看、检查或读取类任务，一律调用 subagent-invoke 委派给 explore-agent，
并清楚说明目标。原样转达报告，不得编造结果。如果 explore-agent 表示任务
超出其能力边界，就如实告知用户并解释原因。不要自己读取文件。
~~~

## 内联输入材料

先创建 `notes/` 目录，再将以下完整内容保存为 `notes/Usage.md`：

~~~markdown
# 使用说明

本目录是一个演示用笔记库。

## 安装

需要 Python 3.13 或更高版本。

## 常用命令

- uv sync：安装依赖
- uv run pytest：运行测试

## 注意事项

凭证一律经环境变量持有，不要写进任何文件。
~~~

## 输入与可见结果

输入：

~~~text
请让 explore-agent 汇报笔记集合中的文件，然后请它根据使用说明创建总结.md。如果它不能写文件，就如实说明能力限制，不要声称已创建。
/exit
~~~

可见交互摘录：

~~~text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke：请 explore-agent 检查笔记集合并汇报内容。
[tool result] notes/Usage.md 中包含这份使用说明。
[tool_call] subagent-invoke：请 explore-agent 根据说明创建总结.md。
[tool result] 写入文件超出 explore-agent 的只读能力范围。
~~~

预期最终答复：没有创建文件。Agent 会说明自己可以检查和总结内容，但不能写入
文件。若由具备写入能力的组件创建总结，内容应包括 Python 版本要求、两条常用
命令，以及凭证只通过环境变量提供的规则。

## 可复现边界

只读能力边界是本例的设计要点：拆分任务会产生协调成本。预期结果是如实报告
能力限制，而不是成功写入文件。
