"""接口层测试的支撑模块：路径常量、脚本驱动、spy 工厂。

测试文件直接 ``from support import ...``；pytest rootdir prepend 模式把本目录
（包形态，含 ``__init__.py``）插入 sys.path。
"""

from __future__ import annotations

import json
from pathlib import Path

from harness import add_fake_provider, make_runtime

TESTS_DIR = Path(__file__).parent.parent

FIXTURES_DIR = TESTS_DIR / "fixtures" / "interfaces"
PROJECTS_DIR = FIXTURES_DIR / "projects"
SCRIPTS_DIR = FIXTURES_DIR / "repl-scripts"


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


# ---------------------------------------------------------------------------
# serve / web 测试的 HTTP 助手（真回环端口 + httpx 客户端）
# ---------------------------------------------------------------------------

import asyncio
import socket
from dataclasses import dataclass
from typing import Any

import httpx


def free_port() -> int:
    """向内核申请一个空闲回环端口（bind 0 后立即释放，小竞争窗口在
    127.0.0.1 上可接受）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class HttpHandle:
    """一次 cmd_serve / cmd_web 拉起的测试句柄。"""

    task: asyncio.Task
    client: httpx.AsyncClient
    runtime: Any
    port: int


async def wait_until(cond, timeout: float = 10.0) -> None:
    """轮询等待条件成立（事件序列驱动断言的就绪同步点）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not cond():
        if loop.time() > deadline:
            raise AssertionError("wait_until 超时")
        await asyncio.sleep(0.02)


async def start_http(monkeypatch, mod, cmd, project, persist_dir,
                     port: int | None = None, **kwargs) -> HttpHandle:
    """以真回环端口拉起 cmd_serve / cmd_web：spy launch 捕获 Runtime，
    轮询 ``/healthz`` 就绪后返回句柄。"""
    captured = spy_launch(monkeypatch, mod)
    port = port or free_port()
    task = asyncio.create_task(
        cmd(str(project), persist=str(persist_dir), port=port, **kwargs))
    client = httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", timeout=httpx.Timeout(15.0))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while True:
        if task.done():
            raise AssertionError(f"HTTP 子命令提前退出：rc={task.result()}")
        if "runtime" in captured:
            try:
                if (await client.get("/healthz")).status_code == 200:
                    break
            except httpx.ConnectError:
                pass
        if loop.time() > deadline:
            raise AssertionError("HTTP 服务未及时就绪")
        await asyncio.sleep(0.02)
    return HttpHandle(task=task, client=client, runtime=captured["runtime"], port=port)


async def stop_http(handle: HttpHandle, expected_rc: int = 0) -> int:
    """shutdown Runtime → cmd 任务应以预期退出码收尾；客户端随后关闭。"""
    await handle.runtime.shutdown()
    rc = await asyncio.wait_for(handle.task, timeout=10)
    assert rc == expected_rc
    await handle.client.aclose()
    return rc
