"""阶段 5 T01–T07：``interfaces/__init__.py`` 共享件（parse_kv_args / 常量）
与信号桥（优雅关闭桥为各子命令本地件，此处经 run 模块取同构实现）。"""

from __future__ import annotations

import asyncio
import os
import signal
import threading

from flowing.interfaces import (
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    EXIT_USAGE_ERROR,
    SUBCOMMANDS,
    parse_kv_args,
)
from flowing.interfaces.run import _install_signal_handlers   # 优雅关闭桥（run 本地件，各子命令同构）


def test_constants():
    """项 1：子命令封闭集与退出码常量。"""
    assert SUBCOMMANDS == ("run", "repl", "cli", "repl-debug", "serve", "web", "test", "compile")
    assert (EXIT_OK, EXIT_RUNTIME_ERROR, EXIT_USAGE_ERROR) == (0, 1, 2)


def test_t01_key_value_and_flag():
    assert parse_kv_args(["--a-b", "x", "--flag"]) == {"a_b": "x", "flag": True}


def test_t02_later_overrides():
    assert parse_kv_args(["--k", "1", "--k", "2"]) == {"k": "2"}


def test_t03_empty():
    assert parse_kv_args([]) == {}


def test_t04_equals_form_not_collected():
    """等号形式不支持：不收入结果（报用法错误是 cli.main 层的职责，不在本函数）。"""
    assert parse_kv_args(["--k=v"]) == {}


def test_t05_no_type_inference():
    result = parse_kv_args(["--n", "123"])
    assert result == {"n": "123"}
    assert isinstance(result["n"], str)


def test_t06_non_main_thread_noop_and_idempotent():
    errors: list[BaseException] = []

    def _call():
        try:
            _install_signal_handlers(object())   # 非主线程：no-op 不抛
        except BaseException as exc:             # noqa: BLE001 - 测试要捕获一切
            errors.append(exc)

    t = threading.Thread(target=_call)
    t.start()
    t.join()
    assert errors == []
    # 主线程重复安装两次：幂等不抛（处理器语义相同，后装覆盖先装）
    _install_signal_handlers(object())
    _install_signal_handlers(object())


async def test_t07_sigterm_initiates_shutdown(bare_runtime):
    """对装过处理器的 Runtime 发 SIGTERM → shutdown() 被发起（非阻塞）。"""
    _install_signal_handlers(bare_runtime)
    os.kill(os.getpid(), signal.SIGTERM)
    # 处理器只发起 shutdown（不 sys.exit）：事件置位由事件循环完成
    await asyncio.wait_for(bare_runtime._shutdown_event.wait(), timeout=5)
    assert bare_runtime._shutdown_event.is_set()
