# Changelog / 更新日志

本项目的全部重要变更记录于此。每个条目同步提供中英文内容。
All notable changes to this project are documented here. Each entry is bilingual (Chinese + English).

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，版本号遵循 0.x 语义化约定：
修订号 = 兼容修复与内部完善；次版本号 = 新增能力或可能非兼容的修改。
各版本的逐项说明见 [`changelogs/`](changelogs/)；本文保留版本总览。
Change-by-change details for each version are in [`changelogs/`](changelogs/); this file remains the release overview.
每个发布版本使用一个 `changelogs/vX.Y.Z.md` 文件；改动逐项成章并同步中英文，每项写明用法。只有存在不兼容性时才补充迁移方法。
Each release has one `changelogs/vX.Y.Z.md` file. Give every change its own bilingual section and describe how to use it; include migration guidance only when a change is incompatible.

## [0.1.1] - 2026-10-01

### 中文

新增 Peers 定向交互插件、回合结束标志、既有消息的 XML 信封映射，以及独立的
Provider 与模型配置命令；修复 REPL 的重复输出、后台思考灰显、等待输入时阻塞
事件循环，以及工具参数校验失败结果缺失调用元数据的问题。

- 新增 `PeersPlugin` 与 `use_peers()`：同一 Runtime 内 Agent 之间按 `peers:`
  目录定向交互（`query-peer` / `message-peer` / `steer-peer`）；
- 新增 `TurnContext.finish` 回合结束标志；
- 三个 Provider 格式基类中映射为 `user` role 的非 `USER` 消息改用 XML 信封；
- 新增 `flowing-config providers` 与 `flowing-config models` 交互配置命令；
- `/messages` 新增 `v` / `verbose` 完整查看，REPL 启动时列出 Runtime 池中的
  全部 Agent；
- 修复 REPL 重复输出 Provider 正文、后台回合思考内容丢失灰显样式、等待输入
  时阻塞事件循环，以及 LLM 参数校验失败结果缺失工具别名与调用 ID。

详细变更和用法见 [v0.1.1 详情](changelogs/v0.1.1.md)。

### English

Add the Peers plugin for directed in-Runtime interaction, a turn-completion
flag, XML envelopes for existing messages mapped to the `user` role, and
standalone Provider and model configuration commands; fix duplicate REPL
output, lost dim styling for background thinking, event-loop blocking while
waiting for input, and missing call metadata on tool argument-validation
errors.

- Add `PeersPlugin` and `use_peers()` for directed agent-to-agent interaction
  within one Runtime through a `peers:` catalog (`query-peer` /
  `message-peer` / `steer-peer`);
- Add the `TurnContext.finish` turn-completion flag;
- Map non-`USER` messages to the `user` role as XML envelopes in the three
  provider format bases;
- Add the `flowing-config providers` and `flowing-config models` interactive
  configuration commands;
- Add `v` / `verbose` to `/messages` and list every agent in the Runtime pool
  at REPL startup;
- Fix duplicate Provider text in the REPL, lost dim styling for background
  turn thinking, event-loop blocking while waiting for input, and missing
  tool alias and call ID on LLM argument-validation error results.

See [v0.1.1 details](changelogs/v0.1.1.md) for the detailed changes and usage.

## [0.1.0] - 2026-09-25

### 中文

首个公开发布版本。

- 框架核心：消息驱动的工作循环、消息级树（fork 与历史手术）、现场组装的
  Context、三层能力描述（Tool / 子 Agent / Skill）、实例级钩子系统、
  provide-inject、自动持久化与崩溃恢复；
- 内置扩展：插件五个（skills / comm / cron / workflow / clipboard）与
  Composable（use_retry / use_compact / use_auto_compact /
  use_system_reminder / use_prompt_until）；
- 接口层八个子命令：repl / cli / repl-debug / run / test / serve / web /
  compile；
- Provider：OpenAI 与 Anthropic 两格式家族及各厂商 adapter，支持 MCP
  （stdio / SSE / streamable HTTP）；
- 双语文档：入门教程、详细教程、精简参考与 API 参考（700+ 测试用例通过）。

详细变更和用法见 [v0.1.0 详情](changelogs/v0.1.0.md)。

### English

First public release.

- Framework core: message-driven work loop, message-level tree (fork and
  history surgery), just-in-time assembled Context, three-layer capability
  description (Tool / subagent / Skill), instance-level hooks,
  provide-inject, automatic persistence and crash recovery;
- Built-in extensions: five plugins (skills / comm / cron / workflow /
  clipboard) and composables (use_retry / use_compact / use_auto_compact /
  use_system_reminder / use_prompt_until);
- Eight CLI subcommands: repl / cli / repl-debug / run / test / serve / web /
  compile;
- Providers: OpenAI and Anthropic format families with vendor adapters, plus
  MCP support (stdio / SSE / streamable HTTP);
- Bilingual documentation: beginner tutorial, detailed tutorial, concise
  reference, and API reference (700+ test cases passing).

See [v0.1.0 details](changelogs/v0.1.0.md) for the detailed changes and usage.

[0.1.1]: https://github.com/Nyanifold/flowing/releases/tag/v0.1.1
[0.1.0]: https://github.com/Nyanifold/flowing/releases/tag/v0.1.0
