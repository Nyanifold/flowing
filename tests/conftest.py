"""pytest 公共配置：fixtures 目录定位、语料拷贝工具与共享 Agent fixtures。

``runtime`` / ``provider`` / ``agent`` 三个 fixture 只依赖 ``harness`` 的最小
替身面（HarnessRuntime + FakeProvider），本目录下任何模块测试都可直接用。
"""

from pathlib import Path

import pytest

from flowing.providers import FakeProvider

from harness import HarnessRuntime, SimpleAgent

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """``tests/fixtures/`` 根目录（目录即契约，见总路线图 §3）。"""
    return FIXTURES_DIR


@pytest.fixture
def copy_fixture(tmp_path):
    """把 fixtures 下的文件拷入 tmp_path 后返回副本路径。

    FileRecordStore 的压缩 / 迁移回写会原地改写文件，语料原件必须保持
     pristine，凡涉及写行为的测试一律经本 fixture 取副本。
    """

    def _copy(rel: str) -> Path:
        src = FIXTURES_DIR / rel
        dst = tmp_path / src.name
        dst.write_bytes(src.read_bytes())
        return dst

    return _copy


@pytest.fixture
async def runtime(tmp_path):
    """最小 HarnessRuntime（已注册 ``test-agent`` 类型），测试后销毁存活 Agent。"""
    rt = HarnessRuntime(tmp_path)
    rt.register_agent_type(SimpleAgent, name="test-agent")
    yield rt
    # 收尾：销毁全部存活 Agent（关 store、停 drain 任务），防跨测试泄漏
    for node_id, node in list(rt._nodes.items()):
        if node is rt:
            continue
        try:
            await node.destroy()
        except Exception:
            pass


@pytest.fixture
def provider(runtime) -> FakeProvider:
    """挂在 ``runtime`` 上名为 ``"fake"`` 的 FakeProvider（脚本回放用）。"""
    p = FakeProvider()
    runtime.add_provider("fake", p)
    return p


@pytest.fixture
async def agent(runtime, provider):
    """一个已启动工作循环的空闲测试 Agent。"""
    return await runtime.create_agent("test-agent")
