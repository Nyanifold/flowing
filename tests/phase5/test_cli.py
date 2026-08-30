"""阶段 5 T51–T61：``cli.py``（main 分发 + cmd_compile）。"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from flowing.interfaces import (
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    EXIT_USAGE_ERROR,
    SUBCOMMANDS,
)
from flowing.interfaces import cli as cli_mod
from flowing.interfaces.cli import cmd_compile, main

from .support import TESTS_DIR

FYA_AGENTS = TESTS_DIR / "fixtures" / "fya" / "agents"


def _fya_project(tmp_path: Path) -> Path:
    """复制两个可编译 .fya 样例到临时项目（fixtures 原件不落产物）。"""
    project = tmp_path / "proj"
    (project / "agents").mkdir(parents=True)
    shutil.copy(FYA_AGENTS / "payment.fya", project / "agents" / "payment.fya")
    shutil.copytree(FYA_AGENTS / "order-agent", project / "agents" / "order-agent",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return project


# ---------------------------------------------------------------------------
# main 分发
# ---------------------------------------------------------------------------

def test_t51_empty_argv(capsys):
    """main([]) → EXIT_USAGE_ERROR，stderr 含子命令列表。"""
    assert main([]) == EXIT_USAGE_ERROR
    err = capsys.readouterr().err
    for name in SUBCOMMANDS:
        assert name in err


def test_t52_unknown_subcommand(capsys):
    """未知子命令 → EXIT_USAGE_ERROR（精确小写匹配，无前缀/模糊匹配）。"""
    assert main(["frobnicate", "."]) == EXIT_USAGE_ERROR
    assert "frobnicate" in capsys.readouterr().err
    # 前缀命中不算命中（"run2" / 大写 "RUN" 均不是子命令）
    assert main(["run2", "."]) == EXIT_USAGE_ERROR
    assert main(["RUN", "."]) == EXIT_USAGE_ERROR


def test_t53_nonexistent_path(capsys):
    """main(["run", "/nonexistent-dir"]) → EXIT_USAGE_ERROR（CLI 层先于
    launch 拒绝）。"""
    assert main(["run", "/nonexistent-dir"]) == EXIT_USAGE_ERROR
    assert "nonexistent-dir" in capsys.readouterr().err


def _stub_cmds(monkeypatch) -> dict:
    """把 cli 模块内的 cmd_* 引用换成替身协程，记录调用入参。"""
    calls: dict = {}

    def _make(name):
        async def _stub(path, main_file=None, **kwargs):
            calls[name] = {"path": path, "main_file": main_file, **kwargs}
            return 0
        return _stub

    for name in ("cmd_run", "cmd_repl", "cmd_repl_debug", "cmd_serve",
                 "cmd_web", "cmd_test"):
        monkeypatch.setattr(cli_mod, name, _make(name))
    monkeypatch.setattr(cli_mod, "cmd_compile",
                        lambda path: calls.setdefault("cmd_compile", {"path": path}) or 0)
    return calls


def test_t55_arg_stripping(monkeypatch, tmp_path):
    """-m/-a/-p 剥离传给显式参数不进 kwargs；--key val 经 parse_kv_args
    进 kwargs。"""
    calls = _stub_cmds(monkeypatch)
    rc = main(["serve", str(tmp_path), "-m", "alt_main.py", "-a", "0.0.0.0",
               "-p", "9000", "--workspace-root", "/ws", "--debug"])
    assert rc == 0
    call = calls["cmd_serve"]
    assert call["main_file"] == "alt_main.py"
    assert call["host"] == "0.0.0.0"
    assert call["port"] == 9000
    assert call["workspace_root"] == "/ws"   # key 里的 '-' 转 '_'
    assert call["debug"] is True             # 布尔 flag
    # flowing 级参数不进 main 的 kwargs
    assert "m" not in call and "a" not in call and "p" not in call
    assert "host" not in call.get("kwargs", {})


def test_t55b_repl_has_no_host_port(monkeypatch, tmp_path):
    """repl/test 等非 serve/web 子命令不收 host/port 显式参数。"""
    calls = _stub_cmds(monkeypatch)
    assert main(["test", str(tmp_path), "-m", "t.py", "--key", "val"]) == 0
    call = calls["cmd_test"]
    assert call["main_file"] == "t.py"
    assert call["key"] == "val"
    assert "host" not in call and "port" not in call


def test_t56_help(monkeypatch, tmp_path, capsys):
    """-h / --help → 打印该子命令用法到 stdout，返回 EXIT_OK。"""
    calls = _stub_cmds(monkeypatch)
    assert main(["serve", str(tmp_path), "-h"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "flowing serve" in out
    assert main(["web", "--help"]) == EXIT_OK   # path 缺省 "."
    assert "flowing web" in capsys.readouterr().out
    assert not calls   # help 不分发
    # --help 不透传给 main：带 --help 时不进 kwargs（上两条未分发即证明）


def test_t57_bare_arg(tmp_path, capsys):
    """<path> 之后出现裸参数 → EXIT_USAGE_ERROR。"""
    assert main(["run", str(tmp_path), "barearg"]) == EXIT_USAGE_ERROR
    assert "barearg" in capsys.readouterr().err


def test_t04_equals_form(tmp_path, capsys):
    """--key=value 等号形式 → EXIT_USAGE_ERROR（T04 的 main 层落地）。"""
    assert main(["run", str(tmp_path), "--k=v"]) == EXIT_USAGE_ERROR
    assert "--k=v" in capsys.readouterr().err


def test_r9_single_dash_failures(tmp_path, capsys):
    """-p 非数字 / -m 缺值 / 未知单横线参数 → EXIT_USAGE_ERROR + stderr
    用法说明。"""
    assert main(["serve", str(tmp_path), "-p", "abc"]) == EXIT_USAGE_ERROR
    assert "端口须为数字" in capsys.readouterr().err
    assert main(["run", str(tmp_path), "-m"]) == EXIT_USAGE_ERROR
    assert "-m 缺值" in capsys.readouterr().err
    assert main(["run", str(tmp_path), "-x", "1"]) == EXIT_USAGE_ERROR
    assert "-x" in capsys.readouterr().err
    assert main(["run", str(tmp_path), "--"]) == EXIT_USAGE_ERROR


def test_t54_cli_alias_same_path(monkeypatch, tmp_path):
    """main(["cli", path]) 与 main(["repl", path]) 进入完全相同的 repl
    代码路径（同一 cmd_repl 引用、同一入参）。"""
    calls = _stub_cmds(monkeypatch)
    assert main(["cli", str(tmp_path), "--k", "v"]) == 0
    assert main(["repl", str(tmp_path), "--k", "v"]) == 0
    assert "cmd_repl" in calls
    # 两次调用入参完全一致（同一代码路径）
    assert list(calls.values()).count(calls["cmd_repl"]) >= 1
    assert calls["cmd_repl"] == {"path": str(tmp_path), "main_file": None, "k": "v"}


def test_default_path_is_cwd(monkeypatch, tmp_path):
    """<path> 缺省 "."（flowing test --key val 等价 flowing test . ...）。"""
    calls = _stub_cmds(monkeypatch)
    assert main(["test", "--key", "val"]) == 0
    assert calls["cmd_test"]["path"] == "."


# ---------------------------------------------------------------------------
# cmd_compile
# ---------------------------------------------------------------------------

def test_t58_compile_twice_noop(tmp_path, capsys):
    """含两个 .fya 的项目 compile 两次：首次产出两个同目录 .py +
    .flowing.meta.yaml；第二次 no-op（mtime 与 hash 均不变）。"""
    project = _fya_project(tmp_path)
    assert main(["compile", str(project)]) == EXIT_OK
    py1 = project / "agents" / "payment.py"
    py2 = project / "agents" / "order-agent" / "agent.py"
    meta1 = project / "agents" / ".flowing.meta.yaml"
    meta2 = project / "agents" / "order-agent" / ".flowing.meta.yaml"
    for f in (py1, py2, meta1, meta2):
        assert f.is_file(), f
    mtimes = {f: f.stat().st_mtime_ns for f in (py1, py2, meta1, meta2)}
    contents = {f: f.read_bytes() for f in (py1, py2)}

    assert main(["compile", str(project)]) == EXIT_OK   # 幂等 no-op
    for f, m in mtimes.items():
        assert f.stat().st_mtime_ns == m, f"{f} mtime 变化"
    for f, c in contents.items():
        assert f.read_bytes() == c   # hash 不变（内容逐字节相同）


def test_t59_artifact_modified(tmp_path, capsys):
    """手工改动某个产物 .py 后 compile → EXIT_RUNTIME_ERROR，stderr 含该
    文件路径，文件未被覆盖，其他已产出文件不回滚。"""
    project = _fya_project(tmp_path)
    assert main(["compile", str(project)]) == EXIT_OK
    py1 = project / "agents" / "payment.py"
    py2 = project / "agents" / "order-agent" / "agent.py"
    # 语义级手改（py_hash 为 AST 口径，注释/空行不触发，改字段值才触发）
    tampered = py1.read_text(encoding="utf-8").replace("'CNY'", "'USD'")
    assert tampered != py1.read_text(encoding="utf-8")
    py1.write_text(tampered, encoding="utf-8")
    other = py2.read_bytes()

    assert main(["compile", str(project)]) == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert str(py1) in err or "payment.py" in err
    assert py1.read_text(encoding="utf-8") == tampered   # 未被覆盖
    assert py2.read_bytes() == other   # 已产出文件不回滚


def test_t60_no_fya(tmp_path, capsys):
    """项目无 .fya → 打印「无可编译文件」，返回 EXIT_OK。"""
    project = tmp_path / "empty-proj"
    project.mkdir()
    assert main(["compile", str(project)]) == EXIT_OK
    assert "无可编译文件" in capsys.readouterr().out


def test_t61_compile_no_event_loop(monkeypatch, tmp_path):
    """compile 同步直调，不进入 asyncio 事件循环（asyncio.run 哨兵）。"""
    project = _fya_project(tmp_path)

    def _forbidden(coro):
        raise AssertionError("compile 不应进入 asyncio.run")

    monkeypatch.setattr(asyncio, "run", _forbidden)
    assert main(["compile", str(project)]) == EXIT_OK
    assert (project / "agents" / "payment.py").is_file()   # 同步编译确实发生
