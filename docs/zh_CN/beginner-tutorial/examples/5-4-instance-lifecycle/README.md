# 示例：5-4 实例生命周期

本例演示命名子 Agent 实例：编排者创建名为 `memo` 的 assistant，让它记住
一个值，再续接同一实例回答追问。

## 运行

将本文内联内容按所示相对文件名保存。通过环境变量设置
`DEEPSEEK_API_KEY`；配置中只含占位符。从项目根目录运行：

~~~console
$ uv run flowing repl .
~~~

## 运行时入口与模型配置

保存为 `main.py`：

~~~python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
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

将以下声明分别保存为 `root.fya` 和
`agents/assistant/agent.fya`：

~~~yaml
# root.fya
description: 编排者：经命名唤起与续接验证子智能体记忆。
model_tag: default
tools:
  - subagent-invoke
subagents:
  - ./agents/assistant
---
$system_prompt:
你是编排者。下属 assistant 有记忆（续接同一实例时记得此前的对话）。
- 用户要求“让 assistant 记住某内容”时：调 subagent-invoke，agent_type 用
  assistant、name 用 "memo"，prompt 里写清要记住的内容，让它复述确认；
- 用户追问 assistant 时：用 resume="memo" 续接同一实例，把它的回答原样
  转达；除非用户明确要求新实例，不要新建。

# agents/assistant/agent.fya
description: 记忆助手：记住用户告知的内容，被问及时复述。
model_tag: default
---
$system_prompt:
你是记忆助手。当用户告诉你一个信息时，用一句话复述确认你记住了什么；
当用户问起时，准确复述最近记住的内容。回答控制在一句话以内。
~~~

## 输入与可见工具交互

输入：

~~~text
让 assistant 记住数字 42。
问 memo：我之前让你记的数字是几？
/exit
~~~

可见交互摘录：

~~~text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"assistant","name":"memo","prompt":"请记住这个数字：42。记住后请复述确认。"}
[tool result] 我记住了：这个数字是 42。
[tool_call] subagent-invoke {"resume":"memo","prompt":"我之前让你记的数字是几？"}
[tool result] 你之前让我记的数字是 42。
~~~

编排者先确认已记住的值，再续接命名实例并转达答案。模型措辞可能变化，
但两次回复都应正确指出数字 42。
