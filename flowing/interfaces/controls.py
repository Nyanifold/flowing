"""``flowing.interfaces.controls`` —— 共享 slash 命令目录（repl 与 serve/web 同源）。

.. rubric:: 功能介绍

repl 输入框与 web 输入框都以 ``/cmd [arg]`` 形式接受控制命令。本模块承载
命令的**单一逻辑源**（返回文本行），两端只是不同渲染：
- repl：对 ``slash_lines(...)`` 返回的行逐行 ``print``（并自行处理改变前台
  绑定的 ``/exit /quit /agent /new`` 与会话级生命周期）；
- serve ``POST /agents/{id}/command``：把 ``slash_lines`` 返回的行回成 JSON，
  供 web 用任意聚焦 agent 执行任意 repl 指令。

命令按目标分为两类：
- **runtime 级**（不需 agent）：``help / agents / snapshot``；
- **agent 级**（作用于给定 agent）：``messages / model / context / status /
  tasks / export / rewind / cancel / pause / resume``。

``messages`` 支持 ``v`` / ``verbose`` 参数，完整序列化消息及其全部内容块；
普通模式只完整显示较短工具结果，超过 500 个渲染字符时截断，其他消息沿用
短摘要。

约定：本目录只含“作用于显式目标并返回文本”的命令；前台绑定切换（``agent``/
``new``）与进程退出（``exit / quit``）是交互壳自身行为，不在此列。

.. seealso::

    ``flowing.interfaces.repl.cmd_repl``
    ``flowing.interfaces.serve`` ``POST /agents/<id>/command``
"""

from __future__ import annotations

import json
import time

from flowing.agent import Agent, _estimate_tool_schema_tokens
from flowing.interfaces import _default_agent_type, _list_agent_records
from flowing.message import (
    Message,
    MessageKind,
    TextBlock,
    _text_tokens,
    estimate_block_tokens,
    to_record,
)
from flowing.runtime import Runtime

_MESSAGES_PREVIEW_LIMIT = 500
"""普通 ``/messages`` 模式中，工具结果渲染内容的字符数上限。"""


def available_model_tags(agent: Agent) -> list[str]:
    """当前项目可用的全部 model tag（model-tags.yaml 的键集，文件序）。

    数据源：agent 所属 Runtime 登记的 model-tags 来源（
    ``runtime._model_tags_path``）现场解析（
    :func:`flowing.model.load_model_tags`，无缓存）。未配置 / 读取失败
    → 空表（名录提示不阻断）。repl ``/model`` 与 serve
    ``GET /agents/<id>/models`` 的同一口径，避免两端漂移。
    """
    tags_path = getattr(agent.runtime, "_model_tags_path", None)
    if not tags_path:
        return []
    try:
        from pathlib import Path

        from flowing.model import load_model_tags
        tags = load_model_tags(Path(tags_path))
    except Exception:
        return []
    return list(tags) if isinstance(tags, dict) else []


HELP_LINES: tuple[str, ...] = (
    "/help                list all commands (this help)",
    "/exit  /quit         exit repl (graceful shutdown, then exit with code 0)",
    "/agent <agent_id>    switch the foreground binding (dormant ids are restored)",
    "/agents              list recorded Agents (including dormant records)",
    "/new [agent_type]    create a new root Agent and bind it",
    "/snapshot            print a read-only snapshot of the current Runtime",
    "/messages [v]       print the message chain (v = full content)",
    "/model [tag]         show current & available tags / switch model_tag",
    "/context [v]         show context window usage estimate (v = per-part breakdown)",
    "/status              print a status rollup of the bound Agent",
    "/tasks [cancel <id>] list background tasks / cancel one",
    "/export [format]     export the bound Agent's message chain (md default)",
    "/rewind <msg_id>     move the current head to a historical message (fork)",
    "/cancel              cooperatively stop the bound Agent",
    "/pause  /resume      pause / resume the bound Agent",
)
"""命令的一句话说明（/help 用；与 repl ``SLASH_COMMANDS`` 一一对应）。"""


def _fold(text: str, limit: int = 80) -> str:
    folded = " ".join(text.split())
    return folded[:limit] + ("…" if len(folded) > limit else "")


def _agent_record_lines(runtime: Runtime, separator: str = "  ") -> list[str]:
    """按给定列分隔符渲染池名录行。"""
    records = _list_agent_records(runtime)
    lines: list[str] = []
    for rec in records:
        mtime = (time.strftime("%Y-%m-%d %H:%M", time.localtime(rec["mtime"]))
                 if rec["mtime"] is not None else "-")
        lines.append(f'{rec["agent_id"]}{separator}"{rec["last_reply"]}"{separator}{mtime}')
    return lines


async def slash_lines(cmd: str, arg: str, agent: Agent | None,
                      runtime: Runtime) -> list[str]:
    """执行一个 slash 命令，返回应展示的文本行（纯函数式：不直接 print）。

    带前导 ``/`` 的 ``cmd`` 会被剥成命令名；``arg`` 为第一个空格之后的
    参数串（可再切子命令）。不识别 / 需绑定 agent 的命令而无 agent 时返回
    提示行而非抛错。
    """
    name = cmd[1:] if cmd.startswith("/") else cmd
    arg = (arg or "").strip()
    lines: list[str] = []

    if name == "help":
        return list(HELP_LINES)
    if name == "agents":
        lines = _agent_record_lines(runtime)
        if not lines:
            return ["(no agent records)"]
        return lines
    if name == "snapshot":
        return [str(runtime.snapshot())]

    # ---- 以下需绑定/目标 agent ----
    if agent is None:
        return ["no agent bound (see /agents, select with /agent <id>)"]

    if name == "messages":
        if arg not in ("", "v", "verbose"):
            return ["usage: /messages [v|verbose]"]
        chain: list[Message] = []
        mid = agent.current_head_id
        while mid is not None:
            m = agent._messages.get(mid)
            if m is None:
                break
            chain.append(m)
            mid = m.parent_id
        if not chain:
            return ["(no messages)"]
        for m in reversed(chain):
            if arg in ("v", "verbose"):
                lines.append(f"--- {m.id} {m.kind.value} ---")
                lines.extend(json.dumps(
                    to_record(m), ensure_ascii=False, indent=2).splitlines())
                continue

            text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
            if m.kind is MessageKind.TOOL:
                # 常见文本工具结果保持原文；混合 / 结构化内容按完整 block
                # 记录渲染，后续统一按可见字符数裁切。
                text_blocks = [b for b in m.content if isinstance(b, TextBlock)]
                if len(text_blocks) != len(m.content):
                    text = json.dumps(to_record(m)["content"], ensure_ascii=False)
                if len(text) > _MESSAGES_PREVIEW_LIMIT:
                    omitted = len(text) - _MESSAGES_PREVIEW_LIMIT
                    text = (text[:_MESSAGES_PREVIEW_LIMIT]
                            + f"… [truncated; {omitted} characters omitted]")
            else:
                text = _fold(text)
            lines.append(f"{m.id[:12]}  {m.kind.value:<9} {text}")
        return lines

    if name == "model":
        if not arg:
            current = getattr(agent, "model_tag", None)
            tags = available_model_tags(agent)
            rendered = ", ".join(f"{t}(*)" if t == current else t for t in tags)
            return [f"model_tag={current}  "
                    f"provider={getattr(agent.model, 'provider', None)}  "
                    f"model={getattr(agent.model, 'model', None)}",
                    f"available tags: {rendered or '(none configured)'}"]
        try:
            agent.model_tag = arg
            return [f"model_tag -> {arg}"]
        except Exception as exc:
            return [f"failed to set model_tag: {exc}"]

    if name == "context":
        if arg and arg not in ("v", "verbose"):
            return ["usage: /context [v|verbose]"]
        est = agent.estimate_context_tokens()
        ratio = est.usage_ratio
        lines = [
            f"context_window={est.context_window if est.context_window is not None else '?'}  "
            f"tokens={est.tokens}  "
            f"usage={f'{ratio:.1%}' if ratio is not None else 'n/a'}",
            f"measured={est.measured}  estimated={est.estimated}  "
            f"anchor={est.anchor_message_id}",
        ]
        if arg not in ("v", "verbose"):
            return lines
        # verbose：分类独立估算——每段 system prompt / 每个启用工具 schema /
        # 每条消息的每个 block 都走本地启发式（不吃锚点实测，故分类和 ≠
        # 总体估计是常态）；算比例后重归一化到 est.tokens 展示
        parts: dict[str, int] = {}
        sys_tokens = sum(_text_tokens(str(block.content.resolve(agent)))
                         for block in agent.prompt_blocks)
        if sys_tokens:
            parts["system prompt"] = sys_tokens
        tool_tokens = sum(
            _estimate_tool_schema_tokens(entry.llm_definition(runtime, agent))
            for entry in agent._tool_entries.values() if entry.visible)
        if tool_tokens:
            parts["tools"] = tool_tokens
        for m in reversed(list(agent.chain.walk(agent.current_head_id))):
            for block in m.content:
                key = f"{m.kind.value}/{type(block).__name__}"
                parts[key] = parts.get(key, 0) + estimate_block_tokens(block)   # 逐块规则的单一来源（与总体估算同口径）
        raw = sum(parts.values())
        for label, n in parts.items():
            share = n / raw if raw else 0.0
            lines.append(
                f"{label:<26} {round(share * est.tokens):>8}  ({share:.1%})")
        lines.append(f"breakdown raw={raw}  normalized to tokens={est.tokens}")
        return lines

    if name == "status":
        return [f"agent_id={agent.node_id}",
                f"model_tag={getattr(agent, 'model_tag', None)}  paused={agent.paused}",
                f"current_head={agent.current_head_id}  messages={len(agent._messages)}  "
                f"tasks={len(agent.get_background_tasks())}"]

    if name == "tasks":
        sub, _, subarg = arg.partition(" ")
        if sub == "cancel":
            ok = agent.cancel_background_task(subarg.strip())
            return ["cancelled" if ok else f"no such task: {subarg.strip()!r}"]
        tasks = agent.get_background_tasks()
        if not tasks:
            return ["(no background tasks)"]
        return [f"{tid} {'done' if t.done() else 'running'}"
                for tid, t in tasks.items()]

    if name == "export":
        chain = []
        mid = agent.current_head_id
        while mid is not None:
            m = agent._messages.get(mid)
            if m is None:
                break
            chain.append(m)
            mid = m.parent_id
        chain.reverse()
        fmt = arg or "md"
        if fmt == "jsonl":
            return [json.dumps(to_record(m), ensure_ascii=False) for m in chain]
        return [f"**{m.kind.value}**: "
                + "".join(b.text for b in m.content if isinstance(b, TextBlock))
                for m in chain]

    if name == "rewind":
        if not arg:
            return ["usage: /rewind <message_id>"]
        if arg not in agent._messages:
            return [f"unknown message_id: {arg!r}"]
        await agent.fork(arg)
        return [f"head -> {agent.current_head_id}"]

    if name == "cancel":
        await agent.cancel()
        return ["cancelled"]

    if name in ("pause", "resume"):
        (agent.pause if name == "pause" else agent.resume)()
        return [f"{name} ok (paused={agent.paused})"]

    if name in ("agent", "new"):
        return [f"/{name} is a shell-level command (handled by the interactive front-end)"]

    return [f"unknown command: /{name}; run /help for available commands"]
