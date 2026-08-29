"""阶段 4 插件基类测试（W01）：测试清单 T01–T04。

覆盖 ``Plugin`` 基类与 ``Runtime.use()`` 的衔接：install 恰好一次、依赖
缺失警告不抛、依赖成环抛 ``DependencyError``、install 内 provide 的值沿
provide 链对任意 Agent 可见。

真 Runtime 侧由 phase2 conftest 的 ``make_runtime`` 驱动（按路径 importlib
载入——不新建 phase4/conftest.py，避免顶级模块名 ``conftest`` 遮蔽，见
阶段 3 易踩坑笔记）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from flowing.errors import DependencyError
from flowing.plugins import Plugin

# 复用阶段 2 的 make_runtime / SimpleAgent（真 Runtime 测试级构造）；
# 按路径载入避免新增 phase4/conftest.py 遮蔽 phase2 的 conftest 模块名。
_PHASE2_CONFTEST = Path(__file__).parent.parent / "phase2" / "conftest.py"
_spec = importlib.util.spec_from_file_location("phase2_conftest", _PHASE2_CONFTEST)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
make_runtime = _mod.make_runtime


class _RecorderPlugin(Plugin):
    """记录 install 调用次数与实参的最小插件。"""

    name = "recorder"
    dependencies: list[str] = []

    def __init__(self) -> None:
        self.install_calls: list[object] = []

    def install(self, runtime) -> None:
        self.install_calls.append(runtime)


class _DepPlugin(Plugin):
    """携带依赖声明的最小插件。"""

    dependencies: list[str] = []

    def __init__(self, name: str, dependencies: list[str]) -> None:
        self.name = name
        self.dependencies = dependencies

    def install(self, runtime) -> None:
        pass


class _ProvidePlugin(Plugin):
    """install 中 provide 一个键值的最小插件。"""

    name = "provider-p"
    dependencies: list[str] = []

    def install(self, runtime) -> None:
        runtime.provide("k", "v")


# ---------------------------------------------------------------------------
# T01：install 恰好一次，实参为该 runtime
# ---------------------------------------------------------------------------


def test_t01_install_called_once_with_runtime(tmp_path):
    runtime = make_runtime(tmp_path)
    plugin = _RecorderPlugin()
    runtime.use(plugin)
    assert plugin.install_calls == [runtime]


# ---------------------------------------------------------------------------
# T02：依赖缺失 → warnings.warn，不抛错
# ---------------------------------------------------------------------------


def test_t02_missing_dependency_warns_not_raises(tmp_path):
    runtime = make_runtime(tmp_path, register_default_type=False)
    with pytest.warns(UserWarning, match="x"):
        runtime.use(_DepPlugin("p", ["x"]))
    assert runtime.get_plugin("p") is not None  # 安装仍然生效


# ---------------------------------------------------------------------------
# T03：依赖成环 → 第二个 use() 抛 DependencyError
# ---------------------------------------------------------------------------


def test_t03_dependency_cycle_raises(tmp_path):
    runtime = make_runtime(tmp_path, register_default_type=False)
    with pytest.warns(UserWarning, match="b"):   # a 先装时 b 未装：缺失警告（预期）
        runtime.use(_DepPlugin("a", ["b"]))
    with pytest.raises(DependencyError):
        runtime.use(_DepPlugin("b", ["a"]))


# ---------------------------------------------------------------------------
# T04：install 中 provide 的值沿链上溯对任意 Agent 可见
# ---------------------------------------------------------------------------


async def test_t04_provide_in_install_visible_to_agents(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.use(_ProvidePlugin())
    agent = await runtime.create_agent("test-agent")
    assert agent.inject("k") == "v"
