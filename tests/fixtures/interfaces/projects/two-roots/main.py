"""接口层测试夹具项目：两个根 Agent（id 定点 ``root-a`` / ``root-b``）。

与 ``projects/ok`` 同构（FakeProvider 注入通道与场景 kwarg 见该项目的
模块 docstring）；root-a 走 provider ``fake-a``（回复 ``alpha-reply``），
root-b 走 ``fake-b``（回复 ``beta-reply``），便于断言绑定迁移后
“旧 Agent 的 delta 不再打印、新 Agent 的打印”。
"""

from __future__ import annotations

from typing import Any

import flowing
from flowing import Runtime
from flowing.agent import Agent
from flowing.message import Message, MessageKind, TextBlock, ToolCallBlock
from flowing.parsable import Parsable
from flowing.providers import FakeProvider, ProviderDelta, ProviderResponse
from flowing.tool import Tool, ToolDefinition


class Echo(Tool):
    """最小回显工具（过程显示的 TOOL 消息摘要回归用）；显式声明 LLM 视图，
    参数 schema 走桥接子集（不自动生成）。"""

    definition = ToolDefinition(name="echo", description="回显文本",
                                params_schema={"text": {"type": "string"}})

    async def execute(self, *, text: str) -> str:
        return text


def _text_response(text: str) -> ProviderResponse:
    return ProviderResponse(
        message=Message(kind=MessageKind.PROVIDER, content=[TextBlock(text=text)]),
        finish=True, provider_data={"stop_reason": "end_turn"})


def _make_provider(reply: str, scenario: str) -> FakeProvider:
    """按场景构造 FakeProvider（注入函数承担 Provider 契约；构造后赋
    generate_fn / stream_fn 实例属性）。"""
    provider = FakeProvider()
    if scenario == "error":
        async def _gen_error(context: Any, model: Any) -> ProviderResponse:
            raise RuntimeError("fake provider down")

        provider.generate_fn = _gen_error
        return provider
    if scenario == "tool":
        calls = {"n": 0}

        async def _gen_tool(context: Any, model: Any) -> ProviderResponse:
            calls["n"] += 1
            if calls["n"] == 1:
                return ProviderResponse(
                    message=Message(kind=MessageKind.PROVIDER, content=[
                        ToolCallBlock(id="call-echo-1", name="echo",
                                      args={"text": "ping"})]),
                    finish=False, provider_data={"stop_reason": "tool_calls"})
            return _text_response(reply)

        provider.generate_fn = _gen_tool
        return provider

    async def _stream(context: Any, model: Any):
        mid = max(len(reply) // 2, 1)
        yield ProviderDelta(kind="text", text=reply[:mid], content_index=0)
        yield ProviderDelta(kind="text", text=reply[mid:], content_index=0)

    provider.stream_fn = _stream
    return provider


class RootAgent(Agent):
    """最小根 Agent：固定 system_prompt，debug 场景带可观察字段。"""

    system_prompt = Parsable("你是接口层测试助手。")

    async def setup(self, scenario: str = "ok", **kwargs: Any) -> None:
        self.model_tag = "b" if self.node_id.endswith("-b") else "default"
        if scenario == "tool":
            self.add_tool("echo")   # 工具调用按别名查 Agent 级绑定（_tool_entries）
        if scenario == "debug":
            self.current_mode = "init"
            self._fragile_ok = True
            self.hooks.after_turn(self._flip_debug_state, by="fixture")

    def _flip_debug_state(self, host: Agent, turn: Any) -> Any:
        """after_turn 观察 handler：首回合后翻转 debug 字段（/watch 回归用）。"""
        if self.current_mode == "init":
            self.current_mode = "after-turn"
        self._fragile_ok = False
        return turn

    def fragile(self) -> str:
        """首回合后必抛错的方法（/watch 求值报错保留语义的回归载体）。"""
        if not self._fragile_ok:
            raise RuntimeError("fragile broken")
        return "ok"


async def main(persist: str | None = None, scenario: str = "ok") -> Runtime:
    runtime = Runtime(persist_dir=persist)
    runtime.set_models(flowing.resolve("@/models.yaml"))
    runtime.set_model_tags(flowing.resolve("@/model-tags.yaml"))
    runtime.provider_registry._instances["fake-a"] = _make_provider("alpha-reply", scenario)
    runtime.provider_registry._instances["fake-b"] = _make_provider("beta-reply", scenario)
    runtime.register_agent_type(RootAgent, name="root")
    if scenario == "tool":
        runtime.register_tool(Echo())
    if not runtime._agent_pool:
        # 项目策略：仅全新项目建双根；重启时不重复建（池回退交由 repl 处理）
        await runtime.create_agent("root", agent_id="root-a", scenario=scenario)
        await runtime.create_agent("root", agent_id="root-b", scenario=scenario)
    return runtime
