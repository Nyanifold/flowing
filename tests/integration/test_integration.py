"""端到端集成测试（T125–T129 + 补做 T88/T90/T91 + A8 凭证边界抽查）。

全部 FakeProvider 驱动、经真 ``Runtime``（``launch`` / ``make_runtime``）
与真 create/recover 管线；对应验收标准 A1–A6 与 A8。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import textwrap
import time
from pathlib import Path

import pytest

from flowing import launch, on
from flowing.agent import Agent
from flowing.errors import CorruptionError, MissingProvideError
from flowing.message import Message, MessageKind, TextBlock
from flowing.parsable import Parsable
from flowing.tool import Tool, ToolDefinition

from harness import (
    add_fake_provider,
    make_runtime,
    script_provider,
    text_response,
    tool_call_response,
)


class WaitTool(Tool):
    """门控工具：等到共享 Event 置位才返回（与 test_agent_turn.py 同形）。"""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate
        self.calls = 0

    definition = ToolDefinition(
        name="wait", description="等待门控", params_schema={})

    async def execute(self) -> str:
        self.calls += 1
        await self.gate.wait()
        return f"done-{self.calls}"


class EchoTool(Tool):
    """即时回显工具（pause/resume 冻结观察用，与 test_agent_turn.py 同形）。"""

    definition = ToolDefinition(
        name="echo", description="回显参数",
        params_schema={"text": {"type": "string"}})

    async def execute(self, *, text: str) -> str:
        return text


async def _yield(n: int = 6) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def _wait_until(pred, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("等待条件超时")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# T125：全链（验收①）
# ---------------------------------------------------------------------------


async def test_t125_full_chain(tmp_path):
    """T125 / A1：launch → create_agent → query/steer/message → 回合推进 →
    pause/resume → cancel → destroy → recover_agent（树与 state 恢复、继续对话）。"""
    (tmp_path / "models.yaml").write_text(
        "fake:\n  provider: fake\n  model: fake-model\n"
        "  context_window: 100000\n  max_output_tokens: 4096\n", encoding="utf-8")
    (tmp_path / "model-tags.yaml").write_text(
        "tags:\n  default: fake\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(textwrap.dedent("""\
        from flowing import Runtime, Agent
        from flowing.parsable import Parsable
        from flowing.providers import FakeProvider

        class MainAgent(Agent):
            system_prompt = Parsable("你是集成测试助手。")
            async def setup(self, **kwargs):
                self.state.register("marker", "init")

        async def main(flag=None, **kwargs):
            runtime = Runtime(persist_dir="@/sessions")
            runtime.set_models("@/models.yaml")
            runtime.set_model_tags("@/model-tags.yaml")
            runtime.register_agent_type(MainAgent, name="main-agent")
            runtime.provider_registry._instances["fake"] = FakeProvider()
            runtime.provide("launch_flag", flag)   # kwargs 原样透传验证
            return runtime
        """), encoding="utf-8")
    runtime = await launch(tmp_path, flag="on")   # CLI --flag on 的等价透传
    assert runtime.inject("launch_flag") == "on"
    provider = runtime.provider_registry._instances["fake"]
    agent = await runtime.create_agent("main-agent", agent_id="agent-main")

    # 1. query → completed
    script_provider(provider, text_response("你好"))
    r1 = await agent.query("hi")
    assert r1.status == "completed" and r1.final_text == "你好"

    # 2. steer：回合进行中注入 → ②.5 吸收、当轮 context 可见
    gate = asyncio.Event()
    runtime.register_tool(WaitTool(gate))
    agent.add_tool("wait")
    step1, _ = tool_call_response(("wait", {}))
    script_provider(provider, step1, text_response("完成"))
    task = asyncio.create_task(agent.query("开始"))
    await _yield()   # 回合进入工具执行（挂在 gate 上）
    await agent.steer("预算上限改为 500")
    await _yield(2)
    gate.set()
    r2 = await asyncio.wait_for(task, 2)
    assert r2.status == "completed" and not r2.turn.aborted
    # 当轮 context 可见：steer 回合的第二次 provider_gen（received[2]；
    # received[0] 是首个 query 回合）收到的消息含该 steer
    second_ctx = provider.received[2]
    assert any("预算上限" in getattr(b, "text", "")
               for m in second_ctx.messages for b in m.content)

    # 3. message() fire-and-forget → 由下一回合消费
    script_provider(provider, text_response("收到"))
    baseline = len(provider.received)
    await agent.message("第二条")
    await _wait_until(lambda: len(provider.received) > baseline)
    assert any("第二条" in getattr(b, "text", "")
               for m in provider.received[-1].messages for b in m.content)

    # 4. pause/resume：provider 调用计数冻结 → 恢复后继续
    runtime.tool_registry.register(EchoTool())
    agent.add_tool("echo")
    step41, _ = tool_call_response(("echo", {"text": "一"}))
    script_provider(provider, step41, text_response("收尾"))
    paused_once = False

    async def _pause_once(a, result):
        nonlocal paused_once
        if not paused_once:
            paused_once = True
            a.pause()
        return result

    agent.hooks.after_tool_call(_pause_once)
    task4 = asyncio.create_task(agent.query("开始4"))
    await _wait_until(lambda: agent.paused)
    frozen = len(provider.received)
    await _yield(3)
    assert len(provider.received) == frozen   # 调用计数冻结
    agent.resume()
    r4 = await asyncio.wait_for(task4, 2)
    assert r4.status == "completed" and r4.final_text == "收尾"

    # 5. cancel：回合取消 → cancelled 且 _executions 清空
    gate5 = asyncio.Event()
    wait5 = WaitTool(gate5)
    runtime.tool_registry.register(wait5, name="wait5")
    agent.add_tool("wait5")
    step51, _ = tool_call_response(("wait5", {}))
    script_provider(provider, step51, text_response("不应到达"))
    task5 = asyncio.create_task(agent.query("开始5"))
    await _yield()   # 回合进入工具执行（挂在 gate5 上）
    await agent.cancel()
    gate5.set()   # 放行工具返回，回合在下一检查点按 abort 收口
    r5 = await asyncio.wait_for(task5, 2)
    assert r5.status == "cancelled"
    assert agent._executions == {}

    # 6. destroy → recover_agent：消息树与 state 恢复，上下文延续可继续对话
    agent.state.marker = "v1"   # 写透持久化
    msg_count = len(agent._messages)
    await agent.destroy()
    assert "agent-main" not in runtime._nodes
    recovered = await runtime.recover_agent("agent-main")
    assert recovered.node_id == "agent-main"
    assert len(recovered._messages) == msg_count   # 消息树全量恢复
    assert recovered.state.marker == "v1"   # state 恢复
    script_provider(provider, text_response("继续中"))
    r6 = await recovered.query("还在吗")
    assert r6.status == "completed" and r6.final_text == "继续中"
    # 上下文延续：本轮 context 含首轮消息
    last_ctx = provider.received[-1]
    assert any("你好" in getattr(b, "text", "")
               for m in last_ctx.messages for b in m.content)
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# T126（翻转，D5）：无写闸门全程（验收③）
# ---------------------------------------------------------------------------


class GateWriteAgent(Agent):
    """setup 中写 state（D5 删写闸门后合法）。"""

    system_prompt = Parsable("闸门测试。")

    async def setup(self, **kwargs) -> None:
        self.state.illegal = 1


class SubInSetupAgent(Agent):
    """setup 中创建子代（S-34 闸门删除后合法）。"""

    system_prompt = Parsable("子代闸门测试。")

    async def setup(self, **kwargs) -> None:
        await self.create_subagent("test-agent")


class RecoverGateAgent(Agent):
    """recover 管线：before_recover（_restore 之前）写 state 不再被拦。"""

    system_prompt = Parsable("恢复闸门测试。")
    gate_blocked: list = []

    @on("before_recover")
    def _try_write(self, args):
        try:
            self.state.early = 1
        except RuntimeError:
            type(self).gate_blocked.append(True)
        return args

    async def setup(self, **kwargs) -> None:
        self.state.register("early", 0)


async def test_t126_no_write_gate_whole_lifecycle(tmp_path):
    """T126（翻转，D5）：setup 中写 state / 创建子代合法；before_recover 写 state
    不抛错（该写会被 recover 时的 _restore 重放覆盖，不是失败）。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.register_agent_type(GateWriteAgent, name="gate-write")
    runtime.register_agent_type(SubInSetupAgent, name="sub-in-setup")
    runtime.register_agent_type(RecoverGateAgent, name="recover-gate")
    # setup 中写 state → 合法（写透落盘）
    agent = await runtime.create_agent("gate-write")
    assert agent.state.illegal == 1
    await agent.destroy()
    # setup 中 create_subagent → 合法（S-34 闸门删除）
    agent2 = await runtime.create_agent("sub-in-setup")
    assert len(agent2._children) == 1
    await agent2.destroy()
    # recover：before_recover（_restore 之后，D2 重排）写 state 不抛错且保留
    # ——重放先于钩子完成，钩子写不再被覆盖（规格 §3.3）
    agent3 = await runtime.create_agent("recover-gate")
    agent3.state.early = 5
    await agent3.destroy()
    recovered = await runtime.recover_agent(agent3.node_id)
    assert RecoverGateAgent.gate_blocked == []
    assert recovered.state.early == 1   # before_recover 的写保留（重放已完成）
    recovered.state.early = 6   # 写透恢复可用
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# T127–T129：mount 幂等重启 / 三档遗忘 / provide 链上溯（验收②③④）
# ---------------------------------------------------------------------------


async def test_t127_mount_idempotent_restart(tmp_path):
    """T127 / A4：固定 id 的节点 destroy 后同 id 再 mount → 走恢复，
    消息树与 state 完好（“同一个根回来了”），可多轮往复。"""
    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    script_provider(provider, text_response("第一轮"), text_response("第二轮"))
    (tmp_path / "root.fya").write_text("", encoding="utf-8")   # recover 分支不解析内容
    agent = await runtime.create_agent("test-agent", agent_id="agent-main")
    await agent.query("一")
    agent.state["cycle"] = 1
    await agent.destroy()
    # 同 id 再 mount → 走恢复（幂等挂载）
    back = await runtime.mount("@/root.fya", agent_id="agent-main")
    assert back.node_id == "agent-main"
    assert len(back._messages) == 2   # 消息树完好
    assert back.state["cycle"] == 1   # state 完好
    # 再跑一个回合后第二轮 destroy → 再 mount：延续性保持
    r = await back.query("二")
    assert r.status == "completed"
    await back.destroy()
    back2 = await runtime.mount("@/root.fya", agent_id="agent-main")
    assert len(back2._messages) == 4
    assert back2.current_head_id == r.turn.message_ids[-1]
    await runtime.shutdown()


async def test_t128_three_tier_forgetting(tmp_path):
    """T128 / A5：destroy（池 key 保留、get_agent 现场恢复）→ archive
    （池/名录移除、get_agent None、文件留档）→ 撞留档目录 create 报 FileExistsError。"""
    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    script_provider(provider, text_response("在"))
    agent = await runtime.create_agent("test-agent", agent_id="agent-x")
    await agent.query("hi")
    # 第一档：destroy —— 池 key 与名录保留，现场可恢复
    await agent.destroy()
    assert "agent-x" in runtime._agent_pool
    assert "agent-x" in runtime.states["core"].get("agents", [])
    restored = await runtime.get_agent("agent-x")
    assert restored is not None and len(restored._messages) == 2
    # 第二档：archive —— 运行时完全遗忘、文件留档
    await runtime.archive_agent("agent-x")
    assert "agent-x" not in runtime._agent_pool
    assert "agent-x" not in runtime.states["core"].get("agents", [])
    assert await runtime.get_agent("agent-x") is None
    with pytest.raises(KeyError):
        await runtime.recover_agent("agent-x")
    assert (runtime._persist_dir / "agent-x" / "tree.jsonl").exists()   # 文件留档
    # 第三档：撞留档目录的显式 create → FileExistsError（防两份历史混杂）
    with pytest.raises(FileExistsError, match="archive"):
        await runtime.create_agent("test-agent", agent_id="agent-x")
    await runtime.shutdown()


async def test_t129_provide_inject_chain_climbing(tmp_path):
    """T129 / A6：Runtime → 根 → 子 → 孙逐层命中与穿透；亲节点 destroy 后
    链断处 MissingProvideError。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.provide("root_key", "root")
    root = await runtime.create_agent("test-agent")
    root.provide("root_layer", "R")
    child = await root.create_subagent("test-agent", name="c")
    child.provide("mid_key", "mid")
    grandchild = await child.create_subagent("test-agent", name="g")
    # 逐层命中与穿透
    assert child.inject("root_layer") == "R"   # 近层命中
    assert grandchild.inject("mid_key") == "mid"   # 穿一层
    assert grandchild.inject("root_layer") == "R"   # 穿两层
    assert grandchild.inject("root_key") == "root"   # 直到链终点
    # 覆盖即刻可见
    runtime.provide("root_key", "root2")
    assert grandchild.inject("root_key") == "root2"
    # 亲节点 destroy（级联销毁子树）后链断：孙实例的上溯在断裂处 MissingProvideError
    await child.destroy()
    assert child.node_id not in runtime._nodes
    with pytest.raises(MissingProvideError):
        grandchild.inject("root_key")
    with pytest.raises(MissingProvideError):
        grandchild.inject("mid_key")
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# T88 / T90 / T91（补做）：recover 管线驱动的持久化恢复
# ---------------------------------------------------------------------------


async def test_t88_recover_torn_tail(tmp_path, copy_fixture):
    """T88（补做）：tree.jsonl 含完整消息 + 撕裂末行 → 完整消息恢复、
    撕裂末行丢弃、current_head_id 指向最后完整消息（决策 9：head 以 core
    袋为准——预写崩溃时落盘的 head）。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent")   # 建立池条目与 session 目录
    await agent.destroy()
    # 用预写语料替换 session 的 tree.jsonl（目录即契约：fixtures/persistence/）
    torn = copy_fixture("persistence/tree-torn-tail.jsonl")
    (agent._session_dir / "tree.jsonl").write_bytes(torn.read_bytes())
    # 决策 9：恢复 head 读 core 袋、不树校验——预写崩溃时落盘的 head
    (agent._session_dir / "core.jsonl").write_text(
        '{"type": "meta", "format_version": 1}\n'
        '{"op": "set", "key": "current_head_id", "value": "m2"}\n')
    recovered = await runtime.recover_agent(agent.node_id)
    assert set(recovered._messages) == {"m1", "m2"}   # 撕裂的 m3 行丢弃
    assert recovered._messages["m2"].parent_id == "m1"
    assert recovered.current_head_id == "m2"   # 指向最后完整消息（袋为准）
    await runtime.shutdown()


async def test_t90_recover_state_replay_migration_corruption(tmp_path, copy_fixture):
    """T90（补做）：state.jsonl 的 set/delete 行重放逐键覆盖 default；
    无版本首行存量文件经迁移链升级；中间行损坏 → CorruptionError（负例）。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)

    class StateAgent(Agent):
        system_prompt = Parsable("状态重放。")

        async def setup(self, **kwargs):
            self.state.register("n", 0)
            self.state.register("s", "default-s")

    runtime.register_agent_type(StateAgent, name="state-agent")

    async def _recover_with(fixture_name: str):
        agent = await runtime.create_agent("state-agent")
        await agent.destroy()
        copy = copy_fixture(f"persistence/{fixture_name}")
        (agent._session_dir / "state.jsonl").write_bytes(copy.read_bytes())
        return agent, await runtime.recover_agent(agent.node_id)

    # set/delete 重放：落盘值覆盖 default；delete 后读回退 default
    agent1, rec1 = await _recover_with("state-ok.jsonl")
    assert rec1.state.n == 5
    assert rec1.state.s == "default-s"
    # 无版本首行存量文件：迁移链升级（replay 内借 sync 回写新版本）+ 值重放
    agent2, rec2 = await _recover_with("state-v0-no-meta.jsonl")
    assert rec2.state.n == 5 and rec2.state.s == "default-s"
    await rec2._state_bag._store.drain()   # 排空迁移回写与压缩
    first_line = (agent2._session_dir / "state.jsonl").read_text(
        encoding="utf-8").splitlines()[0]
    assert json.loads(first_line)["type"] == "meta"   # 迁移后回写含版本首行
    # 中间行损坏（负例）：重放报警并抛 CorruptionError
    agent3 = await runtime.create_agent("state-agent")
    await agent3.destroy()
    corrupt = copy_fixture("persistence/state-corrupt-mid.jsonl")
    (agent3._session_dir / "state.jsonl").write_bytes(corrupt.read_bytes())
    with pytest.raises(CorruptionError):
        await runtime.recover_agent(agent3.node_id)
    await runtime.shutdown()


async def test_t91_recover_gate_and_compaction(tmp_path):
    """T91（补做）：recover 后写透可用（无写闸门，D5）、
    state.jsonl 全量压缩请求发出（压缩三时点①——被删键不留痕）。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent")
    # 制造历史：set → delete 的键与反复改写的键
    agent.state["transient"] = "x"
    agent.state["keep"] = 1
    del agent.state["transient"]
    for i in range(5):
        agent.state["keep"] = i
    await agent.destroy()
    recovered = await runtime.recover_agent(agent.node_id)
    # 写透恢复可用（无写闸门，D5）
    recovered.state["keep"] = 99
    await recovered._state_bag._store.drain()   # 排空压缩请求
    # 压缩时点①：state.jsonl 全量重写为终态——被删键不留行
    text = (agent._session_dir / "state.jsonl").read_text(encoding="utf-8")
    assert "transient" not in text
    records = [json.loads(line) for line in text.splitlines()
               if line and not line.startswith('{"type": "meta"')]
    keep_records = [r for r in records if r.get("key") == "keep"]
    assert len(keep_records) >= 1 and keep_records[-1]["value"] == 99
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# A8：凭证边界抽查
# ---------------------------------------------------------------------------


async def test_a8_credential_boundary(tmp_path):
    """A8：api_key 值不进 tree.jsonl / state.jsonl / core.jsonl / 快照序列化结果。"""
    secret = "sk-a8-credential-boundary"
    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    (tmp_path / "providers.yaml").write_text(textwrap.dedent(f"""\
        realentry:
          adapter: deepseek
          base_url: https://api.deepseek.com
          api_key: {secret}
        """), encoding="utf-8")
    runtime.set_providers("@/providers.yaml")
    real = runtime.provider_registry.get("realentry")   # 实例化（凭证在 config 内）
    assert real.get_credential() == secret
    # 正常跑一个回合 + 写 state + provide 一个假敏感值
    script_provider(provider, text_response("干净内容"))
    agent = await runtime.create_agent("test-agent")
    await agent.query("hi")
    agent.state["note"] = "业务状态"
    runtime.provide("app_secret", secret)   # provide 边界约定：凭证禁止进入——此处验证不泄漏到持久化/快照
    await agent._tree_store.drain()
    await agent._state_bag._store.drain()
    for store_file in runtime._persist_dir.rglob("*.jsonl"):
        assert secret not in store_file.read_text(encoding="utf-8"), store_file
    snap_blob = json.dumps(dataclasses.asdict(runtime.snapshot()), default=str)
    assert secret not in snap_blob
    agent_blob = json.dumps(dataclasses.asdict(agent.snapshot()), default=str)
    assert secret not in agent_blob
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# 双袋结构（D1）与管线重排（D2/D12）
# ---------------------------------------------------------------------------


async def test_dual_bags_structure(tmp_path):
    """D1：core/default 双袋建立、独立文件；agent.state = default 袋；
    child_ids 登记在 core 袋（default 袋不含核心键）。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent")
    # 双袋文件路径独立（FileRecordStore 惰性建文件——写透后实体文件才出现）
    assert agent._core_state._store._path.name == "core.jsonl"
    assert agent._state_bag._store._path.name == "state.jsonl"
    assert agent.state is agent._state_bag   # state property = default 袋
    assert agent._core_state is not agent._state_bag
    # child_ids 在 core 袋（defaults 表登记），default 袋无核心键
    assert "child_ids" in agent._core_state
    assert agent._core_state["child_ids"] == {}
    assert "child_ids" not in agent._state_bag
    await runtime.shutdown()


async def test_meta_json_written_before_setup(tmp_path):
    """D2/D12：身份四键整写 meta.json——before_create 时已可读、setup 前
    完成；身份键不进状态袋（state.jsonl 无 agent_type 行）。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    seen: dict = {}

    class MetaAgent(Agent):
        system_prompt = Parsable("身份写盘。")

        @on("before_create")
        def _peek(self, kwargs):
            meta = json.loads(
                (self._session_dir / "meta.json").read_text(encoding="utf-8"))
            seen["meta"] = meta
            return kwargs

        async def setup(self, **kwargs):
            pass

    runtime.register_agent_type(MetaAgent, name="meta-agent")
    agent = await runtime.create_agent("meta-agent", order_id="x")
    assert set(seen["meta"]) == {"agent_type", "parent_agent_id", "created_at", "args"}
    assert seen["meta"]["agent_type"] == "meta-agent"
    assert seen["meta"]["parent_agent_id"] == runtime.node_id
    assert seen["meta"]["args"]["order_id"] == "x"
    agent.state["probe"] = 1   # 触发 state.jsonl 实体化（惰性建文件）
    await agent._state_bag._store.drain()
    text = (agent._session_dir / "state.jsonl").read_text(encoding="utf-8")
    assert '"agent_type"' not in text
    await runtime.shutdown()


async def test_recover_restore_before_setup(tmp_path):
    """D2：recover 管线 restore 先于 setup——setup 中读 state 见持久值。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    seen: list = []

    class RestoreFirstAgent(Agent):
        system_prompt = Parsable("恢复时序。")

        async def setup(self, **kwargs):
            seen.append(("setup", self.state.get("n")))

    runtime.register_agent_type(RestoreFirstAgent, name="restore-first")
    agent = await runtime.create_agent("restore-first")
    agent.state.n = 5
    await agent.destroy()
    recovered = await runtime.recover_agent(agent.node_id)
    assert seen[0] == ("setup", None)   # create 的 setup：无持久值
    assert seen[1] == ("setup", 5)      # recover 的 setup：重放已完成（D2 重排）
    assert recovered.state.n == 5
    await runtime.shutdown()


async def test_pool_scan_meta_missing_fails(tmp_path):
    """决策 7：meta.json 缺失即失败（池扫描抛 FileNotFoundError），
    不回退读旧 state.jsonl——历史 session 无效。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent")
    await agent.destroy()
    (agent._session_dir / "meta.json").unlink()   # 模拟历史 session（无 meta.json）
    # 新 Runtime 同持久化根：构造时固化 persist_dir → 池扫描 →
    # 身份读取失败（fail fast）
    with pytest.raises(FileNotFoundError):
        make_runtime(tmp_path / "p2", persist=False, models=False,
                     persist_dir=tmp_path / ".flowing")


async def test_head_persisted_and_recovered_bag_authority(tmp_path):
    """D7/决策 9：current_head_id 落盘 core 袋；fork 切换 head 后恢复以袋为
    准（树末条 ≠ head 的典型场景——fork 后未加新消息）；property getter-only
    （S10）：用户写 → AttributeError。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent")
    agent.push(Message(id="m1", kind=MessageKind.USER,
                       content=[TextBlock(text="一")]))
    agent.push(Message(id="m2", kind=MessageKind.USER,
                       content=[TextBlock(text="二")]))
    await agent.fork("m1")   # head 切到历史消息（树末条是 m2）
    assert agent.current_head_id == "m1"
    await agent.destroy()
    recovered = await runtime.recover_agent(agent.node_id)
    assert recovered.current_head_id == "m1"   # 袋为准：fork 目标，非树末条 m2
    # getter-only（S10）：用户写核心量 → AttributeError
    with pytest.raises(AttributeError):
        recovered.current_head_id = "m2"
    with pytest.raises(AttributeError):
        recovered.child_ids = {}
    await runtime.shutdown()
