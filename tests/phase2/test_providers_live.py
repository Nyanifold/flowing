"""阶段 2：真实端点冒烟测试（T130–T134，网络门控，默认跳过，不进验收关键路径）。

门控条件（两者同时具备才运行）：
- 环境变量 ``FLOWING_LIVE_TESTS=1`` 显式开启；
- ``测试资源/providers.md`` 存在（已 gitignore，含 DeepSeek 测试 API key）。

API key 运行时从 providers.md 读取（正则提取 ``deepseek API=...`` 行），
**绝不写进代码 / fixtures / 提交历史**。全部用例失败不阻塞验收。
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

import pytest

from flowing.context import Context
from flowing.message import ImageBlock, Message, MessageKind, TextBlock
from flowing.model import ModelConfig
from flowing.providers import ProviderConfig
from flowing.providers.anthropic import AnthropicMessagesProvider
from flowing.providers.openai import DeepSeekProvider
from flowing.errors import AuthenticationError

RESOURCES = Path(__file__).parent.parent.parent / "测试资源"
PROVIDERS_MD = RESOURCES / "providers.md"

pytestmark = pytest.mark.skipif(
    os.environ.get("FLOWING_LIVE_TESTS") != "1" or not PROVIDERS_MD.exists(),
    reason="网络门控：需 FLOWING_LIVE_TESTS=1 且 测试资源/providers.md 存在",
)


def _deepseek_key() -> str:
    text = PROVIDERS_MD.read_text(encoding="utf-8")
    m = re.search(r"deepseek\s*API\s*=\s*(\S+)", text, re.IGNORECASE)
    assert m, "providers.md 中未找到 deepseek API key 行"
    return m.group(1)


def _ctx(text: str) -> Context:
    return Context(system_prompt=[], tools=[], messages=[
        Message(kind=MessageKind.USER, content=[TextBlock(text=text)])])


def _model() -> ModelConfig:
    return ModelConfig(model="deepseek-chat", provider="deepseek-personal")


async def test_t130_deepseek_roundtrip():
    """T130：DeepSeek chat-completions 真实往返——响应非空、finish、Usage 齐全。"""
    provider = DeepSeekProvider(ProviderConfig({"api_key": _deepseek_key()}))
    response = await provider.generate(_ctx("用一句话回答：1+1 等于几？"), _model())
    assert response.finish is True
    text = "".join(b.text for b in response.message.content if b.type == "text")
    assert text.strip()
    usage = response.message.usage
    assert usage is not None
    assert usage.input > 0 and usage.output > 0
    assert usage.total_tokens == usage.input + usage.output
    assert "prompt_tokens" in usage.raw  # raw 含原始字段


async def test_t131_vision_smoke():
    """T131：无提示发送图片要求识别——宽松命中候选词，验证图片块 → wire 转换。"""
    for image, keywords in (
        ("demo-image1.png", ("分子", "预训练")),
        ("demo-image2.png", ("热图", "矩阵")),
    ):
        data = base64.b64encode((RESOURCES / image).read_bytes()).decode()
        provider = DeepSeekProvider(ProviderConfig({"api_key": _deepseek_key()}))
        ctx = Context(system_prompt=[], tools=[], messages=[Message(
            kind=MessageKind.USER,
            content=[
                ImageBlock(data=data, name=image, mime_type="image/png"),
                TextBlock(text="识别这张图片的内容，简短回答。"),
            ])])
        model = ModelConfig(model="deepseek-vl2", provider="deepseek-personal")
        response = await provider.generate(ctx, model)
        text = "".join(b.text for b in response.message.content if b.type == "text")
        assert any(k in text for k in keywords), f"{image} 识别结果未命中候选词：{text[:200]}"


async def test_t132_anthropic_compatible_endpoint():
    """T132：同一 query 走 DeepSeek anthropic 兼容端点 → 结构等价。"""
    provider = AnthropicMessagesProvider(ProviderConfig({
        "api_key": _deepseek_key(),
        "base_url": "https://api.deepseek.com/anthropic",
    }))
    response = await provider.generate(_ctx("用一句话回答：1+1 等于几？"), _model())
    assert response.finish is True
    text = "".join(b.text for b in response.message.content if b.type == "text")
    assert text.strip()
    usage = response.message.usage
    assert usage is not None and usage.total_tokens == usage.input + usage.output


async def test_t133_error_mapping_authentication():
    """T133：故意错误 api_key → AuthenticationError（不自动重试、不挂起）。"""
    provider = DeepSeekProvider(ProviderConfig({"api_key": "sk-invalid-key-for-test"}))
    with pytest.raises(AuthenticationError):
        await provider.generate(_ctx("ping"), _model())


async def test_t134_tool_calls_roundtrip():
    """T134：声明单个简单工具并要求模型调用 → finish False、ToolCallBlock 正确。"""
    from flowing.tool import ToolDefinition

    provider = DeepSeekProvider(ProviderConfig({"api_key": _deepseek_key()}))
    ctx = Context(
        system_prompt=[], messages=[Message(kind=MessageKind.USER, content=[
            TextBlock(text="请调用 get_weather 工具查询北京的天气。")])],
        tools=[ToolDefinition(
            name="get_weather", description="查询指定城市的天气",
            params_schema={"city": {"type": "string", "description": "城市名"}})],
    )
    response = await provider.generate(ctx, _model())
    assert response.finish is False
    tool_blocks = [b for b in response.message.content if b.type == "tool_call"]
    assert tool_blocks, f"响应无 tool_call 块：{response.message.content}"
    assert tool_blocks[0].name == "get_weather"
    assert "city" in tool_blocks[0].args
