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
        assert m1.id is None and m2.id is None  # 缺省未指定——进入 Agent 边界（入队/挂树）时铸造自增 id
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

    def test_m7_usage_roundtrip(self):
        """M7 补充（X2 收尾）：usage 非 None 时序列化为七字段 + raw dict，还原为 Usage。"""
        from flowing.providers.provider import Usage

        usage = Usage(
            input=10, fresh_input=6, output=5, cache_read=4,
            cache_write=0, reasoning=0, total_tokens=15,
            raw={"prompt_tokens": 10},
        )
        msg = make_message(kind=MessageKind.PROVIDER, usage=usage)
        rec = to_record(msg)
        assert rec["usage"]["input"] == 10
        assert rec["usage"]["raw"]["prompt_tokens"] == 10
        restored = from_record(rec)
        assert restored.usage == usage
        assert restored.usage.raw is not usage.raw  # 深拷贝，不共享


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


class TestMessageQueue:
    """M12–M19：优先级排序 + 同级 FIFO + 阻塞唤醒。"""

    @staticmethod
    def msg(priority=MessagePriority.NORMAL, **kw) -> Message:
        return Message(kind=MessageKind.USER, content=[TextBlock(text="m")],
                       priority=priority, **kw)

    async def test_m12_priority_then_fifo(self):
        """M12：HIGH 插队，同级 FIFO。"""
        q = MessageQueue()
        a = self.msg(MessagePriority.NORMAL)
        b = self.msg(MessagePriority.HIGH)
        c = self.msg(MessagePriority.NORMAL)
        for m in (a, b, c):
            q.enqueue(m)
        assert await q.dequeue() is b
        assert await q.dequeue() is a
        assert await q.dequeue() is c

    async def test_m13_empty_then_enqueue_order(self):
        """M13：空队列入队 NORMAL A、HIGH B → dequeue 先得 B。"""
        q = MessageQueue()
        a = self.msg(MessagePriority.NORMAL)
        b = self.msg(MessagePriority.HIGH)
        q.enqueue(a)
        q.enqueue(b)
        assert await q.dequeue() is b

    async def test_m14_peek(self):
        """M14：peek 不移除；带内队首；空队列返回 None。"""
        q = MessageQueue()
        assert q.peek() is None  # 空队列不阻塞
        a = self.msg(MessagePriority.NORMAL)
        b = self.msg(MessagePriority.HIGH)
        q.enqueue(a)
        q.enqueue(b)
        assert q.peek() is b
        assert len(q) == 2  # 不移除
        assert await q.dequeue() is b
        # 带内队首
        q2 = MessageQueue()
        q2.enqueue(a)
        q2.enqueue(b)
        assert q2.peek(MessagePriority.NORMAL) is a

    async def test_m15_remove(self):
        """M15：remove 在队 id → True 且缩短；不存在 → False 无副作用。"""
        q = MessageQueue()
        a = self.msg()
        q.enqueue(a)
        assert q.remove(a.id) is True
        assert len(q) == 0
        assert q.remove(a.id) is False  # 已出队/不存在
        assert q.remove("nope") is False

    async def test_m16_set_priority_keeps_fifo_seq(self):
        """M16：set_priority 保留原入队序号；已出队 → False。"""
        q = MessageQueue()
        a = self.msg(MessagePriority.HIGH)   # 先入
        b = self.msg(MessagePriority.NORMAL)  # 后入
        q.enqueue(a)
        q.enqueue(b)
        assert q.set_priority(b.id, MessagePriority.HIGH) is True
        assert await q.dequeue() is a  # B 保留原入队序号，晚于 A
        assert await q.dequeue() is b
        assert q.set_priority(b.id, MessagePriority.LOW) is False  # 已出队
        assert len(q) == 0

    async def test_m17_drain_all(self):
        """M17：drain_all 按出队序返回全部并移除；空队列 → [] 不阻塞。"""
        q = MessageQueue()
        assert await q.drain_all() == []
        msgs = [self.msg() for _ in range(3)]
        for m in msgs:
            q.enqueue(m)
        assert await q.drain_all() == msgs
        assert len(q) == 0

    async def test_m18_take_while(self):
        """M18：从队首连续取出至首个不满足者停。"""
        q = MessageQueue()
        a = self.msg(source="s")
        b = self.msg(source="s")
        c = self.msg(source="other")
        for m in (a, b, c):
            q.enqueue(m)
        assert q.take_while(lambda m: m.source == "s") == [a, b]
        assert len(q) == 1
        # 队首即不满足 → [] 队列不变
        assert q.take_while(lambda m: m.source == "s") == []
        assert len(q) == 1
        # 全部满足 → 取空
        assert q.take_while(lambda m: True) == [c]
        assert len(q) == 0

    async def test_m19_dequeue_blocks_until_enqueue(self):
        """M19：空队列 dequeue 挂起；enqueue 后唤醒取得该消息。"""
        import asyncio

        q = MessageQueue()
        got = []

        async def consumer():
            got.append(await q.dequeue())

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)  # 确认挂起
        assert got == []
        m = self.msg()
        q.enqueue(m)
        await asyncio.wait_for(task, timeout=1)
        assert got == [m]

    async def test_wait_not_empty_and_dequeue_nowait(self):
        """spec 附随原语（R-13）：wait_not_empty 不取消息；dequeue_nowait 空 → None。"""
        q = MessageQueue()
        assert q.dequeue_nowait() is None
        m = self.msg()
        q.enqueue(m)
        await q.wait_not_empty()  # 非空立即返回
        assert len(q) == 1  # 不取消息
        assert q.dequeue_nowait() is m
        assert q.dequeue_nowait() is None


class _StubOwner:
    """MessageChain 的属主 stub：只暴露 _messages / _persist_message /
    _persist_tree_record（阶段边界：不接真 Agent，L3 换回 Agent 后
    本组断言即回归基线）。"""

    def __init__(self):
        self._messages: dict[str, Message] = {}
        self.persisted: list[tuple[str, object]] = []

    def _persist_message(self, msg: Message) -> None:
        self.persisted.append(("message", msg))

    def _persist_tree_record(self, record: dict) -> None:
        self.persisted.append(("tree", record))

    def tree_records(self) -> list[dict]:
        return [r for kind, r in self.persisted if kind == "tree"]

    def message_rows(self) -> list[Message]:
        return [m for kind, m in self.persisted if kind == "message"]


def _msg(mid: str, tags: list[str] | None = None, **kw) -> Message:
    return Message(id=mid, kind=MessageKind.USER,
                   content=[TextBlock(text=mid)], tags=tags or [], **kw)


def _linear_chain() -> tuple[_StubOwner, MessageChain]:
    """构造 m1 → m2 → m3 线性链（stub owner）。"""
    owner = _StubOwner()
    for mid, parent in (("m1", None), ("m2", "m1"), ("m3", "m2")):
        m = _msg(mid)
        m.parent_id = parent
        owner._messages[mid] = m
    return owner, MessageChain(owner)


class TestMessageChain:
    def test_m20_insert(self):
        """M20：insert 重挂既有子消息 + 落盘行形态；异常路径。"""
        owner, chain = _linear_chain()
        x = _msg("x")
        assert chain.insert("m1", x) == "x"
        assert x.parent_id == "m1"
        assert owner._messages["m2"].parent_id == "x"
        assert owner._messages["m3"].parent_id == "m2"  # 链不变
        # stub 收到 1 条新消息行 + 1 条 move 变更行
        assert owner.message_rows() == [x]
        assert owner.tree_records() == [{"type": "move", "id": "m2", "parent_id": "x"}]

        # 无子消息 → 等价追加，仅新消息行
        owner2, chain2 = _linear_chain()
        y = _msg("y")
        chain2.insert("m3", y)
        assert y.parent_id == "m3"
        assert owner2.tree_records() == []

        # after_id 不存在 → KeyError；id 冲突 → ValueError
        with pytest.raises(KeyError):
            chain.insert("nope", _msg("z"))
        with pytest.raises(ValueError):
            chain.insert("m1", _msg("m2"))

    def test_m21_branch(self):
        """M21：branch 不动既有子消息；None 开新根；仅新消息行。"""
        owner, chain = _linear_chain()
        x = _msg("x")
        chain.branch("m1", x)
        assert x.parent_id == "m1"
        assert owner._messages["m2"].parent_id == "m1"  # 不变
        assert owner.tree_records() == []  # 仅新消息行
        assert owner.message_rows() == [x]
        # None 开新根：两根并存
        s = _msg("s")
        chain.branch(None, s)
        assert s.parent_id is None
        roots = [m.id for m in owner._messages.values() if m.parent_id is None]
        assert roots == ["m1", "s"]
        with pytest.raises(KeyError):
            chain.branch("nope", _msg("z"))

    def test_m22_remove(self):
        """M22：remove 单节点删，但直接子自动重挂到亲节点（与 insert 对称、
        链连续）；根删除 → 子提升为新根；重复删 → KeyError。"""
        owner, chain = _linear_chain()
        chain.remove("m2")
        assert "m2" not in owner._messages
        assert owner._messages["m3"].parent_id == "m1"   # 直接子重挂到亲节点，无孤儿
        assert owner.tree_records() == [
            {"type": "move", "id": "m3", "parent_id": "m1"},
            {"type": "tombstone", "id": "m2"},
        ]
        with pytest.raises(KeyError):
            chain.remove("m2")
        # 删根：直接子提升为新根（move parent_id=null）
        owner1, chain1 = _linear_chain()
        chain1.remove("m1")
        assert "m1" not in owner1._messages
        assert owner1._messages["m2"].parent_id is None
        assert {"type": "move", "id": "m2", "parent_id": None} in owner1.tree_records()
        # 尾部删除：纯截断，无 move
        owner3, chain3 = _linear_chain()
        chain3.remove("m3")
        assert owner3.tree_records() == [{"type": "tombstone", "id": "m3"}]
        # 整棵删除仍可显式表达：先 reparent 子树到别处、再 remove（可选路线）
        owner2, chain2 = _linear_chain()
        chain2.reparent("m3", to="m1")
        chain2.remove("m2")
        assert owner2._messages["m3"].parent_id == "m1"

    def test_m23_update(self):
        """M23：update 仅替换内容，不动链，stub 收 update 行。"""
        owner, chain = _linear_chain()
        chain.update("m1", [TextBlock(text="新")])
        m1 = owner._messages["m1"]
        assert m1.content == [TextBlock(text="新")]
        assert m1.parent_id is None
        assert owner._messages["m2"].parent_id == "m1"  # 子链不受影响
        (kind, rec), = [p for p in owner.persisted if p[0] == "tree"]
        assert rec["type"] == "update" and rec["id"] == "m1"
        # content 落盘为序列化块（JSON 兼容，供真实 FileRecordStore 消费）
        assert rec["content"] == [{"type": "text", "text": "新"}]
        with pytest.raises(KeyError):
            chain.update("nope", [])

    def test_m24_reparent(self):
        """M24：子树整体移动；成环 → ValueError；id 不存在 → KeyError。"""

        def branched() -> tuple[_StubOwner, MessageChain, Message, Message]:
            owner, chain = _linear_chain()
            m4 = _msg("m4")
            m4.parent_id = "m2"
            m5 = _msg("m5")
            m5.parent_id = "m4"
            owner._messages["m4"] = m4
            owner._messages["m5"] = m5
            return owner, chain, m4, m5

        owner, chain, m4, m5 = branched()
        chain.reparent("m4", to="m1")
        assert m4.parent_id == "m1"
        assert m5.parent_id == "m4"  # 子树不变
        assert {"type": "move", "id": "m4", "parent_id": "m1"} in owner.tree_records()
        # 成环 / 缺 id 检测（在未被手术的新鲜树上）
        _, chain2, _, _ = branched()
        with pytest.raises(ValueError):
            chain2.reparent("m2", to="m4")  # m4 是 m2 的后代 → 成环
        with pytest.raises(ValueError):
            chain2.reparent("m2", to="m2")  # 自身
        with pytest.raises(KeyError):
            chain2.reparent("nope", to="m1")
        with pytest.raises(KeyError):
            chain2.reparent("m1", to="nope")

    def test_m25_remove_by_tags(self):
        """M25：消息级 / block 级 tags 命中即删；空集 → 0。"""
        owner = _StubOwner()
        chain = MessageChain(owner)
        r1 = _msg("r1", tags=["reminder"])
        r2 = _msg("r2", tags=["reminder"])
        keep = _msg("keep")
        for m in (r1, r2, keep):
            chain.branch(None, m)
        assert chain.remove_by_tags({"reminder"}) == 2
        assert list(owner._messages) == ["keep"]
        assert chain.remove_by_tags(set()) == 0

        # block 级 tags（X3：getattr 兜底，ContentBlock 基类无 tags 字段）
        owner2 = _StubOwner()
        chain2 = MessageChain(owner2)
        blk = TextBlock(text="x")
        blk.tags = ["reminder"]  # type: ignore[attr-defined]  # 动态附加，X3 场景
        m = Message(id="b1", kind=MessageKind.SYSTEM, content=[blk])
        chain2.branch(None, m)
        assert chain2.remove_by_tags({"reminder"}) == 1

        # 链中段命中（0904）：删除带子消息的节点 → 直接子重挂到亲节点（无孤儿）
        owner3 = _StubOwner()
        chain3 = MessageChain(owner3)
        a = _msg("a")
        chain3.branch(None, a)
        b = _msg("b", tags=["reminder"])
        chain3.branch("a", b)
        c = _msg("c")
        chain3.branch("b", c)
        assert chain3.remove_by_tags({"reminder"}) == 1
        assert "b" not in owner3._messages
        assert owner3._messages["c"].parent_id == "a"   # 幸存子重挂到亲节点
        assert {"type": "move", "id": "c", "parent_id": "a"} in owner3.tree_records()

    def test_m26_get(self):
        """M26：get 按 id 反查（只读、内存读）；不存在 / 已删除 → KeyError。"""
        owner, chain = _linear_chain()
        m2 = chain.get("m2")
        assert m2 is owner._messages["m2"]   # 树中实况对象，非副本
        with pytest.raises(KeyError):
            chain.get("nope")
        chain.remove("m2")
        with pytest.raises(KeyError):
            chain.get("m2")   # 已 tombstone 的消息按不存在处理

    def test_m27_walk(self):
        """M27：walk 从指定消息沿 parent_id 上溯到根（逆时间序）；None /
        不存在起点 / 孤儿断点 → 容忍终止；不触碰持久化。"""
        owner, chain = _linear_chain()
        # m3 → m2 → m1（逆时间序，含起点本身）
        assert [m.id for m in chain.walk("m3")] == ["m3", "m2", "m1"]
        # 时间序由调用方自行反转
        assert [m.id for m in chain.walk("m3")][::-1] == ["m1", "m2", "m3"]
        # 中间节点起步 → 到根为止
        assert [m.id for m in chain.walk("m2")] == ["m2", "m1"]
        # None（空树游标）与不存在的起点 → 空迭代
        assert list(chain.walk(None)) == []
        assert list(chain.walk("nope")) == []
        # 孤儿断点容忍：m2 被删且 m3 未先 reparent 是假不出的（remove 会
        # 重挂），手工制造断点（parent_id 指向不存在节点）→ 上溯到断点即终止
        orphan = _msg("orphan")
        orphan.parent_id = "ghost"
        owner._messages["orphan"] = orphan
        assert [m.id for m in chain.walk("orphan")] == ["orphan"]
        # 只读：全程不产生任何落盘行
        assert owner.persisted == []
