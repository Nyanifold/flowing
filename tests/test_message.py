"""message 模块测试（简报测试清单 M1–M25）。

按子域分组：模型与序列化（M1–M11）、MessageQueue（M12–M19）、
MessageChain（M20–M25，stub owner 驱动）。
"""

import base64

import pytest

from flowing.message import (
    MEDIA_TOKEN_ESTIMATE,
    AudioBlock,
    FileBlock,
    ImageBlock,
    Message,
    MessageChain,
    MessageKind,
    MessagePriority,
    MessageQueue,
    StructBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    VideoBlock,
    estimate_message_tokens,
    from_record,
    to_record,
)


def make_message(kind=MessageKind.USER, **kw) -> Message:
    kw.setdefault("content", [TextBlock(text="hi")])
    return Message(kind=kind, **kw)


class TestMessageKindAndPriority:
    def test_m1_kind_enum(self):
        """M1：八值枚举，字符串值为落盘值，无 ASSISTANT 旧名。"""
        assert len(MessageKind) == 8
        for name in ("USER", "PROVIDER", "TOOL", "SYSTEM", "PEER", "EVENT", "PLUGIN", "SUBAGENT"):
            assert hasattr(MessageKind, name)
        assert MessageKind.PROVIDER.value == "provider"
        assert not hasattr(MessageKind, "ASSISTANT")

    def test_m2_priority_ordering(self):
        """M2：数值越小越优先。"""
        assert (
            MessagePriority.INTERRUPT
            < MessagePriority.STEER
            < MessagePriority.HIGH
            < MessagePriority.NORMAL
            < MessagePriority.LOW
        )


class TestMessageModel:
    def test_m3_tool_kind_bidirectional_enforcement(self):
        """M3：kind=TOOL ⟺ tool_call_id/tool_status 非 None，双向强制。"""
        with pytest.raises(ValueError):
            make_message(MessageKind.TOOL)  # 缺两者
        with pytest.raises(ValueError):
            make_message(MessageKind.TOOL, tool_call_id="c1")  # 缺 tool_status
        with pytest.raises(ValueError):
            make_message(MessageKind.TOOL, tool_status="completed")  # 缺 tool_call_id
        with pytest.raises(ValueError):
            make_message(MessageKind.USER, tool_call_id="c1", tool_status="completed")
        with pytest.raises(ValueError):
            make_message(MessageKind.USER, tool_call_id="c1")
        ok = make_message(MessageKind.TOOL, tool_call_id="c1", tool_status="completed")
        assert ok.tool_call_id == "c1"

    def test_m4_defaults(self):
        """M4：字段默认值（X1 落实）。"""
        m1, m2 = make_message(), make_message()
        assert m1.id != m2.id
        assert m1.parent_id is None
        assert m1.turn_end is False and m1.partial is False and m1.synthetic is False
        assert m1.source == ""
        assert m1.tags == [] and m1.tags is not m2.tags  # 不共享同一 list
        assert m1.priority == MessagePriority.NORMAL
        assert m1.timestamp.tzinfo is None  # 时区无关（UTC naive）
        assert m1.usage is None
        assert m1.tool_call_id is None and m1.tool_status is None

    def test_m5_struct_block_json_validation(self):
        """M5：StructBlock data 必须 JSON 兼容。"""
        with pytest.raises(ValueError):
            StructBlock(data=object())
        ok = StructBlock(data={"a": [1, 2]})
        assert ok.data == {"a": [1, 2]}

    def test_m6_block_type_literals(self):
        """M6：八 block 的 type 字段固定 Literal 值。"""
        cases = [
            (TextBlock(text="x"), "text"),
            (ThinkingBlock(thinking="x"), "thinking"),
            (ToolCallBlock(id="i", name="n", args={}), "tool_call"),
            (StructBlock(data={}), "struct"),
            (ImageBlock(data="d", name="n"), "image"),
            (VideoBlock(data="d", name="n"), "video"),
            (AudioBlock(data="d", name="n"), "audio"),
            (FileBlock(data="d", name="n"), "file"),
        ]
        for block, expected in cases:
            assert block.type == expected

    def test_m8_media_data_and_name_required(self):
        """M8：媒体块 data/name 必填，缺省即 TypeError。"""
        with pytest.raises(TypeError):
            ImageBlock()  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            ImageBlock(data="d")  # type: ignore[call-arg] 缺 name
        with pytest.raises(TypeError):
            FileBlock(name="n")  # type: ignore[call-arg] 缺 data


class TestSerialization:
    def _sample_messages(self) -> list[Message]:
        """全 kind × 全 block type 样本集。"""
        samples = []
        for kind in MessageKind:
            content = [
                TextBlock(text="文本"),
                ThinkingBlock(thinking="思考", signature="sig"),
                ToolCallBlock(id="call-1", name="tool", args={"a": 1, "b": "中文"}),
                StructBlock(data={"k": [1, 2, {"z": None}]}),
                ImageBlock(data=base64.b64encode(b"img").decode(), name="i.png", mime_type="image/png"),
                VideoBlock(data="dg==", name="v.mp4"),
                AudioBlock(data="dg==", name="a.mp3", mime_type="audio/mpeg"),
                FileBlock(data="dg==", name="f.bin"),
            ]
            kw = {}
            if kind is MessageKind.TOOL:
                kw = {"tool_call_id": "call-1", "tool_status": "completed"}
            samples.append(Message(
                kind=kind, content=content, source="test", tags=["t1", "t2"],
                priority=MessagePriority.HIGH, turn_end=True, **kw,
            ))
        return samples

    def test_m7_roundtrip(self):
        """M7：to_record → from_record 逐字段等值；kind 落盘为字符串值（X2 行格式）。"""
        for msg in self._sample_messages():
            rec = to_record(msg)
            assert rec["type"] == "message"
            assert rec["kind"] == msg.kind.value  # 字符串值落盘
            assert isinstance(rec["priority"], int)
            assert isinstance(rec["timestamp"], str)
            restored = from_record(rec)
            assert restored == msg, f"kind={msg.kind}"
            assert restored.tags is not msg.tags  # 深拷贝，不共享

    def test_m7_fixture_baseline_compatible(self, fixtures_dir):
        """M7 补充：fixtures 的 tree-ok.jsonl 行可被 from_record 解析（行格式冻结基线）。"""
        import json

        lines = (fixtures_dir / "persistence" / "tree-ok.jsonl").read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[0])["type"] == "meta"
        msg = from_record(json.loads(lines[1]))
        assert msg.id == "m1" and msg.kind is MessageKind.USER


class TestEstimateTokens:
    def test_m9_cjk_heuristic(self):
        """M9：100 字符纯中文 ≈ 100 token（非 25）。"""
        msg = make_message(content=[TextBlock(text="中" * 100)])
        assert estimate_message_tokens(msg) == 100

    def test_m10_media_fixed_estimate(self):
        """M10：媒体块固定 MEDIA_TOKEN_ESTIMATE，与 base64 长度无关；空 content → 0。"""
        big = base64.b64encode(b"x" * 225_000).decode()  # 300k base64 字符
        msg = make_message(content=[ImageBlock(data=big, name="big.png")])
        assert estimate_message_tokens(msg) == MEDIA_TOKEN_ESTIMATE == 2000
        assert estimate_message_tokens(make_message(content=[])) == 0

    def test_m11_tool_call_and_struct_blocks(self):
        """M11：ToolCallBlock 计 name + args JSON；StructBlock 计 dumps 长度。"""
        msg = make_message(content=[ToolCallBlock(id="i", name="abcd", args={"k": "v"})])
        # name "abcd"（4 ASCII → 1）+ dumps '{"k": "v"}'（10 ASCII → 3）
        assert estimate_message_tokens(msg) == 1 + 3
        msg2 = make_message(content=[StructBlock(data={"k": "中文"})])
        # dumps '{"k": "中文"}'：9 ASCII → 3，2 非 ASCII → 2
        assert estimate_message_tokens(msg2) == 3 + 2
