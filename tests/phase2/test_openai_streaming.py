"""OpenAI / DeepSeek 家族真 SSE 流式解析（generate_stream 覆写）离网单测。

不发起真实网络：monkeypatch ``httpx.AsyncClient``，把预先写好的
``data: <json>`` / ``data: [DONE]`` 行喂给 generate_stream，校验正文 /
思考 / 工具调用 / usage / finish_reason 逐 delta 产出与组装。
"""

import httpx
import pytest

from flowing.context import Context
from flowing.message import ThinkingBlock, ToolCallBlock
from flowing.model import ModelConfig
from flowing.providers.openai import DeepSeekProvider
from flowing.providers.provider import ProviderDelta


def _ctx() -> Context:
    return Context(system_prompt=[], tools=[], messages=[])


def _model() -> ModelConfig:
    return ModelConfig(model="deepseek-reasoner", provider="deepseek")


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


class _FakeStreamCtx:
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
        return _FakeStreamCtx(_FakeResp(self.lines))


def _run(monkeypatch, lines):
    import asyncio
    client = _FakeClient()
    client.lines = lines

    class _Factory:
        def __call__(self, *a, **k):
            return client

    monkeypatch.setattr(httpx, "AsyncClient", _Factory())
    provider = DeepSeekProvider({"api_key": "k", "base_url": "http://fake/"})

    async def _collect():
        return [d async for d in provider.generate_stream(_ctx(), _model())]
    return asyncio.run(_collect())


def _line(chunk: dict) -> str:
    import json
    return "data: " + json.dumps(chunk) + "\n"


def _accumulate(deltas):
    """复刻 provider_gen 的累积规则：按 content_index 归位成最终 content 列表。

    用与 ``flowing.agent.Agent.provider_gen`` 完全一致的规则拼装，从而
    离网验证「真实内容按正确顺序与归属拼成最终消息」。
    """
    from flowing.message import TextBlock, ThinkingBlock
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
            tx = ((ex.text if isinstance(ex, TextBlock) else "") + d.text)
            acc[d.content_index] = TextBlock(text=tx)
    return [acc[i] for i in sorted(acc)]


def test_thinking_then_text_streamed(monkeypatch):
    deltas = _run(monkeypatch, [
        _line({"choices": [{"index": 0, "delta": {"role": "assistant"},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0,
                            "delta": {"reasoning_content": "step1 "},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0,
                            "delta": {"reasoning_content": "step2"},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {"content": "你好"},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {"content": "世界"},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                         "total_tokens": 15}}),
        "data: [DONE]",
    ])
    # 前两条 thinking（共享 content_index），再两条 text
    kinds = [(d.kind, d.content_index, d.text) for d in deltas]
    think = [d for d in deltas if d.kind == "thinking"]
    text = [d for d in deltas if d.kind == "text"]
    assert [d.text for d in think] == ["step1 ", "step2"]
    assert len({d.content_index for d in think}) == 1      # 同一思考块共享 index
    assert text[0].content_index != think[0].content_index  # 思考/正文分块
    assert text[0].text + text[1].text == "你好世界"
    final = deltas[-1]
    assert final.usage is not None and final.usage.total_tokens == 15
    assert final.provider_data == {"stop_reason": "stop"}
    # 末帧只用 usage/finish，不得占用已累积的 thinking(0)/text(1) index
    assert final.content_index not in {think[0].content_index, text[0].content_index}
    assert final.text == ""


def test_assembled_message_order_and_content(monkeypatch):
    """确定性地验证最终消息：思考块 → 正文块 → 工具块 顺序与内容逐块吻合。"""
    from flowing.message import TextBlock, ThinkingBlock
    deltas = _run(monkeypatch, [
        _line({"choices": [{"index": 0, "delta": {"reasoning_content": "理由一 "},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {"reasoning_content": "理由二"},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {"content": "结论"},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_9", "type": "function",
             "function": {"name": "lookup", "arguments": '{"q":1}'}}]},
            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
               "usage": {"prompt_tokens": 5, "completion_tokens": 7}}),
        "data: [DONE]",
    ])
    content = _accumulate(deltas)
    # 顺序：思考(0) → 正文(1) → 工具(2)；末帧空载不出现
    assert [type(b) for b in content] == [ThinkingBlock, TextBlock, ToolCallBlock]
    assert content[0].thinking == "理由一 理由二"
    assert content[1].text == "结论"
    assert content[2].id == "call_9" and content[2].name == "lookup"
    assert content[2].args == {"q": 1}
    assert all(d.content_index in {0, 1, 2} for d in deltas if d.text or d.block)


def test_tool_call_accumulated(monkeypatch):
    deltas = _run(monkeypatch, [
        _line({"choices": [{"index": 0, "delta": {
            "tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                            "function": {"name": "lookup", "arguments": ""}}]},
            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"q":"天'}}]},
            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '气"}'}}]},
            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
               "usage": {"prompt_tokens": 2, "completion_tokens": 3}}),
        "data: [DONE]",
    ])
    tool = [d for d in deltas if d.block is not None and isinstance(d.block, ToolCallBlock)]
    assert len(tool) == 1
    tc = tool[0].block
    assert tc.id == "call_1" and tc.name == "lookup"
    assert tc.args == {"q": "天气"}


def test_inband_error_classified(monkeypatch):
    with pytest.raises(Exception) as ei:
        _run(monkeypatch, [
            _line({"error": {"message": "quota exceeded"}, "status": 429}),
        ])
    from flowing.errors import RateLimitedError
    assert isinstance(ei.value, RateLimitedError)


def test_thinking_does_not_leak_into_text(monkeypatch):
    """思考与正文绝不合并进同一 content block（与 pi-ai 分块语义一致）。"""
    deltas = _run(monkeypatch, [
        _line({"choices": [{"index": 0, "delta": {"reasoning_content": "r1"},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {"content": "c1"},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ])
    # 通过 agent 组装语义：思考块与文本块应落在不同 content_index
    assert len({d.content_index for d in deltas if d.kind == "thinking"}) == 1
    assert len({d.content_index for d in deltas if d.kind == "text" and d.text}) == 1
