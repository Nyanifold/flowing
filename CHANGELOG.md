# Changelog / 更新日志

本项目的全部重要变更记录于此。每个条目同步提供中英文内容。
All notable changes to this project are documented here. Each entry is bilingual (Chinese + English).

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，版本号遵循 0.x 语义化约定：
修订号 = 兼容修复与内部完善；次版本号 = 新增能力或可能非兼容的修改。
各版本的逐项说明见 [`changelogs/`](changelogs/)；本文保留版本总览。
Change-by-change details for each version are in [`changelogs/`](changelogs/); this file remains the release overview.
每个发布版本使用一个 `changelogs/vX.Y.Z.md` 文件；改动逐项成章并同步中英文，每项写明用法。只有存在不兼容性时才补充迁移方法。
Each release has one `changelogs/vX.Y.Z.md` file. Give every change its own bilingual section and describe how to use it; include migration guidance only when a change is incompatible.

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

[0.1.0]: https://github.com/Nyanifold/flowing/releases/tag/v0.1.0
