"""OpenRouter ``reasoning_details`` 列表形态（Responses 系模型）离网单测。

OpenRouter 对 ``openai/gpt-6-luna`` 这类 Responses 系模型在 chat
completions 流里用 ``delta.reasoning_details`` 回传结构化 item 序列：
逐 token 的 ``reasoning.summary`` 元素 + 收尾的 ``reasoning.encrypted``
元素。本文件验证其语义正确性：

- 逐 token summary 组装后是**可读思考散文**（与原文逐字相等），而不是
  JSON 碎片拼接；
- ``reasoning.encrypted`` item 完整 JSON 进 ``ThinkingBlock.signature``、
  绝不出现在思考文本里；
- 多轮回放把 signature 还原为与原始 item 深等价的 dict 列表——即
  “服务端下一轮收到的东西”在语义上与“服务端上一轮发出的东西”一致；
- REPL 消息摘要渲染出的是思考散文本身。

不发起真实网络：monkeypatch ``httpx.AsyncClient`` 喂预写 SSE 行。
"""

import asyncio
import json

import httpx

from flowing.context import Context
from flowing.message import Message, MessageKind, TextBlock, ThinkingBlock, ToolCallBlock
from flowing.model import ModelConfig
from flowing.providers.openrouter import OpenRouterProvider

# 一条模拟 gpt-6-luna 思考的完整散文（逐 token 切碎喂流）。
PROSE = "I think it's an apple. I'll ask the oracle before guessing."
TOKENS = ["I", " think", " it's", " an apple", ".", " I'll", " ask",
          " the oracle", " before", " guessing", "."]

ENCRYPTED_ITEM = {
    "type": "reasoning.encrypted",
    "data": "gAAAAABqzOZbTvbFO3Ft6NcUXXiBnrzs5ucktz3_xTtgf4lYa6MVi06P"
            "lyWqYczZ8xh8uMtzHDJ0od3CZZDuS7jJEJcLyw9TUY2RIGjOz3uOsadQayI",
    "format": "openai-responses-v1",
    "id": "rs_0a4115dbcd65bea2016acce659cf9087d1b39fb4fb0c855ddd",
    "index": 1,
}


def _ctx() -> Context:
    return Context(system_prompt=[], tools=[], messages=[])


def _model() -> ModelConfig:
    return ModelConfig(model="openai/gpt-6-luna", provider="openrouter")


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
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        return _FakeStreamCtx(_FakeResp(self._lines))


def _run_stream(monkeypatch, lines):
    client = _FakeClient(lines)

    class _Factory:
        def __call__(self, *a, **k):
            return client

    monkeypatch.setattr(httpx, "AsyncClient", _Factory())
    provider = OpenRouterProvider(
        {"api_key": "k", "base_url": "http://fake/"})

    async def _collect():
        return [d async for d in provider.generate_stream(_ctx(), _model())]
    return provider, asyncio.run(_collect())


def _line(chunk: dict) -> str:
    return "data: " + json.dumps(chunk) + "\n"


def _summary_chunk(token: str) -> str:
    return _line({"choices": [{"index": 0, "delta": {"reasoning_details": [
        {"type": "reasoning.summary", "summary": token,
         "format": "openai-responses-v1", "index": 0}]}, "finish_reason": None}]})


def _accumulate(deltas):
    """复刻 ``flowing.agent.Agent.provider_gen`` 的累积规则。"""
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


def _full_stream_lines(with_encrypted=True):
    lines = [_summary_chunk(t) for t in TOKENS]
    if with_encrypted:
        lines.append(_line({"choices": [{"index": 0, "delta": {
            "reasoning_details": [ENCRYPTED_ITEM]}, "finish_reason": None}]}))
    lines += [
        _line({"choices": [{"index": 0, "delta": {"content": "The fruit "},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {"content": "is an apple."},
                            "finish_reason": None}]}),
        _line({"choices": [{"index": 0, "delta": {},
                            "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 20,
                         "total_tokens": 30}}),
        "data: [DONE]",
    ]
    return lines


# ── 入站：逐 token summary 组装成可读散文 ─────────────────────────────

def test_summary_tokens_assemble_readable_prose(monkeypatch):
    provider, deltas = _run_stream(monkeypatch, _full_stream_lines())
    content = _accumulate(deltas)
    assert [type(b) for b in content] == [ThinkingBlock, TextBlock]
    thinking = content[0].thinking
    # 语义校验：与原文逐字相等（不是关键词匹配——任何碎片拼接都过不了）
    assert thinking == PROSE
    # 绝无 JSON 碎片混入
    assert '"' not in thinking and "{" not in thinking
    assert "reasoning.summary" not in thinking
    assert content[1].text == "The fruit is an apple."
    # 所有 thinking delta 共享一个 content_index
    think_idx = {d.content_index for d in deltas if d.kind == "thinking"}
    assert len(think_idx) == 1


def test_encrypted_item_lands_in_signature_not_text(monkeypatch):
    provider, deltas = _run_stream(monkeypatch, _full_stream_lines())
    content = _accumulate(deltas)
    block = content[0]
    assert isinstance(block, ThinkingBlock)
    # encrypted blob 绝不进思考文本
    assert "gAAAA" not in block.thinking and "encrypted" not in block.thinking
    # signature 还原出与原始 item 深等价的 dict
    assert json.loads(block.signature) == ENCRYPTED_ITEM


# ── 出站：多轮回放深等价 ──────────────────────────────────────────────

def test_outbound_replay_restores_items_deep_equal(monkeypatch):
    provider, deltas = _run_stream(monkeypatch, _full_stream_lines())
    content = _accumulate(deltas)
    msg = Message(kind=MessageKind.PROVIDER, content=content)
    mapped = provider._map_message(msg)[0]
    # 回放写回的是 item 列表而非字符串
    assert isinstance(mapped["reasoning_details"], list)
    replayed = mapped["reasoning_details"]
    # encrypted item 与原始 item 深等价（服务端能认出的同一对象）
    assert replayed[-1] == ENCRYPTED_ITEM
    # summary 思考文本完整保留（包装回 summary item）
    assert replayed[0]["type"] == "reasoning.summary"
    assert replayed[0]["summary"] == PROSE
    # 正文不丢
    assert mapped["content"] == "The fruit is an apple."


def test_multiturn_roundtrip_preserves_semantics(monkeypatch):
    """完整多轮语义回环：第 1 轮组装 → 出站回放 → 作为第 1 轮“录制的
    响应”重新入站（模拟服务端视角）→ 思考文本与签名逐项复原。"""
    _, deltas = _run_stream(monkeypatch, _full_stream_lines())
    content = _accumulate(deltas)
    provider = OpenRouterProvider({"api_key": "k", "base_url": "http://fake/"})
    provider._reasoning_dialect = "reasoning_details"
    msg = Message(kind=MessageKind.PROVIDER, content=content)
    replayed = provider._map_message(msg)[0]["reasoning_details"]

    # 第 2 轮请求体：历史消息经 _build_request 进 messages 数组
    ctx = Context(system_prompt=[], tools=[], messages=[msg])
    body = provider._build_request(ctx, _model())
    assistant = body["messages"][0]
    assert assistant["reasoning_details"] == replayed

    # 服务端视角：把回放列表当作非流式响应的 message 重新入站
    provider2 = OpenRouterProvider({"api_key": "k", "base_url": "http://fake/"})
    resp = {"choices": [{"message": dict(assistant), "finish_reason": "stop"}]}
    mapped2 = provider2._map_response(resp).message
    thinking2 = next(b for b in mapped2.content if isinstance(b, ThinkingBlock))
    assert thinking2.thinking == PROSE
    assert json.loads(thinking2.signature) == ENCRYPTED_ITEM


def test_outbound_without_signature_wraps_summary_item(monkeypatch):
    """无 encrypted item 的流（signature=None）：回放包装为 summary
    item，思考文本不丢失。"""
    _, deltas = _run_stream(monkeypatch, _full_stream_lines(with_encrypted=False))
    content = _accumulate(deltas)
    provider = OpenRouterProvider({"api_key": "k", "base_url": "http://fake/"})
    provider._reasoning_dialect = "reasoning_details"
    msg = Message(kind=MessageKind.PROVIDER, content=content)
    replayed = provider._map_message(msg)[0]["reasoning_details"]
    assert len(replayed) == 1
    assert replayed[0]["type"] == "reasoning.summary"
    assert replayed[0]["summary"] == PROSE


# ── 非流式入站 ────────────────────────────────────────────────────────

def test_nonstream_map_response_parses_details_list():
    provider = OpenRouterProvider({"api_key": "k", "base_url": "http://fake/"})
    details = [
        {"type": "reasoning.summary", "summary": "First part. ",
         "format": "openai-responses-v1", "index": 0},
        {"type": "reasoning.summary", "summary": "Second part.",
         "format": "openai-responses-v1", "index": 0},
        ENCRYPTED_ITEM,
    ]
    resp = {"choices": [{"message": {"role": "assistant",
                                     "reasoning_details": details,
                                     "content": "Answer."},
                         "finish_reason": "stop"}]}
    msg = provider._map_response(resp).message
    thinking = next(b for b in msg.content if isinstance(b, ThinkingBlock))
    assert thinking.thinking == "First part. Second part."
    assert json.loads(thinking.signature) == ENCRYPTED_ITEM
    text = next(b for b in msg.content if isinstance(b, TextBlock))
    assert text.text == "Answer."


def test_nonstream_string_dialect_unchanged():
    """字符串形态方言（如 DeepSeek ``reasoning_content``）不受影响。"""
    provider = OpenRouterProvider({"api_key": "k", "base_url": "http://fake/"})
    resp = {"choices": [{"message": {"role": "assistant",
                                     "reasoning_content": "plain thought"},
                         "finish_reason": "stop"}]}
    msg = provider._map_response(resp).message
    thinking = next(b for b in msg.content if isinstance(b, ThinkingBlock))
    assert thinking.thinking == "plain thought"
    assert thinking.signature is None
    provider._reasoning_dialect == "reasoning_content"
    replayed = provider._map_message(msg)[0]
    assert replayed["reasoning_content"] == "plain thought"
    assert "reasoning_details" not in replayed


# ── REPL 渲染语义 ─────────────────────────────────────────────────────

def test_repl_summary_renders_prose_not_json(monkeypatch):
    from flowing.interfaces.repl import _summarize_message
    _, deltas = _run_stream(monkeypatch, _full_stream_lines())
    content = _accumulate(deltas)
    msg = Message(kind=MessageKind.PROVIDER, content=content)
    rendered = _summarize_message(None, msg)
    assert PROSE in rendered
    assert '"reasoning.summary"' not in rendered
    assert "gAAAA" not in rendered
