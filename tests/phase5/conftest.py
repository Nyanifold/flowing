"""阶段 5 接口层（L6）测试的公共 fixtures。

- 项目路径 fixtures：``fixtures/interfaces/projects/`` 四个最小可拉起样例。
- ``persist_dir``：测试级持久化根（一律指到 tmp，不污染 cwd 与 fixtures）。
- **信号卫生**：每个测试前后保存 / 恢复 SIGINT/SIGTERM 处理器（cmd_* 安装
  的处理器闭包指向当次 Runtime，不恢复会污染后续测试）。

输入脚本驱动、launch / get_agent / recover_agent spy 等助手在
``support.py``（沿用 phase4 的 support 模块模式）。
"""

from __future__ import annotations

import signal
from pathlib import Path

import pytest

from .support import PROJECTS_DIR, make_runtime


@pytest.fixture
def project_ok() -> Path:
    return PROJECTS_DIR / "ok"


@pytest.fixture
def project_two_roots() -> Path:
    return PROJECTS_DIR / "two-roots"


@pytest.fixture
def project_main_raises() -> Path:
    return PROJECTS_DIR / "main-raises"


@pytest.fixture
def project_empty() -> Path:
    return PROJECTS_DIR / "empty"


@pytest.fixture
def persist_dir(tmp_path) -> Path:
    """测试级持久化根（一律指到 tmp，不污染 cwd 与 fixtures 原件）。"""
    return tmp_path / ".flowing"


@pytest.fixture(autouse=True)
def _restore_signal_handlers():
    """cmd_* 安装的信号处理器在测试结束后还原，防跨测试污染。"""
    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGINT, old_int)
    signal.signal(signal.SIGTERM, old_term)


@pytest.fixture
async def bare_runtime(tmp_path):
    """真 Runtime（阶段 2 make_runtime 助手：persist + 假模型配置就位）。"""
    rt = make_runtime(tmp_path)
    yield rt
    if not rt._shutdown_event.is_set():
        await rt.shutdown()
