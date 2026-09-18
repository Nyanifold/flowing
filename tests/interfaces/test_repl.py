"""T14–T27：``repl.py``（cmd_repl / _default_agent / SLASH_COMMANDS）。

输入一律经 ``fixtures/interfaces/repl-scripts/*.txt`` 脚本驱动
（``support.drive_input``），输出断言为「期望片段序列」口径（R4：终端
交错不做逐字节比对）。
"""

from __future__ import annotations

import asyncio
import re

import pytest
from types import SimpleNamespace

from flowing.interfaces import EXIT_OK
from flowing.interfaces import repl as repl_mod
from flowing.interfaces.controls import slash_lines
from flowing.interfaces.repl import SLASH_COMMANDS, _default_agent, cmd_repl
from flowing.tool import Tool, ToolDefinition

from harness import script_provider, text_response, tool_call_response

from .support import (
    add_fake_provider,
    drive_input,
    make_runtime,
    read_tree_records,
    script_lines,
    spy_get_agent,
    spy_launch,
    spy_recover_agent,
)


def test_slash_commands_closed_set():
    """v3 slash-command 封闭集：含新命令组、不含 /use(已更名 /agent) 与
    /abort /enqueue /steer。"""
    expected = ("/help", "/exit", "/quit", "/agent", "/agents", "/new",
                "/snapshot", "/messages", "/model", "/context", "/status",
                "/tasks", "/export", "/rewind", "/cancel", "/pause", "/resume")
    assert SLASH_COMMANDS == expected
    assert "/use" not in SLASH_COMMANDS
    assert not {"abort", "enqueue", "steer"} & {c.lstrip("/") for c in SLASH_COMMANDS}


async def test_t14_single_root_bind_and_turn(project_ok, persist_dir, monkeypatch, capsys):
    """池恰一个根 Agent：启动即绑定，输入 "你好" 后 /exit → 恰好一个
    USER 节点 + 一个逻辑 Turn，退出码 EXIT_OK。"""
    spy_launch(monkeypatch, repl_mod)
    drive_input(monkeypatch, script_lines("hello-exit.txt"))
    rc = await cmd_repl(str(project_ok), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "(root)>>>" in out        # 启动即绑定（提示符含 agent_id）
    assert "alpha-reply" in out      # 回复文本出现在输出
    messages = [r for r in read_tree_records(persist_dir, "root")
                if r.get("type") == "message"]
    assert sum(1 for r in messages if r["kind"] == "user") == 1   # 恰好一个 USER 节点
    provider_msgs = [r for r in messages if r["kind"] == "provider"]
    assert len(provider_msgs) == 1                            # 恰好一轮 provider 回复
    assert sum(1 for r in provider_msgs if r["turn_end"]) == 1   # 一个逻辑 Turn 收尾


async def test_t15_two_roots_unbound(project_two_roots, persist_dir, monkeypatch, capsys):
    """池有两个根 Agent → 启动进未绑定态（提示符 (new agent)>>>），
    打印 /agents 提示，进程不退出。"""
    drive_input(monkeypatch, ["/exit"])
    rc = await cmd_repl(str(project_two_roots), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "(new agent)>>>" in out
    assert "/agents" in out   # 启动提示选择路径


async def test_t16_empty_pool_no_create(project_empty, persist_dir, monkeypatch, capsys):
    """全新项目（池空）输入 "你好" → 打印「无法确定 Agent 类型」，
    不创建、不退出。"""
    drive_input(monkeypatch, ["你好", "/exit"])
    rc = await cmd_repl(str(project_empty), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "cannot determine agent type" in out
    assert "(new agent)>>>" in out   # 仍在未绑定态循环中
    # 名录保持为空：无 session 目录产生
    assert not [p for p in persist_dir.iterdir() if p.is_dir()]


async def test_t17_restart_identity_continuity(project_ok, persist_dir, monkeypatch, capsys):
    """池有一个根、重启进程（盘上记录完好）→ 启动绑定经 get_agent
    现场恢复同一 agent_id（身份连续）。"""
    drive_input(monkeypatch, ["你好", "/exit"])
    assert await cmd_repl(str(project_ok), persist=str(persist_dir)) == EXIT_OK
    # 第二次启动：池有记录、活体表空 → 池回退恢复
    captured = spy_get_agent(monkeypatch)
    drive_input(monkeypatch, ["/exit"])
    assert await cmd_repl(str(project_ok), persist=str(persist_dir)) == EXIT_OK
    out = capsys.readouterr().out
    assert "(root)>>>" in out                       # 提示符显示同一 id
    assert captured["root"].node_id == "root"       # 经 get_agent 现场恢复（身份连续）
    assert len(captured["root#instances"]) == 1


async def test_t18_unknown_command(project_ok, persist_dir, monkeypatch, capsys):
    """输入 /frobnicate → 打印未知命令提示，仍在 repl 循环中（后续消息正常投递）。"""
    drive_input(monkeypatch, script_lines("unknown-command.txt"))
    rc = await cmd_repl(str(project_ok), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "unknown command" in out
    assert "alpha-reply" in out   # 未知命令后仍在循环中，消息照常投递


async def test_t19_quit_and_eof(project_ok, persist_dir, monkeypatch, tmp_path):
    """/quit 与 /exit 同路径；EOF（输入脚本耗尽）同路径。"""
    drive_input(monkeypatch, script_lines("quit.txt"))
    assert await cmd_repl(str(project_ok), persist=str(persist_dir)) == EXIT_OK
    drive_input(monkeypatch, script_lines("eof.txt"))   # 无 /exit，脚本耗尽 → EOF
    assert await cmd_repl(str(project_ok), persist=str(tmp_path / "p2")) == EXIT_OK


async def test_t20_messages(project_ok, project_two_roots, tmp_path, monkeypatch, capsys):
    """/messages：未绑定打印提示；已绑定沿 current_head_id 上溯的消息链概览。"""
    # 未绑定（two-roots 启动进未绑定态）
    drive_input(monkeypatch, ["/messages", "/exit"])
    assert await cmd_repl(str(project_two_roots), persist=str(tmp_path / "p1")) == EXIT_OK
    out = capsys.readouterr().out
    assert "no agent bound" in out
    # 已绑定：一轮对话后打印消息链
    drive_input(monkeypatch, ["你好", "/messages", "/exit"])
    assert await cmd_repl(str(project_ok), persist=str(tmp_path / "p2")) == EXIT_OK
    out = capsys.readouterr().out
    assert "user" in out and "provider" in out   # 链概览含 kind 列
    i_user = out.index("user", out.index("/messages"))
    assert "你好" in out[i_user:]                # 用户文本出现在链概览中


async def test_t21_use_switch(project_two_roots, persist_dir, monkeypatch, capsys):
    """/use：未知 id → 提示且绑定不变；命中激活实例 → 切换绑定，
    原 Agent 不 destroy（同一对象直返）。"""
    captured = spy_get_agent(monkeypatch)
    drive_input(monkeypatch, script_lines("bind-switch.txt"))
    rc = await cmd_repl(str(project_two_roots), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    # 未绑定态 /messages 提示
    assert "no agent bound" in out
    # /use ghost → 未知 id 提示，绑定不变（下一条提示符仍是 root-a）
    i_unknown = out.index("unknown agent")
    assert "(root-a)>>>" in out[i_unknown:]
    # 切换后消息投递到新目标
    assert "alpha-reply" in out and "beta-reply" in out
    # 原 Agent 不 destroy：/use 回 root-a 时 get_agent 直返同一对象；
    # 脚本中重复 /use root-a 一次——重复绑定不重复订阅（_bind 早退）
    instances_a = captured["root-a#instances"]
    assert len(instances_a) == 3
    assert all(inst is instances_a[0] for inst in instances_a)


async def test_t21b_use_dormant_recovers(project_two_roots, persist_dir, monkeypatch, capsys):
    """/use 名录中休眠 id → 经 get_agent 现场恢复并切换绑定。"""
    drive_input(monkeypatch, ["/exit"])
    assert await cmd_repl(str(project_two_roots), persist=str(persist_dir)) == EXIT_OK
    # 重启：两休眠根 → 未绑定；/use root-a 走 recover 管线恢复
    recovered = spy_recover_agent(monkeypatch)
    drive_input(monkeypatch, ["你好", "/use root-a", "你好", "/exit"])
    assert await cmd_repl(str(project_two_roots), persist=str(persist_dir)) == EXIT_OK
    out = capsys.readouterr().out
    assert "(new agent)>>>" in out       # 启动未绑定
    assert "(root-a)>>>" in out          # /use 恢复并切换
    assert recovered == ["root-a"]       # 恰好 root-a 经 recover 恢复（root-b 仍休眠）


async def test_t22_error_turn_result(project_ok, persist_dir, monkeypatch, capsys):
    """status="error" 的 TurnResult：照常打印（空文本照常过打印点），repl 不退出。"""
    drive_input(monkeypatch, script_lines("error-turn.txt"))
    rc = await cmd_repl(str(project_ok), persist=str(persist_dir), scenario="error")
    assert rc == EXIT_OK
    out, err = capsys.readouterr()
    assert "Traceback" not in err   # 错误文本走 stdout 打印点，不抛栈退出
    assert out.count("(root)>>>") == 2   # 错误回合后回到提示符，/exit 正常退出


async def test_t23_use_corrupt_session(project_two_roots, persist_dir, monkeypatch, capsys):
    """/use 的 id 在名录中但 session 目录损坏 → recover 原生异常被打印，
    绑定不变，进程不退出。"""
    drive_input(monkeypatch, ["/use root-a", "你好", "/exit"])   # 先让 root-a 产生持久化记录
    assert await cmd_repl(str(project_two_roots), persist=str(persist_dir)) == EXIT_OK
    # 腐蚀 root-a 的 tree.jsonl：meta 首行后插入损坏行（X7：中间行损坏 → CorruptionError）
    tree = persist_dir / "root-a" / "tree.jsonl"
    segments = tree.read_bytes().split(b"\n")
    segments.insert(1, b"{corrupted")
    tree.write_bytes(b"\n".join(segments))
    drive_input(monkeypatch, script_lines("use-corrupt.txt"))
    rc = await cmd_repl(str(project_two_roots), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "failed to restore agent" in out
    i_fail = out.index("failed to restore agent")
    assert "(new agent)>>>" in out[i_fail:]   # 绑定不变（仍未绑定）
    assert "(root-b)>>>" in out[i_fail:]      # 完好的 root-b 仍可恢复绑定


async def test_t24_agents_listing_lazy_read(project_ok, persist_dir, monkeypatch, capsys):
    """/agents：行含 agent_id + 最后回复前缀（懒读 tree.jsonl）+ 目录
    mtime；前缀不回写池元数据。"""
    drive_input(monkeypatch, ["你好", "/exit"])
    assert await cmd_repl(str(project_ok), persist=str(persist_dir)) == EXIT_OK
    captured = spy_launch(monkeypatch, repl_mod)
    drive_input(monkeypatch, script_lines("agents-listing.txt"))
    assert await cmd_repl(str(project_ok), persist=str(persist_dir)) == EXIT_OK
    out = capsys.readouterr().out
    assert re.search(r'root\s+"alpha-reply"\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}', out)
    # 前缀不回写池元数据：池条目只有 create 管线写入的裸名键
    meta = captured["runtime"]._agent_pool["root"]
    assert set(meta) <= {"agent_type", "parent_agent_id", "created_at", "session_dir", "args"}


async def test_t25_default_agent(tmp_path):
    """_default_agent：恰一激活根返回之；零根 / 多根 None；非 Agent 节点
    （Workflow 根形态）不计入；同步直读不触发池恢复。"""
    rt = make_runtime(tmp_path)
    add_fake_provider(rt)
    try:
        assert _default_agent(rt) is None            # 零根
        a1 = await rt.create_agent("test-agent")
        assert _default_agent(rt) is a1              # 恰一根
        a2 = await rt.create_agent("test-agent")
        assert _default_agent(rt) is None            # 多根 → None（不抛错）
        await a2.destroy()
        # Workflow 根形态（非 Agent 的 _nodes 条目）天然排除（isinstance 过滤）
        rt._nodes["workflow-x"] = SimpleNamespace(_parent_id=rt.node_id)
        assert _default_agent(rt) is a1
        rt._nodes.pop("workflow-x")   # 摘除假体（shutdown 会递归 destroy 全部节点）
        await a1.destroy()
        assert _default_agent(rt) is None            # destroy 后活体表无根
    finally:
        await rt.shutdown()


async def test_t26_process_display_and_migration(project_two_roots, persist_dir,
                                                 monkeypatch, capsys):
    """过程显示（X3）：绑定后 delta 流式出现在输出；/use 切换后旧 Agent
    的 repl 订阅摘除、新 Agent 挂上（订阅迁移）。"""
    captured = spy_get_agent(monkeypatch)
    drive_input(monkeypatch, script_lines("migrate.txt"))
    rc = await cmd_repl(str(project_two_roots), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    # 两 Agent 的流式文本各恰好出现一次（订阅不重复、不丢失、不错位）
    assert out.count("alpha-reply") == 1
    assert out.count("beta-reply") == 1
    a = captured["root-a"]
    b = captured["root-b"]
    for hooks_of, expect in ((a, 0), (b, 1)):   # 旧 Agent 摘除 / 新 Agent 挂上
        for hook_name in ("on_provider_delta", "on_turn_append", "after_turn"):
            entries = getattr(hooks_of.hooks, hook_name)[:]
            assert sum(1 for e in entries if e.by == "repl") == expect, hook_name


async def test_t26b_tool_message_summary(project_ok, persist_dir, monkeypatch, capsys):
    """过程显示（X3）：工具调用逐调用成行（名称 + 完整参数），结果全文显示。"""
    drive_input(monkeypatch, ["你好", "/exit"])
    rc = await cmd_repl(str(project_ok), persist=str(persist_dir), scenario="tool")
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert '[tool_call] echo {"text": "ping"}' in out   # 调用 1：独立行 + 完整参数
    assert '[tool_call] echo {"text": "pong"}' in out   # 调用 2：独立行，不合并
    assert "[tool:completed] echo -> ping" in out       # TOOL 结果消息全文
    assert "[tool:completed] echo -> pong" in out
    assert "alpha-reply" in out


async def test_t27_default_repl_eval_is_unknown(project_ok, persist_dir, monkeypatch, capsys):
    """默认 repl 收到 /eval → 按未知命令处理（注入点未启用，封闭集不被污染）。"""
    drive_input(monkeypatch, script_lines("eval-in-default-repl.txt"))
    rc = await cmd_repl(str(project_ok), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "unknown command" in out
    assert not re.search(r"(?m)^default$", out)   # 未执行求值


async def test_v3_new_commands(project_ok, persist_dir, monkeypatch, capsys):
    """v3 repl 新命令冒烟：/model /status /export md /help 在绑定根上工作。"""
    spy_launch(monkeypatch, repl_mod)
    drive_input(monkeypatch, ["你好", "/model", "/status", "/export md",
                              "/help", "/exit"])
    rc = await cmd_repl(str(project_ok), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "model_tag=" in out                # /model
    assert "agent_id=" in out and "paused=" in out   # /status
    assert "alpha-reply" in out               # 回合回复（流式）与 /export md 均含
    assert "/agent <agent_id>" in out         # /help 列出新命令
    assert "/use" not in out.splitlines()[0] if out else True   # /help 不列 /use


async def test_model_command_lists_available_tags(project_ok, persist_dir):
    """/model：首行当前模型三要素；第二行列出全部可用 tag（文件序），
    当前生效 tag 以 (*) 标注。"""
    from flowing.runtime import launch
    runtime = await launch(str(project_ok), persist=str(persist_dir))
    try:
        agent = await runtime.get_agent("root")
        lines = await slash_lines("/model", "", agent, runtime)
        assert lines[0].startswith("model_tag=default")
        assert "provider=fake-a" in lines[0] and "model=fake-model-a" in lines[0]
        assert lines[1].startswith("available tags:")
        assert "default(*)" in lines[1]          # 当前 tag 标注
        assert "fast" in lines[1] and "b" in lines[1]
        assert "b(*)" not in lines[1]            # 仅当前 tag 带 (*)
    finally:
        await runtime.shutdown()


async def test_context_command(project_ok, persist_dir, monkeypatch, capsys):
    """/context：窗口 / 估计占用 / 使用率三要素；/context v 追加分类占比
    （system prompt、tools、各 MessageKind×block type）并重归一化到总体。"""
    spy_launch(monkeypatch, repl_mod)
    drive_input(monkeypatch, ["你好", "/context", "/context v", "/exit"])
    rc = await cmd_repl(str(project_ok), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "context_window=100000" in out    # 窗口大小（fixture models.yaml）
    assert "tokens=" in out and "usage=" in out   # 估计占用与使用率
    assert "measured=" in out and "anchor=" in out
    assert "system prompt" in out            # verbose 分类：system prompt
    assert "user/TextBlock" in out           # verbose 分类：MessageKind × block type
    assert "provider/TextBlock" in out
    assert "normalized to tokens=" in out    # 重归一化注脚
    assert "(%)" in out or "%)" in out       # 占比百分比


class _EchoTool(Tool):
    definition = ToolDefinition(
        name="echo", description="回显参数",
        params_schema={"text": {"type": "string"}})

    async def execute(self, *, text: str) -> str:
        return text


async def test_context_verbose_with_tool_messages(tmp_path):
    """回归：链上含 TOOL 消息时 /context v 正常输出分类表（此前 breakdown
    用「单块临时 Message 保留原 kind」复用逐块估算，kind=TOOL 触发
    __post_init__ 双字段不变量 ValueError；提取 estimate_block_tokens
    后不再需要临时消息）。"""
    rt = make_runtime(tmp_path)
    provider = add_fake_provider(rt)
    agent = await rt.create_agent("test-agent")
    rt.tool_registry.register(_EchoTool())
    agent.add_tool("echo")
    step1, _ = tool_call_response(("echo", {"text": "你好"}))
    script_provider(provider, step1, text_response("完成"))
    result = await agent.query("开始")
    assert result.status == "completed"   # 链上已挂 TOOL 消息
    lines = await slash_lines("/context", "v", agent, rt)
    assert any("tool/TextBlock" in line for line in lines)   # TOOL 消息的分类行
    assert any("normalized to tokens=" in line for line in lines)
    await rt.shutdown()


async def test_sigint_aborts_active_turn_not_session():
    """repl 专用 SIGINT 语义：回合进行中 ^C → abort_turn 取消当轮（会话
    存活），不再走 shutdown 桥。"""
    import os
    import signal

    class _StubAgent:
        def __init__(self):
            self.aborted = False

        def abort_turn(self):
            self.aborted = True

    flags = {"query_active": True, "bound_agent": _StubAgent()}
    prev = repl_mod._install_repl_signal_handlers(None, flags)
    try:
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.sleep(0)   # 让信号处理器在事件循环内落地
        assert flags["bound_agent"].aborted
    finally:
        signal.signal(signal.SIGINT, prev)


def test_readline_completer_slash_commands():
    """Tab 补全：行首 / 词从 SLASH_COMMANDS 补全；非斜杠词不补全。"""
    readline = pytest.importorskip("readline")
    prev = repl_mod._install_readline()
    assert prev is not None
    try:
        completer = readline.get_completer()
        assert completer("/he", 0) == "/help"
        assert completer("/e", 0) in ("/exit", "/export")
        assert completer("/e", 1) in ("/exit", "/export")
        assert completer("/e", 2) is None
        assert completer("hello", 0) is None   # 非斜杠词不补全
    finally:
        readline.set_completer(prev)
