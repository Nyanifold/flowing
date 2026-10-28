# Changelog / 更新日志

本项目的全部重要变更记录于此。每个条目同步提供中英文内容。
All notable changes to this project are documented here. Each entry is bilingual (Chinese + English).

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，版本号遵循 0.x 语义化约定：
修订号 = 兼容修复与内部完善；次版本号 = 新增能力或可能非兼容的修改。
各版本的逐项说明见 [`changelogs/`](changelogs/)；本文保留版本总览。
Change-by-change details for each version are in [`changelogs/`](changelogs/); this file remains the release overview.
每个发布版本使用一个 `changelogs/vX.Y.Z.md` 文件；改动逐项成章并同步中英文，每项写明用法。只有存在不兼容性时才补充迁移方法。
Each release has one `changelogs/vX.Y.Z.md` file. Give every change its own bilingual section and describe how to use it; include migration guidance only when a change is incompatible.

## [0.1.3] - 2026-10-29

### 中文

新增双向 JSON Schema 校验 Composable、空动作占位工具 `builtin::pass` 和显式 Runtime `project_root`；统一工具参数校验诊断，简化 Peers 目录提示；加固工作循环崩溃兜底与 OpenRouter 思考回放。

- 新增 `use_validate_turn_result` 与 `use_validate_enqueued_message`：分别校验最终回复和入站消息，支持 JSON Schema、可配置失败策略及统一诊断；最终回复 steer 修复默认不限次数，有限次数耗尽后回合以 error 结束；
- 新增 `builtin::pass` 形式化空动作，以及 `Runtime(project_root=...)` 嵌入构造方式；
- 工具 LLM 视角参数错误改用统一、稳定的字段诊断；Peers 目录提示只保留目录与必要说明；
- 修复工作循环崩溃时残余 waiter 挂起与回合身份残留，并修复 chat/completions 端点 `reasoning_details` 的解析和多轮回放。

详细变更和用法见 [v0.1.3 详情](changelogs/v0.1.3.md)。

### English

Add bidirectional JSON Schema validation Composables, the no-op `builtin::pass` tool, and explicit Runtime `project_root`; unify tool argument diagnostics and simplify the Peers catalog prompt; harden work-loop crash recovery and OpenRouter reasoning replay.

- Add `use_validate_turn_result` and `use_validate_enqueued_message` for final-reply and inbound-message validation, with JSON Schema, configurable failure policies, and unified diagnostics; final-reply steer repairs are unlimited by default, while exhausting a finite budget ends the turn with an error;
- Add the formal no-op `builtin::pass` tool and direct embedded construction through `Runtime(project_root=...)`;
- Use unified, stable field diagnostics for LLM-facing tool argument errors; keep only the catalog and essential guidance in the Peers prompt;
- Fix hanging residual waiters and stale turn identity on work-loop crashes, and fix `reasoning_details` parsing and multi-turn replay on chat/completions endpoints.

See [v0.1.3 details](changelogs/v0.1.3.md) for the detailed changes and usage.

## [0.1.2] - 2026-10-04

### 中文

`on_dequeue` 钩子消息级化（不兼容，批级变换迁移至 `before_turn` 或
覆写 `_dequeue`）；新增唯一出队接口 `Agent._dequeue_message()` 与
`Agent.count_context_messages()`；修复 `on_dequeue` 异常终止消息工作循环、
Windows 上 record store 退出时被 poison 及关联的 `close()` 挂死。

- **`on_dequeue` 消息级化（不兼容）**：value 由 `list[Message]` 改为单条
  `Message`，覆盖全部消费路径（回合启动批次与回合内 INTERRUPT/STEER
  吸收）；可改写、可 `raise Intercepted` 丢弃（等待者联动 cancelled）；
  丢弃 INTERRUPT 不再 abort 回合；批重塑（删除/替换/合并批次消息）迁移到
  `before_turn` 的 `pending_messages`，或覆写 `Agent._dequeue`；
- 新增 `Agent._dequeue_message(match)`：核心唯一出队接口（内部 API），
  覆写 `_dequeue` 时建议逐条经它出队以保持钩子覆盖；
- 新增 `Agent.count_context_messages()`：统计下一次 LLM 调用将看到的
  上下文消息数（与 `_assemble_context` 消息路径同口径，纯观测无缓存），
  供应用层与 Composable 做策略判据；
- 修复 `on_dequeue` handler 异常终止常驻消息工作循环的问题（钩子异常
  捕获记录、该消息等待者 cancelled、工作循环兜底同时覆盖 `_dequeue`）；
- 修复 Windows 上退出 REPL 时 record store 被 poison（`[WinError 5]`）——
  原子重写现在替换前关闭常驻写句柄；并修复墓碑压缩失败时 `close()`
  的 drain 屏障不被唤醒导致的永久挂死。

详细变更和用法见 [v0.1.2 详情](changelogs/v0.1.2.md)。

### English

The `on_dequeue` hook is now message-level (incompatible; move batch
transformation to `before_turn` or an `_dequeue` override); add the sole
dequeue entry point `Agent._dequeue_message()` and
`Agent.count_context_messages()`; fix `on_dequeue` exceptions killing the
message work loop, record store poisoning on exit on Windows, and the
related `close()` hang.

- **`on_dequeue` is now message-level (incompatible)**: its value changed
  from `list[Message]` to a single `Message`, dispatched across all
  consumption paths (turn-start batches and mid-turn INTERRUPT/STEER
  absorption); handlers may rewrite the message or drop it with
  `raise Intercepted` (waiter resolved as cancelled); dropping an INTERRUPT
  no longer aborts the turn; batch reshaping (remove/replace/merge) moves to
  `before_turn`'s `pending_messages` or an `Agent._dequeue` override;
- Add `Agent._dequeue_message(match)`: the sole core dequeue entry point
  (internal API); overrides of `_dequeue` should dequeue through it per
  message to preserve hook coverage;
- Add `Agent.count_context_messages()`: counts the messages the next LLM
  call would see (same path as `_assemble_context`; pure observation, no
  caching), for policy decisions in the application layer and Composables;
- Fix `on_dequeue` handler exceptions terminating the resident message work
  loop (hook exceptions are caught and logged, the message's waiter resolves
  as cancelled, and the loop guard covers `_dequeue` as a whole);
- Fix record store being poisoned on REPL exit on Windows (`[WinError 5]`) —
  the atomic rewrite now closes the resident write handle before replacement;
  also fix the permanent `close()` hang when tombstone compaction failed
  without waking the drain barrier.

See [v0.1.2 details](changelogs/v0.1.2.md) for the detailed changes and usage.

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

[0.1.3]: https://github.com/Nyanifold/flowing/releases/tag/v0.1.3
[0.1.2]: https://github.com/Nyanifold/flowing/releases/tag/v0.1.2
[0.1.1]: https://github.com/Nyanifold/flowing/releases/tag/v0.1.1
[0.1.0]: https://github.com/Nyanifold/flowing/releases/tag/v0.1.0
