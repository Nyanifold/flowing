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

from flowing.context import Context, PromptSegment
from flowing.message import ImageBlock, Message, MessageKind, TextBlock
from flowing.model import ModelConfig
from flowing.providers import ProviderConfig
from flowing.providers.anthropic import (
    AnthropicMessagesProvider,
    DeepSeekAnthropicProvider,
)
from flowing.providers.openai import DeepSeekProvider
from flowing.errors import AuthenticationError
from flowing.tool import ToolDefinition

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
        model = ModelConfig(model="deepseek-v4-flash-vision-exp",  # 当前唯一视觉模型
                            provider="deepseek-personal")
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


# ── DeepSeek Anthropic 兼容端点：AnthropicMessagesProvider 功能面真机验证 ──
# 依据官方兼容性表（https://api-docs.deepseek.com/zh-cn/guides/anthropic_api）：
# cache_control 全链路忽略（发送不应报错）；thinking 支持但 budget_tokens
# 被忽略；tools/tool_use/tool_result 完全支持；image base64 仅视觉模型
# deepseek-v4-flash-vision-exp 接受；不支持的模型名自动映射 deepseek-v4-flash。


def _ds_anthropic() -> DeepSeekAnthropicProvider:
    return DeepSeekAnthropicProvider(ProviderConfig({"api_key": _deepseek_key()}))


def _ds_model(model_id: str = "deepseek-v4-flash", **kwargs) -> ModelConfig:
    return ModelConfig(model=model_id, provider="deepseek-anthropic", **kwargs)


async def test_t135_anthropic_system_segments_cache_control():
    """T135：system 多段（含 cache="static" → 发送 cache_control）——
    DeepSeek 端点忽略 cache_control 但请求成功，响应正常。"""
    provider = _ds_anthropic()
    ctx = Context(
        system_prompt=[
            PromptSegment(content="你是数学助手，只回答最终数字。",
                          cache="static", name="rule"),
            PromptSegment(content="对话语言：中文。", cache="dynamic", name="lang"),
        ],
        tools=[],
        messages=[Message(kind=MessageKind.USER, content=[TextBlock(text="2+3=?")])])
    response = await provider.generate(ctx, _ds_model())
    assert response.finish is True
    text = "".join(b.text for b in response.message.content if b.type == "text")
    assert "5" in text


async def test_t136_anthropic_tool_two_turn_roundtrip():
    """T136：工具两轮往返——第一轮 tool_use 映射（finish False +
    ToolCallBlock），第二轮回放 assistant 响应 + tool_result 消息被端点
    接受（finish True）。"""
    provider = _ds_anthropic()
    tools = [ToolDefinition(
        name="get_weather", description="查询指定城市的天气",
        params_schema={"city": {"type": "string", "description": "城市名"}})]
    user_msg = Message(kind=MessageKind.USER, content=[
        TextBlock(text="请调用 get_weather 工具查询北京的天气。")])
    r1 = await provider.generate(
        Context(system_prompt=[], tools=tools, messages=[user_msg]), _ds_model())
    assert r1.finish is False
    calls = [b for b in r1.message.content if b.type == "tool_call"]
    assert calls and calls[0].name == "get_weather" and "city" in calls[0].args

    ctx2 = Context(system_prompt=[], tools=tools, messages=[
        user_msg,
        r1.message,   # assistant tool_use 原样回放
        Message(kind=MessageKind.TOOL, tool_call_id=calls[0].id,
                tool_status="completed",
                content=[TextBlock(text="北京：晴，26°C")]),
    ])
    r2 = await provider.generate(ctx2, _ds_model())
    assert r2.finish is True
    assert "".join(b.text for b in r2.message.content if b.type == "text").strip()


async def test_t137_anthropic_thinking_block_roundtrip():
    """T137：thinking_budget → thinking 开启（budget_tokens 被端点忽略，
    开关生效），响应含 thinking 块；原样回放（含 signature）进第二轮被
    端点接受。"""
    provider = _ds_anthropic()
    model = _ds_model("deepseek-v4-pro", thinking_budget=2048)
    question = Message(kind=MessageKind.USER, content=[
        TextBlock(text="9.11 和 9.8 哪个大？只回答较大的数。")])
    r1 = await provider.generate(
        Context(system_prompt=[], tools=[], messages=[question]), model)
    thinking = [b for b in r1.message.content if b.type == "thinking"]
    assert thinking, f"开启 thinking 后响应无 thinking 块：{r1.message.content}"
    assert thinking[0].thinking.strip()

    ctx2 = Context(system_prompt=[], tools=[], messages=[
        question, r1.message,   # thinking 块连同 signature 原样写回
        Message(kind=MessageKind.USER, content=[TextBlock(text="好的，谢谢。")])])
    r2 = await provider.generate(ctx2, model)
    assert r2.finish is True


async def test_t138_anthropic_stream_deltas():
    """T138：generate_stream 真 SSE——text delta 拼接非空、content_index
    单调不减、末帧携带 usage 与 stop_reason。"""
    provider = _ds_anthropic()
    deltas = [d async for d in provider.generate_stream(
        _ctx("用一句话回答：1+1 等于几？"), _ds_model())]
    assert deltas, "流式无任何 delta"
    text = "".join(d.text for d in deltas if d.kind == "text")
    assert text.strip()
    indices = [d.content_index for d in deltas]
    assert indices == sorted(indices), "content_index 非单调"
    final = deltas[-1]
    assert final.usage is not None and final.usage.input > 0
    assert final.provider_data and "stop_reason" in final.provider_data


async def test_t139_anthropic_error_mapping_authentication():
    """T139：anthropic 端点故意错误 api_key → AuthenticationError。"""
    provider = DeepSeekAnthropicProvider(
        ProviderConfig({"api_key": "sk-invalid-key-for-test"}))
    with pytest.raises(AuthenticationError):
        await provider.generate(_ctx("ping"), _ds_model())


async def test_t140_anthropic_vision_smoke():
    """T140：anthropic 端点 image base64 块——唯一视觉模型
    deepseek-v4-flash-vision-exp 接受；宽松命中候选词验证图片块映射。"""
    for image, keywords in (
        ("demo-image1.png", ("分子", "预训练")),
        ("demo-image2.png", ("热图", "矩阵")),
    ):
        data = base64.b64encode((RESOURCES / image).read_bytes()).decode()
        provider = _ds_anthropic()
        ctx = Context(system_prompt=[], tools=[], messages=[Message(
            kind=MessageKind.USER,
            content=[
                ImageBlock(data=data, name=image, mime_type="image/png"),
                TextBlock(text="识别这张图片的内容，简短回答。"),
            ])])
        response = await provider.generate(
            ctx, _ds_model("deepseek-v4-flash-vision-exp"))
        text = "".join(b.text for b in response.message.content if b.type == "text")
        assert any(k in text for k in keywords), \
            f"{image} 识别结果未命中候选词：{text[:200]}"
