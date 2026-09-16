"""阶段 2：Kimi 两类 provider 真机测试（T141–T157 + T162–T166，网络门控）。

Kimi 的接入面分两种（见 ``测试资源/kimi-provider.md``），均为**三协议
并存**（chat/completions / Responses / Anthropic Messages）：

- **kimi code**：Kimi 会员编程权益端点 ``https://api.kimi.com/coding``，
  模型 ``k3`` / ``k3-256k`` / ``kimi-for-coding``（/v1/models 实测）。
  内置 adapter：:class:`KimiCodingProvider`（completions，默认形态）、
  :class:`KimiCodingAnthropicProvider`（Anthropic）；Responses 形态经
  :class:`OpenAIResponsesProvider` 配 ``base_url`` 接入（T166）。
- **moonshot api**：开放平台 ``https://api.moonshot.cn``，模型
  ``kimi-k3`` / ``kimi-k2.7-code``。内置 adapter：
  :class:`MoonshotProvider`（completions）、
  :class:`MoonshotResponsesProvider`（Responses）、
  :class:`MoonshotAnthropicProvider`（Anthropic）。

覆盖矩阵：两类 provider × 各自协议 × 文本 / 图片 × 多轮（第二轮带完整
历史上门，验证消息回放）；kimi code completions 另补流式工具调用用例
（T165，锁定工具参数正确性）。注意：按平台文档 k2.7-code 的 Anthropic
端点强制开启思考（否则 400）——本组用例一律使用框架默认参数
（``thinking_budget=None`` 不发思考字段），若端点因此拒绝则如实暴露为
失败，不用调参规避。

门控条件（两者同时具备才运行）：
- 环境变量 ``FLOWING_LIVE_TESTS=1`` 显式开启；
- ``测试资源/kimi_code_api_key.txt`` 与 ``测试资源/moonshot_api_key.txt``
  存在（已 gitignore）。

API key 运行时从上述文件读取，**绝不写进代码 / fixtures / 提交历史**。
全部用例失败不阻塞验收。
"""

from __future__ import annotations

import base64
import os
import uuid
from pathlib import Path

import pytest

from flowing.context import Context
from flowing.message import ImageBlock, Message, MessageKind, TextBlock
from flowing.model import ModelConfig
from flowing.providers import (
    KimiCodingProvider,
    MoonshotProvider,
    ProviderConfig,
)
from flowing.providers.kimi_coding_anthropic import KimiCodingAnthropicProvider
from flowing.providers.moonshot_anthropic import MoonshotAnthropicProvider
from flowing.providers.moonshot_responses import MoonshotResponsesProvider
from flowing.providers.openai_responses import OpenAIResponsesProvider

RESOURCES = Path(__file__).parent.parent.parent / "测试资源"
IMAGES = Path(__file__).parent.parent / "fixtures" / "images"  # 目录即契约
KC_KEY = RESOURCES / "kimi_code_api_key.txt"      # kimi code 端点 key
MS_KEY = RESOURCES / "moonshot_api_key.txt"       # moonshot 开放平台 key

pytestmark = pytest.mark.skipif(
    os.environ.get("FLOWING_LIVE_TESTS") != "1" or not (KC_KEY.exists() and MS_KEY.exists()),
    reason="网络门控：需 FLOWING_LIVE_TESTS=1 且 kimi/moonshot key 文件存在",
)


def _kc_key() -> str:
    return KC_KEY.read_text(encoding="utf-8").strip()


def _ms_key() -> str:
    return MS_KEY.read_text(encoding="utf-8").strip()


def _model(model_id: str) -> ModelConfig:
    """默认参数的模型配置：测的就是框架与端点的默认行为。"""
    return ModelConfig(model=model_id, provider="kimi-live-test")


def _image_ctx(image: str, prompt: str) -> Context:
    data = base64.b64encode((IMAGES / image).read_bytes()).decode()
    return Context(system_prompt=[], tools=[], messages=[Message(
        kind=MessageKind.USER,
        content=[
            ImageBlock(data=data, name=image, mime_type="image/png"),
            TextBlock(text=prompt),
        ])])


def _text_of(response) -> str:
    return "".join(b.text for b in response.message.content if b.type == "text")


async def _recall_roundtrip(provider, model: ModelConfig,
                            opener: Message, question: str,
                            needle: str, recall_hint: str) -> None:
    """两轮往返公共体：第一轮报编号并复述，第二轮带完整历史追问 recall。

    编号按用例随机生成，避免服务端前缀缓存把不同用例的响应复用成
    同一份而导致 recall 断言失真。第一轮要求模型原样复述编号——
    编号经助手自己的输出进入对话（思考重的模型对「记住暗号」式
    指令可能过度谨慎拒答，复述式 opener 无此问题），第二轮验证
    完整历史（含 assistant 消息回放）被对方收到。
    """
    r1 = await provider.generate(
        Context(system_prompt=[], tools=[], messages=[opener]), model)
    assert r1.finish is True, f"第一轮未正常结束：{_text_of(r1)[:200]}"
    assert needle in _text_of(r1), \
        f"第一轮未复述编号：{_text_of(r1)[:200]}"
    r2 = await provider.generate(
        Context(system_prompt=[], tools=[], messages=[
            opener, r1.message,
            Message(kind=MessageKind.USER, content=[TextBlock(text=question)]),
        ]), model)
    assert r2.finish is True, f"第二轮未正常结束：{_text_of(r2)[:200]}"
    answer = _text_of(r2)
    assert needle in answer, f"{recall_hint}：{answer[:200]}"


async def _image_recall_roundtrip(provider, model: ModelConfig,
                                  image: str, keywords: tuple[str, ...],
                                  recall_keywords: tuple[str, ...] | None = None) -> None:
    """图片两轮往返公共体：第一轮识图，第二轮凭完整历史回忆图内容。

    第一轮用 ``keywords`` 断言识图命中；第二轮是开放式回忆，模型常给
    同义转述（不一定复用第一轮措辞），故用「实质内容候选集」
    ``recall_keywords`` 断言，缺省时回退为 ``keywords``。
    """
    opener = _image_ctx(image, "识别这张图片的内容，简短回答。")
    r1 = await provider.generate(opener, model)
    assert r1.finish is True
    assert any(k in _text_of(r1) for k in keywords), \
        f"识图语义不符：{_text_of(r1)[:200]}"
    r2 = await provider.generate(
        Context(system_prompt=[], tools=[], messages=[
            opener.messages[0], r1.message,
            Message(kind=MessageKind.USER, content=[TextBlock(
                text="我刚才发给你的那张图主要内容是什么？简短回答。")]),
        ]), model)
    assert r2.finish is True
    recall_set = recall_keywords if recall_keywords is not None else keywords
    assert any(k in _text_of(r2) for k in recall_set), \
        f"第二轮未回忆出图片内容：{_text_of(r2)[:200]}"


def _nonce_opener() -> tuple[Message, str]:
    nonce = uuid.uuid4().hex[:6]
    return Message(kind=MessageKind.USER, content=[TextBlock(
        text=f"我的编号是 {nonce}。请原样回复：收到，编号 {nonce}。")]), nonce


_RECALL_QUESTION = "我刚才报给你的编号是多少？只回答编号本身。"

# 图片第二轮开放式回忆的实质内容候选集（第一轮识图断言仍用原关键词）
_IMAGE1_RECALL = ("分子", "预训练", "聚合物", "P-SMILES", "掩码",
                  "对比", "性质预测", "框架")
_IMAGE2_RECALL = ("热图", "热力图", "矩阵", "matplotlib", "相关性",
                  "相关系数", "heatmap")


# ── kimi code（Anthropic 协议，api.kimi.com/coding）────────────────────


async def test_t141_kimi_code_text_multiturn_k3():
    """T141：kimi code 端点 k3 文本多轮——记住随机编号并回忆。"""
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        KimiCodingAnthropicProvider(ProviderConfig({"api_key": _kc_key()})), _model("k3"),
        opener, _RECALL_QUESTION, nonce, "k3 两轮后未回忆起编号")


async def test_t142_kimi_code_image_multiturn_k3():
    """T142：kimi code 端点 k3 图片多轮——识图后第二轮凭历史回忆图内容。"""
    await _image_recall_roundtrip(
        KimiCodingAnthropicProvider(ProviderConfig({"api_key": _kc_key()})), _model("k3"),
        "demo-image1.png", ("分子", "预训练"), _IMAGE1_RECALL)


async def test_t143_kimi_code_text_multiturn_kimi_for_coding():
    """T143：kimi code 端点 kimi-for-coding 文本多轮。"""
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        KimiCodingAnthropicProvider(ProviderConfig({"api_key": _kc_key()})),
        _model("kimi-for-coding"),
        opener, _RECALL_QUESTION, nonce, "kimi-for-coding 两轮后未回忆起编号")


async def test_t144_kimi_code_image_multiturn_kimi_for_coding():
    """T144：kimi code 端点 kimi-for-coding 图片多轮。"""
    await _image_recall_roundtrip(
        KimiCodingAnthropicProvider(ProviderConfig({"api_key": _kc_key()})),
        _model("kimi-for-coding"),
        "demo-image2.png", ("热图", "矩阵"), _IMAGE2_RECALL)


# ── moonshot api：OpenAI chat/completions（内置 MoonshotProvider）───────────


async def test_t145_moonshot_openai_text_multiturn_k3():
    """T145：moonshot chat/completions kimi-k3 文本多轮。"""
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        MoonshotProvider(ProviderConfig({"api_key": _ms_key()})), _model("kimi-k3"),
        opener, _RECALL_QUESTION, nonce,
        "kimi-k3（chat/completions）两轮后未回忆起编号")


async def test_t146_moonshot_openai_image_multiturn_k3():
    """T146：moonshot chat/completions kimi-k3 图片多轮。"""
    await _image_recall_roundtrip(
        MoonshotProvider(ProviderConfig({"api_key": _ms_key()})), _model("kimi-k3"),
        "demo-image1.png", ("分子", "预训练"), _IMAGE1_RECALL)


async def test_t147_moonshot_openai_text_multiturn_k27code():
    """T147：moonshot chat/completions kimi-k2.7-code 文本多轮。"""
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        MoonshotProvider(ProviderConfig({"api_key": _ms_key()})),
        _model("kimi-k2.7-code"),
        opener, _RECALL_QUESTION, nonce,
        "kimi-k2.7-code（chat/completions）两轮后未回忆起编号")


async def test_t148_moonshot_openai_image_multiturn_k27code():
    """T148：moonshot chat/completions kimi-k2.7-code 图片多轮。"""
    await _image_recall_roundtrip(
        MoonshotProvider(ProviderConfig({"api_key": _ms_key()})),
        _model("kimi-k2.7-code"),
        "demo-image2.png", ("热图", "矩阵"), _IMAGE2_RECALL)


# ── moonshot api：Anthropic 兼容端点（/anthropic）───────────────────────


async def test_t149_moonshot_anthropic_text_multiturn_k3():
    """T149：moonshot Anthropic 端点 kimi-k3 文本多轮。"""
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        MoonshotAnthropicProvider(ProviderConfig({"api_key": _ms_key()})), _model("kimi-k3"),
        opener, _RECALL_QUESTION, nonce, "kimi-k3（anthropic）两轮后未回忆起编号")


async def test_t150_moonshot_anthropic_image_multiturn_k3():
    """T150：moonshot Anthropic 端点 kimi-k3 图片多轮。"""
    await _image_recall_roundtrip(
        MoonshotAnthropicProvider(ProviderConfig({"api_key": _ms_key()})), _model("kimi-k3"),
        "demo-image1.png", ("分子", "预训练"), _IMAGE1_RECALL)


async def test_t151_moonshot_anthropic_text_multiturn_k27code():
    """T151：moonshot Anthropic 端点 kimi-k2.7-code 文本多轮（默认参数，
    不发思考字段——平台文档称该模型此端点强制思考，若拒绝则如实暴露）。"""
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        MoonshotAnthropicProvider(ProviderConfig({"api_key": _ms_key()})),
        _model("kimi-k2.7-code"),
        opener, _RECALL_QUESTION, nonce,
        "kimi-k2.7-code（anthropic）两轮后未回忆起编号")


async def test_t152_moonshot_anthropic_image_multiturn_k27code():
    """T152：moonshot Anthropic 端点 kimi-k2.7-code 图片多轮（默认参数）。"""
    await _image_recall_roundtrip(
        MoonshotAnthropicProvider(ProviderConfig({"api_key": _ms_key()})),
        _model("kimi-k2.7-code"),
        "demo-image2.png", ("热图", "矩阵"), _IMAGE2_RECALL)


# ── moonshot api：OpenAI Responses（内置 MoonshotResponsesProvider）──────────


def _moonshot_responses() -> MoonshotResponsesProvider:
    return MoonshotResponsesProvider(ProviderConfig({"api_key": _ms_key()}))


async def test_t153_moonshot_responses_text_multiturn_k3():
    """T153：moonshot Responses 端点 kimi-k3 文本多轮。"""
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        _moonshot_responses(), _model("kimi-k3"),
        opener, _RECALL_QUESTION, nonce,
        "kimi-k3（responses）两轮后未回忆起编号")


async def test_t154_moonshot_responses_image_multiturn_k3():
    """T154：moonshot Responses 端点 kimi-k3 图片多轮。"""
    await _image_recall_roundtrip(
        _moonshot_responses(), _model("kimi-k3"),
        "demo-image1.png", ("分子", "预训练"), _IMAGE1_RECALL)


async def test_t155_moonshot_responses_text_multiturn_k27code():
    """T155：moonshot Responses 端点 kimi-k2.7-code 文本多轮（默认参数）。"""
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        _moonshot_responses(), _model("kimi-k2.7-code"),
        opener, _RECALL_QUESTION, nonce,
        "kimi-k2.7-code（responses）两轮后未回忆起编号")


async def test_t156_moonshot_responses_image_multiturn_k27code():
    """T156：moonshot Responses 端点 kimi-k2.7-code 图片多轮（默认参数）。"""
    await _image_recall_roundtrip(
        _moonshot_responses(), _model("kimi-k2.7-code"),
        "demo-image2.png", ("热图", "矩阵"), _IMAGE2_RECALL)


async def test_t157_moonshot_responses_stream_k3():
    """T157：Responses 端点真 SSE 流式冒烟——text delta 拼接含答案、
    content_index 单调不减、末帧携带 usage 与 stop_reason。"""
    deltas = [d async for d in _moonshot_responses().generate_stream(
        Context(system_prompt=[], tools=[], messages=[Message(
            kind=MessageKind.USER,
            content=[TextBlock(text="用一句话回答：1+1 等于几？")])]),
        _model("kimi-k3"))]
    assert deltas, "流式无任何 delta"
    text = "".join(d.text for d in deltas if d.kind == "text")
    assert "2" in text, f"流式拼接答案语义不符：{text[:200]}"
    indices = [d.content_index for d in deltas]
    assert indices == sorted(indices), "content_index 非单调"
    final = deltas[-1]
    assert final.usage is not None and final.usage.output > 0
    assert final.provider_data and "stop_reason" in final.provider_data


# ── kimi code：completions 默认形态（内置 KimiCodingProvider）────────────

async def test_t162_kimi_code_completions_text_multiturn_k3():
    """T162：kimi code completions（默认形态）k3 文本多轮。"""
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        KimiCodingProvider(ProviderConfig({"api_key": _kc_key()})),
        _model("k3"),
        opener, _RECALL_QUESTION, nonce,
        "k3（kimi code completions）两轮后未回忆起编号")


async def test_t163_kimi_code_completions_image_multiturn_k3():
    """T163：kimi code completions k3 图片多轮。"""
    await _image_recall_roundtrip(
        KimiCodingProvider(ProviderConfig({"api_key": _kc_key()})),
        _model("k3"),
        "demo-image1.png", ("分子", "预训练"), _IMAGE1_RECALL)


async def test_t164_kimi_code_completions_text_multiturn_kimi_for_coding():
    """T164：kimi code completions kimi-for-coding 文本多轮。"""
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        KimiCodingProvider(ProviderConfig({"api_key": _kc_key()})),
        _model("kimi-for-coding"),
        opener, _RECALL_QUESTION, nonce,
        "kimi-for-coding（completions）两轮后未回忆起编号")


async def test_t165_kimi_code_completions_tool_stream_args():
    """T165：kimi code completions 流式工具调用——ToolCallBlock 参数必须
    完整（锁定流式工具参数回归：空参数会让 bash 拿到 {}）。"""
    from flowing.tool import ToolDefinition
    provider = KimiCodingProvider(ProviderConfig({"api_key": _kc_key()}))
    tools = [ToolDefinition(
        name="bash", description="运行 shell 命令",
        params_schema={"command": {"type": "string", "description": "命令"}})]
    ctx = Context(system_prompt=[], tools=tools, messages=[Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="请调用 bash 工具执行 echo hello")])])
    deltas = [d async for d in provider.generate_stream(ctx, _model("k3"))]
    blocks = [d.block for d in deltas if d.kind == "tool_use" and d.block]
    assert blocks, "流式无工具块产出"
    assert blocks[0].name == "bash"
    assert blocks[0].args.get("command"), \
        f"流式工具参数缺失/为空：{blocks[0].args}"


async def test_t166_kimi_code_responses_text_multiturn_k3():
    """T166：kimi code Responses 协议（OpenAIResponsesProvider + base_url
    覆盖，无独立内置 adapter）k3 文本多轮。"""
    provider = OpenAIResponsesProvider(ProviderConfig({
        "api_key": _kc_key(),
        "base_url": "https://api.kimi.com/coding/v1"}))
    opener, nonce = _nonce_opener()
    await _recall_roundtrip(
        provider, _model("k3"),
        opener, _RECALL_QUESTION, nonce,
        "k3（kimi code responses）两轮后未回忆起编号")
