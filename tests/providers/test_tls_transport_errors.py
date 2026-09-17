"""Provider TLS 传输错误归一化测试。"""

from __future__ import annotations

import ssl

import httpx
import pytest

from flowing.context import Context
from flowing.errors import NetworkError
from flowing.model import ModelConfig
from flowing.providers.anthropic_messages import AnthropicMessagesProvider
from flowing.providers.openai_completions import OpenAICompletionsProvider
from flowing.providers.openai_responses import OpenAIResponsesProvider
from flowing.providers.provider import ProviderConfig


_PROVIDER_TYPES = (
    OpenAICompletionsProvider,
    OpenAIResponsesProvider,
    AnthropicMessagesProvider,
)


def _context() -> Context:
    return Context(system_prompt=[], tools=[], messages=[])


def _model() -> ModelConfig:
    return ModelConfig(model="test-model", provider="test")


class _TlsFailingClient:
    """让两种 HTTP 调用入口都直接抛出未被 httpx 包装的 TLS 异常。"""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        raise ssl.SSLError("SSLV3_ALERT_BAD_RECORD_MAC")

    def stream(self, *args, **kwargs):
        raise ssl.SSLError("SSLV3_ALERT_BAD_RECORD_MAC")


@pytest.mark.parametrize("provider_type", _PROVIDER_TYPES)
async def test_tls_error_from_post_is_network_error(monkeypatch, provider_type):
    """非流式请求的原始 ssl.SSLError 归一为 NetworkError。"""

    monkeypatch.setattr(httpx, "AsyncClient", _TlsFailingClient)
    provider = provider_type(ProviderConfig({
        "api_key": "test-key",
        "base_url": "http://provider.invalid",
    }))

    with pytest.raises(NetworkError, match="provider network failure"):
        await provider.generate(_context(), _model())


@pytest.mark.parametrize("provider_type", _PROVIDER_TYPES)
async def test_tls_error_from_stream_is_network_error(monkeypatch, provider_type):
    """流式请求的原始 ssl.SSLError 归一为 NetworkError。"""

    monkeypatch.setattr(httpx, "AsyncClient", _TlsFailingClient)
    provider = provider_type(ProviderConfig({
        "api_key": "test-key",
        "base_url": "http://provider.invalid",
    }))

    with pytest.raises(NetworkError, match="provider network failure"):
        async for _ in provider.generate_stream(_context(), _model()):
            pass
