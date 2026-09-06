"""阶段 2：runtime 主体测试（T93–T113、T40/T41、T124）。

覆盖：launch/resolve/@ 上下文（T93–T96）、插件与依赖校验（T97/T98）、
配置链与命名空间（T99/T100）、Resource（T101）、provide/inject（T102）、
全局状态（T103）、路径族（T104/T105）、set_providers（T106）、create/recover
管线（T107–T113 + 补做 T40/T41）、get_agent_class 三形态（T124）。

真 Runtime 经 ``make_runtime``（conftest，contextvar 直连）构造；``launch``
本体由 T93/T94 端到端覆盖。
"""

from __future__ import annotations

import asyncio
import textwrap
import uuid
from pathlib import Path

import pytest

from flowing import launch, on
from flowing.agent import Agent
from flowing.errors import (
    ConfigNamespaceConflictError,
    ConfigNotReadyError,
    DependencyError,
    FormatError,
    MissingFieldError,
    MissingProvideError,
    ResourceNameConflictError,
    ResourceNotFoundError,
    UnknownHookPointError,
)
from flowing.parsable import PENDING, Parsable
from flowing.runtime import Runtime, resolve

from conftest import (
    PluginStub,
    SimpleAgent,
    add_fake_provider,
    make_runtime,
    script_provider,
    text_response,
)


# ---------------------------------------------------------------------------
# 工具 Agent 类
# ---------------------------------------------------------------------------


class RewriteAgent(Agent):
    """T107：before_create 改写 kwargs。"""

    system_prompt = Parsable("改写钩子 agent。")

    @on("before_create")
    def _rewrite(self, kwargs):
        kwargs["order_id"] = "rewritten"
        return kwargs

    async def setup(self, order_id: str = "", **kwargs) -> None:
        self.order_id = order_id


class PendingAgent(Agent):
    """T40：.fya 式 PENDING 延迟定义（``field: _`` 的解析结果形态，裸哨兵）。"""

    system_prompt = PENDING

    fired_after_create: list = []

    @on("after_create")
    def _observe(self, _value):
        type(self).fired_after_create.append(1)

    async def setup(self, **kwargs) -> None:
        pass


class PendingWrappedAgent(Agent):
    """T40 补：PENDING 的 Parsable 包裹形态（哨兵在 ``source`` 位）。"""

    extra_field = Parsable(PENDING)

    async def setup(self, **kwargs) -> None:
        pass


class BadHookAgent(Agent):
    """T110：@on 暂记指向不存在的钩子点（setup 结束前无人 declare）。"""

    system_prompt = Parsable("坏钩子 agent。")

    @on("no_such_hook_point")
    def _bad(self):
        pass

    async def setup(self, **kwargs) -> None:
        pass


class CountingAgent(Agent):
    """T41：before_turn 内累加持久化计数（重复注册会被计数器暴露）。"""

    system_prompt = Parsable("计数 agent。")

    @on("before_turn")
    def _count(self, turn):
        self.state.turns = self.state.turns + 1
        return turn

    async def setup(self, **kwargs) -> None:
        self.state.register("turns", 0)


class HookLogAgent(Agent):
    """T112：记录 create/recover 两对钩子的触发次序。"""

    system_prompt = Parsable("钩子日志 agent。")
    events: list = []

    @on("before_create")
    def _bc(self, kwargs):
        type(self).events.append("before_create")
        return kwargs

    @on("after_create")
    def _ac(self, _value):
        type(self).events.append("after_create")

    @on("before_recover")
    def _br(self, args):
        type(self).events.append("before_recover")
        return args

    @on("after_recover")
    def _ar(self, _value):
        type(self).events.append("after_recover")

    async def setup(self, **kwargs) -> None:
        pass


# ---------------------------------------------------------------------------
# T93–T96：launch / resolve / @ 上下文
# ---------------------------------------------------------------------------


async def test_t93_launch_and_early_resolve(tmp_path):
    """T93：合法 main.py 子项目 launch 返回 Runtime；resolve 在 Runtime() 前可用。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text(textwrap.dedent("""\
        import flowing
        from flowing import Runtime

        async def main(**kwargs):
            resolved = flowing.resolve("@/config.yaml")   # Runtime() 构造之前也可用
            runtime = Runtime()
            runtime.provide("_t93_resolved", resolved)
            return runtime
        """), encoding="utf-8")
    runtime = await launch(proj)
    assert runtime.project_root == proj.resolve()
    assert runtime.inject("_t93_resolved") == proj.resolve() / "config.yaml"
    await runtime.shutdown()


async def test_t94_concurrent_launches_isolated(tmp_path):
    """T94：两个并发 Task 各自 launch 不同子项目，project_root 互不串扰。"""
    projects = []
    for i in (1, 2):
        proj = tmp_path / f"proj{i}"
        proj.mkdir()
        (proj / "main.py").write_text(textwrap.dedent("""\
            import asyncio
            from flowing import Runtime

            async def main(**kwargs):
                await asyncio.sleep(0.01)   # 让出 loop，制造真实并发交错
                return Runtime()
            """), encoding="utf-8")
        projects.append(proj)
    rt1, rt2 = await asyncio.gather(*(launch(p) for p in projects))
    assert rt1.project_root == projects[0].resolve()
    assert rt2.project_root == projects[1].resolve()
    await rt1.shutdown()
    await rt2.shutdown()


async def test_t95_no_launch_context_raises():
    """T95：无 launch 上下文 resolve() 与裸 Runtime() 均抛 RuntimeError。"""
    with pytest.raises(RuntimeError):
        resolve("@/x")
    with pytest.raises(RuntimeError):
        Runtime()


async def test_t96_init_lazy_zero_providers(tmp_path):
    """T96：launch 上下文内 Runtime() 成功，provider 实例缓存为空（懒创建零启动）。"""
    runtime = make_runtime(tmp_path)
    assert runtime.provider_registry._instances == {}
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# T97–T99：插件与依赖校验、配置命名空间
# ---------------------------------------------------------------------------


async def test_t97_use_dependency_warn_and_cycle(tmp_path):
    """T97：依赖缺失警告不抛；成环在引入环的那次 install() 抛 DependencyError。"""
    runtime = make_runtime(tmp_path)
    with pytest.warns(UserWarning, match="missing plugin dependency"):
        runtime.install(PluginStub("a", dependencies=["b"]))
    with pytest.raises(DependencyError):
        runtime.install(PluginStub("b", dependencies=["a"]))   # a↔b 成环
    await runtime.shutdown()


async def test_t98_get_plugin_strict_forms(tmp_path):
    """T98：get_plugin 已装命中实例；未装默认 KeyError、strict=False 返回 None。"""
    runtime = make_runtime(tmp_path)
    plugin = PluginStub("cron")
    runtime.install(plugin)
    assert runtime.get_plugin("cron") is plugin
    with pytest.raises(KeyError):
        runtime.get_plugin("missing")
    assert runtime.get_plugin("missing", strict=False) is None
    # 重复安装同名插件 → 后安装者报错
    with pytest.raises(ValueError, match="installed twice"):
        runtime.install(PluginStub("cron"))
    await runtime.shutdown()


async def test_t99_config_namespace(tmp_path):
    """T99：两插件注册同一命名空间 → 第二个冲突报错；未注册命名空间可读。"""
    (tmp_path / "config.yaml").write_text("foo:\n  bar: 7\n", encoding="utf-8")
    runtime = make_runtime(tmp_path)
    runtime.install(PluginStub("p1", on_install=lambda rt: rt.register_config_namespace("i18n", object())))
    with pytest.raises(ConfigNamespaceConflictError):
        runtime.install(PluginStub("p2", on_install=lambda rt: rt.register_config_namespace("i18n", object())))
    # config 含未注册命名空间：不报错、可读
    assert runtime.get_config("foo.bar") == 7
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# T100–T103：配置链 / Resource / provide-inject / 全局状态
# ---------------------------------------------------------------------------


async def test_t100_config_priority_chain(tmp_path, monkeypatch):
    """T100：用户级 > 项目级 > 框架默认；set_config 覆盖层最优先；未就绪抛错。"""
    (tmp_path / "config.yaml").write_text(
        "agent:\n  timeout: 60\n  max_turns: 5\n", encoding="utf-8")
    user_home = tmp_path / "userhome"
    user_home.mkdir()
    (user_home / "config.yaml").write_text("agent:\n  timeout: 120\n", encoding="utf-8")
    monkeypatch.setenv("FLOWING_CONFIG_HOME", str(user_home))
    runtime = make_runtime(tmp_path)
    assert runtime.get_config("agent.timeout") == 120   # 用户级压项目级
    assert runtime.get_config("agent.max_turns") == 5   # 未覆盖沿用项目级
    assert runtime.get_config("agent.max_depth") == 10  # 均未覆盖用框架默认
    runtime.set_config("agent.timeout", 7)
    assert runtime.get_config("agent.timeout") == 7   # 覆盖层最优先
    # 未就绪闸（模拟模块顶层 / __init__ 完成前的调用时机）
    runtime._config_ready = False
    with pytest.raises(ConfigNotReadyError):
        runtime.get_config("agent.timeout")
    runtime._config_ready = True
    await runtime.shutdown()


async def test_t101_resources(tmp_path):
    """T101：register/get Resource 往返；未注册 ResourceNotFoundError；重名冲突。"""
    runtime = make_runtime(tmp_path)
    pool = object()
    runtime.register_resource("db", pool)
    assert runtime.get_resource("db") is pool
    with pytest.raises(ResourceNotFoundError):
        runtime.get_resource("ghost")
    with pytest.raises(ResourceNameConflictError):
        runtime.register_resource("db", pool)
    await runtime.shutdown()


async def test_t102_provide_inject_chain(tmp_path):
    """T102：链终点兜底 / 覆盖即刻可见 / 祖先穿透中间层 / 链底无则 MissingProvideError。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.provide("k", 1)
    agent = await runtime.create_agent("test-agent")
    assert agent.inject("k") == 1   # 链终点兜底
    runtime.provide("k", 2)
    assert agent.inject("k") == 2   # 覆盖更新即刻可见
    # 祖先 provide 穿透中间层命中
    child = await agent.create_subagent("test-agent", name="c")
    grandchild = await child.create_subagent("test-agent", name="g")
    runtime.provide("deep", "root-value")
    assert grandchild.inject("deep") == "root-value"
    agent.provide("mid", "parent-value")
    assert child.inject("mid") == "parent-value"
    assert grandchild.inject("mid") == "parent-value"
    with pytest.raises(MissingProvideError):
        grandchild.inject("nope")
    with pytest.raises(MissingProvideError):
        runtime.inject("nope")
    await runtime.shutdown()


async def test_t102b_provide_inject_injection_key(tmp_path):
    """T102 补：provide/inject 以 InjectionKey 直传——str(key) 归一落键名，
    子 Agent 经 InjectionKey 与裸名双通道命中同一槽位。"""
    from flowing.params import InjectionKey

    key: InjectionKey[str] = InjectionKey("i18n.locale")
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.provide(key, "zh")   # InjectionKey 直传（runtime.provide 的 str(key) 归一）
    agent = await runtime.create_agent("test-agent")
    assert runtime._provided["i18n.locale"] == "zh"   # 槽位 key 恒为键名字符串
    assert agent.inject(key) == "zh"        # InjectionKey 通道
    assert agent.inject("i18n.locale") == "zh"   # 裸名通道（同一槽位）
    assert runtime.inject(key) == "zh"
    await runtime.shutdown()


async def test_t103_global_state(tmp_path):
    """T103：全局状态写透 + 进程重启重放；开启即 replay（D13）；幂等同视图；
    default 袋（runtime.state）。"""
    persist = tmp_path / "persist"
    runtime = make_runtime(tmp_path, persist=False, persist_dir=persist)
    runtime.register_state("x")   # 创建即 replay（开空间即恢复）
    runtime.states["x"].k = 1
    # 幂等：同命名空间重复开启返回同一视图（无定义可比——defaults 已消除）
    assert runtime.register_state("x") is runtime.states["x"]
    await runtime.shutdown()
    # 进程重启（新 Runtime 同 persist_dir）：开启即 replay，持久值立即可见
    runtime2 = make_runtime(tmp_path, persist=False, persist_dir=persist)
    runtime2.register_state("x")
    assert runtime2.states["x"].k == 1
    # Mapping 语义（S13）：["ns"] / in / .get()
    assert "x" in runtime2.states
    assert "nope" not in runtime2.states
    assert runtime2.states.get("nope") is None
    # backend 校验（D3 同签名）
    with pytest.raises(ValueError):
        runtime2.register_state("y", backend="memory")
    await runtime2.shutdown()
    # default 袋（D13）：runtime.state = states["default"]，与 agent.state 对称
    assert runtime2.state is runtime2.states["default"]
    # install 中开启命名空间 → 创建即 replay（持久值立即可见）
    runtime3 = make_runtime(tmp_path / "p3")

    def _install_writes(rt: Runtime) -> None:
        rt.register_state("gw")
        rt.states["gw"].k = 1   # 开启即 replay，写透立即可用（D5/D13）

    runtime3.install(PluginStub("gate-writer", on_install=_install_writes))
    assert runtime3.states["gw"].k == 1
    await runtime3.shutdown()


# ---------------------------------------------------------------------------
# T104–T106：路径族 / set_providers
# ---------------------------------------------------------------------------


async def test_t104_resolve_path(tmp_path):
    """T104：@/ 锚定项目根；./ 缺 source_dir 报错；../../ 越根合法保留绝对路径。"""
    runtime = make_runtime(tmp_path)
    assert runtime.resolve_path("@/a/b") == tmp_path.resolve() / "a/b"
    with pytest.raises(ValueError):
        runtime.resolve_path("./x")
    deep = tmp_path / "a" / "b"
    assert runtime.resolve_path("../../x", source_dir=deep) == tmp_path.resolve() / "x"
    escaped = runtime.resolve_path("../../../x", source_dir=deep)
    assert escaped == tmp_path.resolve().parent / "x"   # 越根合法
    await runtime.shutdown()


async def test_t105_to_project_path(tmp_path):
    """T105：根内归一化为 @/ 形式；根外保留绝对路径。"""
    runtime = make_runtime(tmp_path)
    assert runtime.to_project_path(tmp_path.resolve() / "a") == "@/a"
    assert runtime.to_project_path(Path("/etc/x")) == "/etc/x"
    await runtime.shutdown()


async def test_t106_set_providers_rebuilds(tmp_path):
    """T106：set_providers 重建候选清单，新条目可按名懒取。"""
    runtime = make_runtime(tmp_path)
    (tmp_path / "providers.yaml").write_text(textwrap.dedent("""\
        myentry:
          adapter: deepseek
          base_url: https://api.deepseek.com
          api_key: sk-dummy
        """), encoding="utf-8")
    runtime.set_providers("@/providers.yaml")
    assert "myentry" in runtime.provider_registry._candidates
    provider = runtime.provider_registry.get("myentry")   # 懒取实例化
    assert provider.config["api_key"] == "sk-dummy"
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# T107–T113 + T40/T41：create / recover 管线
# ---------------------------------------------------------------------------


async def test_t107_before_create_rewrites_kwargs(tmp_path):
    """T107：before_create 改写 kwargs → setup 收到改写值、池元数据 args 同步。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.register_agent_type("rewrite-agent", RewriteAgent)
    agent = await runtime.create_agent("rewrite-agent", order_id="orig")
    assert agent.order_id == "rewritten"
    assert runtime._agent_pool[agent.node_id]["args"]["order_id"] == "rewritten"
    await runtime.shutdown()


async def test_t108_agent_id_allocation(tmp_path):
    """T108：自动 id 带 agent- 前缀且唯一；指定 id 成功；重复 id ValueError 无注册。"""
    runtime = make_runtime(tmp_path)
    a1 = await runtime.create_agent("test-agent")
    a2 = await runtime.create_agent("test-agent")
    assert a1.node_id.startswith("agent-") and a2.node_id.startswith("agent-")
    assert a1.node_id != a2.node_id
    a3 = await runtime.create_agent("test-agent", agent_id="agent-xxx")
    assert a3.node_id == "agent-xxx"
    pool_before = dict(runtime._agent_pool)
    nodes_before = set(runtime._nodes)
    with pytest.raises(ValueError, match="already exists"):
        await runtime.create_agent("test-agent", agent_id="agent-xxx")
    assert runtime._agent_pool == pool_before   # 无任何注册
    assert set(runtime._nodes) == nodes_before
    await runtime.shutdown()


async def test_t109_session_dir_existence_check(tmp_path, monkeypatch):
    """T109：指定 id 撞已存在目录（archive 留档态）→ FileExistsError；
    自动 id 撞目录不报错重新生成。"""
    runtime = make_runtime(tmp_path)
    archived_dir = runtime._persist_dir / "agent-archived"
    archived_dir.mkdir(parents=True)   # 名录无 id + 目录存在 = 归档留档态
    with pytest.raises(FileExistsError, match="archive"):
        await runtime.create_agent("test-agent", agent_id="agent-archived")
    assert "agent-archived" not in runtime._agent_pool
    assert "agent-archived" not in runtime._nodes
    # 自动生成 id 撞目录 → 防御性重新生成（create_agent 内 from uuid import
    # uuid4 是调用期绑定，monkeypatch uuid.uuid4 生效）
    (runtime._persist_dir / "agent-collision").mkdir()
    real_uuid4 = uuid.uuid4
    pending = ["collision"]

    def fake_uuid4():
        return pending.pop(0) if pending else real_uuid4()

    monkeypatch.setattr(uuid, "uuid4", fake_uuid4)
    agent = await runtime.create_agent("test-agent")
    assert agent.node_id != "agent-collision"
    assert agent.node_id.startswith("agent-")
    await runtime.shutdown()


async def test_t40_pending_checkpoint(tmp_path):
    """T40（补做）：PENDING 未兑现 → 管线第 6 步 MissingFieldError；
    after_create 不触发、_nodes/池无半注册。"""
    PendingAgent.fired_after_create = []
    runtime = make_runtime(tmp_path)
    runtime.register_agent_type("pending-agent", PendingAgent)
    runtime.register_agent_type("pending-wrapped", PendingWrappedAgent)
    nodes_before = set(runtime._nodes)
    with pytest.raises(MissingFieldError) as exc_info:
        await runtime.create_agent("pending-agent")
    # 裸哨兵形态：MissingFieldError 为单字段结构（field / agent_type）
    assert exc_info.value.field == "system_prompt"
    assert exc_info.value.agent_type == "pending-agent"
    # Parsable 包裹形态（哨兵在 source 位）同样被检查器识别
    with pytest.raises(MissingFieldError) as exc_info2:
        await runtime.create_agent("pending-wrapped")
    assert exc_info2.value.field == "extra_field"
    assert PendingAgent.fired_after_create == []   # after_create 不触发
    assert set(runtime._nodes) == nodes_before   # 无半注册
    assert len(runtime._agent_pool) == 0
    await runtime.shutdown()


async def test_t41_recover_reruns_setup_idempotent(tmp_path):
    """T41（补做）：recover 在新实例上重跑 setup → 钩子/handler 不重复注册，
    行为一致（计数器逐回合 +1 而非 +2），持久化 state 延续。"""
    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    runtime.register_agent_type("counting-agent", CountingAgent)
    script_provider(provider, text_response("r1"), text_response("r2"))
    agent = await runtime.create_agent("counting-agent")
    r1 = await agent.query("一")
    assert r1.status == "completed"
    assert agent.state.turns == 1
    await agent.destroy()
    recovered = await runtime.recover_agent(agent.node_id)
    assert len(list(recovered.hooks._hook_points["before_turn"])) == 1   # 幂等不重复
    assert recovered.state.turns == 1   # 落盘值覆盖 default
    r2 = await recovered.query("二")
    assert r2.status == "completed"
    assert recovered.state.turns == 2   # 行为一致：恰好 +1（双注册会变 +2）
    await runtime.shutdown()


async def test_t110_unknown_hook_point(tmp_path):
    """T110：@on 暂记未结算 → create 第 6 步 UnknownHookPointError，
    消息列出 hook_name 与方法名。"""
    runtime = make_runtime(tmp_path)
    runtime.register_agent_type("bad-hook", BadHookAgent)
    with pytest.raises(UnknownHookPointError) as exc_info:
        await runtime.create_agent("bad-hook")
    assert "no_such_hook_point" in str(exc_info.value)
    assert "_bad" in str(exc_info.value)
    assert len(runtime._agent_pool) == 0
    await runtime.shutdown()


async def test_t111_recover_args_and_override(tmp_path):
    """T111：recover 默认透传持久化 args；override_args 覆盖。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent", agent_id="agent-ord", order_id="123")
    assert agent.order_id == "123"
    await agent.destroy()
    recovered = await runtime.recover_agent("agent-ord")
    assert recovered.order_id == "123"   # 持久化 args 透传
    await recovered.destroy()
    overridden = await runtime.recover_agent("agent-ord", order_id="789")
    assert overridden.order_id == "789"   # override 覆盖持久化值
    with pytest.raises(KeyError):
        await runtime.recover_agent("agent-ghost")   # 不在池即 KeyError
    await runtime.shutdown()


async def test_t112_recover_hook_pairs_not_mixed(tmp_path):
    """T112：recover 不触发 before/after_create，触发 before/after_recover。"""
    HookLogAgent.events = []
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.register_agent_type("hook-log", HookLogAgent)
    agent = await runtime.create_agent("hook-log")
    assert HookLogAgent.events == ["before_create", "after_create"]
    await agent.destroy()
    await runtime.recover_agent(agent.node_id)
    assert HookLogAgent.events == [
        "before_create", "after_create", "before_recover", "after_recover"]
    await runtime.shutdown()


async def test_t113_recover_parent_chain(tmp_path):
    """T113：亲节点在池不在 _nodes → 逐级向上恢复到 Runtime 止；亲节点悬空 → 孤儿警告不抛。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    parent = await runtime.create_agent("test-agent")
    child = await parent.create_subagent("test-agent", name="c")
    await parent.destroy()   # 级联销毁：亲子实例均丢、池 key 保留
    assert parent.node_id not in runtime._nodes
    assert child.node_id not in runtime._nodes
    recovered = await runtime.recover_agent(child.node_id)
    assert parent.node_id in runtime._nodes   # 亲代链逐级恢复（R14）
    assert recovered._parent_id == parent.node_id
    # 亲节点悬空（不在 _nodes 也不在池、非 runtime-0）→ 孤儿警告，继续恢复本节点
    ghost_child = await runtime.create_agent("test-agent", parent_id="ghost-parent")
    await ghost_child.destroy()
    with pytest.warns(UserWarning, match="dangling"):
        orphan = await runtime.recover_agent(ghost_child.node_id)
    assert orphan.node_id == ghost_child.node_id
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# T124：get_agent_class 三形态（R-03 本期最小链）
# ---------------------------------------------------------------------------


class PayAgent(Agent):
    system_prompt = Parsable("支付 agent。")

    async def setup(self, **kwargs) -> None:
        pass


class OtherAgent(Agent):
    system_prompt = Parsable("另一个 agent。")

    async def setup(self, **kwargs) -> None:
        pass


async def test_t124_get_agent_class_registry_forms(tmp_path):
    """T124：限定名只查注册表；裸名 default:: 优先 builtin::；不命中 KeyError。"""
    runtime = make_runtime(tmp_path)
    runtime.register_agent_type("payment-agent", PayAgent, namespace="myplugin")
    assert runtime.get_agent_class("myplugin::payment-agent") is PayAgent
    with pytest.raises(KeyError):
        runtime.get_agent_class("myplugin::ghost")   # 限定名不走文件查找链
    # 裸名视图：default 优先 builtin（插件覆盖原生行为的通道）
    runtime.register_agent_type("x-agent", PayAgent, namespace="builtin")
    runtime.register_agent_type("x-agent", OtherAgent)   # 缺省 default::
    assert runtime.get_agent_class("x-agent") is OtherAgent
    assert runtime.get_agent_class("builtin::x-agent") is PayAgent
    assert OtherAgent.registry_key == "default::x-agent"
    with pytest.raises(KeyError):
        runtime.get_agent_class("ghost")
    await runtime.shutdown()


async def test_t124b_get_agent_class_py_path_form(tmp_path):
    """T124 补（R-03 路径形态）：手写 .py 恰好一个子类 / ::ClassName 消歧 /
    零子类 FormatError / .fya 编译装配（阶段 3 接缝已接通）/ 不命中 KeyError。"""
    runtime = make_runtime(tmp_path)
    (tmp_path / "solo_agent.py").write_text(textwrap.dedent("""\
        from flowing import Agent
        from flowing.parsable import Parsable

        class SoloAgent(Agent):
            system_prompt = Parsable("solo")

            async def setup(self, **kwargs):
                pass
        """), encoding="utf-8")
    cls = runtime.get_agent_class("@/solo_agent.py")
    assert cls.__name__ == "SoloAgent"
    assert cls.registry_key == "@/::solo-agent"   # 目录派生键（@/ 下根相对）
    assert runtime.get_agent_class("@/solo_agent.py") is cls   # 派生键短路复用
    # 裸名 + source_dir：文件链覆盖注册表
    assert runtime.get_agent_class("solo-agent", source_dir=tmp_path) is cls
    # 多子类 → FormatError 指引消歧；路径::ClassName 形态绕开限制
    (tmp_path / "multi_agent.py").write_text(textwrap.dedent("""\
        from flowing import Agent
        from flowing.parsable import Parsable

        class FirstAgent(Agent):
            system_prompt = Parsable("一")
            async def setup(self, **kwargs):
                pass

        class SecondAgent(Agent):
            system_prompt = Parsable("二")
            async def setup(self, **kwargs):
                pass
        """), encoding="utf-8")
    with pytest.raises(FormatError, match="ClassName"):
        runtime.get_agent_class("@/multi_agent.py")
    picked = runtime.get_agent_class("@/multi_agent.py::SecondAgent")
    assert picked.__name__ == "SecondAgent"
    # 零子类 → FormatError
    (tmp_path / "empty_agent.py").write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(FormatError, match="no Agent subclass"):
        runtime.get_agent_class("@/empty_agent.py")
    # .fya 命中 → 编译装配（阶段 3 接缝已接通：compile_fya_class 现场合成）
    (tmp_path / "x.fya").write_text("", encoding="utf-8")
    x_cls = runtime.get_agent_class("@/x.fya")
    assert issubclass(x_cls, Agent) and x_cls.__name__ == "XAgent"
    assert x_cls.registry_key == "@/::x"   # 派生键回写（与 .py 通道同构）
    assert runtime.get_agent_class("x", source_dir=tmp_path) is x_cls   # 裸名文件链经派生键短路复用
    # 目录形态：存在但无候选 → KeyError；含 .fya 候选 → 编译装配
    (tmp_path / "emptydir").mkdir()
    with pytest.raises(KeyError):
        runtime.get_agent_class("@/emptydir")
    agent_dir = tmp_path / "myagent"
    agent_dir.mkdir()
    (agent_dir / "agent.fya").write_text("", encoding="utf-8")
    my_cls = runtime.get_agent_class("@/myagent")
    assert issubclass(my_cls, Agent) and my_cls.__name__ == "MyagentAgent"
    # 不存在的路径 → KeyError
    with pytest.raises(KeyError):
        runtime.get_agent_class("@/missing.py")
    await runtime.shutdown()
