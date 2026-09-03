"""阶段 3 builtins 核心内置与标准子智能体测试（W31–W32）：测试清单 72–76。

FinishTool 动态 schema 与置位收尾（72）、SubagentInvokeTool 同步/异步
两路径（73/74）、ExploreAgent 只读工具集（75）、register_builtins 注册面
（76）。真 Agent 侧由 phase2 conftest 的 HarnessRuntime 迷你管线 /
``make_runtime`` 真 Runtime 驱动（按路径载入，避免顶级 conftest 遮蔽）。
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from flowing.agent import Agent
from flowing.builtins import ExploreAgent, FinishTool
from flowing.builtins.tools import SubagentInvokeTool
from flowing.message import MessageKind, StructBlock, TextBlock
from flowing.parsable import Parsable

_PHASE2_CONFTEST = Path(__file__).parent.parent / "phase2" / "conftest.py"
_spec = importlib.util.spec_from_file_location("phase2_conftest", _PHASE2_CONFTEST)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
HarnessRuntime = _mod.HarnessRuntime
SimpleAgent = _mod.SimpleAgent
make_runtime = _mod.make_runtime
add_fake_provider = _mod.add_fake_provider
script_provider = _mod.script_provider
text_response = _mod.text_response
tool_call_response = _mod.tool_call_response


class WorkerAgent(Agent):
    """测试 73/74 的子 Agent 类型（system_prompt 用于 provider 侧判别身份）。"""

    system_prompt = Parsable("你是工人助手。")

    async def setup(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _sys_text(context) -> str:
    """Context 的 system prompt 段拼合（PromptSegment.content 已是 str）。"""
    return "".join(seg.content for seg in context.system_prompt)


@pytest.fixture
async def runtime(tmp_path):
    rt = HarnessRuntime(tmp_path)
    rt.register_agent_type("test-agent", SimpleAgent)
    rt.register_agent_type("worker", WorkerAgent)
    yield rt
    for node_id, node in list(rt._nodes.items()):
        if node is rt:
            continue
        try:
            await node.destroy()
        except Exception:
            pass


@pytest.fixture
def provider(runtime):
    return add_fake_provider(runtime)


async def _drain_until(predicate, timeout: float = 5.0):
    """轮询等待条件成立（watcher/后台任务投递需若干事件循环 tick）。"""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待超时：条件未成立")


# ---------------------------------------------------------------------------
# 清单 72：FinishTool 动态 schema 与置位收尾
# ---------------------------------------------------------------------------

_OUTPUT_DECL = {
    "score": {"type": "integer", "minimum": 0, "maximum": 100,
              "description": "代码质量评分（0-100）"},
    "pass": {"type": "boolean", "description": "是否通过审查"},
    "issues": {"type": "array", "description": "发现的问题列表"},
}


async def test_t72_finish_dynamic_schema(runtime, provider):
    """72：entry 的 ``output:`` 声明展开为 finish 动态参数；注册表骨架定义不变。"""
    agent = await runtime.create_agent("test-agent")
    entry = agent.add_tool("finish", body={"output": _OUTPUT_DECL})
    definition = entry.llm_definition(runtime, agent)
    # LLM 可见产物：summary + 三个动态键
    assert set(definition.params_schema) == {"summary", "score", "pass", "issues"}
    assert definition.params_schema["score"]["maximum"] == 100
    # 注册表骨架定义仍只有 summary（clone 语义，不改全局注册表）
    skeleton = runtime.tool_registry.get("finish").definition
    assert set(skeleton.params_schema) == {"summary"}


async def test_t72_finish_execute_sets_finish_output(runtime):
    """72：execute 置位 ``caller.current_turn.finish_output`` 且返回
    ``{"summary": ..., **动态字段}``（直调 execute 层验证置位语义）。"""
    import types

    caller = types.SimpleNamespace(
        current_turn=types.SimpleNamespace(finish_output=None))
    tool = FinishTool()
    result = await tool({"summary": "s", "score": 90, "pass": True, "issues": []},
                        caller=caller)
    assert result.status == "completed"
    assert result.output == {"summary": "s", "score": 90, "pass": True, "issues": []}
    assert caller.current_turn.finish_output == result.output   # 置位同一载荷


async def test_t72_finish_full_turn(runtime, provider):
    """72（回合级）：finish 工具调用置位即请求本回合自然结束，载荷进
    ``last_result``（与阶段 2 T71 的置位转移契约一致）。"""
    agent = await runtime.create_agent("test-agent")
    agent.add_tool("finish", body={"output": _OUTPUT_DECL})
    step1, _ = tool_call_response(
        ("finish", {"summary": "审查完成", "score": 88, "pass": True, "issues": []}))
    script_provider(provider, step1)
    result = await agent.query("交卷")
    assert result.status == "completed"
    assert agent.last_result == {"summary": "审查完成", "score": 88,
                                 "pass": True, "issues": []}


# ---------------------------------------------------------------------------
# 清单 73：subagent-invoke 同步路径
# ---------------------------------------------------------------------------

async def test_t73_subagent_invoke_sync(runtime, provider):
    """73：同步路径返回 dict 平铺五字段，**不**另发 SUBAGENT 消息。"""
    agent = await runtime.create_agent("test-agent")
    agent.add_agent("worker")
    agent.add_tool("subagent-invoke")
    step1, calls = tool_call_response(
        ("subagent-invoke", {"agent_type": "worker", "prompt": "干活",
                             "name": "w1"}))
    # 调用序：亲节点（工具调用）→ 子（文本交卷）→ 亲节点（收尾文本）
    script_provider(provider, step1, text_response("子结果文本"),
                    text_response("亲代收尾"))
    result = await agent.query("唤起子代理")
    assert result.status == "completed"
    tool_msg = next(agent._messages[mid] for mid in result.turn.message_ids
                    if agent._messages[mid].kind is MessageKind.TOOL)
    block = next(b for b in tool_msg.content if isinstance(b, StructBlock))
    payload = block.data
    # SubagentResult 全字段平铺
    assert payload["status"] == "completed"
    assert payload["name_alias"] == "w1"
    assert payload["subagent_id"].startswith("agent-")
    assert payload["result"] == "子结果文本"
    assert payload["subagent_status"] == "completed"
    # 不另发 SUBAGENT 消息（避免同源结果二次入队）
    assert not any(m.kind is MessageKind.SUBAGENT for m in agent._messages.values())


@pytest.mark.parametrize("args", [
    {"agent_type": "worker", "resume": "w1"},   # 同给
    {},                                          # 同缺
])
async def test_t73_subagent_invoke_mutex_validation(runtime, args):
    """73：``agent_type`` 与 ``resume`` 互斥且至少其一；违反 → error 结果。"""
    agent = await runtime.create_agent("test-agent")
    tool = SubagentInvokeTool()
    result = await tool({"name": "", "prompt": "p", "asynchronized": False, **args},
                        caller=agent)
    assert result.status == "error" and "mutually exclusive" in result.error


# ---------------------------------------------------------------------------
# 清单 74：subagent-invoke 异步路径（asynchronized=True）
# ---------------------------------------------------------------------------

async def test_t74_subagent_invoke_async(runtime, provider):
    """74：创建段同步 await（失败同步产 error）；运行段后台，立即返回
    started 收据；结局经 SUBAGENT 消息送达。"""
    child_gate = asyncio.Event()

    async def gen(context, model):
        if "工人助手" in _sys_text(context):
            await child_gate.wait()   # 子 Agent 运行段受门控
            return text_response("后台完成")
        # 亲代 Agent：第一步唤起（异步），第二步收尾
        if not hasattr(gen, "called"):
            gen.called = True
            resp, _ = tool_call_response(
                ("subagent-invoke", {"agent_type": "worker", "prompt": "后台跑",
                                     "name": "bg", "asynchronized": True}))
            return resp
        return text_response("亲代收尾")

    provider.generate_fn = gen
    agent = await runtime.create_agent("test-agent")
    agent.add_agent("worker")
    agent.add_tool("subagent-invoke")
    result = await agent.query("后台唤起")
    assert result.status == "completed"
    tool_msg = next(agent._messages[mid] for mid in result.turn.message_ids
                    if agent._messages[mid].kind is MessageKind.TOOL)
    payload = next(b for b in tool_msg.content if isinstance(b, StructBlock)).data
    # 立即返回 started 收据（子 Agent 已创建并在跑）；B14：pending + 注册键块
    assert payload == {"invoked": "bg", "status": "started"}
    assert tool_msg.tool_status == "pending"
    assert any(isinstance(b, TextBlock) and "后台任务 ID" in b.text
               for b in tool_msg.content)
    task_id = next(t.split("：", 1)[1]
                   for t in (b.text for b in tool_msg.content
                             if isinstance(b, TextBlock))
                   if t.startswith("后台任务 ID："))
    assert task_id in agent._background_tasks   # B14：注册表在册（强引用/取消/destroy 覆盖）
    # 运行段结局经 SUBAGENT 消息送达
    child_gate.set()
    await _drain_until(lambda: any(
        m.kind is MessageKind.SUBAGENT for m in agent._messages.values()))
    sub_msg = next(m for m in agent._messages.values()
                   if m.kind is MessageKind.SUBAGENT)
    assert "后台完成" in "".join(
        getattr(b, "text", "") for b in sub_msg.content)
    # 运行段完成 → 注册表移除（完成即弃，B9）
    await _drain_until(
        lambda: task_id not in agent._background_tasks)


async def test_t74_async_creation_failure_is_sync_error(runtime):
    """74：asynchronized=True 的创建段失败（未知别名）→ 同步 error 结果。"""
    agent = await runtime.create_agent("test-agent")
    tool = SubagentInvokeTool()
    result = await tool({"name": "", "agent_type": "ghost", "prompt": "p",
                         "resume": "", "asynchronized": True}, caller=agent)
    assert result.status == "error"


# ---------------------------------------------------------------------------
# 清单 75：ExploreAgent（只读工具集 + plain 文本回传）
# ---------------------------------------------------------------------------

async def test_t75_explore_agent_readonly(runtime, provider):
    """75：亲代 Agent 声明 explore-agent 并经 subagent-invoke 唤起；子 Agent
    工具目录仅含 read/grep/glob 三个只读工具，结果以 plain 文本回传。"""
    child_contexts = []

    async def gen(context, model):
        if "read-only code-exploration assistant" in _sys_text(context):
            child_contexts.append(context)
            return text_response("src 下有 main.py 与 util.py 两个文件。")
        if not hasattr(gen, "called"):
            gen.called = True
            resp, _ = tool_call_response(
                ("subagent-invoke", {"agent_type": "explore-agent",
                                     "prompt": "列出 src 下文件", "name": "exp"}))
            return resp
        return text_response("亲代收尾")

    provider.generate_fn = gen
    agent = await runtime.create_agent("test-agent")
    agent.add_agent("explore-agent")
    agent.add_tool("subagent-invoke")
    result = await agent.query("派人探索")
    assert result.status == "completed"
    # 子 Agent 工具目录仅含三个只读工具
    assert child_contexts, "子 Agent 回合未发生"
    assert {d.name for d in child_contexts[0].tools} == {"read", "grep", "glob"}
    # plain 文本经 SubagentResult 回传（同步路径平铺在工具结果里）
    tool_msg = next(agent._messages[mid] for mid in result.turn.message_ids
                    if agent._messages[mid].kind is MessageKind.TOOL)
    payload = next(b for b in tool_msg.content if isinstance(b, StructBlock)).data
    assert payload["result"] == "src 下有 main.py 与 util.py 两个文件。"


# ---------------------------------------------------------------------------
# 清单 76：register_builtins 注册面
# ---------------------------------------------------------------------------

async def test_t76_register_builtins(tmp_path):
    """76：八个工具注册进 ``builtin::``、explore-agent 注册进 ``builtin::``；
    不产生任何 LLM 可见性（``Context.tools`` 为空直到显式声明）。"""
    runtime = make_runtime(tmp_path)
    try:
        # Runtime.__init__ 已经 register_builtins 调用点
        for name in ("subagent-invoke", "finish", "read", "write", "bash",
                     "edit", "grep", "glob"):
            assert f"builtin::{name}" in runtime.tool_registry
            assert name not in runtime.tool_registry   # __contains__ 仅全限定键（P3-07）
        assert runtime.get_agent_class("builtin::explore-agent") is ExploreAgent
        # 注册 ≠ 可见：未显式声明的 Agent 的 Context.tools 为空
        add_fake_provider(runtime)
        agent = await runtime.create_agent("test-agent")
        context = agent._assemble_context()
        assert context.tools == []
    finally:
        await runtime.shutdown()