"""``oneshot.py``（cmd_cli 一次性对话子命令）。

口径：stdout 断言用「期望片段序列」（过程显示不逐字节比对）；错误与
诊断一律走 stderr。项目 fixtures 与 repl 测试同源（scenario kwarg 经
launch → 项目 main 预置 FakeProvider）。cmd_cli 的 flowing 级选项经
``opts`` 字典传入（不占 main kwargs 名）。
"""

from __future__ import annotations

import pytest

from flowing.interfaces import (
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    EXIT_USAGE_ERROR,
)
from flowing.interfaces.oneshot import cmd_cli

from .support import read_tree_records, spy_get_agent


async def test_plain_output_is_final_reply_only(project_ok, persist_dir, capsys):
    """非 verbose：stdout 恰好是最终回复一行（无提示符、无过程行），
    恰好一个 USER 消息落盘。"""
    rc = await cmd_cli(str(project_ok), persist=str(persist_dir),
                       opts={"input": "你好"})
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert out == "alpha-reply\n"
    messages = [r for r in read_tree_records(persist_dir, "root")
                if r.get("type") == "message"]
    assert sum(1 for r in messages if r["kind"] == "user") == 1


async def test_verbose_tool_process_output(project_ok, persist_dir, capsys):
    """verbose：工具调用摘要行与最终回复都上屏，退出码 0。"""
    rc = await cmd_cli(str(project_ok), persist=str(persist_dir),
                       opts={"input": "ping", "verbose": True},
                       scenario="tool")
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "[tool_call] echo" in out
    assert "[tool:completed] echo -> ping" in out
    assert "alpha-reply" in out


async def test_verbose_thinking_streamed_even_non_tty(project_ok, persist_dir,
                                                      capsys):
    """verbose：思考段增量上屏——非 tty 也输出（与 repl 的管道策略不同，
    -v 语义即全量过程）。"""
    rc = await cmd_cli(str(project_ok), persist=str(persist_dir),
                       opts={"input": "你好", "verbose": True},
                       scenario="thinking")
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "让我想想。" in out
    assert "alpha-reply" in out


async def test_model_tag_switch(project_ok, persist_dir, capsys):
    """-m <tag>：本回合换模型标签（fake-a → fake-b），回复来自新模型。"""
    rc = await cmd_cli(str(project_ok), persist=str(persist_dir),
                       opts={"input": "你好", "model_tag": "b"})
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert out == "beta-reply\n"


async def test_explicit_agent_selects_target(project_two_roots, persist_dir,
                                             monkeypatch, capsys):
    """-t root-b：多根中点名目标 → 经 get_agent 取到并投递给 b。"""
    captured = spy_get_agent(monkeypatch)
    rc = await cmd_cli(str(project_two_roots), persist=str(persist_dir),
                       opts={"input": "你好", "agent_id": "root-b"})
    assert rc == EXIT_OK
    assert "beta-reply" in capsys.readouterr().out
    assert captured["root-b"].node_id == "root-b"


async def test_unknown_agent_id(project_ok, persist_dir, capsys):
    """-t 未知 id → stderr「unknown agent」+ EXIT_RUNTIME_ERROR。"""
    rc = await cmd_cli(str(project_ok), persist=str(persist_dir),
                       opts={"input": "你好", "agent_id": "ghost"})
    assert rc == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "unknown agent" in err
    assert "ghost" in err


async def test_default_single_active_root_used_directly(project_ok, persist_dir,
                                                        monkeypatch, capsys):
    """无 -t：单根激活直用——不经 get_agent（不恢复）、不新建。"""
    captured = spy_get_agent(monkeypatch)
    rc = await cmd_cli(str(project_ok), persist=str(persist_dir),
                       opts={"input": "你好"})
    assert rc == EXIT_OK
    assert "alpha-reply" in capsys.readouterr().out
    assert not captured   # 无恢复动作


async def test_dormant_single_root_recovers(project_ok, persist_dir,
                                            monkeypatch, capsys):
    """无 -t：盘上唯一休眠根 → 经 get_agent 现场恢复（身份连续）。"""
    assert await cmd_cli(str(project_ok), persist=str(persist_dir),
                         opts={"input": "你好"}) == EXIT_OK
    captured = spy_get_agent(monkeypatch)
    assert await cmd_cli(str(project_ok), persist=str(persist_dir),
                         opts={"input": "继续"}) == EXIT_OK
    assert "root" in captured
    assert "alpha-reply" in capsys.readouterr().out


async def test_slash_input_messages(project_ok, persist_dir, capsys):
    """INPUT 为 slash command：执行并打印，不进消息流（无 USER 落盘）。"""
    rc = await cmd_cli(str(project_ok), persist=str(persist_dir),
                       opts={"input": "/messages"})
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "(no messages)" in out
    messages = [r for r in read_tree_records(persist_dir, "root")
                if r.get("type") == "message"]
    assert not messages   # slash 不进消息流


async def test_slash_runtime_level_without_agent(project_empty, persist_dir,
                                                 capsys):
    """runtime 级 slash（/snapshot）在无法解析 Agent 的空项目照常执行。"""
    rc = await cmd_cli(str(project_empty), persist=str(persist_dir),
                       opts={"input": "/snapshot"})
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "runtime-0" in out   # Runtime 快照的 node_id


async def test_message_without_any_root(project_empty, persist_dir, capsys):
    """消息路径解析不出 Agent（池无根记录）→ stderr +
    EXIT_RUNTIME_ERROR。"""
    rc = await cmd_cli(str(project_empty), persist=str(persist_dir),
                       opts={"input": "你好"})
    assert rc == EXIT_RUNTIME_ERROR
    assert "cannot determine agent type" in capsys.readouterr().err


async def test_error_turn_exits_1(project_ok, persist_dir, capsys):
    """status=error → stderr 兜底说明 + EXIT_RUNTIME_ERROR。"""
    rc = await cmd_cli(str(project_ok), persist=str(persist_dir),
                       opts={"input": "你好"}, scenario="error")
    assert rc == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "turn finished with status=error" in err


async def test_empty_input_direct_call(project_ok, persist_dir, capsys):
    """编程直调空 input → 用法错误（CLI 层兜底语义，不 launch）。"""
    rc = await cmd_cli(str(project_ok), persist=str(persist_dir),
                       opts={"input": "   "})
    assert rc == EXIT_USAGE_ERROR
    assert "usage:" in capsys.readouterr().err
