"""阶段 4 comm 总线单元测试（W29–W30）：测试清单 T61–T73。

总线是纯内存机制对象，本文件直接构造 ``Communication()`` 驱动（不走插件
安装——插件面测试在 test_comm_plugin.py）；异步链路用极小 timeout 与
``asyncio.Event`` 驱动，不依赖真实等待。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from flowing.errors import (
    DuplicateEndpointError,
    SignalDeliveryError,
    SignalTimeoutError,
)
from flowing.plugins.comm import Communication, EventEnvelope, SignalEnvelope


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# T61：重复注册 → DuplicateEndpointError，原映射不变
# ---------------------------------------------------------------------------


def test_t61_duplicate_endpoint_rejected():
    bus = Communication()
    h1 = lambda env: "h1"   # noqa: E731
    h2 = lambda env: "h2"   # noqa: E731
    bus.register_endpoint("a", h1)
    with pytest.raises(DuplicateEndpointError) as exc_info:
        bus.register_endpoint("a", h2)
    assert exc_info.value.endpoint_id == "a"
    assert bus._endpoints["a"] is h1   # 原映射不变


# ---------------------------------------------------------------------------
# T62 / T63：send 即发即忘与同步可感知的投递失败
# ---------------------------------------------------------------------------


def test_t62_send_fire_and_forget_envelope_filled():
    bus = Communication()
    received: list[SignalEnvelope] = []
    bus.register_endpoint("a", received.append)
    before = _utcnow()
    assert bus.send(sender="s", target="a", type="t", payload={"x": 1}) is None
    (env,) = received
    assert env.sender == "s" and env.type == "t" and env.payload == {"x": 1}
    assert env.correlation_id is None and env.reply_to is None
    assert env.created_at.tzinfo is None          # naive UTC
    assert before <= env.created_at <= _utcnow()  # 总线构造时填充


def test_t63_send_to_missing_target_raises():
    bus = Communication()
    with pytest.raises(SignalDeliveryError) as exc_info:
        bus.send(sender="a", target="ghost", type="t", payload={})
    assert exc_info.value.target == "ghost"


# ---------------------------------------------------------------------------
# T64–T67：request-reply 全链
# ---------------------------------------------------------------------------


async def test_t64_request_reply_roundtrip():
    """T64：对端 handler 调 handle.reply → await request 返回 payload；
    接收侧 on_signal 未收到 '_reply' 类型信封（保留类型不 dispatch）。"""
    bus = Communication()
    seen_by_b: list[SignalEnvelope] = []

    def _b_on_signal(env: SignalEnvelope):
        seen_by_b.append(env)
        h_b.reply(env, {"ok": True})   # 晚期绑定：回调只在 create_handle 返回后被调用

    h_b = bus.create_handle("b", on_signal=_b_on_signal)
    h_a = bus.create_handle("a")
    result = await h_a.request("b", "permission_request", {"tool": "x"}, timeout=1.0)
    assert result == {"ok": True}
    # b 侧只见过 request 信封一次，从未收到 '_reply'
    assert len(seen_by_b) == 1
    assert seen_by_b[0].type == "permission_request"
    assert seen_by_b[0].correlation_id is not None
    assert seen_by_b[0].reply_to == "a"
    # 总线级 request 入口同语义（reply_handler 显式传入）
    seen_by_b.clear()
    result2 = await bus.request("a", "b", "q", {"n": 2}, reply_handler=h_a, timeout=1.0)
    assert result2 == {"ok": True}
    assert all(env.type != "_reply" for env in seen_by_b)


async def test_t65_request_timeout_and_late_reply_discarded():
    """T65：对端永不回复 → 约 timeout 后抛 SignalTimeoutError；
    超时后迟到回复被静默丢弃（无异常、不完成任何 future）。"""
    bus = Communication()
    captured: list[SignalEnvelope] = []
    bus.create_handle("b", on_signal=captured.append)   # 永不回复
    h_a = bus.create_handle("a")
    start = asyncio.get_running_loop().time()
    with pytest.raises(SignalTimeoutError) as exc_info:
        await h_a.request("b", "q", {}, timeout=0.05)
    assert asyncio.get_running_loop().time() - start >= 0.05
    assert exc_info.value.target == "b"
    assert h_a._pending_replies == {}   # 超时后 future 已摘除（无泄漏）
    # 迟到回复：静默丢弃 + warnings.warn（可观测而非无声）
    late = captured[0]
    with pytest.warns(UserWarning, match="no matching pending future"):
        bus.reply("b", late.reply_to, late.correlation_id, {"late": True})


async def test_t66_request_cancelled_on_destroy():
    """T66：request 等待中 handle.destroy() → await 侧收到 CancelledError。"""
    bus = Communication()
    bus.create_handle("b")   # 永不回复
    h_a = bus.create_handle("a")
    pending = asyncio.create_task(h_a.request("b", "q", {}, timeout=None))
    await asyncio.sleep(0)   # 让 request 推进到等待点
    h_a.destroy()
    with pytest.raises(asyncio.CancelledError):
        await pending


async def test_t67_request_to_missing_target_raises_at_send():
    """T67：request 的 target 不存在 → 发送阶段即抛 SignalDeliveryError
    （不进入等待，且不留 pending future 泄漏）。"""
    bus = Communication()
    h_a = bus.create_handle("a")
    with pytest.raises(SignalDeliveryError):
        await h_a.request("ghost", "q", {}, timeout=10.0)
    assert h_a._pending_replies == {}


# ---------------------------------------------------------------------------
# T68–T71：publish / subscribe / reply 的容错与清理语义
# ---------------------------------------------------------------------------


async def test_t68_publish_fault_tolerant_and_fire_and_forget():
    """T68：订阅者 A 抛异常、B 正常 → B 仍被调用且 publish 不抛；
    awaitable 回调转后台 Task 不阻塞发布者。"""
    bus = Communication()
    done = asyncio.Event()

    def bad(env: EventEnvelope):
        raise RuntimeError("boom")

    async def slow(env: EventEnvelope):
        await asyncio.sleep(0.02)
        done.set()

    bus.subscribe("A", "x", bad)
    bus.subscribe("B", "x", slow)
    assert bus.publish("x", {}) is None        # publish 本身不抛
    assert not done.is_set()                   # fire-and-forget：返回时不等回调完成
    await asyncio.wait_for(done.wait(), 1.0)   # 后台任务最终完成


def test_t69_publish_no_subscriber_noop_and_resubscribe_overrides():
    """T69：无订阅者 topic 的 publish 是空操作；同一订阅者重复订阅同
    topic → 覆盖旧回调（至多一条）。"""
    bus = Communication()
    assert bus.publish("nobody", {"k": 1}) is None   # 无任何副作用
    assert bus._subscriptions == {}

    calls: list[str] = []
    bus.subscribe("s", "y", lambda env: calls.append("old"))
    bus.subscribe("s", "y", lambda env: calls.append("new"))
    bus.publish("y", {})
    assert calls == ["new"]


def test_t70_cleanup_semantics_differ_deliberately():
    """T70：unsubscribe / unsubscribe_all 对不存在订阅 → 幂等空操作；
    unregister_endpoint 对不存在端点 → 自然 KeyError（有意不同）。"""
    bus = Communication()
    bus.unsubscribe("ghost", "topic")       # 不抛
    bus.unsubscribe_all("ghost")            # 不抛
    with pytest.raises(KeyError):
        bus.unregister_endpoint("ghost")


async def test_t71_reply_without_pending_future_or_target_discarded_with_warn():
    """T71：correlation_id 无匹配 pending future 或 target 已注销 →
    静默丢弃 + warnings.warn；不产生任何钩子/dispatch 事件。"""
    bus = Communication()
    seen: list[SignalEnvelope] = []
    h_a = bus.create_handle("a", on_signal=seen.append)
    # 情形一：无匹配 pending future（接收侧句柄丢弃）
    with pytest.warns(UserWarning, match="no matching pending future"):
        bus.reply("b", "a", "no-such-correlation", {})
    # 情形二：target 端点已注销（总线侧丢弃）
    h_a.destroy()
    with pytest.warns(UserWarning, match="target endpoint .* does not exist"):
        bus.reply("b", "a", "no-such-correlation", {})
    assert seen == []   # '_reply' 信封从未进入接收回调


async def test_t72_handle_destroy_idempotent_full_cleanup():
    """T72：句柄有一条订阅与一个 pending request → 连续两次 destroy()：
    第一次后订阅消失、端点注销、request 的 await 收到 CancelledError；
    第二次无任何效果不抛。"""
    bus = Communication()
    bus.create_handle("b")   # 永不回复
    h = bus.create_handle("x")
    h.subscribe("topic", lambda env: None)
    pending = asyncio.create_task(h.request("b", "q", {}, timeout=None))
    await asyncio.sleep(0)

    h.destroy()
    assert "x" not in bus._endpoints        # 端点注销
    assert bus._subscriptions == {}         # 订阅消失
    assert h._pending_replies == {}         # futures 清表
    with pytest.raises(asyncio.CancelledError):
        await pending

    h.destroy()   # 幂等：无任何效果不抛
    assert "x" not in bus._endpoints


def test_t73_handle_publish_overrides_publisher_identity():
    """T73：event 含 "publisher" 键 → 被覆盖为 handle.endpoint_id
    （句柄路径不允许伪造发布者）。"""
    bus = Communication()
    seen: list[EventEnvelope] = []
    bus.subscribe("sub-1", "topic", seen.append)
    h = bus.create_handle("a")
    h.publish("topic", {"publisher": "forged", "x": 1})
    (env,) = seen
    assert env.publisher == "a"
    assert env.event["publisher"] == "a"    # event 字典内的键同被覆盖
    assert env.event["x"] == 1
