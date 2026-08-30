"""阶段 5 接口层（L6）测试的支撑模块：路径常量、脚本驱动、spy 工厂。

（沿用 phase4 的 support 模块模式：测试文件直接 ``from support import ...``；
pytest rootdir  prepend 模式把本目录插入 sys.path。）
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).parent.parent

FIXTURES_DIR = TESTS_DIR / "fixtures" / "interfaces"
PROJECTS_DIR = FIXTURES_DIR / "projects"
SCRIPTS_DIR = FIXTURES_DIR / "repl-scripts"

# 复用阶段 2 的真 Runtime 测试助手（make_runtime / add_fake_provider）：
# tests 非包结构，经文件位置动态加载 phase2 conftest 模块。该模块 import 期
# 会 sys.path.insert(0, tests/)——这里做快照/恢复，避免把 tests/ 顶到
# tests/phase2 之前而劫持后者测试内的「from conftest import ...」
_spec = importlib.util.spec_from_file_location(
    "phase2_conftest", TESTS_DIR / "phase2" / "conftest.py")
phase2_conftest = importlib.util.module_from_spec(_spec)
_saved_sys_path = sys.path[:]
try:
    _spec.loader.exec_module(phase2_conftest)   # type: ignore[union-attr]
finally:
    sys.path[:] = _saved_sys_path
make_runtime = phase2_conftest.make_runtime
add_fake_provider = phase2_conftest.add_fake_provider


def script_lines(name: str) -> list[str]:
    """读 ``fixtures/interfaces/repl-scripts/<name>``（一行一条输入）。"""
    return (SCRIPTS_DIR / name).read_text(encoding="utf-8").splitlines()


def drive_input(monkeypatch, lines: list[str]) -> None:
    """把 ``builtins.input`` 换成脚本驱动：prompt 打印到 stdout（tty 行为），
    每行回显，脚本耗尽抛 EOFError。"""
    it = iter(lines)

    def _fake_input(prompt: str = "") -> str:
        print(prompt, end="")
        try:
            line = next(it)
        except StopIteration:
            raise EOFError
        print(line)   # 行回显（tty 行为）
        return line

    monkeypatch.setattr("builtins.input", _fake_input)


def read_tree_records(persist_dir: Path, agent_id: str) -> list[dict]:
    """读 session 目录 tree.jsonl 的全部完整行（测试断言用）。"""
    path = Path(persist_dir) / agent_id / "tree.jsonl"
    if not path.exists():
        return []
    return [json.loads(seg) for seg in path.read_text(encoding="utf-8").splitlines()
            if seg.strip()]


def spy_launch(monkeypatch, mod) -> dict:
    """包装指定 cmd 模块内的 ``launch`` 引用，捕获运行中的 Runtime。"""
    captured: dict = {}
    orig = mod.launch

    async def _wrap(path, **kwargs):
        rt = await orig(path, **kwargs)
        captured["runtime"] = rt
        return rt

    monkeypatch.setattr(mod, "launch", _wrap)
    return captured


def spy_get_agent(monkeypatch) -> dict:
    """spy ``Runtime.get_agent``：记录每次恢复 / 直返的 Agent 实例。"""
    from flowing.runtime import Runtime

    captured: dict[str, object] = {}
    orig = Runtime.get_agent

    async def _wrap(self, agent_id, **kwargs):
        agent = await orig(self, agent_id, **kwargs)
        captured.setdefault(agent_id, agent)   # 首见对象
        captured.setdefault(f"{agent_id}#instances", []).append(agent)   # 全序列（身份断言用）
        return agent

    monkeypatch.setattr(Runtime, "get_agent", _wrap)
    return captured


def spy_recover_agent(monkeypatch) -> list:
    """spy ``Runtime.recover_agent``：记录每次现场恢复的 agent_id 顺序。"""
    from flowing.runtime import Runtime

    recovered: list[str] = []
    orig = Runtime.recover_agent

    async def _wrap(self, agent_id, **kwargs):
        agent = await orig(self, agent_id, **kwargs)
        recovered.append(agent_id)
        return agent

    monkeypatch.setattr(Runtime, "recover_agent", _wrap)
    return recovered
