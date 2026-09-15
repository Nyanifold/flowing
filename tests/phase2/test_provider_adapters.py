"""阶段 2：内置 adapter 的 wire 映射测试（T19/T20，mock transport 驱动）。"""

from __future__ import annotations

import pytest

from flowing.context import Context, PromptSegment
from flowing.errors import InvalidRequestError
from flowing.message import Message, MessageKind, TextBlock, ToolCallBlock
from flowing.model import ModelConfig
from flowing.providers import ProviderConfig
from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.deepseek import DeepSeekProvider
from flowing.providers.openai_completions import OpenAICompletionsProvider


def _model() -> ModelConfig:
    return ModelConfig(model="test-model", provider="test")


class _MockOpenAI(OpenAICompletionsProvider):
    name = "mock-openai-t19"

    def __init__(self, canned):
        super().__init__(ProviderConfig({"api_key": "sk-x"}))
        self._canned = canned
        self.sent: list[dict] = []

    async def _post(self, path, body):
        self.sent.append(body)
        return self._canned


class _MockAnthropic(AnthropicMessagesProvider):
    name = "mock-anthropic-t20"

    def __init__(self, canned):
        super().__init__(ProviderConfig({"api_key": "sk-x"}))
        self._canned = canned
        self.sent: list[dict] = []

    async def _post(self, path, body):
        self.sent.append(body)
        return self._canned


async def test_t19_openai_tool_calls_finish_false():
    """T19：OpenAI 家族 mock 响应含 tool_calls → finish is False 且
    provider_data["stop_reason"]=="tool_calls"。"""
    canned = {
        "model": "deepseek-chat",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "search", "arguments": '{"q": "x"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    provider = _MockOpenAI(canned)
    ctx = Context(system_prompt=[], tools=[], messages=[
        Message(kind=MessageKind.USER, content=[TextBlock(text="查一下")])])
    response = await provider.generate(ctx, _model())
    assert response.finish is False
    assert response.provider_data["stop_reason"] == "tool_calls"
    tool_block = response.message.content[0]
    assert tool_block.type == "tool_call"
    assert tool_block.name == "search" and tool_block.args == {"q": "x"}
    assert tool_block.id == "call_1"


async def test_t20_anthropic_cache_control_mapping():
    """T20：cache="static" segment → 请求体对应 block 带 cache_control；
    cache="dynamic" 不带。"""
    canned = {
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 7, "output_tokens": 1,
                  "cache_read_input_tokens": 2, "cache_creation_input_tokens": 3},
    }
    provider = _MockAnthropic(canned)
    ctx = Context(
        system_prompt=[
            PromptSegment(content="静态规则", cache="static", name="rules"),
            PromptSegment(content="动态部分", cache="dynamic", name="dyn"),
        ],
        tools=[],
        messages=[Message(kind=MessageKind.USER, content=[TextBlock(text="hi")])],
    )
    response = await provider.generate(ctx, _model())
    body = provider.sent[0]
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in body["system"][1]
    # Anthropic 原生 input 不含 cache：input = fresh + cache_read + cache_write
    usage = response.message.usage
    assert usage.fresh_input == 7 and usage.cache_read == 2 and usage.cache_write == 3
    assert usage.input == 12 and usage.total_tokens == 13 and usage.output == 1
    assert response.finish is True
    assert response.provider_data["stop_reason"] == "end_turn"


async def test_anthropic_parallel_tool_results_merged():
    """并行工具批：同一连续段的多条 TOOL 消息归并为一条 user 消息的多
    tool_result 块（Anthropic 对并行 tool_use 的配对要求）；孤立单条
    TOOL 消息与非 TOOL 断段语义不变。"""
    canned = {
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 1},
    }
    provider = _MockAnthropic(canned)
    ctx = Context(system_prompt=[], tools=[], messages=[
        Message(kind=MessageKind.USER, content=[TextBlock(text="并行查两个")]),
        Message(kind=MessageKind.PROVIDER, content=[
            ToolCallBlock(id="tu_1", name="search", args={"q": "a"}),
            ToolCallBlock(id="tu_2", name="search", args={"q": "b"}),
        ]),
        # 连续段：两个并行 tool_use 的结果
        Message(kind=MessageKind.TOOL, tool_call_id="tu_1",
                tool_status="completed",
                content=[TextBlock(text="结果一")]),
        Message(kind=MessageKind.TOOL, tool_call_id="tu_2",
                tool_status="error",
                content=[TextBlock(text="结果二失败")]),
        Message(kind=MessageKind.USER, content=[TextBlock(text="再查一个")]),
        # 孤立单条（前一条非 TOOL，断段后独立映射）
        Message(kind=MessageKind.TOOL, tool_call_id="tu_3",
                tool_status="completed",
                content=[TextBlock(text="结果三")]),
    ])
    await provider.generate(ctx, _model())
    msgs = provider.sent[0]["messages"]
    assert [m["role"] for m in msgs] == [
        "user", "assistant", "user", "user", "user"]
    # 连续段归并：一条 user 消息含两个 tool_result 块，顺序与配对锚保持
    merged = msgs[2]["content"]
    assert [b["type"] for b in merged] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in merged] == ["tu_1", "tu_2"]
    assert merged[0]["content"] == [{"type": "text", "text": "结果一"}]
    assert "is_error" not in merged[0]
    assert merged[1]["is_error"] is True   # tool_status="error" 映射保留
    # 断段：中间 USER 消息原样；孤立 TOOL 仍为一条 user 消息单块
    assert msgs[3]["content"] == [{"type": "text", "text": "再查一个"}]
    single = msgs[4]["content"]
    assert len(single) == 1
    assert single[0]["type"] == "tool_result"
    assert single[0]["tool_use_id"] == "tu_3"


class _MockDeepSeek(DeepSeekProvider):
    name = "mock-deepseek-thinking"

    def __init__(self, canned):
        super().__init__(ProviderConfig({"api_key": "sk-x"}))
        self._canned = canned
        self.sent: list[dict] = []

    async def _post(self, path, body):
        self.sent.append(body)
        return self._canned


_DEEPSEEK_CANNED = {
    "model": "deepseek-v4-pro",
    "choices": [{
        "message": {"role": "assistant", "content": "ok"},
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
}


async def test_deepseek_thinking_params_mapping():
    """DeepSeek adapter：_extra["thinking"] 字符串包装 / dict 透传 /
    非法类型报错；_extra["reasoning_effort"] 顶层透传；缺省都不发送；
    thinking_budget 不被本 adapter 消费。"""
    ctx = Context(system_prompt=[], tools=[], messages=[
        Message(kind=MessageKind.USER, content=[TextBlock(text="hi")])])

    # 字符串形态 → 包装为 {"type": ...}
    provider = _MockDeepSeek(_DEEPSEEK_CANNED)
    await provider.generate(ctx, ModelConfig(
        model="deepseek-v4-pro", provider="test",
        extra={"thinking": "enabled", "reasoning_effort": "high"}))
    body = provider.sent[0]
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "high"

    # dict 形态 → 原样透传
    provider = _MockDeepSeek(_DEEPSEEK_CANNED)
    await provider.generate(ctx, ModelConfig(
        model="deepseek-v4-pro", provider="test",
        extra={"thinking": {"type": "disabled"}}))
    assert provider.sent[0]["thinking"] == {"type": "disabled"}

    # 非法类型 → InvalidRequestError
    provider = _MockDeepSeek(_DEEPSEEK_CANNED)
    with pytest.raises(InvalidRequestError):
        await provider.generate(ctx, ModelConfig(
            model="deepseek-v4-pro", provider="test",
            extra={"thinking": True}))

    # 缺省不发送；thinking_budget 不被消费（DeepSeek 无预算概念）
    provider = _MockDeepSeek(_DEEPSEEK_CANNED)
    await provider.generate(ctx, ModelConfig(
        model="deepseek-v4-pro", provider="test", thinking_budget=8192))
    body = provider.sent[0]
    assert "thinking" not in body
    assert "reasoning_effort" not in body


_OPENAI_CANNED = {
    "model": "test-model",
    "choices": [{
        "message": {"role": "assistant", "content": "ok"},
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
}


async def test_openai_extra_body_passthrough():
    """extra_body dict 原样合入请求体；同名键按合入顺序覆盖
    （基类固定字段 → extra_body → adapter 专有字段）；非 dict 抛
    InvalidRequestError。"""
    ctx = Context(system_prompt=[], tools=[], messages=[
        Message(kind=MessageKind.USER, content=[TextBlock(text="hi")])])

    # 基类：extra_body 合入，且覆盖基类固定字段同名键
    provider = _MockOpenAI(_OPENAI_CANNED)
    await provider.generate(ctx, ModelConfig(
        model="test-model", provider="test", max_output_tokens=100,
        extra={"extra_body": {"max_tokens": 50, "vendor_flag": True}}))
    body = provider.sent[0]
    assert body["vendor_flag"] is True
    assert body["max_tokens"] == 50

    # 非 dict → InvalidRequestError
    provider = _MockOpenAI(_OPENAI_CANNED)
    with pytest.raises(InvalidRequestError):
        await provider.generate(ctx, ModelConfig(
            model="test-model", provider="test",
            extra={"extra_body": ["not", "a", "dict"]}))

    # DeepSeek：adapter 专有字段在 extra_body 之后写入，同名键以 adapter 为准
    provider = _MockDeepSeek(_DEEPSEEK_CANNED)
    await provider.generate(ctx, ModelConfig(
        model="deepseek-v4-pro", provider="test",
        extra={"thinking": "enabled", "reasoning_effort": "high",
               "extra_body": {"reasoning_effort": "low", "vendor_flag": 1}}))
    body = provider.sent[0]
    assert body["reasoning_effort"] == "high"
    assert body["thinking"] == {"type": "enabled"}
    assert body["vendor_flag"] == 1
