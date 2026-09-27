# 0-1 · 第一个智能体

## 前置阅读

[0-0 环境安装](0-0-env-setup.md)（Python ≥ 3.13、已安装
`flowing-agent`）。配置中的 `api_key` 若使用 `{{env.DEEPSEEK_API_KEY}}`，需在当前
shell 设置 `DEEPSEEK_API_KEY`；若改用用户级配置并直接在其中填写凭证，则无需
该环境变量。若要试用下文的 OpenRouter 模型，还需设置 `OPENROUTER_API_KEY`。
请自行新建一个空项目目录，按下文保存项目文件；Provider 与模型
配置按本篇说明放入用户级目录后，再从项目目录运行命令。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| flowing 子项目 | 一个目录 + 入口 `main.py` + 声明式 Agent 定义（`.fya` 文件）；flowing 应用的单位 |
| `.fya` 文件 | flowing 的声明式定义文件：YAML 头部声明元信息，`---` 分隔的具名块承载提示词或脚本（如 `$system_prompt:`） |
| 模型接入三文件 | `providers.yaml`（provider 条目：一个条目 = 一个 API key 身份）、`models.yaml`（模型条目：一个条目 = 一个具体模型）、`model-tags.yaml`（标签 → 模型条目名映射） |
| `model_tag` | Agent 声明里指向模型标签的字段；经“两跳解析”变成一次真实 API 调用 |
| `launch` | `flowing.launch(path, **kwargs)`：Runtime 的唯一创建入口 |
| 逻辑 Turn（回合） | 从消费一条消息到模型给出最终答复的执行过程；“回合”固定译 Turn，结果由 `TurnResult` 表示 |

## 目标

从空目录到一次完整对话：配好模型接入、拉起一个 flowing 子项目、用四种
访问方式各完成一次问答，并理解消息如何驱动回合。

## 正文

### 一个 flowing 子项目长什么样

flowing 应用的单位是**项目目录**。本例把 `providers.yaml` 和 `models.yaml`
放在用户级配置目录，项目中保留 `main.py`、Agent 声明和
`model-tags.yaml`。下文给出完整配置内容；如果希望改用项目内的
`providers.yaml` / `models.yaml`，可以启用入口中的对应设置。

`root.fya` 是声明式 Agent 定义：YAML 头部声明元信息，`---` 分隔后的
`$system_prompt:` 具名块是系统提示词（唯一必填内容）：

```yaml
description: 最小问答助手
model_tag: default
---
$system_prompt:
你是一个简洁的中文助手，回答控制在三句话以内。{% if user_name %}用户叫做 {{ user_name }}，回答时可以直接以名字相称。{% endif %}
```

没有任何 Python 代码——这就是一个有完整行为能力的 Agent。`{{ user_name }}`
那半句此刻先忽略，主线示例的演示 2 会用到它（模板现场求值的原理见 4-9）。

把下面完整代码保存为 `main.py`。它负责**组装**：构造 Runtime、登记模型配置、
挂载根 Agent：

```python
from flowing import Runtime


async def main(user_name: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 固定 agent_id → 幂等挂载：第二次启动走恢复，“同一个根回来了”
    root = await runtime.mount("@/root.fya", agent_id="agent-main")
    if user_name is not None:
        root.user_name = user_name
    return runtime
```

入口中的 `set_providers()` 和 `set_models()` 已注释，因此 Runtime 使用
`~/.flowing` 中的默认配置；`set_model_tags()` 仍启用，所以本例从项目根目录
读取 `model-tags.yaml`。若将标签文件也放入 `~/.flowing`，可一并注释
`set_model_tags()`。若选择项目级 `providers.yaml` / `models.yaml`，则取消前两行
注释，并将对应文件放在项目根目录。

注意：根 Agent 用 `mount()` 挂载，`agent_id` 固定时是幂等挂载——首次
启动新建，此后每次启动同一 id 已在池中，自动走恢复管线，“同一个根回来了”。

### 模型接入：三个 yaml 与 model_tag 两跳解析

智能体系统跑不起来的第一大原因是模型没配对。flowing 把模型接入拆成三个
文件，职责单一、可独立替换。以下完整配置依次保存为 `providers.yaml`、
`models.yaml` 和 `model-tags.yaml`；本例将前两份放在 `~/.flowing`，将标签文件
放在项目根目录。若使用项目级配置，取消 `main.py` 中前两行的注释：

```yaml
# providers.yaml —— provider 条目：一个条目 = 一个 API key 身份
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"   # 凭证经环境变量注入，不硬编码
openrouter:
  adapter: openrouter
  base_url: https://openrouter.ai/api/v1
  api_key: "{{env.OPENROUTER_API_KEY}}"
```

```yaml
# models.yaml —— 模型条目：一个条目 = 一个具体模型，通过 provider 字段绑定 provider 条目
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
openrouter-gpt-6-luna:
  provider: openrouter
  model: openai/gpt-6-luna
  "reasoning.effort": high
```

```yaml
# model-tags.yaml —— 标签 → 模型条目名映射（单值，无回退链）
tags:
  default: deepseek-flash
  luna: openrouter-gpt-6-luna
```

Agent 声明里的 `model_tag: default` 如何变成一次真实的 API 调用？
**两跳解析**：

```
model_tag("default") ──① model-tags.yaml──▶ 条目名("deepseek-flash")
                     ──② models.yaml──────▶ ModelConfig(provider=deepseek,
                                             model=deepseek-v4-flash)
model_tag("luna")    ──① model-tags.yaml──▶ 条目名("openrouter-gpt-6-luna")
                     ──② models.yaml──────▶ ModelConfig(provider=openrouter,
                                             model=openai/gpt-6-luna,
                                             extra={"reasoning.effort": "high"})
```

三个文件分别声明接入身份、模型及标签映射：`models.yaml` 中的 `provider`
选择 `providers.yaml` 的条目，`model` 是发给该 provider 的模型 ID；标签只指向
`models.yaml` 的条目名。当前 `default` 仍使用 DeepSeek。将 Agent 的
`model_tag` 改为 `luna`，就会经 OpenRouter 调用 `openai/gpt-6-luna`。
`"reasoning.effort": high` 是该模型条目的字面扩展键，只随 GPT-6 Luna 传给
OpenRouter；DeepSeek 条目没有该设置。未设置 `OPENROUTER_API_KEY` 时，加载配置会告警，
`default` 仍可使用 DeepSeek；选择 `luna` 时需要该密钥。

规则要点：标签未定义时回退 `default` 标签；`default` 也未定义 → 报错，
**不静默降级**。`{{env.X}}` 在条目加载期替换，缺失环境变量替换为空串并
`warnings.warn` 告警、**加载不中断**（缺失凭证的实际后果——如 401——在
该条目首次调用时暴露）。

#### 共享用户级模型配置

`Runtime` 默认从 `$FLOWING_CONFIG_HOME` 读取 `providers.yaml`、`models.yaml`
和 `model-tags.yaml`；未设置 `FLOWING_CONFIG_HOME` 时，该目录是
`~/.flowing`。因此，集中配置到 `~/.flowing` 后，项目目录可以不再复制这些
文件。这个默认来源不会扫描当前项目目录；本例 `main.py` 中的
`set_providers()` / `set_models()` 已注释，因而从用户级目录读取这两份配置；
`set_model_tags()` 仍启用，所以标签映射仍在项目目录。后续教程沿用共享的
Provider 和模型配置时，无需在每个示例目录重新声明 `providers.yaml` 和
`models.yaml`；对应的 `set_providers()` / `set_models()` 调用也应保持注释。若要
共享标签映射，再注释 `set_model_tags()` 并把 `model-tags.yaml` 放到用户级目录。

用户级配置中的 `models.yaml` 和 `model-tags.yaml` 可直接使用本篇上面的内容。
`providers.yaml` 可以继续用环境变量占位符，也可以在本机私有配置中直接写凭证：

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "YOUR_DEEPSEEK_API_KEY"
```

使用默认目录时，创建目录、将 `providers.yaml` 和 `models.yaml` 放进去；如需
把 `model-tags.yaml` 也集中管理，也可一并放入该目录。为凭证文件设置权限：

```console
$ mkdir -p ~/.flowing
$ chmod 600 ~/.flowing/providers.yaml
```

把凭证保存在用户级配置中，可避免它随项目源码提交或随项目文件分享；文件里
仍是明文凭证，应只允许当前用户读取。若设置了 `FLOWING_CONFIG_HOME`，上述文件
应放在该变量指定的目录中，并对其中的 `providers.yaml` 设置同样的权限。

### launch、Runtime 与“消息驱动回合”

`flowing.launch(path, **kwargs)` 是创建 Runtime 的**唯一入口**。它做的事很薄：
登记 `@` 项目根上下文 → import 子项目
`main.py` → `await main(**kwargs)` → 复位上下文。不解析配置、不认识插件、
不开端口——这些都在 `main()` 或更外层。

Runtime 是**对象图根**：持有全部 Agent 节点、全局工具注册表与智能体池。
两个常用语义一句话版：`await runtime` 阻塞至 `shutdown()`（CLI/嵌入方用
它保持进程存活）；`shutdown()` 递归销毁全部 Agent 并排空持久化——
write-behind 落盘（提交只排队），不 `shutdown()` 直接退进程会丢尾部记录
（细节见 1-8 / 4-1）。

回合如何发生？非 `/` 的交互输入经 `query()` 投递成一条消息入队；Agent 的
常驻工作循环出队消费、开逻辑 Turn：组装上下文 → 调模型 → 响应挂树落盘 →
收尾。`query()` 等到的是 `TurnResult`：`result.status`（`"completed"` /
`"blocked"` / `"cancelled"` / `"error"`）与 `result.final_text`（本回合
最后一条 PROVIDER 消息的文本）。四种结局**都会** resolve 给等待者——
调用方永不挂起（完整循环见 1-1）。

### 四种访问方式一览

同一个子项目可用四种方式暴露，全部建立在 `launch(path)` 之上，区别只
在暴露方式（完整对比留 6-1）：

| 方式 | 命令 | 形态 |
|---|---|---|
| `flowing cli` | `flowing cli . "一句话"` | 一次性对话：投递一条 INPUT、打印结果即退出，面向脚本 / 管道 |
| `flowing repl` | `flowing repl .` | 交互式 REPL（提示符绑定 Agent）；**后续各篇示例一律以此为准** |
| `flowing web` | `flowing web .` | HTTP API + 内置默认前端，浏览器打开即用 |
| `flowing serve` | `flowing serve .` | 纯 HTTP API、无前端，供自建 UI / 服务对接 |

## 本篇不覆盖

- `@` 项目根上下文的机制细节——4-8；
- 恢复管线与幂等挂载的内部时序——1-8 看表象、4-1 讲机制；
- 配置链（config.yaml 三层合并）与三文件的来源路径优先级、
  `set_providers` / `set_models` / `set_model_tags` 编程覆盖——6-3；
- 模型选择的完整话题（多标签设计、运行期 `agent.model_tag = ...` 换模型）——3-4；
- serve 的 HTTP 面细节与嵌入式暴露——6-1 / 6-2。

## 主线示例

**演示 1：同一个内联项目，四种方式各跑一次**。模型回复会因调用而异，
下方给出示例输入与输出；命令所需文件已在本篇完整列出。

```console
# ① cli：一次性对话
$ uv run flowing cli . "用一句话介绍你自己。"
我是简洁的中文助手，可以帮你快速解答问题。

# ② repl：交互式（交互输入直接显示在提示符后；回复为示例输出）
$ uv run flowing repl .
(agent-main)>>> 用一句话介绍你自己。
[thinking]（推理痕迹已省略）
我是一个简洁的中文AI助手，专注用三句话以内帮你解答问题。
(agent-main)>>> /exit

# ③ web：HTTP API + 内置前端（-a 主机、-p 端口；演示顺序先于 serve）
# 终端 A：启动后保持服务运行
$ uv run flowing web . -a 127.0.0.1 -p 8411
# 终端 B：发送请求并查看结果
$ curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8411/
200
$ curl -s -X POST http://127.0.0.1:8411/agents/agent-main/message \
    -H "Content-Type: application/json" \
    -d '{"text": "用一句话介绍你自己。"}'
{"message_id": "5", "final_text": "我是简洁的中文AI助手，乐于用简练的话帮你解答问题。"}

# ④ serve：纯 HTTP API（无前端；/healthz 自检 + message 投递）
# 终端 A：启动后保持服务运行
$ uv run flowing serve . -a 127.0.0.1 -p 8412
# 终端 B：发送请求并查看结果
$ curl -s http://127.0.0.1:8412/healthz
{"status": "ok"}
$ curl -s -X POST http://127.0.0.1:8412/agents/agent-main/message \
    -H "Content-Type: application/json" \
    -d '{"text": "用一句话介绍你自己。"}'
{"message_id": "7", "final_text": "我是简洁的中文AI助手，随时用简短回答帮你解决问题。"}
```

读这段演示：`cli` 打印 `final_text` 即退出；`repl` 流式打印生成中的正文、
`/exit` 触发 `shutdown()` 后退出；
`web` 与 `serve` 共用同一组 HTTP 端点（`POST /agents/<agent-id>/message`
投递一条消息并等待回合结果），差别只在 web 多一个内置前端页面（所以
演示顺序先 web 后 serve）。

**演示 2：运行时参数注入**（只演示现象，原理见 4-9）。上文内联的入口声明了
`user_name` 形参——CLI 的 `--key value` 经 `launch` 原样透传
`main(**kwargs)`；mount 之后 `root.user_name = user_name`，
`root.fya` 模板里的 `{% if user_name %}` 在下次组装上下文时现场求值。
在完成演示 1 后运行这条命令；前面的输入没有出现名字，因此对话历史中尚无该值：

```console
$ uv run flowing repl . --user_name 小明
(agent-main)>>> 我叫什么名字？请只回答名字本身。
[thinking]（推理痕迹已省略）
小明
(agent-main)>>> /exit
```

Agent 当场以注入的名字“小明”作答——此前对话历史里从未出现过这个名字，
它只能来自 system prompt 的现场求值。

完整的 `root.fya`、`main.py` 和三份模型配置已分别以内联代码块给出；
上面的命令、请求正文和示例响应包含复现两组演示所需的输入与可观察输出。

## 小结

1. 子项目 = `main.py`（组装策略）+ `root.fya`（Agent 声明）+ 模型三文件；
2. `model_tag` 经“标签 → 条目名 → ModelConfig”两跳解析，不静默降级；
3. `launch` 唯一入口、Runtime 对象图根；`query` 等 `TurnResult`，四结局不挂起；
4. 同一子项目四种暴露：cli / repl / web / serve，后续各篇示例一律用 repl。
