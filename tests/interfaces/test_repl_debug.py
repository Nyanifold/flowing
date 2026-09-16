"""T28–T34：``repl_debug.py``（/eval /watch /eval-runtime /watch-runtime）。

全部经 ``cmd_repl_debug`` 委托 ``cmd_repl`` 的注入点驱动，输入脚本见
``fixtures/interfaces/repl-scripts/debug-*.txt``。
"""

from __future__ import annotations

import re

from flowing.interfaces import EXIT_OK
from flowing.interfaces.repl_debug import cmd_repl_debug

from .support import drive_input, read_tree_records, script_lines


async def test_t28_eval_model_tag(project_ok, persist_dir, monkeypatch, capsys):
    """已绑定，/eval model_tag → 打印当前值，不产生任何消息节点。"""
    drive_input(monkeypatch, script_lines("debug-eval.txt"))
    rc = await cmd_repl_debug(str(project_ok), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert re.search(r"(?m)^default$", out)   # model_tag 当前值独占一行
    assert "alpha-reply" not in out           # 未发起任何回合
    messages = [r for r in read_tree_records(persist_dir, "root")
                if r.get("type") == "message"]
    assert messages == []                     # 不产生消息节点


async def test_t29_watch_reprint_on_change(project_ok, persist_dir, monkeypatch, capsys):
    """/watch current_mode：首次打印；值变化后（首回合翻转）再次进提示符
    自动打印新值；值未变不打印。"""
    drive_input(monkeypatch, script_lines("debug-watch.txt"))
    rc = await cmd_repl_debug(str(project_ok), persist=str(persist_dir), scenario="debug")
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "[watch] current_mode => init" in out        # 首次立即打印
    assert "[watch] current_mode => after-turn" in out  # 变更后自动打印
    assert out.count("[watch] current_mode =>") == 2    # 值未变不再打印


async def test_t30_eval_unbound(project_empty, persist_dir, monkeypatch, capsys):
    """未绑定，/eval model_tag → 打印「no agent bound」。"""
    drive_input(monkeypatch, script_lines("debug-eval.txt"))
    rc = await cmd_repl_debug(str(project_empty), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "no agent bound: cannot evaluate expression" in out
    assert not re.search(r"(?m)^default$", out)


async def test_t31_eval_bad_syntax(project_ok, persist_dir, monkeypatch, capsys):
    """/eval {{ bad syntax → 原样打印异常（不包装、无 traceback），回到提示符。"""
    drive_input(monkeypatch, script_lines("debug-bad-syntax.txt"))
    rc = await cmd_repl_debug(str(project_ok), persist=str(persist_dir))
    assert rc == EXIT_OK
    out, err = capsys.readouterr()
    assert "syntax" in out          # 异常 str 原样打印
    assert "Traceback" not in err   # 无 traceback
    assert out.count("(root)>>>") == 2   # 回到提示符，不退出


async def test_t32_eval_runtime(project_empty, persist_dir, monkeypatch, capsys):
    """任意状态（未绑定也可），/eval-runtime self.snapshot().plugins →
    打印插件列表。"""
    drive_input(monkeypatch, script_lines("debug-eval-runtime.txt"))
    rc = await cmd_repl_debug(str(project_empty), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert re.search(r"(?m)^\[\]$", out)   # 空插件列表原样打印（不依赖绑定）


async def test_t33_empty_expr_usage(project_ok, persist_dir, monkeypatch, capsys):
    """/eval、/watch 空表达式 → 打印用法提示，不执行。"""
    drive_input(monkeypatch, script_lines("debug-usage.txt"))
    rc = await cmd_repl_debug(str(project_ok), persist=str(persist_dir))
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "usage: /eval <expr>" in out
    assert "usage: /watch <expr>" in out


async def test_t34_watch_error_retained(project_ok, persist_dir, monkeypatch, capsys):
    """watch 求值报错 → 打印错误且保留该 watch，下次继续尝试。"""
    drive_input(monkeypatch, script_lines("debug-watch-error.txt"))
    rc = await cmd_repl_debug(str(project_ok), persist=str(persist_dir), scenario="debug")
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "[watch] self.fragile() => ok" in out   # 注册时求值成功
    # 首回合后 fragile() 必抛错：每次 pre_prompt 重估都打印错误且保留 watch
    # （回合 1 后一次、/eval 后一次、回合 2 后一次），/eval 自身再打印一次
    assert out.count("fragile broken") == 4


async def test_t34b_watch_migration(project_two_roots, persist_dir, monkeypatch, capsys):
    """/use 切换后 Agent watch 改用新 Agent；Runtime watch 不受影响。"""
    drive_input(monkeypatch, script_lines("debug-watch-migrate.txt"))
    rc = await cmd_repl_debug(str(project_two_roots), persist=str(persist_dir),
                              scenario="debug")
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "[watch] node_id => root-a" in out   # 注册时（绑定 root-a）
    assert "[watch] node_id => root-b" in out   # /use root-b 后重估打印新值
    assert out.count("[watch] self.node_id => runtime-0") == 1   # Runtime watch 不受切换影响
