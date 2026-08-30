"""阶段 5 T08–T13：``run.py``（cmd_run / cmd_test）。"""

from __future__ import annotations

import asyncio
import os
import signal

from flowing.interfaces import EXIT_OK, EXIT_RUNTIME_ERROR
from flowing.interfaces import run as run_mod
from flowing.interfaces.run import cmd_run, cmd_test
from flowing.runtime import Runtime

from .support import spy_launch


async def _wait_signal_handler_installed(task: asyncio.Task) -> None:
    """等 cmd_run 装上 SIGTERM 处理器（处理器不再是默认 SIG_DFL）。"""
    for _ in range(1000):
        if task.done() or signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("信号处理器未及时安装")


async def test_t08_run_sigterm_graceful_shutdown(project_ok, persist_dir, monkeypatch):
    """合法项目 cmd_run 后发 SIGTERM → 优雅关闭完整（destroy 后活体表只剩
    Runtime 自身、_shutdown_event 置位），退出码 EXIT_OK。"""
    captured = spy_launch(monkeypatch, run_mod)
    task = asyncio.create_task(cmd_run(str(project_ok), persist=str(persist_dir)))
    await _wait_signal_handler_installed(task)
    assert not task.done()   # 长驻：await runtime 阻塞等待信号
    os.kill(os.getpid(), signal.SIGTERM)
    rc = await asyncio.wait_for(task, timeout=10)
    assert rc == EXIT_OK
    rt = captured["runtime"]
    assert rt._shutdown_event.is_set()
    assert set(rt._nodes) == {rt.node_id}   # destroy 完成：Agent 全部摘除


async def test_t09_main_raises(project_main_raises, capsys):
    """main 抛 ValueError → EXIT_RUNTIME_ERROR，stderr 含异常信息，不再等信号。"""
    rc = await asyncio.wait_for(cmd_run(str(project_main_raises)), timeout=10)
    assert rc == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "main boom" in err
    assert "launch" in err


async def test_t10_empty_project_idle(project_empty, persist_dir):
    """未 mount 任何节点的项目：合法空转，await runtime 只等 shutdown。"""
    task = asyncio.create_task(cmd_run(str(project_empty), persist=str(persist_dir)))
    await _wait_signal_handler_installed(task)
    assert not task.done()   # 空转不退出
    os.kill(os.getpid(), signal.SIGTERM)
    rc = await asyncio.wait_for(task, timeout=10)
    assert rc == EXIT_OK


async def test_t11_test_ok(project_ok, persist_dir, monkeypatch):
    """合法项目 cmd_test → 退出码 0，有限时间内退出（shutdown 无悬挂）。"""
    captured = spy_launch(monkeypatch, run_mod)
    rc = await asyncio.wait_for(cmd_test(str(project_ok), persist=str(persist_dir)), timeout=10)
    assert rc == EXIT_OK
    assert captured["runtime"]._shutdown_event.is_set()


async def test_t12_mount_missing_fya(project_main_raises, capsys):
    """main 中 mount("@/missing.fya") → 退出码 1，stderr 指出失败在 launch 阶段。"""
    rc = await cmd_test(str(project_main_raises), scenario="mount-missing")
    assert rc == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "launch" in err


async def test_t13_snapshot_failure_still_shuts_down(project_ok, persist_dir, monkeypatch):
    """快照冒烟断言失败 → EXIT_RUNTIME_ERROR，且仍执行 shutdown 后才退出。"""
    captured = spy_launch(monkeypatch, run_mod)

    def _boom(self, **kwargs):
        raise RuntimeError("snapshot broken")

    monkeypatch.setattr(Runtime, "snapshot", _boom)
    rc = await cmd_test(str(project_ok), persist=str(persist_dir))
    assert rc == EXIT_RUNTIME_ERROR
    assert captured["runtime"]._shutdown_event.is_set()   # shutdown 仍执行
