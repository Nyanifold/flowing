"""tests/fakes.py —— 最小假 Agent / 假 Runtime 替身（duck-typed）。

不含真 Agent / Runtime：parsable 的 Agent 绑定求值、provide 上溯、context
逐块求值全部由本模块替身驱动。

替身契约（与被测实现实际读取的属性一一对应）：

- 假 Runtime：``env`` / ``config`` / ``resolve_path``（委托 L0
  ``paths.resolve_path``）/ ``get_node``（``_nodes`` 查表，缺失键抛
  ``KeyError``——T-68 链断裂场景依赖此行为）；同时满足
  ``ProvideNode`` 协议（``node_id`` / ``_provided`` / ``runtime`` 自指
  / ``provide`` / ``inject``），自注册为 ``_nodes`` 首条目（S-12）。
- 假 Agent：``runtime`` / ``_extra`` / ``source_dir`` / ``__dict__``（普通
  实例）/ ``ProvideNode`` 协议面
  / ``parsable()`` 绑定工厂（对应 ``Agent.parsable`` 的替身：构造后置
  ``_instance``）。
"""

from __future__ import annotations

import os
from pathlib import Path
from types import MappingProxyType
from typing import Any

from flowing.parsable import Parsable
from flowing.paths import resolve_path as _resolve_path
from flowing.provide import inject_from


class FakeRuntime:
    """最小假 Runtime：env / config / resolve_path / get_node + ProvideNode。"""

    def __init__(self, project_root: str | Path, *, config: dict | None = None) -> None:
        self.project_root = Path(project_root)
        self.env = MappingProxyType(os.environ)  # os.environ 只读视图（R-5）
        self.config = {} if config is None else config
        # ProvideNode 协议面：Runtime 自指 runtime、无 _parent_id（链终点）
        self.node_id = "runtime-0"
        self._provided: dict[str, Any] = {}
        self._nodes: dict[str, Any] = {self.node_id: self}  # 自注册（S-12）

    @property
    def runtime(self) -> FakeRuntime:
        return self  # Runtime 的 runtime 自指：链终点的协议面表达

    def provide(self, key: str, value: Any) -> None:
        self._provided[key] = value  # 同 key 重复 provide = 覆盖更新（S-37）

    def inject(self, key: str) -> Any:
        return inject_from(self, self, key)

    def register(self, node: Any) -> None:
        self._nodes[node.node_id] = node

    def get_node(self, node_id: str) -> Any:
        return self._nodes[node_id]  # 缺失键抛 KeyError（inject_from 链断裂分支）

    def resolve_path(self, path: str, *, source_dir: Path | None = None) -> Path:
        return _resolve_path(path, project_root=self.project_root, source_dir=source_dir)


class FakeAgent:
    """最小假 Agent：Parsable 绑定求值与 provide 链中间节点。"""

    def __init__(
        self,
        runtime: FakeRuntime,
        *,
        source_dir: str | Path | None = None,
        node_id: str = "agent-fake",
        parent_id: str | None = "runtime-0",
        state: dict | None = None,
        extra: dict | None = None,
    ) -> None:
        self.runtime = runtime
        # source_dir 以方法形态对齐真 Agent（flowing/agent.py Agent.source_dir
        # 是方法；parsable 的 FILE_REF/include 基准经 source_dir() 调用取）——
        # 0904 前此处是普通实例属性，与真 Agent 协议漂移（F2 修复）。
        self._source_dir = None if source_dir is None else Path(source_dir)
        self._extra = {} if extra is None else extra
        # D10：parsable 不再读 agent._state——无状态袋接口（R-6 取消）
        # ProvideNode 协议面
        self.node_id = node_id
        self._provided: dict[str, Any] = {}
        if parent_id is not None:
            self._parent_id = parent_id
        runtime.register(self)

    def source_dir(self) -> Path | None:
        """文件上下文目录（对齐 Agent.source_dir 方法形态）。"""
        return self._source_dir

    def provide(self, key: str, value: Any) -> None:
        self._provided[key] = value

    def inject(self, key: str) -> Any:
        return inject_from(self.runtime, self, key)

    def parsable(self, source: Any) -> Parsable:
        """创建已绑定本实例的 Parsable（``Agent.parsable`` 的替身）。"""
        p = Parsable(source)
        p._instance = self
        return p


class FakeWorkflow:
    """ProvideNode 协议的第三类 duck-typed 节点（仿 Workflow 形状）。"""

    def __init__(self, runtime: FakeRuntime, *, node_id: str = "workflow-fake") -> None:
        self.runtime = runtime
        self.node_id = node_id
        self._provided: dict[str, Any] = {}
        self._parent_id = runtime.node_id
        runtime.register(self)

    def provide(self, key: str, value: Any) -> None:
        self._provided[key] = value

    def inject(self, key: str) -> Any:
        return inject_from(self.runtime, self, key)
