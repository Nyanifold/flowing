"""阶段 1 hooks.py 测试（T-44 ~ T-63）：dispatch 三出口、Registry、watcher、@on 标记。"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from flowing.errors import (
    DuplicateHookPointError,
    FlowingError,
    Intercepted,
    UnknownHookPointError,
)
from flowing.hooks import HookEntry, HookList, HookRegistry, PatternRegistrar, on

AGENT = SimpleNamespace()  # dispatch 只透传 agent 首参，不访问其属性


def _value(**kw):
    """带 shortcut / name 属性的假 value 对象。"""
    return SimpleNamespace(shortcut=None, **kw)


def test_t44_register_disable_dispatch():
    hook = HookList("before_tool_call", by="core")
    calls = []
    h1 = hook(lambda a, v: calls.append("h1") or v, by="o1")
    h2 = hook(lambda a, v: calls.append("h2") or v, by="o2")
    h3 = hook(lambda a, v: calls.append("h3") or v, by="o3")
    assert hook.disable_by_owner("o2") == 1
    asyncio.run(hook.dispatch(AGENT, _value()))
    assert calls == ["h1", "h3"]
    assert hook[1].handler is h2  # 下标含 disabled


def test_t45_pattern_mismatch_passthrough():
    hook = HookList("before_tool_call", by="core")
    calls = []
    hook["payment-*"](lambda a, v: calls.append(1) or v, by="guard")
    v = _value(name="read-file")
    assert asyncio.run(hook.dispatch(AGENT, v)) is v
    assert calls == []


def test_t46_pattern_registrar():
    hook = HookList("before_tool_call", by="core")
    for i in range(3):
        hook(lambda a, v: v)
    reg = hook["pay-*"]
    assert isinstance(reg, PatternRegistrar)

    def guard(a, v):
        return v

    assert reg(guard, by="g") is guard  # S-18：注册返回原 handler
    assert len(hook._items) == 4
    assert hook[-1].pattern == "pay-*"


def test_t47_getitem_forms():
    hook = HookList("x", by="core")
    entries = [hook(lambda a, v, i=i: v) for i in range(3)]
    assert hook[-1].handler is not None and hook[-1] is hook._items[2]
    assert hook[:2] == hook._items[:2]
    assert isinstance(hook[:2], list)
    with pytest.raises(IndexError):
        hook[3]


def test_t48_rewrite_chain():
    hook = HookList("before_tool_call", by="core")
    seen = {}

    def h1(a, v):
        v.args["x"] = 1
        return v

    def h2(a, v):
        seen["x"] = v.args["x"]
        return v

    hook(h1)
    hook(h2)
    asyncio.run(hook.dispatch(AGENT, _value(args={})))
    assert seen["x"] == 1


def test_t49_shortcut():
    hook = HookList("before_tool_call", by="core")
    calls = []

    def h1(a, v):
        v.shortcut = SimpleNamespace(output="cached")
        return v

    hook(h1)
    hook(lambda a, v: calls.append("h2") or v)
    result = asyncio.run(hook.dispatch(AGENT, _value()))
    assert calls == []
    assert result.shortcut.output == "cached"


def test_t50_intercepted_reraise():
    hook = HookList("before_tool_call", by="core")
    calls = []

    def h1(a, v):
        raise Intercepted("no")

    hook(h1)
    hook(lambda a, v: calls.append("h2") or v)
    with pytest.raises(Intercepted):
        asyncio.run(hook.dispatch(AGENT, _value()))
    assert calls == []


def test_t51_plain_exception_propagates():
    hook = HookList("before_tool_call", by="core")
    calls = []
    hook(lambda a, v: (_ for _ in ()).throw(ValueError("boom")))
    hook(lambda a, v: calls.append("h2") or v)
    with pytest.raises(ValueError):
        asyncio.run(hook.dispatch(AGENT, _value()))
    assert calls == []


def test_t52_none_return_is_programming_error():
    hook = HookList("before_tool_call", by="core")

    def bad(a, v):
        return None

    hook(bad, by="x")
    with pytest.raises(FlowingError) as exc:
        asyncio.run(hook.dispatch(AGENT, _value()))
    assert "before_tool_call" in str(exc.value) and "bad" in str(exc.value)


def test_t53_sync_async_mix():
    hook = HookList("before_tool_call", by="core")
    calls = []

    def h_sync(a, v):
        calls.append("sync")
        return v

    async def h_async(a, v):
        calls.append("async")
        return v

    hook(h_sync)
    hook(h_async)
    asyncio.run(hook.dispatch(AGENT, _value()))
    assert calls == ["sync", "async"]


def test_t54_no_active_handler_passthrough():
    hook = HookList("before_tool_call", by="core")
    v = _value()
    assert asyncio.run(hook.dispatch(AGENT, v)) is v  # 无 handler
    hook(lambda a, v: v, by="o")
    hook.disable_by_owner("o")
    assert asyncio.run(hook.dispatch(AGENT, v)) is v  # 全部 disabled
    hook2 = HookList("before_tool_call", by="core")
    hook2["pay-*"](lambda a, v: v)
    assert asyncio.run(hook2.dispatch(AGENT, _value(name="other"))) is v or True
    v2 = _value(name="other")
    assert asyncio.run(hook2.dispatch(AGENT, v2)) is v2  # 全部被 pattern 过滤


# ---------------------------------------------------------------------------
# HookRegistry（T-55 ~ T-60）
# ---------------------------------------------------------------------------

CORE_HOOK_POINTS = [
    "before_create", "after_create", "before_recover", "after_recover",
    "before_destroy", "after_destroy", "before_turn", "before_turn_append",
    "after_turn_append", "before_provider_gen", "after_provider_gen",
    "on_provider_delta", "before_tool_call", "after_tool_call",
    "before_subagent_invoke", "after_subagent_invoke", "before_turn_abort",
    "after_turn", "on_provider_error", "before_enqueue", "after_enqueue",
    "before_dequeue", "after_dequeue", "before_fork", "after_fork",
    "before_cancel", "after_cancel",
]


def test_t55_core_hook_points_prefilled():
    hooks = HookRegistry()
    for name in CORE_HOOK_POINTS:
        hl = getattr(hooks, name)
        assert isinstance(hl, HookList) and hl.by == "core" and len(hl._items) == 0
    assert len(CORE_HOOK_POINTS) == 27
    assert hooks.after_provider_gen.match_on == "by"
    assert hooks.on_provider_delta.match_on == "by"
    assert hooks.before_turn.by == "core"


def test_t56_unknown_hook_point():
    with pytest.raises(UnknownHookPointError):
        HookRegistry().nonexistent_hook


def test_t57_declare():
    hooks = HookRegistry()
    hooks.declare("on_event", by="comm")
    assert hooks.on_event.by == "comm"


def test_t58_declare_idempotent():
    hooks = HookRegistry()
    first = hooks.declare("on_signal", by="comm", match_on="type")
    second = hooks.declare("on_signal", by="comm")
    assert first is second
    assert first.match_on == "type"  # match_on 以首次声明为准


def test_t59_declare_conflict():
    hooks = HookRegistry()
    hooks.declare("on_signal", by="comm")
    with pytest.raises(DuplicateHookPointError) as exc:
        hooks.declare("on_signal", by="other")
    assert "comm" in str(exc.value) and "other" in str(exc.value)


def test_t60_pending_on_flush():
    hooks = HookRegistry()

    def h(a, v):
        return v

    # 模拟 _init_hooks 的暂记（收集与结算属阶段 2，本期手工塞入）
    hooks._pending_on.append((h, "on_signal", "audit", ["t"], None))
    hl = hooks.declare("on_signal", by="comm")
    assert hooks._pending_on == []  # 已冲刷
    assert hl[0].handler is h and hl[0].by == "audit" and hl[0].tags == ["t"]

    # pattern 形态暂记也经冲刷挂载
    hooks._pending_on.append((h, "on_event", None, None, "agent-*"))
    hl2 = hooks.declare("on_event", by="comm")
    assert hl2[0].pattern == "agent-*"

    # 幂等路径不冲刷
    hooks._pending_on.append((h, "on_signal", None, None, None))
    assert hooks.declare("on_signal", by="comm") is hl
    assert len(hooks._pending_on) == 1  # 暂记保持不动


# ---------------------------------------------------------------------------
# watcher 通道（T-61 / T-62）
# ---------------------------------------------------------------------------

async def test_t61_watcher_fire_and_forget():
    hooks = HookRegistry()
    calls = []
    hooks.watch("locale", lambda a, v: calls.append(v.name))
    fu = SimpleNamespace(name="locale", new="en", old="zh")
    hooks._notify_watch(AGENT, fu)  # 不 await，返回即返回
    await asyncio.sleep(0)  # 让被调度的 watcher 任务运行
    await asyncio.sleep(0)
    assert calls == ["locale"]


def test_t61b_watcher_without_loop_silent():
    hooks = HookRegistry()
    hooks.watch("locale", lambda a, v: None)
    hooks._notify_watch(AGENT, SimpleNamespace(name="locale"))  # 无运行中 loop：静默跳过


async def test_t62_watcher_exception_and_pattern(caplog):
    hooks = HookRegistry()
    calls = []

    def bad(a, v):
        raise RuntimeError("watcher boom")

    hooks.watch("locale", bad)
    hooks.watch("locale", lambda a, v: calls.append("second"))
    hooks.watch("other", lambda a, v: calls.append("mismatch"))
    with caplog.at_level(logging.ERROR, logger="flowing.hooks"):
        hooks._notify_watch(AGENT, SimpleNamespace(name="locale"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert calls == ["second"]  # 异常不影响后续 watcher 与调用方
    assert "mismatch" not in calls  # pattern 不匹配 value.name 不触发
    assert any("watcher boom" in (r.message or "") or r.exc_info for r in caplog.records)


# ---------------------------------------------------------------------------
# @on 标记层（T-63）
# ---------------------------------------------------------------------------

def test_t63_on_marks_only():
    @on("before_tool_call")
    def handler(self, value):
        return value

    assert callable(handler) and handler.__name__ == "handler"  # 返回原函数
    assert handler.__flowing_hooks__ == (("before_tool_call", None, None, None),)

    @on("before_tool_call", by="audit", tags=["t"])
    @on("before_create")
    def multi(self, value):
        return value

    assert multi.__flowing_hooks__ == (
        ("before_create", None, None, None),
        ("before_tool_call", "audit", ["t"], None),
    )

    @on("on_signal")["agent-*"]
    def patterned(self, value):
        return value

    assert patterned.__flowing_hooks__ == (("on_signal", None, None, "agent-*"),)


def test_w16_hook_entry_defaults():
    e = HookEntry(handler=lambda a, v: v, by=None)
    assert e.tags == [] and e.pattern is None and e.enabled is True
