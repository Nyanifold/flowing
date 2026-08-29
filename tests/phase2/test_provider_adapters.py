"""阶段 2：内置 adapter 的 wire 映射测试（T19/T20，mock transport 驱动）。"""

from __future__ import annotations

import pytest

from flowing.context import Context, PromptSegment
from flowing.message import Message, MessageKind, TextBlock
from flowing.model import ModelConfig
from flowing.providers import ProviderConfig
from flowing.providers.anthropic import AnthropicMessagesProvider
from flowing.providers.openai import OpenAICompletionsProvider


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
