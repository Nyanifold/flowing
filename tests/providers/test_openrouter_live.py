"""OpenRouter 指定模型的网络门控场景测试。

只有同时设置 ``FLOWING_LIVE_TESTS=1`` 与 ``OPENROUTER_API_KEY`` 才发起真实
请求。模型目录核对日期与能力可能变化；离网协议断言见
``test_openrouter.py``。
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from flowing.context import Context
from flowing.errors import InvalidRequestError
from flowing.message import ImageBlock, Message, MessageKind, TextBlock
from flowing.model import ModelConfig
from flowing.providers import ProviderConfig
from flowing.providers.openrouter import OpenRouterProvider
from flowing.tool import ToolDefinition


pytestmark = pytest.mark.skipif(
    os.environ.get("FLOWING_LIVE_TESTS") != "1"
    or not os.environ.get("OPENROUTER_API_KEY"),
    reason="网络门控：需 FLOWING_LIVE_TESTS=1 与 OPENROUTER_API_KEY",
)

MODEL_IDS = (
    "qwen/qwen3.8-flash",
    "~deepseek/deepseek-flash-latest",
    "openai/gpt-6-luna",
    "anthropic/claude-sonnet-5",
    "xiaomi/mimo-v2.6-flash",
)
IMAGES = Path(__file__).parent.parent / "fixtures" / "images"


def _provider() -> OpenRouterProvider:
    return OpenRouterProvider(ProviderConfig({
        "api_key": os.environ["OPENROUTER_API_KEY"],
    }))


def _model(model_id: str, *, effort: str | None = None,
           max_output_tokens: int = 512) -> ModelConfig:
    extra = {} if effort is None else {"reasoning.effort": effort}
    return ModelConfig(
        model=model_id, provider="openrouter",
        max_output_tokens=max_output_tokens, extra=extra)


def _context(text: str) -> Context:
    return Context(system_prompt=[], tools=[], messages=[Message(
        kind=MessageKind.USER, content=[TextBlock(text=text)])])


def _text(response) -> str:
    if response.message is None:
        return ""
    return "".join(block.text for block in response.message.content
                   if block.type == "text")


def _assert_completed_response(response) -> None:
    assert response.message is not None
    assert response.model
    assert response.finish is True
    assert response.provider_data.get("stop_reason") is not None
    assert response.message.usage is not None
    assert response.message.usage.total_tokens > 0
    assert "openrouter_metadata" in response.provider_data


@pytest.mark.parametrize("model_id", MODEL_IDS)
async def test_live_openrouter_normal_and_false_premise(model_id):
    provider = _provider()
    answer = await provider.generate(
        _context("只输出 42：17 + 25 等于多少？"), _model(model_id))
    _assert_completed_response(answer)
    assert "42" in _text(answer), f"{model_id}: {_text(answer)[:160]}"

    correction = await provider.generate(
        _context("有人说法国首都是柏林。请判断这个说法是否正确，并给出法国首都。"),
        _model(model_id, max_output_tokens=2048),
    )
    _assert_completed_response(correction)
    assert "巴黎" in _text(correction) or "Paris" in _text(correction), \
        f"{model_id}: {_text(correction)[:200]}"


@pytest.mark.parametrize("model_id", MODEL_IDS)
async def test_live_openrouter_image_input(model_id):
    image_path = IMAGES / "demo-image2.png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    context = Context(system_prompt=[], tools=[], messages=[Message(
        kind=MessageKind.USER,
        content=[
            ImageBlock(data=encoded, name=image_path.name, mime_type="image/png"),
            TextBlock(text="用一个短语说明这张图主要展示什么。"),
        ],
    )])
    # 图像模型可能消耗较多 reasoning token；给出足够的 completion 上限，
    # 避免只返回思考块、没有最终短答。
    response = await _provider().generate(
        context, _model(model_id, max_output_tokens=2048))
    _assert_completed_response(response)
    assert _text(response).strip(), f"{model_id}: image response was empty"


@pytest.mark.parametrize("model_id", MODEL_IDS)
async def test_live_openrouter_tool_call_and_result_roundtrip(model_id):
    provider = _provider()
    tool = ToolDefinition(
        name="lookup",
        description="返回固定测试标记，不执行外部操作。",
        params_schema={"query": {"type": "string"}},
    )
    user = Message(
        kind=MessageKind.USER,
        content=[TextBlock(
            text=("必须调用 lookup 工具一次，query 参数请原样设为 "
                  "'openrouter-agent-test'。不要直接回答；工具返回后，请原样输出工具结果中的标记。")
        )],
    )
    first = await provider.generate(
        Context(system_prompt=[], tools=[tool], messages=[user]),
        _model(model_id, max_output_tokens=512),
    )
    assert first.message is not None
    calls = [block for block in first.message.content if block.type == "tool_call"]
    assert calls, f"{model_id}: expected tool_call, got {_text(first)!r}"
    call = calls[0]
    assert call.name == "lookup"
    assert call.args.get("query") == "openrouter-agent-test"

    result_token = "OR_TOOL_RESULT_73A1"
    second = await provider.generate(
        Context(system_prompt=[], tools=[tool], messages=[
            user,
            first.message,
            Message(kind=MessageKind.TOOL, tool_call_id=call.id,
                    tool_status="completed", content=[TextBlock(
                        text=f"lookup result: {result_token}")]),
        ]),
        _model(model_id, max_output_tokens=2048),
    )
    _assert_completed_response(second)
    assert result_token in _text(second), \
        f"{model_id}: tool result not used: {_text(second)[:200]}"


@pytest.mark.parametrize("model_id", MODEL_IDS)
@pytest.mark.parametrize("effort", ["low", "medium", "high"])
async def test_live_openrouter_reasoning_effort(model_id, effort):
    try:
        response = await _provider().generate(
            _context("17 + 25 是多少？请简短作答。"),
            _model(model_id, effort=effort, max_output_tokens=160),
        )
    except InvalidRequestError as exc:
        # OpenRouter 会按模型能力拒绝不支持的档位；仅接受明确与 reasoning
        # 或 effort 有关的参数错误，避免把其他请求构造 bug 当成能力差异。
        detail = str(exc).lower()
        assert "reason" in detail or "effort" in detail, str(exc)
        pytest.skip(f"{model_id} rejects reasoning.effort={effort}: {exc}")
    _assert_completed_response(response)
    assert "42" in _text(response), \
        f"{model_id} ({effort}): {_text(response)[:160]}"


async def test_live_openrouter_weak_tool_hint_is_observation_only():
    tool = ToolDefinition(
        name="lookup", description="返回与 query 对应的测试资料。",
        params_schema={"query": {"type": "string"}},
    )
    context = Context(system_prompt=[], tools=[tool], messages=[Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="回答 17 + 25。需要时可以调用 lookup。")],
    )])
    response = await _provider().generate(
        context, _model("qwen/qwen3.8-flash"))
    assert response.message is not None
    assert response.finish is True or any(
        block.type == "tool_call" for block in response.message.content)
