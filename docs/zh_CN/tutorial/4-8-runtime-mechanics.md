# 4-8 · Runtime 机制全量与 @ 路径解析

## 前置阅读

[0-1 第一个智能体](0-1-hello-agent.md)（`launch` / `mount` 表象）、
[1-8 运行时目录](1-8-runtime-and-agent-dirs.md)（持久化根）。下方的完整双
Runtime 演示会从程序内嵌文本创建两份项目配置。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 创建管线 | `launch` → `main()` → `mount` / `create_agent` / `recover_agent` 的完整时序：登记 @ 上下文 → import 并 await `main(**kwargs)` → 复位 |
| `@` 上下文 | `launch` 登记的项目根（ContextVar，按 asyncio Task 隔离）；`@/` 前缀的解析基准 |
| 三注册表 | 全局 Tool / Agent 类型 / Skill 三个注册表（`ns::name` 全限定键），裸名视图 `default::` 优先于 `builtin::`，**文件覆盖注册表** |
| Resource | `runtime.register_resource` 的树外直引：跨 Agent 重资源（连接池、索引）的共享通道，不走注入链 |
| 智能体池 | Runtime 的 agent 池注册表（`agent_id → 身份四键`）；构造期扫描名录、惰性加载，永不实例化未用条目 |

## 目标

Runtime 类的机制全量：创建管线全图、三注册表与命名空间、`@` 路径
机制、provide 终点与 Resource、智能体池、snapshot 两级观测。

## 正文

### 创建管线全图

```
launch(path, **kwargs)
  ├─ 登记 @ 上下文（ContextVar = path 的绝对值；按 Task 隔离）
  ├─ import <path>/main.py → await main(**kwargs)（kwargs 原样透传）
  │    └─ Runtime()：固化 project_root / 注册 builtins / 扫 provider 候选
  │       与智能体池（均不实例化）/ 三层配置浅合并 / 自登记 core+default 袋
  │    └─ install / provide / register_resource / mount / recover_agent …
  └─ 复位 @ 上下文（main() 返回后；已 spawn 的子 Task 不受影响）
```

`mount` / `create_agent` / `recover_agent` 共用一条管线（委托
`Runtime.create_agent`）：`__new__` 预绑身份 → `__init__` 同步骨架 →
写 `meta.json` → `before_create` → `setup(**kwargs)` → PENDING 检查 →
模型解析 → 注册 → `after_create` → 启动常驻工作循环。恢复管线同构
（`_restore` 重放 + `before_recover` / `after_recover` 钩子对）。

### @ 路径机制

- `@/` = **子项目根**（`launch` 的 `path` 参数）；`./` `../` 以引用方
  的 source_dir 为基准；含 `::` 的 `文件::类名` 用于消歧；
- 两入口：模块级 `flowing.resolve()`（只在 `launch` 的 `main()` 调用
  栈内可用——上下文复位后抛 `RuntimeError`）与
  `runtime.resolve_path()`（Runtime 持有固化根，随时可用）；
- **按 asyncio Task 隔离**：一进程可并发多个 Runtime，各自 `@/` 互不
  串扰（demo ①实证）；真正的进程级强隔离（多租户 / A/B）用子进程。

### 三注册表与命名空间

`ToolRegistry` / `AgentRegistry` / `SkillRegistry` 同构：`ns::name`
全限定键；裸名查找 `default::` 优先于 `builtin::`（插件覆盖内置的
通道，0-2“命名空间省略”的机制答案）；**文件覆盖注册表**——裸名先走
source_dir 文件链。文件派生的注册带路径命名空间（如 `@/tools::demo`），
永不污染默认视图。

### provide 终点 / Resource / 智能体池

- provide 链终点 = Runtime：根 provide 对全树可见（1-5）；
- Resource 树外直引：重资源不经注入链、不受 provide 语义约束（demo ③）；
- 智能体池：构造期只扫描名录不实例化；`get_agent` 触发“有 key 无
  value → 现场恢复”；provider 同理——候选清单构造期就位、首次 get
  才实例化（demo ④：1 候选 / 0 实例）。

### snapshot：两级观测通道

`runtime.snapshot()`（节点表 / 插件清单 / 池）与 `agent.snapshot()`
（模型 / 队列 / 执行条目 / 消息树视图）——**拉取通道**：全字段 JSON
可序列化，provide 值绝不进入；只观察不控制（6-3 运维的前置机制）。

## 本篇不覆盖

- 插件两阶段启用（install 全局注册 → setup 实例级 use）——5-2；
- 智能体池的运维清理（`archive_orphans` / 遗忘三档）——6-3；
- 配置链三层合并的完整话题——6-3；
- Parsable 求值体系——4-9。

## 主线示例

`demo_runtime.py`：同进程拉起两个 Runtime；完整终端留档内嵌于下文：

```console
$ uv run python demo_runtime.py
== ① 双 Runtime 共存与 @ 隔离 ==
   A 项目根与传入路径一致：True
   B 项目根与传入路径一致：True
   B 的 @/root.fya 解析到 B 自己的根：True
== ② 模块级 resolve() 的可用域 ==
   launch 返回后调用 flowing.resolve() → RuntimeError: @ context not registered…
== ③ provide 终点 / Resource（树外直引）==
   根 inject('app_name') = 'demo-a'（链终点 = Runtime）
   get_resource('db').dsn = 'sqlite:///demo.db'（不走注入链，直引共享实例）
== ④ 池注册表 / provider 懒加载 ==
   池条目: ['agent-main']
   provider 候选 1 个 / 已实例化 0 个
== ⑤ snapshot（拉取通道）==
   runtime.snapshot: nodes=['agent-main', 'runtime-0'] plugins=[]
   agent.snapshot: node_id=agent-main model=deepseek-v4-flash queue=MessageQueueInfo(size=0, pending=0) tree=MessageTreeInfo（消息树视图）
```

读这段留档：两个 Runtime 同进程并存、各自的 `@/` 锚定自己的项目根
（B 的根是嵌套目录）——Task 隔离的实证；模块级 `resolve()` 随上下文
复位而关闭，Runtime 固化根不受此限；懒加载让“配了 1 个 provider、
实例化 0 个”成为常态。

### 完整可运行材料

将下方完整程序保存为 `demo_runtime.py`。程序会在临时位置写入两份最小
项目配置、分别拉起 Runtime，并在关闭后清理临时文件。程序不会调用
Provider，凭证仅保留为环境变量占位符。

```python
import asyncio
import tempfile
from pathlib import Path

import flowing
from flowing import launch


def write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "main.py": '''from flowing import Runtime

async def main(resume=None):
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
''',
        "root.fya": '''description: Runtime 机制演示助手：最小问答。
model_tag: default
---
$system_prompt:
你是简洁的助手。
''',
        "providers.yaml": '''deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
''',
        "models.yaml": '''deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
''',
        "model-tags.yaml": '''tags:
  default: deepseek-flash
''',
    }
    for filename, content in files.items():
        (root / filename).write_text(content, encoding="utf-8")


class FakeDB:
    def __init__(self, dsn: str):
        self.dsn = dsn


async def main() -> None:
    with tempfile.TemporaryDirectory() as temporary_root:
        base = Path(temporary_root)
        project_a, project_b = base / "a", base / "b"
        write_project(project_a)
        write_project(project_b)

        print("== ① 双 Runtime 共存与 @ 隔离 ==")
        rt_a = await launch(str(project_a))
        rt_b = await launch(str(project_b))
        print("   A 项目根与传入路径一致："
              f"{rt_a.project_root == project_a.resolve()}")
        print("   B 项目根与传入路径一致："
              f"{rt_b.project_root == project_b.resolve()}")
        print("   B 的 @/root.fya 解析到 B 自己的根："
              f"{rt_b.resolve_path('@/root.fya') == rt_b.project_root / 'root.fya'}")

        print("== ② 模块级 resolve() 的可用域 ==")
        try:
            flowing.resolve("@/providers.yaml")
            print("   意外可用（与契约不符）")
        except RuntimeError as exc:
            print(f"   launch 返回后调用 flowing.resolve() → RuntimeError: {exc}")

        print("== ③ provide 终点 / Resource（树外直引）==")
        rt_a.provide("app_name", "demo-a")
        rt_a.register_resource("db", FakeDB("sqlite:///demo.db"))
        root_a = await rt_a.get_agent("agent-main")
        print(f"   根 inject('app_name') = {root_a.inject('app_name')!r}（链终点 = Runtime）")
        print(f"   get_resource('db').dsn = {root_a.get_resource('db').dsn!r}"
              "（不走注入链，直引共享实例）")

        print("== ④ 池注册表 / provider 懒加载 ==")
        print(f"   池条目: {sorted(rt_a._agent_pool)}")
        print(f"   provider 候选 {len(rt_a.provider_registry._candidates)} 个 / "
              f"已实例化 {len(rt_a.provider_registry._instances)} 个")

        print("== ⑤ snapshot（拉取通道）==")
        snapshot = rt_a.snapshot()
        print(f"   runtime.snapshot: nodes={sorted(snapshot.nodes)} "
              f"plugins={snapshot.plugins}")
        agent_snapshot = root_a.snapshot()
        print(f"   agent.snapshot: node_id={agent_snapshot.node_id} "
              f"model={agent_snapshot.model.model} "
              f"queue={agent_snapshot.message_queue} "
              f"tree={type(agent_snapshot.messages).__name__}（消息树视图）")
        await rt_a.shutdown()
        await rt_b.shutdown()


asyncio.run(main())
```

使用 `uv run python demo_runtime.py` 运行。完整留档输出见上方；程序唯一
的凭证输入是环境变量占位符 `DEEPSEEK_API_KEY`，不会发出 Provider 请求。

## 小结

1. `launch` 唯一入口：登记 @ → await main() → 复位；管线全图一条委托；
2. `@/` 锚子项目根、按 Task 隔离；模块级 `resolve()` 仅限 main() 栈内；
3. 三注册表同构：裸名 `default::` > `builtin::`，文件覆盖注册表；
4. provide 终点 Runtime、Resource 树外直引、池与 provider 皆惰性；
5. snapshot 两级拉取观测，只观察不控制。
