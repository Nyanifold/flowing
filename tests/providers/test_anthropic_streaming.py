"""Anthropic Messages 真 SSE 流式解析（generate_stream 覆写）离网单测。

monkeypatch ``httpx.AsyncClient`` 喂 ``data: <json>`` 帧，校验正文 /
思考(含 signature) / 工具 / usage / stop_reason 逐 delta 产出。
"""

import asyncio

import httpx
import pytest

from flowing.context import Context
from flowing.message import TextBlock, Message, MessageKind, ToolCallBlock
from flowing.model import ModelConfig
from flowing.providers.anthropic import AnthropicProvider
from flowing.providers.provider import ProviderDelta


def _ctx() -> Context:
    return Context(system_prompt=[], tools=[], messages=[
        Message(kind=MessageKind.USER, content=[TextBlock(text="hi")])])


def _model() -> ModelConfig:
    return ModelConfig(model="claude-x", provider="anthropic", thinking_budget=1024)


class _FakeResp:
    def __init__(self, lines):
        self.status_code = 200
        self.headers = {}
        self._lines = lines

    async def aread(self):
        return b"{}"

    def aiter_lines(self):
        async def _g():
            for line in self._lines:
                yield line
        return _g()


class _FakeCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self):
        self.lines = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        return _FakeCtx(_FakeResp(self.lines))


def _run(monkeypatch, lines):
    client = _FakeClient()
    client.lines = lines

    class _Factory:
        def __call__(self, *a, **k):
            return client

    monkeypatch.setattr(httpx, "AsyncClient", _Factory())
    provider = AnthropicProvider({"api_key": "k", "base_url": "http://fake/"})

    async def _collect():
        return [d async for d in provider.generate_stream(_ctx(), _model())]
    return asyncio.run(_collect())


def _line(chunk: dict) -> str:
    import json
    return "data: " + json.dumps(chunk) + "\n"


def test_text_and_thinking_streamed_with_signature(monkeypatch):
    deltas = _run(monkeypatch, [
        _line({"type": "message_start", "message": {
            "id": "msg_1", "usage": {"input_tokens": 10,
                                     "cache_read_input_tokens": 2}}}),
        _line({"type": "content_block_start", "index": 0,
               "content_block": {"type": "thinking", "thinking": "",
                                 "signature": "sig_abc"}}),
        _line({"type": "content_block_delta", "index": 0,
               "delta": {"type": "thinking_delta", "thinking": "想一步 "}}),
        _line({"type": "content_block_delta", "index": 0,
               "delta": {"type": "thinking_delta", "thinking": "想两步"}}),
        _line({"type": "content_block_stop", "index": 0}),
        _line({"type": "content_block_start", "index": 1,
               "content_block": {"type": "text", "text": ""}}),
        _line({"type": "content_block_delta", "index": 1,
               "delta": {"type": "text_delta", "text": "你好"}}),
        _line({"type": "content_block_delta", "index": 1,
               "delta": {"type": "text_delta", "text": "世界"}}),
        _line({"type": "content_block_stop", "index": 1}),
        _line({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
               "usage": {"output_tokens": 6}}),
        _line({"type": "message_stop"}),
    ])
    think = [d for d in deltas if d.kind == "thinking"]
    text = [d for d in deltas if d.kind == "text"]
    # 思考分块 + 签名保留（首条 thinking delta 携带 signature）
    assert "".join(d.text for d in think) == "想一步 想两步"
    assert all(d.content_index == 0 for d in think)
    assert think[0].signature == "sig_abc"
    # 正文独立分块
    assert text[0].text + text[1].text == "你好世界"
    assert text[0].content_index == 1
    final = deltas[-1]
    assert final.usage is not None and final.usage.output == 6
    assert final.provider_data == {"stop_reason": "end_turn"}
    # 末帧只载 usage/finish，不得占用已累积的 thinking(0)/text(1) index
    assert final.content_index not in {0, 1}
    assert final.text == ""


def _accumulate(deltas):
    """复刻 provider_gen 的累积规则（与 openai 家族测试同款）。"""
    from flowing.message import TextBlock as TB, ThinkingBlock
    acc = {}
    for d in deltas:
        if d.block is not None:
            acc[d.content_index] = d.block
        elif d.kind == "thinking":
            ex = acc.get(d.content_index)
            if isinstance(ex, ThinkingBlock):
                th = ex.thinking + d.text
                sig = ex.signature or d.signature
            else:
                th = d.text
                sig = d.signature
            acc[d.content_index] = ThinkingBlock(thinking=th, signature=sig)
        elif d.text or d.content_index in acc:
            ex = acc.get(d.content_index)
            tx = ((ex.text if isinstance(ex, TB) else "") + d.text)
            acc[d.content_index] = TB(text=tx)
    return [acc[i] for i in sorted(acc)]


def test_assembled_order_content_and_signature(monkeypatch):
    """确定性验证：思考块(signature 保留)→正文块→末帧空载不污染内容。"""
    from flowing.message import TextBlock as TB, ThinkingBlock
    deltas = _run(monkeypatch, [
        _line({"type": "content_block_start", "index": 0,
               "content_block": {"type": "thinking", "thinking": "",
                                 "signature": "sig_x"}}),
        _line({"type": "content_block_delta", "index": 0,
               "delta": {"type": "thinking_delta", "thinking": "想a"}}),
        _line({"type": "content_block_delta", "index": 0,
               "delta": {"type": "thinking_delta", "thinking": "想b"}}),
        _line({"type": "content_block_stop", "index": 0}),
        _line({"type": "content_block_start", "index": 1,
               "content_block": {"type": "text", "text": ""}}),
        _line({"type": "content_block_delta", "index": 1,
               "delta": {"type": "text_delta", "text": "答"}}),
        _line({"type": "content_block_stop", "index": 1}),
        _line({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
               "usage": {"output_tokens": 3, "input_tokens": 4}}),
        _line({"type": "message_stop"}),
    ])
    content = _accumulate(deltas)
    assert [type(b) for b in content] == [ThinkingBlock, TB]
    assert content[0].thinking == "想a想b"
    assert content[0].signature == "sig_x"   # 签名保留（Anthropic 回放必需）
    assert content[1].text == "答"
    # 末帧空载 delta 不得出现在最终 content（也不得覆盖思考块）
    assert not any(getattr(d, "text", None) == "" and d.kind == "text"
                   for d in deltas[:-1])


def test_tool_use_via_input_json_delta(monkeypatch):
    deltas = _run(monkeypatch, [
        _line({"type": "message_start", "message": {"usage": {}}}),
        _line({"type": "content_block_start", "index": 0,
               "content_block": {"type": "tool_use", "id": "tu_1",
                                 "name": "lookup", "input": ""}}),
        _line({"type": "content_block_delta", "index": 0,
               "delta": {"type": "input_json_delta", "partial_json": '{"q":"天'}}),
        _line({"type": "content_block_delta", "index": 0,
               "delta": {"type": "input_json_delta", "partial_json": '气"}'}}),
        _line({"type": "content_block_stop", "index": 0}),
        _line({"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
        _line({"type": "message_stop"}),
    ])
    tool = [d for d in deltas if d.block is not None and isinstance(d.block, ToolCallBlock)]
    assert len(tool) == 1
    tc = tool[0].block
    assert tc.id == "tu_1" and tc.name == "lookup"
    assert tc.args == {"q": "天气"}


def test_tool_use_start_with_empty_dict_input(monkeypatch):
    """回归：content_block_start 的 tool_use 带空 dict input（kimi code 端点
    线形）时，args 不得以 "{}" 为初值——否则与后续 input_json_delta 拼接成
    两个 JSON 对象，收尾解析失败退化为空参数。"""
    deltas = _run(monkeypatch, [
        _line({"type": "message_start", "message": {"usage": {}}}),
        _line({"type": "content_block_start", "index": 0,
               "content_block": {"type": "tool_use", "id": "tool_abc",
                                 "name": "bash", "input": {}}}),
        _line({"type": "content_block_delta", "index": 0,
               "delta": {"type": "input_json_delta",
                         "partial_json": '{"command":"echo'}}),
        _line({"type": "content_block_delta", "index": 0,
               "delta": {"type": "input_json_delta",
                         "partial_json": ' hello"}'}}),
        _line({"type": "content_block_stop", "index": 0}),
        _line({"type": "message_delta", "delta": {"stop_reason": "tool_use"}}),
        _line({"type": "message_stop"}),
    ])
    tool = [d for d in deltas if d.block is not None and isinstance(d.block, ToolCallBlock)]
    assert len(tool) == 1
    tc = tool[0].block
    assert tc.id == "tool_abc" and tc.name == "bash"
    assert tc.args == {"command": "echo hello"}


def test_inband_error_classified(monkeypatch):
    with pytest.raises(Exception) as ei:
        _run(monkeypatch, [
            _line({"type": "error",
                   "error": {"type": "overloaded_error", "message": "busy"}}),
        ])
    from flowing.errors import ServerError
    assert isinstance(ei.value, ServerError)
