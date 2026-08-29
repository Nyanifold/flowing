"""阶段 2 agent 批次（W11–W28）测试的公共 harness。

背景：``runtime.py``（W29–W40）属下一批次，本批次测试经最小 harness 直接
驱动真 ``Agent``——``HarnessRuntime`` 提供 Agent 消费的 runtime 侧接口面
（``provider_registry`` / ``tool_registry`` / ``_nodes`` / ``_agent_pool`` /
ProvideNode 协议 / ``resolve_path`` / ``env`` / ``config`` /
``get_agent_class`` / ``create_agent`` 迷你管线 / ``_model_tags_path`` /
``_models_path``），``create_agent`` 承担 create 管线的最小步骤
（``__new__`` 预绑 ``node_id`` / ``runtime`` / ``_parent_id`` /
``_session_dir`` → ``__init__`` → ``setup()`` → 写闸门解锁 → 池/活体表
注册 → 工作循环 Task 启动）。runtime 批次落地后本 harness 可由真管线替换。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))   # tests/ 目录入径（fakes.py）

from flowing.agent import Agent
from flowing.builtins import register_builtins
from flowing.errors import ResourceNotFoundError
from flowing.message import Message, MessageKind, TextBlock, ToolCallBlock
from flowing.model import ModelConfig
from flowing.parsable import Parsable
from flowing.paths import pascal_to_kebab
from flowing.providers import FakeProvider, ProviderRegistry, ProviderResponse, Usage

from fakes import FakeRuntime


class SimpleAgent(Agent):
    """最小测试 Agent：固定 system_prompt，无额外装配。"""

    system_prompt = Parsable("你是测试助手。")

    async def setup(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class HarnessRuntime(FakeRuntime):
    """Agent 消费的 runtime 侧最小接口面（阶段 2 agent 批次专用）。"""

    def __init__(self, project_root: str | Path, *, config: dict | None = None) -> None:
        super().__init__(project_root, config=config)
        self._persist_dir = self.project_root / "sessions"
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self.provider_registry = ProviderRegistry({})
        self.tool_registry = ToolRegistryPlaceholder()
        self._agent_pool: dict[str, dict[str, Any]] = {}
        self._agent_classes: dict[str, type[Agent]] = {}
        self._model_tags_path: Path | None = None
        self._models_path: Path | None = None
        register_builtins(self)   # 内置 finish 工具（R-02 占位注册面）

    # -- 注册面 ------------------------------------------------------------
    def register_tool(self, tool: Any, *, name: str | None = None,
                      namespace: str | None = None) -> None:
        self.tool_registry.register(tool, name=name, namespace=namespace)

    def register_agent_type(self, name: str, cls: type[Agent],
                            *, namespace: str = "default") -> None:
        cls.registry_key = f"{namespace}::{name}"
        self._agent_classes[name] = cls

    def get_agent_class(self, agent_type: str, *, source_dir: Path | None = None) -> type[Agent]:
        return self._agent_classes[agent_type]   # 裸名只查注册表；KeyError 口径

    def get_resource(self, name: str, type_hint: Any = None) -> Any:
        raise ResourceNotFoundError(name)

    def add_provider(self, name: str, provider: Any) -> None:
        """预置 provider 实例（绕过懒创建，测试直挂 FakeProvider）。"""
        self.provider_registry._instances[name] = provider

    # -- 迷你 create 管线（W35 真身属 runtime 批次） -------------------------
    async def create_agent(
        self,
        agent_type: str | type[Agent],
        *,
        parent_id: str | None = None,
        session_dir: str | Path | None = None,
        model: ModelConfig | None = None,
        unlock_gate: bool = True,
        start_loop: bool = True,
        **setup_kwargs: Any,
    ) -> Agent:
        cls = (self.get_agent_class(agent_type)
               if isinstance(agent_type, str) else agent_type)
        agent_id = f"agent-{uuid4().hex}"
        inst = cls.__new__(cls)
        inst.node_id = agent_id
        inst.runtime = self
        inst._parent_id = parent_id or self.node_id
        inst._session_dir = (Path(session_dir) if session_dir is not None
                             else self._persist_dir / agent_id)
        inst._session_dir.mkdir(parents=True, exist_ok=True)
        inst.__init__()
        await inst.setup(**setup_kwargs)
        if unlock_gate:
            # create 管线「初始 state 写盘」后的解锁点（真身属 W35）
            object.__setattr__(inst._state_bag, "_write_gate_open", True)
        inst.model = model or ModelConfig(
            model="fake-model", provider="fake",
            context_window=100000, max_output_tokens=4096)
        self._agent_pool[agent_id] = {
            "agent_type": (agent_type if isinstance(agent_type, str)
                           else pascal_to_kebab(cls.__name__)),
            "session_dir": str(inst._session_dir),
            "args": dict(setup_kwargs),
        }
        self._nodes[agent_id] = inst
        if start_loop:
            inst._loop_task = asyncio.create_task(inst._work_loop())
        return inst

    async def get_agent(self, agent_id: str, *, strict: bool = False) -> Any:
        return self._nodes.get(agent_id)   # 「有 key 无 value → 现场恢复」属 runtime 批次


class ToolRegistryPlaceholder:
    """最小工具注册表替身：bare-name 视图（default:: 优先于 builtin::）。

    与 R-02 占位 ``flowing.tool.ToolRegistry`` 同语义；独立替身是为了让
    测试断言不依赖 L4 真身的文件链行为。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def register(self, tool: Any, *, name: str | None = None,
                 namespace: str | None = None) -> None:
        ns = namespace or "default"
        bare = name if name is not None else tool.definition.name
        key = f"{ns}::{bare}"
        self._tools[key] = tool
        tool.registry_key = key

    def get(self, name_or_path: str, *, source_dir: Path | None = None) -> Any:
        if "::" in name_or_path:
            return self._tools[name_or_path]
        for ns in ("default", "builtin"):
            if f"{ns}::{name_or_path}" in self._tools:
                return self._tools[f"{ns}::{name_or_path}"]
        from flowing.errors import ToolNotFoundError

        raise ToolNotFoundError(name_or_path)


# ---------------------------------------------------------------------------
# Provider 响应构造助手（注入函数承担 Provider 契约，见 FakeProvider 规约）
# ---------------------------------------------------------------------------


def text_response(text: str, *, usage: Usage | None = None,
                  finish: bool = True, stop_reason: str = "end_turn") -> ProviderResponse:
    """一条纯文本 PROVIDER 响应（finish=True 自然收尾）。"""
    msg = Message(kind=MessageKind.PROVIDER, content=[TextBlock(text=text)],
                  usage=usage)
    return ProviderResponse(message=msg, finish=finish,
                            provider_data={"stop_reason": stop_reason})


def tool_call_response(*calls: tuple[str, dict], text: str = "") -> ProviderResponse:
    """一条携带 tool_call 块的 PROVIDER 响应（finish=False 进工具循环）。

    ``calls`` 元素为 ``(工具别名, args)``；返回 ``(response, [ToolCallBlock])``
    以便测试按 id 断言配对。
    """
    blocks = [TextBlock(text=text)] if text else []
    call_blocks = [
        ToolCallBlock(id=f"call-{i}-{uuid4().hex[:8]}", name=name, args=args)
        for i, (name, args) in enumerate(calls)
    ]
    msg = Message(kind=MessageKind.PROVIDER, content=[*blocks, *call_blocks])
    return ProviderResponse(message=msg, finish=False,
                            provider_data={"stop_reason": "tool_calls"}), call_blocks


def script_provider(provider: FakeProvider, *steps: Any) -> None:
    """把 FakeProvider 绑成脚本回放：逐步弹出 ProviderResponse / 异常 /
    ``async (context, model) -> ProviderResponse`` 回调。"""
    it = iter(steps)

    async def _gen(context: Any, model: Any) -> ProviderResponse:
        step = next(it)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return await step(context, model)
        return step

    provider.generate_fn = _gen


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def runtime(tmp_path):
    rt = HarnessRuntime(tmp_path)
    rt.register_agent_type("test-agent", SimpleAgent)
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
    p = FakeProvider()
    runtime.add_provider("fake", p)
    return p


@pytest.fixture
async def agent(runtime, provider):
    """一个已启动工作循环的空闲测试 Agent。"""
    return await runtime.create_agent("test-agent")
