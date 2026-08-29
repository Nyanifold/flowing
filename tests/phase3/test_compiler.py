"""阶段 3 compiler 测试（W26–W28）：测试清单 57–65 + 验收 1 的 fya 全链 e2e。

覆盖 ``_merge_named_blocks`` 四条导航规则（57）、``compile_fya_file``
幂等与 hash 闸（58/59/60/62）、``compile_project``（61）、
``compile_fya_class`` 内存合成（63，含 args 推导旁路）、``文件::类名``
消歧（64，真实 Runtime）、skill 字面解析（65）、runtime 两处 ``.fya``
接缝接通后的端到端回合（验收标准 1）。

落盘测试一律复制 fixture 到 ``tmp_path`` 再操作（边界：不向 fixtures
目录写产物）。真 Runtime 助手按路径载入 phase2 conftest（避免新建
phase3/conftest.py 的顶级模块名遮蔽问题，见批次 3 笔记）。
"""

from __future__ import annotations

import importlib.util
import inspect
import shutil
import textwrap
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from flowing.agent import Agent
from flowing.compiler import (
    COMPILER_VERSION,
    _build,
    _merge_named_blocks,
    compile_fya_class,
    compile_fya_file,
    compile_project,
)
from flowing.errors import ArtifactModifiedError, FormatError, NameMismatchError
from flowing.parser import EntryRef, parse_fya
from flowing.parsable import PENDING, Parsable
from flowing.runtime import AGENT_NAMING

# 复用阶段 2 的真 Runtime 测试助手；按路径载入避免新增 phase3/conftest.py
# （会以顶级模块名 ``conftest`` 入 sys.modules，遮蔽 phase2 的裸导入）。
_PHASE2_CONFTEST = Path(__file__).parent.parent / "phase2" / "conftest.py"
_spec = importlib.util.spec_from_file_location("phase2_conftest", _PHASE2_CONFTEST)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
make_runtime = _mod.make_runtime
add_fake_provider = _mod.add_fake_provider
script_provider = _mod.script_provider
text_response = _mod.text_response

FIXTURES = Path(__file__).parent.parent / "fixtures" / "fya"


# ---------------------------------------------------------------------------
# 清单 57：多层具名块导航四规则（落点 = compiler 装配层 _merge_named_blocks）
# ---------------------------------------------------------------------------

def test_t57_fixture_merge_navigation():
    """57（fixture 全件）：order-agent 的深层块命中 as 键别名段、PENDING 槽物化、
    顶层块末端写入。"""
    text = (FIXTURES / "agents" / "order-agent" / "agent.fya").read_text(encoding="utf-8")
    doc = parse_fya(text, entry_fields={"tools", "subagents"}, naming=AGENT_NAMING)
    # 块未填回是 parser 层契约（清单 1）；装配层填回后：
    _merge_named_blocks(doc.fields, doc.blocks)
    pay_body = doc.fields["tools"][0].body
    # `$tools.pay.args.cwd.description:` 命中 `working_dir as cwd`（as 键仅以
    # 别名段寻址）；其 PENDING 空补丁物化为空映射后写入末端
    assert pay_body["args"]["working_dir as cwd"] == {"description": "本订单的工作目录"}
    # 顶层块：末端缺失 → 写入；`description: _`（PENDING）→ 块替换
    assert doc.fields["system_prompt"].startswith("你是订单处理助手")
    assert doc.fields["description"] == "处理电商订单的查询、支付与物流跟踪。"


def _fields_with_pay() -> dict:
    """单条 tools 条目的最小 fields（别名 pay，args 含 as 键与 specified 固定值）。"""
    return {
        "tools": [
            EntryRef(raw="payment", alias="pay", body={
                "args": {"working_dir as cwd": PENDING, "currency": "USD"}}),
        ],
    }


def test_t57_list_segment_alias_miss():
    """57 规则 1：列表段按别名精确匹配，未命中 → FormatError。"""
    with pytest.raises(FormatError, match="未命中任何条目别名"):
        _merge_named_blocks(_fields_with_pay(), {"tools.nope.description": "x"})


def test_t57_as_key_only_alias_addressable():
    """57 规则 2：含 as 的键仅以别名段寻址——规范名段不命中（中间段缺失）。"""
    with pytest.raises(FormatError, match="中间段"):
        _merge_named_blocks(
            _fields_with_pay(), {"tools.pay.args.working_dir.description": "x"})
    # 对照：别名段命中
    fields = _fields_with_pay()
    _merge_named_blocks(fields, {"tools.pay.args.cwd.description": "本目录\n"})
    assert fields["tools"][0].body["args"]["working_dir as cwd"] == {"description": "本目录"}


def test_t57_pending_terminal_write_and_conflict():
    """57 规则 3/4：末端 PENDING → 写入；末端已有实际值 → 冲突 FormatError。"""
    fields = _fields_with_pay()
    _merge_named_blocks(fields, {"tools.pay.args.cwd": "/srv/order\n"})
    assert fields["tools"][0].body["args"]["working_dir as cwd"] == "/srv/order"
    with pytest.raises(FormatError, match="冲突"):
        _merge_named_blocks(_fields_with_pay(), {"tools.pay.args.currency": "CNY"})


def test_t57_terminal_on_entry_alias_rejected():
    """57 边界：路径在条目别名处耗尽（块不能整体替换条目）→ FormatError。"""
    with pytest.raises(FormatError, match="末端落在条目"):
        _merge_named_blocks(_fields_with_pay(), {"tools.pay": "x"})


# ---------------------------------------------------------------------------
# 清单 58–62：compile_fya_file / compile_project（幂等与 hash 闸）
# ---------------------------------------------------------------------------

def _copy_payment(tmp_path: Path) -> Path:
    dst = tmp_path / "payment.fya"
    shutil.copy(FIXTURES / "agents" / "payment.fya", dst)
    return dst


@pytest.fixture
def write_spy(monkeypatch):
    """``Path.write_text`` 写盘间谍：本环境文件系统 mtime 粒度粗（同刻写入
    mtime_ns 不可区分），「no-op = 未重写」一律以写盘调用计数断言。"""
    writes: list[Path] = []
    orig = Path.write_text

    def _spy(self: Path, *args, **kwargs):
        writes.append(self)
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _spy)
    return writes


def test_t58_compile_file_and_idempotent(tmp_path, write_spy):
    """58：首编译产 .py + meta 三字段；二次编译 no-op（同路径、不再写盘）。"""
    fya = _copy_payment(tmp_path)
    product = compile_fya_file(fya)
    assert product == tmp_path / "payment.py" and product.exists()
    meta = YAML(typ="safe").load((tmp_path / ".flowing.meta.yaml").read_text(encoding="utf-8"))
    entry = meta["payment.fya"]
    assert entry["fya_hash"].startswith("sha256:")
    assert entry["py_hash"].startswith("sha256:")
    assert entry["compiler_version"] == COMPILER_VERSION
    first_content = product.read_text(encoding="utf-8")
    write_spy.clear()
    again = compile_fya_file(fya)   # 二次编译 → no-op
    assert again == product
    assert product not in write_spy   # 产物未被重写
    assert tmp_path / ".flowing.meta.yaml" not in write_spy   # meta 同样未重写
    assert product.read_text(encoding="utf-8") == first_content


def test_t59_artifact_modified_gate(tmp_path):
    """59：语义级手改产物 → ArtifactModifiedError 且不覆盖；仅格式化改动不触发。"""
    fya = _copy_payment(tmp_path)
    product = compile_fya_file(fya)
    original = product.read_text(encoding="utf-8")
    # 语义级手改（改字段值）→ 闸
    product.write_text(original.replace("'CNY'", "'USD'"), encoding="utf-8")
    with pytest.raises(ArtifactModifiedError):
        compile_fya_file(fya)
    assert "'USD'" in product.read_text(encoding="utf-8")   # 未被覆盖
    # 恢复产物后仅改格式化（加注释/空行）→ py_hash 为 AST 口径，不触发
    product.write_text(original, encoding="utf-8")
    compile_fya_file(fya)   # 先回到正轨（重算 meta）
    formatted = product.read_text(encoding="utf-8")
    product.write_text(
        formatted.replace("class PaymentAgent(Agent):",
                          "class PaymentAgent(Agent):  # 手加注释")
        + "\n\n", encoding="utf-8")
    assert compile_fya_file(fya) == product   # 不抛 = 未触发闸


def test_t60_comment_only_fya_change_is_noop(tmp_path, write_spy):
    """60：只改 .fya 注释/空行 → no-op（fya_hash 为解析结构口径）。"""
    fya = _copy_payment(tmp_path)
    product = compile_fya_file(fya)
    first_content = product.read_text(encoding="utf-8")
    write_spy.clear()
    fya.write_text("# 头部注释\n\n" + fya.read_text(encoding="utf-8") + "\n\n",
                   encoding="utf-8")
    compile_fya_file(fya)
    assert product not in write_spy   # 注释/空行不进解析产物 → 未重写
    # 对照：语义改动（改 description）→ 重编译
    fya.write_text(fya.read_text(encoding="utf-8").replace(
        "发起支付并返回交易结果", "发起支付并返回交易收据"), encoding="utf-8")
    compile_fya_file(fya)
    assert product in write_spy
    assert "交易收据" in product.read_text(encoding="utf-8")
    assert product.read_text(encoding="utf-8") != first_content


def test_t61_compile_project(tmp_path, write_spy):
    """61：两个 .fya → 两次 compile_project（首返两产物 + meta，次 no-op）；
    空项目 → []。"""
    _copy_payment(tmp_path)
    (tmp_path / "tally.fya").write_text(textwrap.dedent("""\
        name: tally
        description: 计数助手。
        ---
        $system_prompt:
        你是计数助手。
        """), encoding="utf-8")
    products = compile_project(tmp_path)
    assert products == sorted([tmp_path / "payment.py", tmp_path / "tally.py"])
    assert all(p.exists() for p in products)
    write_spy.clear()
    assert compile_project(tmp_path) == products   # 第二次：同样两个路径
    assert not [p for p in write_spy if p in products]   # 全量 no-op（无产物重写）
    empty = tmp_path / "empty"
    empty.mkdir()
    assert compile_project(empty) == []   # 无 .fya 项目 → 空列表


def test_t62_compiler_version_bump_forces_recompile(tmp_path, write_spy):
    """62：meta 的 compiler_version 落后 → 视同 fya_hash 变化强制重编译。"""
    fya = _copy_payment(tmp_path)
    product = compile_fya_file(fya)
    meta_path = tmp_path / ".flowing.meta.yaml"
    meta = YAML(typ="safe").load(meta_path.read_text(encoding="utf-8"))
    meta["payment.fya"]["compiler_version"] = "0.0.0"
    import io

    yaml = YAML(typ="safe")
    buf = io.StringIO()
    yaml.dump(dict(meta), buf)
    meta_path.write_text(buf.getvalue(), encoding="utf-8")
    write_spy.clear()
    compile_fya_file(fya)
    assert product in write_spy   # 强制重编译（重写了产物）


# ---------------------------------------------------------------------------
# 清单 63：compile_fya_class（内存合成，不写文件）
# ---------------------------------------------------------------------------

def test_t63_compile_fya_class(tmp_path):
    """63：合成 Agent 子类——类属性注入 / $script 成员入类体 / args 桥接；
    不写任何文件。"""
    fya = _copy_payment(tmp_path)
    cls = compile_fya_class(fya)
    assert issubclass(cls, Agent) and cls is not Agent
    assert cls.__name__ == "PaymentAgent"   # kebab→Pascal + Agent 后缀策略
    # 类属性注入
    assert isinstance(cls.source_file, str) and cls.source_file.endswith("payment.fya")
    assert isinstance(cls.description, Parsable) and "支付" in cls.description.source
    assert isinstance(cls.system_prompt, Parsable)
    assert "支付处理助手" in cls.system_prompt.source
    # $script 的 @on 成员在类体上（__flowing_hooks__ 打标）
    hook_fns = [m for m in vars(cls).values() if getattr(m, "__flowing_hooks__", None)]
    assert len(hook_fns) == 1
    assert hook_fns[0].__flowing_hooks__[0][0] == "before_tool_call"
    # 无装配工作（无 tools/subagents/未知字段）→ 用户 setup 保持原名与签名
    assert "setup" in vars(cls)
    assert list(inspect.signature(cls.setup).parameters) == [
        "self", "amount", "user_id", "currency"]
    # args 桥接为 args_model（fya args: 优先）
    fields = cls.args_model.model_fields
    assert set(fields) == {"amount", "currency", "user_id"}
    assert fields["amount"].is_required() and fields["user_id"].is_required()
    assert fields["currency"].default == "CNY"
    # 纯内存：不写任何文件
    assert not (tmp_path / "payment.py").exists()
    assert not (tmp_path / ".flowing.meta.yaml").exists()


def test_t63b_args_derived_from_setup_signature(tmp_path):
    """63 补：无 ``args:`` 时从 ``$script`` 用户 setup 签名推导 args_model。"""
    fya = tmp_path / "tally.fya"
    fya.write_text(textwrap.dedent("""\
        description: 计数助手。
        ---
        $system_prompt:
        你是计数助手。
        ---
        $script:
        async def setup(self, count: int, label: str = "x"):
            self.count = count
            self.label = label
        """), encoding="utf-8")
    cls = compile_fya_class(fya)
    fields = cls.args_model.model_fields
    assert set(fields) == {"count", "label"}
    assert fields["count"].is_required() and fields["label"].default == "x"
    # 缺标注 → ValueError（spec：构造期抛 ValueError，编程错误通道）
    bad = tmp_path / "bad.fya"
    bad.write_text(textwrap.dedent("""\
        ---
        $system_prompt:
        你是助手。
        ---
        $script:
        async def setup(self, count):
            pass
        """), encoding="utf-8")
    with pytest.raises(ValueError, match="缺类型标注"):
        compile_fya_class(bad)


def test_t63c_name_assertion_and_class_name_field(tmp_path):
    """63 补：name 一致性断言（不符 → NameMismatchError）与显式 class_name。"""
    fya = tmp_path / "foo.fya"
    fya.write_text("name: payment\n", encoding="utf-8")
    with pytest.raises(NameMismatchError) as exc_info:
        compile_fya_class(fya)
    assert exc_info.value.declared == "payment" and exc_info.value.inferred == "foo"
    ok = tmp_path / "bar.fya"
    ok.write_text("class_name: CustomBar\n", encoding="utf-8")
    assert compile_fya_class(ok).__name__ == "CustomBar"


# ---------------------------------------------------------------------------
# 清单 64：文件::类名 消歧（真实 Runtime + multi/agents.py fixture）
# ---------------------------------------------------------------------------

def test_t64_file_colon_class_disambiguation(tmp_path):
    """64：多类文件经 ``::ClassName`` 精确取类；无 ``::`` 且多类 → FormatError。

    回归（派生键短路须带 class_name 判别）：先 ``::A`` 后 ``::B`` 各自精确
    取到、互不串扰；再次 ``::A`` 经（含类名的）派生键短路复用同一类对象。
    """
    shutil.copytree(FIXTURES / "agents" / "multi", tmp_path / "multi")
    runtime = make_runtime(tmp_path)
    # 无 :: 且多类 → 按消歧规则报错
    with pytest.raises(FormatError, match="ClassName"):
        runtime.get_agent_class("@/multi/agents.py")
    # :: 指向不存在的类 → FormatError
    with pytest.raises(FormatError, match="消歧失败"):
        runtime.get_agent_class("@/multi/agents.py::GhostAgent")
    # ::ClassName 精确取类；先 A 后 B 互不串扰（B 不得误命中 A 的短路）
    cls_a = runtime.get_agent_class("@/multi/agents.py::PaymentAgent")
    assert cls_a.__name__ == "PaymentAgent" and issubclass(cls_a, Agent)
    cls_b = runtime.get_agent_class("@/multi/agents.py::RefundAgent")
    assert cls_b.__name__ == "RefundAgent" and issubclass(cls_b, Agent)
    assert cls_b is not cls_a
    # 派生键短路复用：再次 ::A 返回同一类对象（不重复加载）
    assert runtime.get_agent_class("@/multi/agents.py::PaymentAgent") is cls_a


# ---------------------------------------------------------------------------
# 清单 65：skill 形态字面解析（装配层本体属阶段 4，本期只验证字面层）
# ---------------------------------------------------------------------------

def test_t65_skill_literal_parse():
    """65：``summarize.skill.fya`` 经 parse_fya 产出合法 FyaDocument。"""
    text = (FIXTURES / "skills" / "summarize.skill.fya").read_text(encoding="utf-8")
    doc = parse_fya(text)
    assert doc.fields["name"] == "summarize"
    assert doc.fields["description"].startswith("把给定文本压缩")
    assert set(doc.blocks) == {"prompt"}
    assert "要点列表" in doc.blocks["prompt"]
    assert doc.script is not None and "def setup" in doc.script


# ---------------------------------------------------------------------------
# glob 装配缝接线（测试 55 的端到端形态）与 name 推断撞名（测试 54 端到端升级）
# ---------------------------------------------------------------------------

def test_glob_entries_wired_in_build(tmp_path):
    """55 端到端：compiler 装配层对 ``subagents:`` 的 glob 条目先分流显式、
    展开命中同一资源跳过（不报错），不同资源各自成条目。"""
    for letter in ("a", "b"):
        d = tmp_path / letter
        d.mkdir()
        (d / "agent.fya").write_text(
            f"description: {letter}\n---\n$system_prompt:\n你是 {letter}。\n",
            encoding="utf-8")
    top = tmp_path / "top.fya"
    top.write_text(textwrap.dedent("""\
        ---
        $system_prompt:
        你是调度助手。
        ---
        $script:
        pass
        """), encoding="utf-8")
    # 显式 + glob 命中同一资源：glob 条目跳过
    top.write_text("subagents:\n  - ./a\n  - ./*/\n---\n$system_prompt:\n调度。\n",
                   encoding="utf-8")
    asm = _build(top)
    assert asm.source.count("EntryRef(") == 2   # a 显式 + b glob；./a 的 glob 命中被跳过
    assert "alias='a'" in asm.source and "alias='b'" in asm.source


# ---------------------------------------------------------------------------
# 验收标准 1：fya 目录形态 + 单文件形态全链（解析 → 装配 → 合成 → 注册 →
# Runtime 创建 → FakeProvider 回合）
# ---------------------------------------------------------------------------

async def test_e2e_fya_full_chain_runtime_turn(tmp_path):
    """验收 1：order-agent（目录形态）经 Runtime 创建并跑通一个 FakeProvider
    回合；其 subagents 条目经文件链命中 payment.fya（单文件形态）、tools
    条目命中 payment/TOOL.fya（script 形态）。"""
    shutil.copytree(FIXTURES / "agents" / "order-agent", tmp_path / "order-agent")
    shutil.copy(FIXTURES / "agents" / "payment.fya", tmp_path / "order-agent" / "payment.fya")
    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    script_provider(provider, text_response("订单已收到"))
    agent = await runtime.create_agent("@/order-agent")
    try:
        # 合成类与装配产物
        assert type(agent).__name__ == "OrderAgent"
        assert type(agent).registry_key == "@/order-agent::order-agent"
        assert set(agent._tool_entries) == {"pay"}        # tools 条目经文件链命中 TOOL.fya
        assert set(agent._subagent_entries) == {"pay"}    # subagents 条目命中 payment.fya
        assert agent._extra["model"] == "main"            # 未知字段落 _extra
        # 子 Agent 类型经接缝编译注册（单文件形态合成）
        sub_cls = runtime.get_agent_class("payment", source_dir=tmp_path / "order-agent")
        assert sub_cls.__name__ == "PaymentAgent"
        # FakeProvider 回合跑通
        result = await agent.query("帮我下单")
        assert result.status == "completed" and "订单已收到" in result.final_text
    finally:
        await runtime.shutdown()
