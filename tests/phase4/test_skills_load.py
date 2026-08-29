"""阶段 4 skills 加载流程测试（W12–W15）：测试清单 T24–T32、T34。

LLM 入口（skill-load 工具）由 FakeProvider 脚本回放驱动；编程式入口直接
``await agent.skill_load(...)``。观测点用钩子（``after_enqueue`` 捕 PLUGIN
消息、``after_tool_call`` 捕 ToolResult）——不读内部队列结构。
"""

from __future__ import annotations

import pytest

from flowing.errors import (
    EntryNameConflictError,
    FormatError,
    Intercepted,
    MissingProvideError,
)
from flowing.hooks import HookList
from flowing.message import MessageKind
from flowing.parser import EntryRef
from flowing.plugins.skills import Skill, use_skill

from skills_support import (
    HarnessRuntime,
    SimpleAgent,
    SkillHostAgent,
    add_fake_provider,
    make_skill_runtime,
    script_provider,
    text_response,
    tool_call_response,
)


def _capture_messages(agent, kind: MessageKind) -> list:
    """经 after_enqueue 钩子收集指定 kind 的入队消息。"""
    collected = []

    def _collect(agent, msg):
        if msg.kind is kind:
            collected.append(msg)
        return msg

    agent.hooks.after_enqueue(_collect, by="test")
    return collected


def _capture_tool_results(agent) -> list:
    """经 after_tool_call 钩子收集 ToolResult。"""
    collected = []

    def _collect(agent, result):
        collected.append(result)
        return result

    agent.hooks.after_tool_call(_collect, by="test")
    return collected


# ---------------------------------------------------------------------------
# T24：LLM 经 skill-load 加载——PLUGIN 消息入队 + 收据不含正文
# ---------------------------------------------------------------------------


async def test_t24_llm_skill_load_roundtrip(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    runtime.register_skill(Skill(name="summarize", description="摘要技能。",
                                 content="SUM_BODY_UNIQUE 标记"))
    provider = add_fake_provider(runtime)
    agent = await runtime.create_agent(
        "skill-host", skills=["summarize as sum"])
    use_skill(agent)
    agent.add_tool("skill-load")
    plugin_messages = _capture_messages(agent, MessageKind.PLUGIN)
    tool_results = _capture_tool_results(agent)

    step1, _ = tool_call_response(("skill-load", {"name": "sum"}))
    script_provider(provider, step1, text_response("完成"))
    result = await agent.query("加载技能")
    assert result.status == "completed"

    # 恰好一条 PLUGIN 消息，kind/source 正确且承载正文
    assert len(plugin_messages) == 1
    msg = plugin_messages[0]
    assert msg.source == "skill:sum"
    assert "SUM_BODY_UNIQUE 标记" in msg.content[0].text
    # ToolResult 是简短收据且不含正文
    assert len(tool_results) == 1
    assert tool_results[0].status == "completed"
    assert tool_results[0].output == {"loaded": "sum"}
    assert "SUM_BODY_UNIQUE" not in str(tool_results[0].output)


# ---------------------------------------------------------------------------
# T25：enabled=False → LLM 入口 error；编程式入口不受限
# ---------------------------------------------------------------------------


async def test_t25_disabled_entry_llm_rejected_programmatic_allowed(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    runtime.register_skill(Skill(name="audit", description="审计技能。",
                                 content="AUDIT_BODY"))
    provider = add_fake_provider(runtime)
    agent = await runtime.create_agent(
        "skill-host", skills=[{"audit": {"enabled": False}}])
    use_skill(agent)
    agent.add_tool("skill-load")
    plugin_messages = _capture_messages(agent, MessageKind.PLUGIN)
    tool_results = _capture_tool_results(agent)

    step1, _ = tool_call_response(("skill-load", {"name": "audit"}))
    script_provider(provider, step1, text_response("收尾"))
    result = await agent.query("加载 audit")
    assert result.status == "completed"
    assert tool_results[0].status == "error"   # FlowingError 包装为 LLM 可见 error
    assert "audit" in tool_results[0].error
    assert plugin_messages == []               # 无 PLUGIN 消息

    loaded = await agent.skill_load("audit")   # 编程式入口不做 enabled 检查
    assert "AUDIT_BODY" in loaded.content


# ---------------------------------------------------------------------------
# T26：before_skill_load 改写 args → on_load 与正文渲染收到改写值
# ---------------------------------------------------------------------------


async def test_t26_before_hook_rewrites_args(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    agent = await runtime.create_agent(
        "skill-host", start_loop=False, skills=["with-args as wa"])
    use_skill(agent)

    def _clamp(agent, ctx):
        ctx.args["max_length"] = 100
        return ctx

    agent.hooks.before_skill_load(_clamp, by="test")
    result = await agent.skill_load("wa")
    assert "长度上限 100" in result.content              # 正文渲染收到改写值
    assert agent._on_load_args["max_length"] == 100      # on_load 同样收到


# ---------------------------------------------------------------------------
# T27：before_skill_load raise Intercepted → 工具调用 blocked，链路终止
#
# execute 内抛出的 Intercepted 经 Tool.__call__ 的专门分支转为 blocked
# （与 before_tool_call 拦截同一出口、同一 reason 塑形）。
# ---------------------------------------------------------------------------


async def test_t27_intercepted_blocks_load(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    agent = await runtime.create_agent(
        "skill-host", skills=["with-args as wa"])
    use_skill(agent)
    agent.add_tool("skill-load")
    plugin_messages = _capture_messages(agent, MessageKind.PLUGIN)
    tool_results = _capture_tool_results(agent)
    after_calls = []

    def _ban(agent, ctx):
        raise Intercepted("禁用")

    def _after(agent, content):
        after_calls.append(content)
        return content

    agent.hooks.before_skill_load(_ban, by="test")
    agent.hooks.after_skill_load(_after, by="test")

    step1, _ = tool_call_response(("skill-load", {"name": "wa"}))
    script_provider(provider, step1, text_response("收尾"))
    result = await agent.query("加载 wa")
    assert result.status == "completed"
    assert tool_results[0].status == "blocked"   # Intercepted 传播为 blocked 结果
    assert not hasattr(agent, "_on_load_args")   # on_load 未执行
    assert plugin_messages == []                 # 无 PLUGIN 入队
    assert after_calls == []                     # after_skill_load 不触发


# ---------------------------------------------------------------------------
# T28：after_skill_load 改写 body → PLUGIN 与 SkillResult 逐字相同；空串合法
# ---------------------------------------------------------------------------


async def test_t28_after_hook_rewrites_body(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    runtime.register_skill(Skill(name="audit", description="d", content="原始正文"))
    agent = await runtime.create_agent(
        "skill-host", start_loop=False, skills=["audit"])
    use_skill(agent)
    plugin_messages = _capture_messages(agent, MessageKind.PLUGIN)

    def _rewrite(agent, content):
        content.body = "改写后正文"
        return content

    agent.hooks.after_skill_load(_rewrite, by="test")
    result = await agent.skill_load("audit")
    assert result.content == "改写后正文"
    assert plugin_messages[-1].content[0].text == "改写后正文"   # 逐字相同

    # body 改为空串合法：PLUGIN 照常入队
    agent.hooks.after_skill_load.remove_by_owner("test")

    def _blank(agent, content):
        content.body = ""
        return content

    agent.hooks.after_skill_load(_blank, by="test")
    result2 = await agent.skill_load("audit")
    assert result2.content == ""
    assert plugin_messages[-1].content[0].text == ""


# ---------------------------------------------------------------------------
# T29：未安装 SkillPlugin → use_skill 抛 MissingProvideError
# ---------------------------------------------------------------------------


async def test_t29_missing_plugin_raises(tmp_path):
    runtime = HarnessRuntime(tmp_path)
    runtime.register_agent_type("test-agent", SimpleAgent)
    agent = await runtime.create_agent("test-agent", start_loop=False)
    with pytest.raises(MissingProvideError):
        use_skill(agent)


# ---------------------------------------------------------------------------
# T30：启用后钩子点存在；未启用的同类 Agent 访问抛 UnknownHookPointError
# ---------------------------------------------------------------------------


async def test_t30_hook_points_declared_only_when_enabled(tmp_path):
    from flowing.errors import UnknownHookPointError

    runtime = make_skill_runtime(tmp_path)
    agent = await runtime.create_agent("skill-host", start_loop=False, skills=["sum"])
    use_skill(agent)
    assert isinstance(agent.hooks.before_skill_load, HookList)
    assert isinstance(agent.hooks.after_skill_load, HookList)

    plain = await runtime.create_agent("skill-host", start_loop=False)
    with pytest.raises(UnknownHookPointError):
        plain.hooks.before_skill_load


# ---------------------------------------------------------------------------
# T31：skill_load 未声明别名 → KeyError；注入表达式 key 缺失 → MissingProvideError
# ---------------------------------------------------------------------------


async def test_t31_undeclared_alias_and_missing_inject_key(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    agent = await runtime.create_agent(
        "skill-host", start_loop=False,
        skills=["sum", {"with-args as wa": {"args": {
            "work_directory": "{{ self.inject('missing_key') }}"}}}])
    use_skill(agent)
    with pytest.raises(KeyError):
        await agent.skill_load("ghost")
    with pytest.raises(MissingProvideError):
        await agent.skill_load("wa")   # 注入表达式求值沿 provide 链缺失即报错


# ---------------------------------------------------------------------------
# T32：skill_add 归一——EntryRef 与 alias/body 同传 / 未知键 / args 键含 as /
# 同 alias 冲突
# ---------------------------------------------------------------------------


async def test_t32_skill_add_normalization_errors(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    agent = await runtime.create_agent("skill-host", start_loop=False, skills=["sum"])
    use_skill(agent)

    with pytest.raises(FormatError):
        agent.skill_add(EntryRef(raw="sum", alias="s"), alias="x")
    with pytest.raises(FormatError):
        agent.skill_add(EntryRef(raw="sum", alias="s"), body={"args": {}})
    with pytest.raises(FormatError, match="未知键"):
        agent.skill_add("with-args", alias="w1", body={"bogus": 1})
    with pytest.raises(FormatError, match="as"):
        agent.skill_add("with-args", alias="w2",
                        body={"args": {"max_length as ml": 500}})
    with pytest.raises(EntryNameConflictError):
        agent.skill_add("sum")   # 同 alias 已存在（setup 已声明）


# ---------------------------------------------------------------------------
# T34：绑函数约定——Agent 类已自定义 skill_load → use_skill 后仍是用户定义
# ---------------------------------------------------------------------------


class _CustomLoadAgent(SkillHostAgent):
    """类级自定义 skill_load（检查后跳过约定：绑定方不得覆盖）。"""

    async def skill_load(self, name: str):
        return f"custom:{name}"


async def test_t34_user_defined_skill_load_preserved(tmp_path):
    runtime = make_skill_runtime(tmp_path)
    runtime.register_agent_type("custom-load", _CustomLoadAgent)
    agent = await runtime.create_agent("custom-load", start_loop=False, skills=["sum"])
    use_skill(agent)
    assert await agent.skill_load("sum") == "custom:sum"   # 用户定义保留（含类级方法探测）
