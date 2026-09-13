"""阶段 4 _unstable/logging 测试（W41–W42）：测试清单 T127–T134。

全部真 Runtime（``make_runtime``）+ FakeProvider 脚本回放驱动——
``LoggingPlugin.install`` 依赖 ``runtime.get_plugin`` 探测面，故不用
HarnessRuntime（唯一例外：T131 未安装插件的负例，install 不发生，
HarnessRuntime 足够）。落盘断言直读 ``<session>/logging.jsonl``。
"""

from __future__ import annotations

import pytest

from flowing._unstable.logging import (
    LoggingPlugin,
    logging_plugin_key,
)
from flowing.errors import FlowingError, MissingProvideError, ServerError
from flowing.plugins.comm import CommPlugin
from flowing.plugins.skills import Skill, SkillPlugin, use_skill
from flowing.tool import Tool, ToolDefinition

from logging_support import (
    HarnessRuntime,
    LogAgent,
    SimpleAgent,
    add_fake_provider,
    hooks_of,
    make_logging_runtime,
    make_runtime,
    read_log,
    script_provider,
    text_response,
    tool_call_response,
)


class EchoTool(Tool):
    definition = ToolDefinition(
        name="echo", description="回显参数",
        params_schema={"text": {"type": "string"}})

    async def execute(self, *, text: str) -> str:
        return text


class LogSkillAgent(LogAgent):
    """setup 中 ``use_logging`` + ``use_skill`` 的测试 Agent。"""

    async def setup(self, **kwargs) -> None:
        await super().setup(**kwargs)
        use_skill(self)


def _rows_by_hook(agent, hook: str) -> list[dict]:
    return [row for row in read_log(agent) if row["hook"] == hook]


# ---------------------------------------------------------------------------
# T127：安装面 + 探测（W41）
# ---------------------------------------------------------------------------


async def test_t127_install_surface_and_detection(tmp_path):
    """T127：新 Runtime install(LoggingPlugin(level="INFO")) → inject 得实例；
    detected 只含已安装的内置扩展名（未安装的点名不登记、不访问）。"""
    runtime = make_runtime(tmp_path)
    runtime.install(LoggingPlugin(level="INFO"))
    try:
        plugin = runtime.inject(logging_plugin_key)
        assert isinstance(plugin, LoggingPlugin)
        assert plugin.level == "INFO"
        assert plugin.max_value_repr == 500          # 默认截断长度
        assert plugin.detected == frozenset()        # 四扩展均未装
        assert runtime.get_plugin("logging") is plugin
    finally:
        await runtime.shutdown()

    # 先装 skill/comm 再装 logging → detected 恰好登记这两个
    rt2 = make_runtime(tmp_path / "p2")
    rt2.install(SkillPlugin(), CommPlugin(), LoggingPlugin(level="DEBUG"))
    try:
        plugin2 = rt2.inject(logging_plugin_key)
        assert plugin2.detected == frozenset({"skill", "comm"})
        assert plugin2.level == "DEBUG"
    finally:
        await rt2.shutdown()


# ---------------------------------------------------------------------------
# T128：INFO 关键节点（W42）
# ---------------------------------------------------------------------------


async def test_t128_info_key_nodes(tmp_path):
    """T128：level="INFO" → 生命周期/turn 边界/消息出入队/LLM 边界/工具
    边界/子 Agent/取消/fork 各触发一次 → logging.jsonl 每条一行 JSON
    （含 ts/agent_id/level/hook/value）；value 只记摘要。"""
    runtime, provider = make_logging_runtime(tmp_path, level="INFO")
    runtime.register_tool(EchoTool())
    agent = await runtime.create_agent("log-agent")
    try:
        agent.add_tool("echo")

        # turn 边界 + 消息出入队 + LLM 边界
        script_provider(provider, text_response("ok"))
        result = await agent.query("hi")
        assert result.status == "completed"

        # 工具边界
        step1, _ = tool_call_response(("echo", {"text": "回显"}))
        script_provider(provider, step1, text_response("完成"))
        result = await agent.query("调用工具")
        assert result.status == "completed"

        # LLM 错误边界
        script_provider(provider, ServerError("boom"))
        result = await agent.query("报错")
        assert result.status == "error"

        # 子 Agent 边界（亲代 Agent 钩子）
        agent.add_agent("test-agent", alias="kid")
        script_provider(provider, text_response("子回复"))
        sub = await agent.invoke_subagent("kid", prompt="审查", name="reviewer")
        assert sub.subagent_status == "completed"

        # 取消与 fork
        await agent.cancel()
        await agent.fork(agent.current_head_id)

        # 生命周期收尾
        await agent.destroy()

        rows = read_log(agent)
        assert rows, "应有日志行"
        # 行结构：每条一行 JSON，含规约字段
        for row in rows:
            assert {"ts", "agent_id", "level", "hook", "value",
                    "handler_tag"} <= set(row)
            assert row["agent_id"] == agent.node_id
            assert row["level"] == "INFO"
            assert row["handler_tag"] is None
            assert row["ts"].endswith("Z")

        triggered = set(hooks_of(rows))
        expected = {
            "after_create",                    # 生命周期（create 管线）
            "before_destroy", "after_destroy",  # 生命周期（destroy 管线）
            "before_turn", "after_turn",       # turn 边界
            "after_enqueue", "after_dequeue",  # 消息出入队
            "before_provider_gen", "after_provider_gen",  # LLM 边界
            "on_provider_error",
            "before_tool_call", "after_tool_call",        # 工具边界
            "before_subagent_invoke", "after_subagent_invoke",
            "before_cancel", "after_cancel",
            "before_fork", "after_fork",
        }
        assert expected <= triggered
        # INFO 不覆盖全集：非关键节点无输出
        assert "before_enqueue" not in triggered
        assert "on_provider_delta" not in triggered
        assert "before_turn_append" not in triggered

        # value 只记摘要（类型名 + 标识字段）
        (tc_row,) = [r for r in rows
                     if r["hook"] == "before_tool_call" and "echo" in r["value"]]
        assert tc_row["value"].startswith("ToolCall(")
        msg_rows = [r for r in rows
                    if r["hook"] == "after_enqueue"
                    and r["value"].startswith("Message(")]
        assert msg_rows                        # 每次 query 各一条 USER 入队
        assert "kind='user'" in msg_rows[0]["value"]
        (err_row,) = _rows_by_hook(agent, "on_provider_error")
        assert "error=ServerError" in err_row["value"]
    finally:
        await runtime.shutdown()


# ---------------------------------------------------------------------------
# T129：DEBUG 全集 + repr 截断（W42）
# ---------------------------------------------------------------------------


async def test_t129_debug_full_set_and_repr_truncation(tmp_path):
    """T129：level="DEBUG" → 该实例已存在的全部钩子点都输出（枚举
    _hook_points），value 为 repr 截断 max_value_repr（可调）；
    on_provider_delta 逐条输出。"""
    runtime, provider = make_logging_runtime(
        tmp_path, level="DEBUG", max_value_repr=50)
    agent = await runtime.create_agent("log-agent")
    try:
        script_provider(provider, text_response("x" * 2000))
        result = await agent.query("hi")
        assert result.status == "completed"

        rows = read_log(agent)
        triggered = set(hooks_of(rows))
        # INFO 清单外的核心点也有输出（全集枚举）
        for name in ("before_enqueue", "before_dequeue", "before_turn_append",
                     "after_turn_append", "on_provider_delta", "before_turn"):
            assert name in triggered, name
        # 非流式合成一条全量 delta → 恰好一行
        assert len(_rows_by_hook(agent, "on_provider_delta")) == 1

        # value 为 repr 截断 50 字符
        gen_rows = _rows_by_hook(agent, "after_provider_gen")
        assert gen_rows and all(
            len(row["value"]) <= 50 for row in gen_rows)
        assert gen_rows[0]["value"].startswith("ProviderResponse(")

        # 挂载幂等去重：全部钩子点各有恰好一条观察 handler
        # （after_create/after_recover 另载 _attach_debug 内部 handler）
        for name, hook_list in agent.hooks._hook_points.items():
            n = sum(1 for e in hook_list if e.by == "logging")
            assert n == (2 if name in ("after_create", "after_recover")
                         else 1), name
    finally:
        await runtime.shutdown()


# ---------------------------------------------------------------------------
# T130：等级运行期可写（W41/W42）
# ---------------------------------------------------------------------------


async def test_t130_level_runtime_writable(tmp_path):
    """T130：运行期 level = "OFF" → 下一次钩子触发早退、无新增行；改回
    "INFO" 即恢复（handler 每次回读不缓存）；非法值 → FlowingError。"""
    runtime, provider = make_logging_runtime(tmp_path, level="INFO")
    agent = await runtime.create_agent("log-agent")
    try:
        plugin = runtime.get_plugin("logging")
        baseline = len(read_log(agent))
        assert baseline > 0

        plugin.level = "OFF"
        script_provider(provider, text_response("ok"))
        result = await agent.query("hi")
        assert result.status == "completed"
        assert len(read_log(agent)) == baseline     # OFF 早退，无新增行

        plugin.level = "INFO"                       # 改回即恢复（不缓存）
        script_provider(provider, text_response("ok2"))
        result = await agent.query("again")
        assert result.status == "completed"
        assert len(read_log(agent)) > baseline

        with pytest.raises(FlowingError):
            plugin.level = "VERBOSE"
        assert plugin.level == "INFO"               # 非法写入不生效
        with pytest.raises(FlowingError):
            LoggingPlugin(level="bogus")            # 构造期同律校验
    finally:
        await runtime.shutdown()


# ---------------------------------------------------------------------------
# T131：双层启用零开销（W42）
# ---------------------------------------------------------------------------


async def test_t131_missing_plugin_and_zero_cost(tmp_path):
    """T131：未安装 LoggingPlugin → use_logging(self) 抛
    MissingProvideError；未调用 use_logging 的 Agent → 零输出、
    无 logging.jsonl 文件（零开销）。"""
    # 未安装插件（HarnessRuntime：install 不发生，无需 get_plugin 面）
    harness = HarnessRuntime(tmp_path / "no-plugin")
    harness.register_agent_type(LogAgent, name="log-agent")
    with pytest.raises(MissingProvideError):
        await harness.create_agent("log-agent", start_loop=False)

    # 已装插件但 Agent 未 use_logging → 零输出、无文件、无 handler
    runtime, provider = make_logging_runtime(
        tmp_path / "plain", agent_cls=SimpleAgent)
    agent = await runtime.create_agent("log-agent")
    try:
        script_provider(provider, text_response("ok"))
        result = await agent.query("hi")
        assert result.status == "completed"
        assert not (agent._session_dir / "logging.jsonl").exists()
        assert read_log(agent) == []
        for hook_list in agent.hooks._hook_points.values():
            assert [e for e in hook_list if e.by == "logging"] == []
    finally:
        await runtime.shutdown()


# ---------------------------------------------------------------------------
# T132：插件探测白名单（W42）
# ---------------------------------------------------------------------------


async def test_t132_extension_whitelist(tmp_path):
    """T132：装了 skills 插件且 Agent use_skill → before/after_skill_load
    触发有输出行（INFO 白名单即覆盖）；未装 skills 插件 → 对应点名不挂钩
    （不抛 UnknownHookPointError）；装了插件但 Agent 未 use_skill →
    枚举自然跳过。"""
    # ① 装 SkillPlugin + Agent use_skill：INFO 级白名单覆盖扩展钩子点
    runtime, provider = make_logging_runtime(
        tmp_path / "skill-info", level="INFO",
        pre_plugins=(SkillPlugin(),), agent_cls=LogSkillAgent)
    runtime.register_skill(Skill(name="audit", description="d", content="正文"))
    agent = await runtime.create_agent("log-agent")
    try:
        plugin = runtime.get_plugin("logging")
        assert plugin.detected == frozenset({"skill"})
        agent.skill_add("audit")
        await agent.skill_load("audit")
        triggered = set(hooks_of(read_log(agent)))
        assert "before_skill_load" in triggered
        assert "after_skill_load" in triggered
    finally:
        await runtime.shutdown()

    # ② 未装 SkillPlugin：DEBUG 全集也不含未安装的扩展点名（不抛
    # UnknownHookPointError）
    rt2, provider2 = make_logging_runtime(tmp_path / "no-skill", level="DEBUG")
    agent2 = await rt2.create_agent("log-agent")
    try:
        assert rt2.get_plugin("logging").detected == frozenset()
        script_provider(provider2, text_response("ok"))
        result = await agent2.query("hi")
        assert result.status == "completed"
        assert "before_skill_load" not in agent2.hooks._hook_points
        assert "on_signal" not in agent2.hooks._hook_points
        assert "on_cron_trigger" not in agent2.hooks._hook_points
        assert "before_skill_load" not in hooks_of(read_log(agent2))
    finally:
        await rt2.shutdown()

    # ③ 装了 SkillPlugin 但该 Agent 未 use_skill：枚举自然跳过
    rt3, provider3 = make_logging_runtime(
        tmp_path / "skill-no-use", level="DEBUG", pre_plugins=(SkillPlugin(),))
    agent3 = await rt3.create_agent("log-agent")
    try:
        assert rt3.get_plugin("logging").detected == frozenset({"skill"})
        assert "before_skill_load" not in agent3.hooks._hook_points
        script_provider(provider3, text_response("ok"))
        result = await agent3.query("hi")
        assert result.status == "completed"
        assert "before_skill_load" not in hooks_of(read_log(agent3))
    finally:
        await rt3.shutdown()


# ---------------------------------------------------------------------------
# T133：写盘失败降级 + handler 异常不传播（W42）
# ---------------------------------------------------------------------------


async def test_t133_write_failure_degrades_and_handler_errors(tmp_path, capsys):
    """T133：写盘失败（模拟 IO 错误）→ 打印一次 stderr 警告后该 Agent
    不再尝试写盘，业务管线不被打断；handler 自身异常 → stderr 警告，
    不传播。"""
    runtime, provider = make_logging_runtime(tmp_path, level="INFO")
    agent = await runtime.create_agent("log-agent")
    try:
        capsys.readouterr()   # 清空创建期可能的输出

        # handler 自身异常：value 的标识字段访问爆炸 → stderr 警告，不传播
        class _Hostile:
            shortcut = None   # dispatch 收尾要读 shortcut，保持良性

            def __getattr__(self, name):
                raise RuntimeError(f"boom:{name}")

        await agent.hooks.before_turn.dispatch(agent, _Hostile())
        err = capsys.readouterr().err
        assert "handler raised" in err and "before_turn" in err

        # 模拟 IO 错误：把 logging.jsonl 替换为同名目录（open 必 OSError），
        # 不影响 tree.jsonl / state.jsonl 的落盘
        log_path = agent._session_dir / "logging.jsonl"
        assert log_path.exists()
        log_path.unlink()
        log_path.mkdir()

        script_provider(provider, text_response("ok"))
        result = await agent.query("one")
        assert result.status == "completed"         # 业务管线不被打断
        err = capsys.readouterr().err
        assert err.count("failed to write log") == 1   # 一次性警告

        script_provider(provider, text_response("ok"))
        result = await agent.query("two")
        assert result.status == "completed"
        err = capsys.readouterr().err
        assert "failed to write log" not in err     # 后续静默降级
    finally:
        await runtime.shutdown()


# ---------------------------------------------------------------------------
# T134：recover 重挂幂等（W42）
# ---------------------------------------------------------------------------


async def test_t134_recover_reattach_idempotent(tmp_path):
    """T134：recover 管线重跑 setup → _attach_debug 经 after_recover
    兜底重挂，幂等去重不重复输出。"""
    from collections import Counter

    runtime, provider = make_logging_runtime(tmp_path, level="DEBUG")
    agent = await runtime.create_agent("log-agent")
    script_provider(provider, text_response("ok"))
    await agent.query("hi")
    agent_id = agent.node_id
    await runtime.shutdown()

    # 新 Runtime 同目录 = 进程重启；recover 管线重跑 setup → use_logging
    # 重挂；after_recover 触发 _attach_debug 兜底
    rt2, provider2 = make_logging_runtime(tmp_path, level="DEBUG")
    recovered = await rt2.recover_agent(agent_id)
    try:
        rows = read_log(recovered)
        # after_recover 行存在：recover 管线上重挂生效（同一文件 append）
        assert any(row["hook"] == "after_recover" for row in rows)

        # 幂等去重：每个钩子点的观察 handler 不叠加
        for name, hook_list in recovered.hooks._hook_points.items():
            n = sum(1 for e in hook_list if e.by == "logging")
            assert n == (2 if name in ("after_create", "after_recover")
                         else 1), name

        # 不重复输出：recover 后一次 query，关键钩子各恰好一行新增
        baseline = len(read_log(recovered))
        script_provider(provider2, text_response("ok"))
        result = await recovered.query("again")
        assert result.status == "completed"
        new_rows = read_log(recovered)[baseline:]
        counts = Counter(hooks_of(new_rows))
        for name in ("before_turn", "after_turn", "before_provider_gen",
                     "after_provider_gen"):
            assert counts[name] == 1, (name, counts)
    finally:
        await rt2.shutdown()
