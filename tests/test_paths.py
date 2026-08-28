"""paths 模块测试（简报测试清单 P1–P16，fixtures 用 tests/fixtures/paths/）。"""

import os
from pathlib import Path

import pytest

from flowing.errors import FormatError
from flowing.paths import (
    NamingRules,
    classify_ref,
    infer_name,
    kebab_to_pascal,
    kebab_to_snake,
    pascal_to_kebab,
    probe_candidates,
    resolve_path,
    snake_to_kebab,
    to_project_path,
)

PROJ = Path("/proj")

# P13–P14 用本地构造的 NamingRules（AGENT_NAMING 常量本体归 L3 runtime）
AGENT_NAMING = NamingRules(
    suffixes=(".agent.fya", ".fya", ".py"),
    generic_names=frozenset({"AGENT.fya", "agent.fya"}),
)


class TestClassifyRef:
    def test_p1_path_prefixes_and_absolute(self):
        """P1：./ ../ @/ 绝对路径 → 全 path。"""
        for raw in ("./x", "../x", "@/x", "/abs/x"):
            assert classify_ref(raw) == "path", raw

    def test_p2_double_colon_inside_path_not_parsed(self):
        """P2：路径内 :: 不解析。"""
        assert classify_ref("./a::b") == "path"

    def test_p3_qualified_and_bare(self):
        """P3：ns::name → qualified；裸名 → bare。"""
        assert classify_ref("ns::name") == "qualified"
        assert classify_ref("pay") == "bare"

    def test_p4_edges(self):
        """P4：边缘形态（多 ::、::右段含 /、裸 @、反斜杠、文件::类名）。"""
        assert classify_ref("a::b::c") == "qualified"
        assert classify_ref("a::b/c") == "qualified"
        assert classify_ref("@") == "bare"
        assert classify_ref("subagents\\live2d") == "path"
        assert classify_ref("./agents.py::OrderAgent") == "path"

    def test_p4_file_class_disambiguation_without_prefix(self):
        """X4：``文件::类名`` 左段含路径特征（/ 或以 .py 结尾）→ path。"""
        assert classify_ref("agents.py::OrderAgent") == "path"
        assert classify_ref("sub/agents.py::OrderAgent") == "path"
        assert classify_ref("ns::OrderAgent") == "qualified"

    def test_windows_drive_and_unc_classified_as_path(self):
        """验收标准 1：盘符 / UNC 绝对路径判定为 path。"""
        assert classify_ref("C:\\x") == "path"
        assert classify_ref("C:/x") == "path"
        assert classify_ref("\\\\server\\share") == "path"

    def test_tilde_is_path(self):
        """PATH_PREFIXES docstring：~ 是合法前缀，永远按绝对路径触发。"""
        assert classify_ref("~") == "path"
        assert classify_ref("~/x") == "path"
        assert classify_ref("~\\x") == "path"


class TestResolvePath:
    def test_p5_project_prefix(self):
        """P5：@/ → project_root。"""
        assert resolve_path("@/a/b", project_root=PROJ) == Path("/proj/a/b")

    def test_p6_relative_without_source_dir_raises(self):
        """P6：./ 前缀且无 source_dir → ValueError。"""
        with pytest.raises(ValueError):
            resolve_path("./x", project_root=PROJ, source_dir=None)

    def test_p7_multi_level_parent_escape_is_legal(self):
        """P7：多级 ../ 越出根合法。"""
        assert resolve_path(
            "../../x", project_root=PROJ, source_dir=Path("/proj/ag")
        ) == Path("/x")

    def test_p8_backslash_normalization(self):
        """P8：前缀判定中 \\ 与 / 等价。"""
        sd = Path("/proj/ag")
        assert resolve_path(".\\x", project_root=PROJ, source_dir=sd) == Path("/proj/ag/x")
        assert resolve_path("..\\..\\x", project_root=PROJ, source_dir=sd) == Path("/x")
        assert resolve_path("~\\x", project_root=PROJ) == Path(os.path.expanduser("~/x"))

    def test_p9_tilde_expansion(self):
        """P9：~ / ~/x 经 expanduser 展开后按绝对路径处理。"""
        assert resolve_path("~", project_root=PROJ) == Path(os.path.expanduser("~"))
        assert resolve_path("~/x", project_root=PROJ) == Path(os.path.expanduser("~/x"))

    def test_p10_bare_project_root(self):
        """P10：裸 @/ 表示根目录本身。"""
        assert resolve_path("@/", project_root=PROJ) == PROJ

    def test_plain_relative_with_slash_uses_source_dir(self):
        """含 / 的普通相对路径以 source_dir 为基准。"""
        assert resolve_path(
            "sub/x", project_root=PROJ, source_dir=Path("/proj/ag")
        ) == Path("/proj/ag/sub/x")

    def test_windows_drive_and_unc_treated_as_absolute(self):
        """验收标准 1：盘符 / UNC 形态按绝对路径原样接受。"""
        assert resolve_path("C:/x", project_root=PROJ) == Path("C:/x")
        assert resolve_path("C:\\x", project_root=PROJ) == Path("C:/x")


class TestToProjectPath:
    def test_p11_inside_outside_root(self):
        """P11：根内 → @/ 相对形式；根本身 → @/；根外 → 绝对原样。"""
        assert to_project_path(Path("/proj/a/b"), project_root=PROJ) == "@/a/b"
        assert to_project_path(PROJ, project_root=PROJ) == "@/"
        assert to_project_path(Path("/etc/x"), project_root=PROJ) == "/etc/x"


class TestProbeCandidates:
    def test_p12_first_hit_wins_and_none_on_miss(self, fixtures_dir):
        """P12：按序探测首命中返回；全 miss → None。"""
        payment_dir = fixtures_dir / "paths" / "proj" / "tools" / "payment"
        hit = probe_candidates(payment_dir, ["TOOL.fya", "payment.tool.fya"])
        assert hit == payment_dir / "TOOL.fya"
        assert probe_candidates(payment_dir, ["nope.fya", "nope.py"]) is None


class TestInferName:
    def test_p13_generic_name_falls_back_to_dirname(self, fixtures_dir):
        """P13：通用文件名取目录名。"""
        agent_fya = fixtures_dir / "paths" / "proj" / "agents" / "order-agent" / "agent.fya"
        assert infer_name(agent_fya, naming=AGENT_NAMING) == "order-agent"
        upper = fixtures_dir / "paths" / "proj" / "agents" / "legacy-agent" / "AGENT.fya"
        assert infer_name(upper, naming=AGENT_NAMING) == "legacy-agent"

    def test_p14_suffix_strip_and_snake_to_kebab(self, fixtures_dir):
        """P14：去 .py + snake→kebab。"""
        pay_agent = fixtures_dir / "paths" / "proj" / "tools" / "pay_agent.py"
        assert infer_name(pay_agent, naming=AGENT_NAMING) == "pay-agent"
        # .agent.fya 先于 .fya 剥离
        assert infer_name("agents/x.agent.fya", naming=AGENT_NAMING) == "x"

    def test_p15_format_errors(self):
        """P15：剥离后空串 / 结果非法 kebab → FormatError。"""
        with pytest.raises(FormatError):
            infer_name(".fya", naming=AGENT_NAMING)  # 全名恰为后缀 → 空串
        with pytest.raises(FormatError):
            infer_name("My Agent.fya", naming=AGENT_NAMING)  # 非法 kebab


class TestNameConversions:
    def test_p16_kebab_to_snake(self):
        assert kebab_to_snake("payment-agent") == "payment_agent"
        with pytest.raises(FormatError):
            kebab_to_snake("Pay")

    def test_p16_snake_to_kebab(self):
        assert snake_to_kebab("payment_agent") == "payment-agent"
        with pytest.raises(FormatError):
            snake_to_kebab("")

    def test_p16_kebab_to_pascal(self):
        assert kebab_to_pascal("pay-agent") == "PayAgent"
        assert kebab_to_pascal("pay") == "Pay"

    def test_p16_pascal_to_kebab(self):
        assert pascal_to_kebab("PaymentAgent") == "payment-agent"
        assert pascal_to_kebab("HTTPClient") == "http-client"
        with pytest.raises(FormatError):
            pascal_to_kebab("paymentAgent")
