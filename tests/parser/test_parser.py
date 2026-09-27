"""Fya 解析器测试：测试清单 1–15。

fixtures“目录即契约”：``fya/agents/order-agent/agent.fya``（目录形态正例，
多模块共用）、``fya/agents/bad-syntax.fya``（负例集合，按切片使用）。
"""

from __future__ import annotations

import pytest

from flowing.errors import FormatError
from flowing.parser import (
    EntryRef,
    load_fya_yaml,
    normalize_entries,
    parse_fya,
    split_as,
    split_fya,
)
from flowing.parsable import PENDING
from flowing.runtime import AGENT_NAMING


@pytest.fixture
def order_agent_text(fixtures_dir) -> str:
    return (fixtures_dir / "fya" / "agents" / "order-agent" / "agent.fya").read_text(
        encoding="utf-8"
    )


@pytest.fixture
def bad_syntax_text(fixtures_dir) -> str:
    return (fixtures_dir / "fya" / "agents" / "bad-syntax.fya").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# parse_fya（清单 1、2、14、15）
# ---------------------------------------------------------------------------


class TestParseFya:
    def test_t01_entries_alias_and_blocks_not_merged(self, order_agent_text):
        """清单 1：别名落定；具名块原样持有、未填回 fields。

        注：fixture 多模块共用（含深层块与 $script），故 blocks 断言
        为“包含 system_prompt 原文且 script 不进 blocks”而非整表相等。
        """
        doc = parse_fya(order_agent_text, entry_fields={"tools"})
        entry = doc.fields["tools"][0]
        assert isinstance(entry, EntryRef)
        assert entry.alias == "pay"
        assert entry.raw == "payment"
        assert doc.blocks["system_prompt"] == "你是订单处理助手，负责发起支付与查询订单状态。\n"
        assert "script" not in doc.blocks
        assert doc.script == "def setup(self):\n    pass\n"
        # 块未填回 fields
        assert "system_prompt" not in doc.fields
        assert "script" not in doc.fields

    def test_t02_pending_scalar(self, order_agent_text):
        """清单 2：``description: _`` → PENDING。"""
        doc = parse_fya(order_agent_text)
        assert doc.fields["description"] is PENDING

    def test_t14_edge_three_states(self):
        """清单 14：空文件 / 仅 YAML 无块 / 仅块无 YAML 均合法。"""
        empty = parse_fya("")
        assert empty.fields == {} and empty.blocks == {} and empty.script is None

        yaml_only = parse_fya("a: 1\n")
        assert yaml_only.fields == {"a": 1}
        assert yaml_only.blocks == {} and yaml_only.script is None

        blocks_only = parse_fya("---\n$x:\nbody\n")
        assert blocks_only.fields == {}
        assert blocks_only.blocks == {"x": "body\n"}

    def test_t15_entry_fields_declared_but_absent(self):
        """清单 15：声明字段不存在不报错；声明字段非列表 → FormatError。"""
        doc = parse_fya("a: 1\n", entry_fields={"tools"})
        assert "tools" not in doc.fields
        with pytest.raises(FormatError):
            parse_fya("tools: {a: 1}\n", entry_fields={"tools"})


# ---------------------------------------------------------------------------
# split_fya（清单 3、4、5）
# ---------------------------------------------------------------------------


class TestSplitFya:
    def test_t03_basic_split(self):
        """清单 3：YAML 段 / 具名块 / $script 三分，原文保留。"""
        raw = split_fya("a: 1\n---\n$system_prompt:\n你好\n---\n$script:\npass")
        assert raw.yaml_text == "a: 1\n"
        assert raw.blocks == {"system_prompt": "你好\n"}
        assert raw.script == "pass"

    def test_t04_duplicate_block_path(self, bad_syntax_text):
        """清单 4：两个同名 $x: 块 → FormatError。"""
        with pytest.raises(FormatError, match="duplicate block path"):
            split_fya(bad_syntax_text)

    def test_t05_subscript_segment_forbidden(self, bad_syntax_text):
        """清单 5：下标段块头 ``$tools[0].x:`` → FormatError（取负例 3 切片）。"""
        marker = "$tools[0].x:"
        frag = bad_syntax_text[bad_syntax_text.index(marker):]
        with pytest.raises(FormatError, match="invalid block header"):
            split_fya("---\n" + frag)


# ---------------------------------------------------------------------------
# load_fya_yaml（清单 6、7、8）
# ---------------------------------------------------------------------------


class TestLoadFyaYaml:
    def test_t06_pending_mapping(self):
        """清单 6：标量 ``_`` → PENDING。"""
        assert load_fya_yaml("a: _")["a"] is PENDING

    def test_t07_dollar_quoted_underscore_is_plain_string(self):
        """清单 7：``a: "$"_"`` 是普通字符串，不触发 PENDING。"""
        assert load_fya_yaml('a: "$\\"_\\""')["a"] == '$"_"'

    def test_t08_top_level_list_rejected_with_line(self, bad_syntax_text):
        """清单 8：顶层为列表 → FormatError，报文含行号（取负例 1 的 YAML 段）。"""
        head = bad_syntax_text.split("\n---\n")[0]
        with pytest.raises(FormatError, match="line 4"):
            load_fya_yaml(head)

    def test_yaml_syntax_error_wraps_with_line(self):
        """YAML 语法错误 → FormatError，包装报文带原始行号。"""
        with pytest.raises(FormatError, match="line 1"):
            load_fya_yaml("a: [1,\nb: 2")


# ---------------------------------------------------------------------------
# split_as（清单 9、10）
# ---------------------------------------------------------------------------


class TestSplitAs:
    def test_t09_three_forms(self):
        """清单 9：as 切分三形态（含空白宽容）。"""
        assert split_as("payment as pay") == ("payment", "pay")
        assert split_as("payment") == ("payment", None)
        assert split_as("a  as  b") == ("a", "b")

    def test_t10_multiple_as_or_empty_side(self):
        """清单 10：多 as / 任一侧空 → FormatError。"""
        with pytest.raises(FormatError):
            split_as("a as b as c")
        with pytest.raises(FormatError):
            split_as(" as b")


# ---------------------------------------------------------------------------
# normalize_entries（清单 11、12、13）
# ---------------------------------------------------------------------------


class TestNormalizeEntries:
    def test_t11_duplicate_alias_not_reported_here(self):
        """清单 11：两路径推断同别名，撞名不在本层报错。"""
        refs = normalize_entries(["./a/payment", "./b/payment"], naming=AGENT_NAMING)
        assert [r.alias for r in refs] == ["payment", "payment"]

    def test_t12_list_item_rejected(self):
        """清单 12：列表项是列表 → FormatError。"""
        with pytest.raises(FormatError):
            normalize_entries([["a", "b"]])

    def test_t13_pending_body_means_empty_override(self):
        """清单 13：``- x: _`` → 空覆写；标量 / 列表覆写值 → FormatError。"""
        assert normalize_entries([{"x": PENDING}]) == [EntryRef("x", "x", {})]
        with pytest.raises(FormatError):
            normalize_entries([{"x": 1}])
        with pytest.raises(FormatError):
            normalize_entries([{"x": ["a"]}])

    def test_pending_list_item_rejected(self):
        """规约补充：列表项本身为 ``_``（PENDING）→ FormatError。"""
        with pytest.raises(FormatError):
            normalize_entries([PENDING])
