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
from flowing.errors import (
    ArtifactModifiedError,
    EntryNameConflictError,
    FormatError,
    NameMismatchError,
)
from flowing.parser import EntryRef, parse_fya
from flowing.parsable import Parsable
from flowing.parsable import PENDING, Parsable
from flowing.runtime import AGENT_NAMING
from flowing.subagents import _expand_glob_entries

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
    with pytest.raises(FormatError, match="does not match any entry alias"):
        _merge_named_blocks(_fields_with_pay(), {"tools.nope.description": "x"})


def test_t57_as_key_only_alias_addressable():
    """57 规则 2：含 as 的键仅以别名段寻址——规范名段不命中（中间段缺失）。"""
    with pytest.raises(FormatError, match="middle segment"):
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
    with pytest.raises(FormatError, match="conflict"):
        _merge_named_blocks(_fields_with_pay(), {"tools.pay.args.currency": "CNY"})


def test_t57_terminal_on_entry_alias_rejected():
    """57 边界：路径在条目别名处耗尽（块不能整体替换条目）→ FormatError。"""
    with pytest.raises(FormatError, match="last segment lands on entry"):
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
    with pytest.raises(ValueError, match="lacks a type annotation"):
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
    with pytest.raises(FormatError, match="disambiguation failed"):
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


async def test_t54_e2e_name_conflict_via_file_chain(tmp_path):
    """54 端到端：两个同名资源的 .fya agent（``./a/payment`` 与
    ``./b/payment``，别名均推断为 payment）经真实文件链装配 →
    ``EntryNameConflictError``（撞名检测在 add_agent，与类来源无关）。"""
    for sub in ("a", "b"):
        d = tmp_path / sub / "payment"
        d.mkdir(parents=True)
        (d / "agent.fya").write_text(
            f"description: {sub}\n---\n$system_prompt:\n你是支付助手。\n",
            encoding="utf-8")
    (tmp_path / "top.fya").write_text(
        "subagents:\n  - ./a/payment\n  - ./b/payment\n"
        "---\n$system_prompt:\n调度。\n",
        encoding="utf-8")
    runtime = make_runtime(tmp_path)
    try:
        with pytest.raises(EntryNameConflictError):
            await runtime.create_agent("@/top.fya")
    finally:
        await runtime.shutdown()


def test_glob_at_prefix_requires_project_root(tmp_path):
    """``_expand_glob_entries`` 无 launch 上下文时 ``@/`` glob 显式
    ValueError（不静默退回 cwd——与 ToolRegistry.get 的 @/ 失败姿态一致）。"""
    with pytest.raises(ValueError, match="launch context"):
        _expand_glob_entries(["@/agents/*/"], naming=AGENT_NAMING,
                             source_dir=tmp_path)


def test_glob_prescan_passes_malformed_items_through(tmp_path):
    """@/ 预扫描只对单键映射取键：空映射等形态错误条目放过给下游
    ``normalize_entries`` 统一报 FormatError（不泄漏裸 StopIteration）。"""
    with pytest.raises(FormatError, match="single-key mapping"):
        _expand_glob_entries([{}], naming=AGENT_NAMING, source_dir=tmp_path)


# ---------------------------------------------------------------------------
# tools: / subagents: glob 命中过滤（glob_accept，只看名字）与字段分派 naming
# ---------------------------------------------------------------------------

def test_tools_glob_accept_name_only_rules(tmp_path, caplog):
    """tools: glob 过滤为纯名字分析：目录探测工具候选链；显式标记仅纳入
    .tool.fya；其余 .fya / .py（含无标记的 impl.py）直接纳入；其它后缀
    跳过。"""
    import logging

    from flowing.tool.core import TOOL_NAMING
    from flowing.tool.registry import _tool_glob_accept

    d = tmp_path / "goal"
    (d / "extra").mkdir(parents=True)          # 有入口子目录 → 纳入
    (d / "extra" / "TOOL.fya").write_text(
        "name: extra\ntype: cli\ndescription: 子目录工具\n"
        "command: echo hi\nargs:\n  x: {type: string, default: a}\n",
        encoding="utf-8")
    (d / "junk").mkdir()                        # 无入口子目录 → 跳过
    (d / "set-goal.tool.fya").write_text(       # 显式工具标记 → 纳入
        "name: set-goal\ntype: script\ndescription: 设定目标\n"
        "callable: ./impl.py::set_goal\n"
        "args:\n  goal: {type: string, description: 目标}\n",
        encoding="utf-8")
    (d / "x.agent.fya").write_text(             # 显式 agent 标记 → 跳过
        "description: 智能体\n---\n$system_prompt:\n你好。\n", encoding="utf-8")
    (d / "plain.fya").write_text(               # 裸 fya → 纳入
        "description: 裸 fya\n---\n$system_prompt:\n你好。\n", encoding="utf-8")
    (d / "impl.py").write_text(                 # .py → 纳入（不做内容嗅探）
        "async def set_goal(*, goal: str, caller):\n    return {'goal': goal}\n",
        encoding="utf-8")
    (d / "note.md").write_text("# 便签\n", encoding="utf-8")   # 其它后缀 → 跳过
    with caplog.at_level(logging.WARNING, logger="flowing.subagents"):
        refs = _expand_glob_entries(
            ["@/goal/*"], naming=TOOL_NAMING,
            source_dir=tmp_path, project_root=tmp_path,
            glob_accept=_tool_glob_accept)
    assert sorted(r.alias for r in refs) == [
        "extra", "impl", "plain", "set-goal"]
    skipped = [r.message for r in caplog.records if "glob hit skipped" in r.message]
    assert len(skipped) == 3   # junk/、x.agent.fya、note.md


def test_subagents_glob_accept_name_only_rules(tmp_path, caplog):
    """subagents: glob 过滤与 tools 对称：目录探测 agent 候选链；显式标记
    仅纳入 .agent.fya；其余 .fya / .py 直接纳入；其它后缀跳过。"""
    import logging

    from flowing.agent_registry import _agent_glob_accept

    d = tmp_path / "agents"
    (d / "a").mkdir(parents=True)               # 有入口子目录 → 纳入
    (d / "a" / "agent.fya").write_text(
        "description: a\n---\n$system_prompt:\n你是 a。\n", encoding="utf-8")
    (d / "empty").mkdir()                       # 无入口子目录 → 跳过
    (d / "thing.tool.fya").write_text(          # 显式工具标记 → 跳过
        "name: thing\ntype: cli\ndescription: 工具\ncommand: echo x\n"
        "args:\n  x: {type: string, default: a}\n", encoding="utf-8")
    (d / "b.agent.fya").write_text(             # 显式 agent 标记 → 纳入
        "description: b\n---\n$system_prompt:\n你是 b。\n", encoding="utf-8")
    (d / "plain.fya").write_text(               # 裸 fya → 纳入
        "name: plain\ntype: cli\ndescription: 裸 fya 工具\n"
        "command: echo hi\nargs:\n  x: {type: string, default: a}\n",
        encoding="utf-8")
    (d / "worker.py").write_text(               # .py → 纳入（不做内容嗅探）
        "from flowing import Agent\n\n\nclass Worker(Agent):\n    pass\n",
        encoding="utf-8")
    (d / "helper.py").write_text("X = 1\n", encoding="utf-8")   # 同上
    with caplog.at_level(logging.WARNING, logger="flowing.subagents"):
        refs = _expand_glob_entries(
            ["@/agents/*"], naming=AGENT_NAMING,
            source_dir=tmp_path, project_root=tmp_path,
            glob_accept=_agent_glob_accept)
    assert sorted(r.alias for r in refs) == ["a", "b", "helper", "plain", "worker"]
    skipped = [r.message for r in caplog.records if "glob hit skipped" in r.message]
    assert len(skipped) == 2   # empty/、thing.tool.fya


def test_tool_fya_malformed_still_fails_fast(tmp_path):
    """显式工具形态（.tool.fya）内容损坏照常 FormatError（定点解析不变）。"""
    from flowing.tool.registry import ToolRegistry

    bad = tmp_path / "bad.tool.fya"
    bad.write_text("name: bad\ntype: script\n", encoding="utf-8")   # 缺 callable
    with pytest.raises(FormatError, match="callable"):
        ToolRegistry(project_root=tmp_path).get(str(bad))


def test_build_tools_glob_uses_tool_naming(tmp_path):
    """端到端（_build）：tools: glob 的别名推断走 TOOL_NAMING（.tool.fya
    剥出合法别名 set-goal，而非 .tool 残留导致 FormatError）。"""
    d = tmp_path / "goal"
    d.mkdir()
    (d / "impl.py").write_text(
        "async def set_goal(*, goal: str, caller):\n    return {'goal': goal}\n",
        encoding="utf-8")
    (d / "set-goal.tool.fya").write_text(
        "name: set-goal\ntype: script\ndescription: 设定目标\n"
        "callable: ./impl.py::set_goal\n"
        "args:\n  goal: {type: string, description: 目标}\n",
        encoding="utf-8")
    top = tmp_path / "top.fya"
    top.write_text("tools:\n  - ./goal/*.tool.fya\n---\n$system_prompt:\n调度。\n",
                   encoding="utf-8")
    asm = _build(top)
    assert "alias='set-goal'" in asm.source
    assert asm.source.count("EntryRef(") == 1


async def test_glob_included_invalid_resource_fails_at_creation(tmp_path):
    """glob 层不做内容校验：无标记的 impl.py 按名字规则纳入后，在 Agent
    创建期 eager 解析抛 FormatError（fail-fast 时点 = mount）。"""
    d = tmp_path / "goal"
    d.mkdir()
    (d / "impl.py").write_text("X = 1\n", encoding="utf-8")   # 无打标无子类
    top = tmp_path / "top.fya"
    top.write_text("tools:\n  - ./goal/*\n---\n$system_prompt:\n调度。\n",
                   encoding="utf-8")
    runtime = make_runtime(tmp_path)
    try:
        with pytest.raises(FormatError, match="no @flowing_tool-marked"):
            await runtime.create_agent("@/top.fya")
    finally:
        await runtime.shutdown()


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


# ---------------------------------------------------------------------------
# F3 回归（0904）：compile_fya_class 接受 project_root——launch 上下文复位后
# （ContextVar=None）编译含 @/ 条目的 .fya 也能展开；Runtime 运行时编译把
# 自身 project_root 传入（不再依赖 launch 时机）
# ---------------------------------------------------------------------------

def test_f3_at_glob_needs_project_root_without_launch_context(tmp_path):
    """无 launch 上下文（ContextVar=None）时 @/ glob 缺 project_root 报错。"""
    proj = tmp_path
    tool_dir = proj / "tools" / "demo-tool"
    tool_dir.mkdir(parents=True)
    (tool_dir / "TOOL.fya").write_text(
        "type: script\n"
        "description: demo 工具。\n"
        "callable: ./impl.py::demo_fn\n",
        encoding="utf-8",
    )
    (tool_dir / "impl.py").write_text(
        "async def demo_fn() -> str:\n    return 'ok'\n",
        encoding="utf-8",
    )
    fya = proj / "root.fya"
    fya.write_text(
        "description: root\n"
        "tools:\n"
        '  - "@/tools/*"\n',
        encoding="utf-8",
    )
    # 修复前：_project_root() 无上下文 → @/ 条目直接 ValueError
    with pytest.raises(ValueError):
        compile_fya_class(fya)
    # 修复后：显式 project_root（Runtime 传入）→ glob 正常展开、类可合成
    cls = compile_fya_class(fya, project_root=proj)
    assert cls.__name__.endswith("Agent")   # 合成成功即 @/ glob 已展开（此前此处抛 ValueError）


# ---------------------------------------------------------------------------
# F4 回归（0904）：Runtime.mount 接受目录形态 Agent 根（含 agent.fya）——
# 与单文件形态并存、幂等挂载同 create_agent 路径形态
# ---------------------------------------------------------------------------

async def test_f4_mount_accepts_directory_form(tmp_path):
    """mount 目录形态：@/order-agent（含 agent.fya）挂根成功、固定 id 幂等恢复。"""
    shutil.copytree(FIXTURES / "agents" / "order-agent", tmp_path / "order-agent")
    shutil.copy(FIXTURES / "agents" / "payment.fya", tmp_path / "order-agent" / "payment.fya")
    runtime = make_runtime(tmp_path)
    try:
        agent = await runtime.mount("@/order-agent", agent_id="oa-root")
        assert type(agent).__name__ == "OrderAgent"
        assert agent.node_id == "oa-root"
        # 幂等：同 id 再 mount → 恢复同一根（不新建、不报重复创建）
        again = await runtime.mount("@/order-agent", agent_id="oa-root")
        assert again.node_id == "oa-root"
        assert type(again).__name__ == "OrderAgent"
        # 目录内无合法 agent 候选（空目录）→ KeyError 系报错（不静默）
        (tmp_path / "empty").mkdir()
        with pytest.raises(Exception):
            await runtime.mount("@/empty")
        # 其它文件形态仍被拒（.py 根走 create_agent）
        (tmp_path / "plain.py").write_text("", encoding="utf-8")
        with pytest.raises(ValueError):
            await runtime.mount("@/plain.py")
    finally:
        await runtime.shutdown()


# ---------------------------------------------------------------------------
# 0904：.fya 未写 description 时，agent description = $script setup 的
# docstring 整体（cleandoc 全文）
# ---------------------------------------------------------------------------

def test_fya_description_falls_back_to_setup_docstring(tmp_path):
    fya = tmp_path / "desc-root.fya"
    fya.write_text(
        "args:\n"
        "  task: str\n"
        "---\n"
        "$script:\n"
        "async def setup(self, task: str):\n"
        "    \"\"\"装配入口说明。\n"
        "\n"
        "第二段同样进入描述（整体回退）。\n"
        "    \"\"\"\n"
        "    self.task = task\n",
        encoding="utf-8",
    )
    cls = compile_fya_class(fya)
    assert isinstance(cls.description, Parsable)
    assert cls.description.source == \
        "装配入口说明。\n\n第二段同样进入描述（整体回退）。"


def test_fya_description_explicit_wins_over_setup_docstring(tmp_path):
    """description 字段存在时优先，不回退 setup docstring。"""
    fya = tmp_path / "desc-root2.fya"
    fya.write_text(
        "description: 显式描述\n"
        "---\n"
        "$script:\n"
        "async def setup(self):\n"
        "    \"\"\"不应被使用的 docstring。\"\"\"\n"
        "    pass\n",
        encoding="utf-8",
    )
    cls = compile_fya_class(fya)
    assert cls.description.source == "显式描述"


# ---------------------------------------------------------------------------
# 0904：.fya setup 注解经 pydantic 解析——Annotated + Field 元数据
# （description/约束）在无 args 声明时正确进入推导的 args schema
# ---------------------------------------------------------------------------

def test_fya_setup_supports_pydantic_field_annotations(tmp_path):
    fya = tmp_path / "pf.fya"
    fya.write_text(
        "---\n"
        "$script:\n"
        "from typing import Annotated\n"
        "from pydantic import Field\n"
        "async def setup(self, task: Annotated[str, Field(description='任务说明', min_length=2)],\n"
        "                retries: Annotated[int, Field(description='重试次数', ge=0)] = 3):\n"
        "    self.task = task\n",
        encoding="utf-8",
    )
    cls = compile_fya_class(fya)
    props = cls.args_model.model_json_schema()["properties"]
    assert props["task"]["description"] == "任务说明"
    assert props["task"]["minLength"] == 2
    assert props["retries"]["description"] == "重试次数"
    assert props["retries"]["minimum"] == 0
    assert props["retries"]["default"] == 3


def test_fya_setup_missing_annotation_still_raises(tmp_path):
    """缺注解的 setup 参数仍按作者笔误通道报 ValueError（不落入 pydantic）。"""
    fya = tmp_path / "noann.fya"
    fya.write_text(
        "---\n"
        "$script:\n"
        "async def setup(self, task):\n"
        "    self.task = task\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="lacks a type annotation"):
        compile_fya_class(fya)


def test_fya_script_split_module_level_and_class_body(tmp_path):
    """$script 二分：self 方法入类体，其余顶层语句（import / 无 self 函数 /
    类声明 / 赋值）按原序提升到模块级；from __future__ 置于产物最前。"""
    fya = tmp_path / "split.fya"
    fya.write_text(textwrap.dedent("""\
        description: 二分验证。
        ---
        $script:
        from __future__ import annotations

        import os

        SUFFIX = "-ok"


        class Helper:
            def render(self, x: int) -> str:
                return f"{x}{SUFFIX}"


        def slugify(text: str) -> str:
            return text.lower().replace(" ", "-")


        async def setup(self, task: str):
            self.task = task
            self.helper = Helper()
            self.slug = slugify(task)
            self.sep = os.sep


        def extra_method(self) -> str:
            return self.helper.render(len(self.task)) + "/" + self.slug
        """), encoding="utf-8")
    asm = _build(fya, project_root=tmp_path)
    # 发射形态：future import 最前；模块级段在类上方且保序
    src = asm.source
    assert src.splitlines()[2] == "from __future__ import annotations"
    assert src.index("import os") < src.index("SUFFIX") < src.index("class Helper") \
        < src.index("def slugify") < src.index("class SplitAgent")
    cls = compile_fya_class(fya)
    # self 方法入类体；非 self 成员不在类体（顶层赋值是模块级常量而非类属性）
    assert "setup" in vars(cls) and "extra_method" in vars(cls)
    assert "Helper" not in vars(cls) and "slugify" not in vars(cls)
    assert not hasattr(cls, "SUFFIX")
    # 方法体经模块 globals 可见提升后的名字（运行行为）
    import asyncio
    inst = cls.__new__(cls)
    asyncio.run(inst.setup(task="Hello World"))
    assert inst.extra_method() == "11-ok/hello-world"


def test_fya_script_selfless_on_handler_rejected(tmp_path):
    """顶层 @on 装饰的无 self 函数 → FormatError（提升到模块级会静默丢失
    注册，作者笔误必须死在编译期）。"""
    fya = tmp_path / "badon.fya"
    fya.write_text(textwrap.dedent("""\
        description: x
        ---
        $script:
        from flowing import on

        @on('before_tool_call')
        def guard(tool_call):
            return tool_call
        """), encoding="utf-8")
    with pytest.raises(FormatError, match="must take self"):
        compile_fya_class(fya)
