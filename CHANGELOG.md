# Changelog / 更新日志

本项目的全部重要变更记录于此。每个条目同步提供中英文内容。
All notable changes to this project are documented here. Each entry is bilingual (Chinese + English).

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，版本号遵循 0.x 语义化约定：
修订号 = 兼容修复与内部完善；次版本号 = 新增能力或可能非兼容的修改。

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

[0.1.0]: https://github.com/Nyanifold/flowing/releases/tag/v0.1.0
