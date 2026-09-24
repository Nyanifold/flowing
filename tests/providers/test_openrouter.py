"""OpenRouter adapter 离网映射、错误归类与流式元数据测试。"""

from __future__ import annotations

import json

import httpx
import pytest

from flowing.context import Context, PromptSegment
from flowing.errors import (
    AuthenticationError,
    InvalidRequestError,
    RateLimitedError,
    RequestTooLargeError,
    ServerError,
)
from flowing.message import (
    AudioBlock,
    FileBlock,
    ImageBlock,
    Message,
    MessageKind,
    TextBlock,
    ToolCallBlock,
    VideoBlock,
)
from flowing.model import ModelConfig, load_models
from flowing.providers import ProviderConfig
from flowing.providers.openai_completions import _HttpResponseError
from flowing.providers.openrouter import OpenRouterProvider
from flowing.providers.provider import ProviderRegistry, load_provider_candidates
from flowing.tool import ToolDefinition


def _context(messages=None, tools=None) -> Context:
    return Context(
        system_prompt=[PromptSegment(content="system prompt", cache="dynamic",
                                     name="system")],
        tools=tools or [],
        messages=messages or [
            Message(kind=MessageKind.USER, content=[TextBlock(text="hello")])],
    )


def _model(*, extra=None, model="qwen/qwen3.8-flash") -> ModelConfig:
    return ModelConfig(model=model, provider="openrouter", extra=extra)


class _MockOpenRouter(OpenRouterProvider):
    def __init__(self, response=None, error=None):
        super().__init__(ProviderConfig({"api_key": "test-key"}))
        self.response = response or {
            "model": "qwen/qwen3.8-flash",
            "choices": [{"message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
        }
        self.error = error
        self.sent = []

    async def _post(self, path, body):
        self.sent.append((path, body))
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.parametrize(
    "effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max"])
async def test_openrouter_reasoning_effort_is_model_scoped(effort):
    provider = _MockOpenRouter()
    await provider.generate(
        _context(), _model(extra={"reasoning.effort": effort}))
    body = provider.sent[0][1]
    assert body["reasoning"] == {"effort": effort}
    assert body["model"] == "qwen/qwen3.8-flash"


async def test_openrouter_reasoning_effort_preserves_extra_body_reasoning():
    provider = _MockOpenRouter()
    await provider.generate(
        _context(),
        _model(extra={
            "reasoning.effort": "high",
            "extra_body": {"reasoning": {"exclude": True},
                           "provider": {"order": ["fast"]}},
        }),
    )
    body = provider.sent[0][1]
    assert body["reasoning"] == {"exclude": True, "effort": "high"}
    assert body["provider"] == {"order": ["fast"]}


@pytest.mark.parametrize("value", [1, True, None, {"effort": "high"}])
async def test_openrouter_rejects_non_string_effort_before_post(value):
    provider = _MockOpenRouter()
    with pytest.raises(InvalidRequestError, match="reasoning.effort must be a string"):
        await provider.generate(
            _context(), _model(extra={"reasoning.effort": value}))
    assert provider.sent == []


async def test_openrouter_tool_hint_and_tool_call_response_mapping():
    tool = ToolDefinition(
        name="lookup", description="Look up a fact",
        params_schema={"query": {"type": "string"}},
    )
    provider = _MockOpenRouter(response={
        "model": "openai/gpt-6-luna",
        "choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "lookup", "arguments": '{"query":"pi-ai"}'},
            }],
        }, "finish_reason": "tool_calls"}],
    })
    ctx = _context(tools=[tool])
    ctx.system_prompt.append(PromptSegment(content="Use lookup for current facts.",
                                           cache="dynamic", name="tool-hint"))
    response = await provider.generate(ctx, _model())
    body = provider.sent[0][1]
    assert body["tools"][0]["function"]["name"] == "lookup"
    assert any(m["role"] == "system" and "Use lookup" in m["content"]
               for m in body["messages"])
    assert response.finish is False
    assert isinstance(response.message.content[0], ToolCallBlock)
    assert response.message.content[0].args == {"query": "pi-ai"}
    assert response.provider_data["stop_reason"] == "tool_calls"


async def test_openrouter_replays_tool_call_and_result_on_next_request():
    tool_call = ToolCallBlock(id="call_7", name="lookup", args={"query": "x"})
    ctx = _context(messages=[
        Message(kind=MessageKind.USER, content=[TextBlock(text="look this up")]),
        Message(kind=MessageKind.PROVIDER, content=[tool_call]),
        Message(kind=MessageKind.TOOL, tool_call_id="call_7",
                tool_status="completed", content=[TextBlock(text="result")]),
    ])
    provider = _MockOpenRouter()
    await provider.generate(ctx, _model())
    body = provider.sent[0][1]
    assistant, result = body["messages"][2:]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call_7"
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"query": "x"}'
    assert result == {"role": "tool", "tool_call_id": "call_7", "content": "result"}


async def test_openrouter_invalid_tool_arguments_raise_invalid_request():
    provider = _MockOpenRouter(response={
        "model": "qwen/qwen3.8-flash",
        "choices": [{"message": {"role": "assistant", "content": None,
                                  "tool_calls": [{
                                      "id": "call_1", "type": "function",
                                      "function": {"name": "lookup", "arguments": "{"},
                                  }]}, "finish_reason": "tool_calls"}],
    })
    with pytest.raises(InvalidRequestError, match="not valid JSON"):
        await provider.generate(_context(), _model())


def test_openrouter_maps_tool_result_and_image_content():
    provider = _MockOpenRouter()
    image = ImageBlock(data="aW1n", name="result.png", mime_type="image/png")
    ctx = _context(messages=[
        Message(kind=MessageKind.TOOL, tool_call_id="call_1",
                tool_status="completed",
                content=[TextBlock(text="found"), image]),
    ])
    body = provider._build_request(ctx, _model())
    assert body["messages"][1] == {
        "role": "tool", "tool_call_id": "call_1", "content": "found"}
    assert body["messages"][2]["role"] == "user"
    assert body["messages"][2]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,aW1n"},
    }


def test_openrouter_maps_user_image_content():
    provider = _MockOpenRouter()
    ctx = _context(messages=[Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="describe"),
                 ImageBlock(data="aW1n", name="x.png", mime_type="image/png")],
    )])
    body = provider._build_request(ctx, _model())
    assert body["messages"][1]["content"][1]["type"] == "image_url"


@pytest.mark.parametrize("media", [
    AudioBlock(data="YQ==", name="a.wav", mime_type="audio/wav"),
    VideoBlock(data="dg==", name="v.mp4", mime_type="video/mp4"),
    FileBlock(data="Zg==", name="f.pdf", mime_type="application/pdf"),
])
def test_openrouter_rejects_unsupported_user_media(media):
    provider = _MockOpenRouter()
    ctx = _context(messages=[Message(kind=MessageKind.USER, content=[media])])
    with pytest.raises(InvalidRequestError, match="does not support"):
        provider._build_request(ctx, _model())


def test_openrouter_rejects_unsupported_tool_result_media():
    provider = _MockOpenRouter()
    ctx = _context(messages=[Message(
        kind=MessageKind.TOOL, tool_call_id="call_1",
        tool_status="completed",
        content=[FileBlock(data="Zg==", name="f.pdf")],
    )])
    with pytest.raises(InvalidRequestError, match="does not support 'file'"):
        provider._build_request(ctx, _model())


async def test_openrouter_nonstream_response_metadata_is_preserved():
    provider = _MockOpenRouter(response={
        "model": "qwen/qwen3.8-flash",
        "choices": [{"message": {"role": "assistant", "content": "ok"},
                     "finish_reason": "stop"}],
        "openrouter_metadata": {"provider_name": "example", "route": ["a"]},
    })
    response = await provider.generate(_context(), _model())
    assert response.provider_data == {
        "stop_reason": "stop",
        "openrouter_metadata": {"provider_name": "example", "route": ["a"]},
    }


@pytest.mark.parametrize("metadata", [None, "not a mapping", ["bad"]])
async def test_openrouter_ignores_missing_or_invalid_nonstream_metadata(metadata):
    response_body = {
        "model": "qwen/qwen3.8-flash",
        "choices": [{"message": {"role": "assistant", "content": "ok"},
                     "finish_reason": "stop"}],
    }
    if metadata is not None:
        response_body["openrouter_metadata"] = metadata
    provider = _MockOpenRouter(response=response_body)
    response = await provider.generate(_context(), _model())
    assert response.provider_data == {"stop_reason": "stop"}


@pytest.mark.parametrize("status,body,error_type", [
    (400, {"error": {"message": "invalid request"}}, InvalidRequestError),
    (401, {"error": {"message": "bad key"}}, AuthenticationError),
    (403, {"error": {"message": "forbidden"}}, AuthenticationError),
    (413, {"error": {"message": "too large"}}, RequestTooLargeError),
    (422, {"error": {"message": "unprocessable"}}, InvalidRequestError),
    (429, {"error": {"message": "slow down"}}, RateLimitedError),
    (500, {"error": {"message": "server error"}}, ServerError),
])
async def test_openrouter_http_errors_are_classified(status, body, error_type):
    provider = _MockOpenRouter(error=_HttpResponseError(status, body))
    with pytest.raises(error_type):
        await provider.generate(_context(), _model())


def test_openrouter_metadata_header_is_shared_by_sync_and_stream_paths():
    provider = _MockOpenRouter()
    assert provider._additional_headers() == {
        "X-OpenRouter-Metadata": "enabled"}
    assert provider._stream_headers()["X-OpenRouter-Metadata"] == "enabled"
    assert provider._stream_headers()["Authorization"] == "Bearer test-key"


def test_openrouter_provider_config_resolves_registered_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_TEST_KEY", "unit-test-key")
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text(
        "openrouter-test:\n"
        "  adapter: openrouter\n"
        "  base_url: https://openrouter.example/v1\n"
        "  api_key: '{{env.OPENROUTER_TEST_KEY}}'\n",
        encoding="utf-8",
    )
    candidates = load_provider_candidates(yaml_path)
    assert candidates["openrouter-test"][0] is OpenRouterProvider
    provider = ProviderRegistry(candidates).get("openrouter-test")
    assert provider.name == "openrouter"
    assert provider.config["api_key"] == "unit-test-key"
    assert provider.config["base_url"] == "https://openrouter.example/v1"


def test_openrouter_nonstream_thinking_response_mapping():
    provider = _MockOpenRouter()
    response = provider._map_response({
        "model": "openrouter/model",
        "choices": [{
            "message": {"role": "assistant", "reasoning_details": [{"text": "reason"}],
                        "content": "answer"},
            "finish_reason": "stop",
        }],
    })
    assert [block.type for block in response.message.content] == ["thinking", "text"]
    assert response.message.content[0].thinking == '[{"text": "reason"}]'


class _FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, lines):
        self.lines = lines

    async def aread(self):
        return b"{}"

    def aiter_lines(self):
        async def _generate():
            for line in self.lines:
                yield line
        return _generate()


class _FakeStreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


class _FakeStreamClient:
    def __init__(self, lines):
        self.response = _FakeResponse(lines)
        self.kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, **kwargs):
        self.kwargs = kwargs
        return _FakeStreamContext(self.response)


async def test_openrouter_stream_carries_metadata_on_final_delta(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]},
        {"openrouter_metadata": {"route": ["provider-a"]},
         "choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 2, "completion_tokens": 1}},
    ]
    lines = [f"data: {json.dumps(chunk)}" for chunk in chunks] + ["data: [DONE]"]
    client = _FakeStreamClient(lines)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)
    provider = OpenRouterProvider(ProviderConfig({
        "api_key": "test-key", "base_url": "https://openrouter.example/v1"}))
    deltas = [d async for d in provider.generate_stream(_context(), _model())]
    final = deltas[-1]
    assert final.provider_data == {
        "stop_reason": "stop", "openrouter_metadata": {"route": ["provider-a"]}}
    assert final.usage.total_tokens == 3
    assert client.kwargs["headers"]["X-OpenRouter-Metadata"] == "enabled"


async def test_openrouter_stream_without_metadata_keeps_base_shape(monkeypatch):
    chunks = [
        {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    ]
    lines = [f"data: {json.dumps(chunk)}" for chunk in chunks] + ["data: [DONE]"]
    client = _FakeStreamClient(lines)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)
    provider = OpenRouterProvider(ProviderConfig({
        "api_key": "test-key", "base_url": "https://openrouter.example/v1"}))
    deltas = [d async for d in provider.generate_stream(_context(), _model())]
    assert deltas[-1].provider_data == {"stop_reason": "stop"}


async def test_openrouter_actual_post_sends_metadata_header(monkeypatch):
    class _Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"ok": True}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            self.url = url
            self.kwargs = kwargs
            return _Response()

    client = _Client()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)
    provider = OpenRouterProvider(ProviderConfig({
        "api_key": "test-key", "base_url": "https://openrouter.example/v1"}))
    assert await provider._post("/chat/completions", {"model": "m"}) == {"ok": True}
    assert client.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert client.kwargs["headers"]["X-OpenRouter-Metadata"] == "enabled"


def test_fixture_openrouter_model_contains_literal_effort(fixtures_dir):
    config = load_models(fixtures_dir / "providers" / "models.yaml")["openrouter-gpt6"]
    assert config["reasoning.effort"] == "high"
