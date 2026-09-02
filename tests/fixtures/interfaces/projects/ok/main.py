"""阶段 5 接口层测试夹具项目：单根 Agent（id 定点 ``root``），FakeProvider 驱动。

测试 Provider 的注入通道：``cmd_* → launch → main(**kwargs)`` 是接口层
「不解析配置」边界下唯一的参数通道——``--scenario`` / ``--persist`` 经
kwargs 进来，由项目 ``main`` 自己的策略把 FakeProvider 预置进
``provider_registry._instances``（与阶段 2 测试「直挂 FakeProvider」
同一 hook）。接口层自始至终不感知这些语义。

场景一览（``scenario`` kwarg）：

- ``ok``（默认）：流式两段 delta 回复。
- ``error``：provider 每次调用抛 ``RuntimeError`` → ``TurnResult(status="error")``。
- ``tool``：首轮返回 ``echo`` 工具调用，次轮流式文本回复（过程显示回归用）。
- ``debug``：``ok`` 之上加 ``current_mode`` / ``fragile()`` 可观察字段，
  首个回合后经 ``after_turn`` handler 翻转（repl-debug 的 /watch 回归用）。

身份连续策略（项目级「新建 vs 恢复」策略归 main，不归框架）：池名录为空
才建根；有盘上记录时不重复创建，留给 repl 启动绑定的池回退恢复。
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
        # 显式钉住 model_tag：赋值落实例 __dict__（repl-debug 的
        # ``/eval model_tag`` 依赖实例属性摊平，类属性不经 vars() 暴露），
        # 同时走正常模型解析路径（model-tags.yaml 就位，见同目录文件）
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
    # 测试不得污染 cwd（默认 <cwd>/.flowing）：持久化根一律经构造参数指到 tmp
    runtime = Runtime(persist_dir=persist)
    runtime.set_models(flowing.resolve("@/models.yaml"))
    runtime.set_model_tags(flowing.resolve("@/model-tags.yaml"))
    runtime.provider_registry._instances["fake-a"] = _make_provider("alpha-reply", scenario)
    runtime.provider_registry._instances["fake-b"] = _make_provider("beta-reply", scenario)
    runtime.register_agent_type("root", RootAgent)
    if scenario == "tool":
        runtime.register_tool(Echo())
    if not runtime._agent_pool:
        # 项目策略：仅全新项目建根；有盘上记录时不重复建（重启身份连续，
        # 池回退恢复是 repl 启动段的职责）
        await runtime.create_agent("root", agent_id="root", scenario=scenario)
    return runtime
