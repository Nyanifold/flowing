"""workflow 基类测试：测试清单 T79–T83、T91、T92。

全部用真 Runtime（``make_runtime``）驱动——create_agent / archive_agent /
_nodes / provide 链都是真管线。workflow 子类在本文件就地定义（不经
resolve_workflow——loader 测试在 test_workflow_loader.py）。
"""

from __future__ import annotations

import pytest

from flowing.errors import Intercepted, ToolNotFoundError
from flowing.plugins.workflow import Workflow
from flowing.tool import ToolResult

from workflow_support import (
    EchoTool,
    RecorderAgent,
    add_fake_provider,
    make_runtime,
)


class DemoWorkflow(Workflow):
    """最小 workflow 子类：run 原样回显参数（本文件不经 run 驱动时无害）。"""

    async def run(self, prompt: str | None = None, **kwargs) -> dict:
        return {"prompt": prompt, **kwargs}


async def test_t79_provide_inject_chain_and_parenting(tmp_path):
    """T79：wf.provide(k, v) 后 create_agent → child.inject(k) == v；
    child 的子 Agent 同样可见；child._parent_id == wf.node_id；
    child.node_id in wf._agents。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    caller = await runtime.create_agent("test-agent")
    wf = DemoWorkflow(caller=caller, runtime=runtime)
    try:
        wf.provide("wf-key", "wf-value")
        child = await wf.create_agent("test-agent")
        assert child.inject("wf-key") == "wf-value"
        assert child._parent_id == wf.node_id
        assert child.node_id in wf._agents
        assert runtime._nodes[wf.node_id] is wf   # 构造即注册
        grandchild = await runtime.create_agent("test-agent",
                                                parent_id=child.node_id)
        assert grandchild.inject("wf-key") == "wf-value"   # 后代同样可见
    finally:
        await wf.destroy()
        await runtime.shutdown()


async def test_t80_destroy_archives_children_idempotent(tmp_path):
    """T80：await wf.destroy() → child 被归档（_nodes/_agent_pool 移除）
    且 session 目录保留；重复 destroy() 为空操作。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    wf = DemoWorkflow(caller=None, runtime=runtime)
    child = await wf.create_agent("test-agent")
    session_dir = child._session_dir

    await wf.destroy()
    assert child.node_id not in runtime._nodes
    assert child.node_id not in runtime._agent_pool
    assert session_dir.exists()          # 归档留档：文件保留
    assert wf.node_id not in runtime._nodes   # 节点已注销
    assert wf._agents == {}

    await wf.destroy()   # 幂等：无任何效果不抛
    await runtime.shutdown()


async def test_t81_no_invoke_subagent(tmp_path):
    """T81：Workflow 无 invoke_subagent 入口（裁决定稿）。"""
    runtime = make_runtime(tmp_path)
    wf = DemoWorkflow(caller=None, runtime=runtime)
    try:
        assert not hasattr(wf, "invoke_subagent")
    finally:
        await wf.destroy()
        await runtime.shutdown()


async def test_t82_tool_call_independent_hooks_caller_isolated(tmp_path):
    """T82：caller Agent 的 before_tool_call 挂总是 Intercepted 的 handler
    → wf.tool_call 工具正常执行（Agent 钩子未触发），wf 自己的
    before_tool_call handler 被调用。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.register_tool(EchoTool())
    caller = await runtime.create_agent("test-agent")
    wf = DemoWorkflow(caller=caller, runtime=runtime)
    try:
        denied_calls = []

        def _deny(agent, call):
            denied_calls.append(call)
            raise Intercepted("denied")

        caller.hooks.before_tool_call(_deny, by="test")

        wf_seen = []

        def _wf_audit(w, call):
            wf_seen.append(call)
            return call

        wf.hooks.before_tool_call(_wf_audit, by="test")

        result = await wf.tool_call("echo-tool", text="hello")
        assert result.status == "completed"          # Agent 钩子未拦截
        assert result.output == {"echo": "hello"}    # 工具正常执行
        assert denied_calls == []                    # caller 钩子从未触发
        assert len(wf_seen) == 1                     # wf 自己的 handler 被调用
        assert wf_seen[0].id.startswith("workflow-")  # S-41② 合成 id
    finally:
        await wf.destroy()
        await runtime.shutdown()


async def test_t83_tool_call_not_found_shortcut_intercept(tmp_path):
    """T83：ghost → ToolNotFoundError；handler 改写 args 生效；shortcut
    短路产物经收尾 normalize_output 归一；Intercepted → blocked。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.register_tool(EchoTool())
    try:
        # 未注册工具 → ToolNotFoundError
        wf = DemoWorkflow(caller=None, runtime=runtime)
        with pytest.raises(ToolNotFoundError):
            await wf.tool_call("ghost")

        # handler 改写 args → 工具收到改写后参数
        wf_rewrite = DemoWorkflow(caller=None, runtime=runtime)

        def _rewrite(w, call):
            call.args["text"] = "rewritten"
            return call

        wf_rewrite.hooks.before_tool_call(_rewrite, by="test")
        result = await wf_rewrite.tool_call("echo-tool", text="original")
        assert result.output == {"echo": "rewritten"}

        # shortcut 短路：工具不执行，after_tool_call 照常触发，收尾归一兜底
        wf_shortcut = DemoWorkflow(caller=None, runtime=runtime)
        after_seen = []

        def _shortcut(w, call):
            call.shortcut = ToolResult(status="completed", output=("a", "b"))
            return call

        def _after(w, result):
            after_seen.append(result)
            return result

        wf_shortcut.hooks.before_tool_call(_shortcut, by="test")
        wf_shortcut.hooks.after_tool_call(_after, by="test")
        result = await wf_shortcut.tool_call("echo-tool", text="never-executed")
        assert result.status == "completed"
        assert result.output == ["a", "b"]   # tuple 原料 → 收尾归一为 list
        assert len(after_seen) == 1          # shortcut 路径 after 照常触发

        # Intercepted 硬阻断 → blocked，after_tool_call 不触发
        wf_blocked = DemoWorkflow(caller=None, runtime=runtime)
        after_blocked = []

        def _intercept(w, call):
            raise Intercepted("wf 侧拒绝")

        wf_blocked.hooks.before_tool_call(_intercept, by="test")
        wf_blocked.hooks.after_tool_call(
            lambda w, r: after_blocked.append(r) or r, by="test")
        result = await wf_blocked.tool_call("echo-tool", text="x")
        assert result.status == "blocked"
        assert result.output == "wf 侧拒绝"   # reason 作为 output（LLM 可见）
        assert after_blocked == []
    finally:
        for w in (wf, wf_rewrite, wf_shortcut, wf_blocked):
            await w.destroy()
        await runtime.shutdown()


async def test_t91_root_workflow_parenting_and_caller_none(tmp_path):
    """T91：根节点 workflow（caller=None）：_parent_id 指向 Runtime，
    inject 上溯一步即达 Runtime 层；self.caller.* 属编程错误
    （AttributeError on None，框架不检查）。"""
    runtime = make_runtime(tmp_path)
    runtime.provide("root-key", "root-value")
    wf = DemoWorkflow(caller=None, runtime=runtime)
    try:
        assert wf._parent_id == runtime.node_id
        assert wf.inject("root-key") == "root-value"
        with pytest.raises(AttributeError):
            wf.caller.query("x")   # None.query → AttributeError，框架不预设检查
    finally:
        await wf.destroy()
        await runtime.shutdown()


async def test_t92_create_agent_no_dedup_only_create_hooks(tmp_path):
    """T92：同名 agent_type 重复 create_agent 产生多个独立实例（无去重）；
    创建路径只触发 before_create/after_create，不触发
    on_subagent_invoke/on_subagent_returns。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.register_agent_type(RecorderAgent, name="recorder")
    RecorderAgent.fired = []
    wf = DemoWorkflow(caller=None, runtime=runtime)
    try:
        c1 = await wf.create_agent("recorder")
        c2 = await wf.create_agent("recorder")
        assert c1 is not c2 and c1.node_id != c2.node_id   # 独立实例，无去重
        assert RecorderAgent.fired.count("before_create") == 2
        assert RecorderAgent.fired.count("after_create") == 2
        assert "on_subagent_invoke" not in RecorderAgent.fired
        assert "on_subagent_returns" not in RecorderAgent.fired
    finally:
        await wf.destroy()
        await runtime.shutdown()
