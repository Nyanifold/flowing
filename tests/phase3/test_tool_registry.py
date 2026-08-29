"""阶段 3 tool 注册表（W19）测试：测试清单 35–46。

覆盖 ``ToolRegistry.get`` 单入口三分语义（限定名只查注册表 / 裸名
default:: > builtin:: + source_dir 定向文件链 / 路径形态候选链）、
``.py`` 命中的打标/子类判别、``.fya`` 命中的四型实例化、派生键短路复用、
name 断言、``.fya``/``.py`` 并存告警、``register`` 重名约束、
``get_tool_class`` 薄委托、``__contains__`` 仅全限定键（P3-07）。

fixtures：``fya/tools/make-payment/``（script + callable 指针）与
``fya/tools/run-tests/``（cli 形态，与批次 3 的清单 48 共用）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from flowing.errors import (
    AmbiguousToolError,
    FormatError,
    NameMismatchError,
    ToolNameConflictError,
    ToolNotFoundError,
)
from flowing.tool import (
    CliTool,
    ScriptTool,
    Tool,
    ToolDefinition,
    ToolRegistry,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class _DummyTool(Tool):
    """最小注册用工具（直接子类化 Tool 的逃生舱写法）。"""

    def __init__(self, name: str) -> None:
        self.definition = ToolDefinition(name=name, description="d")

    async def execute(self):
        return None


# ---------------------------------------------------------------------------
# .py 命中：打标函数提升 / 子类实例化 / 歧义判别（清单 35、36、37）
# ---------------------------------------------------------------------------


class TestPyHit:
    def test_t35_marked_function_promoted(self, tmp_path):
        """清单 35：@flowing_tool 打标函数文件 → 自动提升并注册。"""
        _write(tmp_path / "make_payment.py", '''
from flowing.tool import flowing_tool


@flowing_tool
async def make_payment(order_id: str, amount: float) -> dict:
    """对指定订单发起支付。"""
    return {"tx": "fake"}
''')
        registry = ToolRegistry()
        tool = registry.get("make-payment", source_dir=tmp_path)
        assert isinstance(tool, ScriptTool)
        assert tool.definition.name == "make-payment"
        assert tool.definition.description == "对指定订单发起支付。"
        # 注册落账派生键并回写 registry_key
        assert tool.registry_key is not None
        assert tool.registry_key.endswith("::make-payment")
        assert tool.registry_key in registry
        # 再次同名调用：派生键短路复用（同一实例，不重复实例化）
        assert registry.get("make-payment", source_dir=tmp_path) is tool

    def test_t36_decorator_name_mismatch(self, tmp_path):
        """清单 36：@flowing_tool("other-name") 与文件名推断名不符 →
        NameMismatchError。"""
        _write(tmp_path / "make_payment.py", '''
from flowing.tool import flowing_tool


@flowing_tool("other-name")
async def make_payment(order_id: str) -> dict:
    return {}
''')
        with pytest.raises(NameMismatchError):
            ToolRegistry().get("make-payment", source_dir=tmp_path)

    def test_t37_marked_and_subclass_coexist(self, tmp_path):
        """清单 37：同一 .py 同时存在打标函数与 Tool 子类 → AmbiguousToolError。"""
        _write(tmp_path / "mixed_tool.py", '''
from flowing.tool import ScriptTool, flowing_tool


@flowing_tool
async def mixed_tool(x: int) -> int:
    return x


class MixedTool(ScriptTool):
    description = "d"

    async def execute(self, *, x: int) -> int:
        return x
''')
        with pytest.raises(AmbiguousToolError):
            ToolRegistry().get("mixed-tool", source_dir=tmp_path)

    def test_t37_two_marked_functions(self, tmp_path):
        """清单 37：两个打标函数 → AmbiguousToolError。"""
        _write(tmp_path / "double_tool.py", '''
from flowing.tool import flowing_tool


@flowing_tool
async def first_fn(x: int) -> int:
    return x


@flowing_tool
async def second_fn(x: int) -> int:
    return x
''')
        with pytest.raises(AmbiguousToolError):
            ToolRegistry().get("double-tool", source_dir=tmp_path)

    def test_t37_no_legal_definition(self, tmp_path):
        """清单 37：.py 内打标函数与 ScriptTool 子类皆无 → FormatError。"""
        _write(tmp_path / "empty_tool.py", "X = 1\n")
        with pytest.raises(FormatError):
            ToolRegistry().get("empty-tool", source_dir=tmp_path)

    def test_t37b_single_subclass_instantiated(self, tmp_path):
        """恰好一个 ScriptTool 子类 → 实例化（name 由类名 kebab 化推断）。"""
        _write(tmp_path / "sub_tool.py", '''
from flowing.tool import ScriptTool


class SubTool(ScriptTool):
    """子类 docstring 描述。"""

    async def execute(self, *, x: int) -> int:
        return x
''')
        tool = ToolRegistry().get("sub-tool", source_dir=tmp_path)
        assert isinstance(tool, ScriptTool)
        assert type(tool).__name__ == "SubTool"
        assert tool.definition.name == "sub-tool"
        assert tool.definition.description == "子类 docstring 描述。"

    def test_t37c_subclass_explicit_name_mismatch(self, tmp_path):
        """子类显式 name 声明与文件推断名不符 → NameMismatchError。"""
        _write(tmp_path / "sub_tool.py", '''
from flowing.tool import ScriptTool


class SubTool(ScriptTool):
    name = "other-name"
    description = "d"

    async def execute(self, *, x: int) -> int:
        return x
''')
        with pytest.raises(NameMismatchError):
            ToolRegistry().get("sub-tool", source_dir=tmp_path)


# ---------------------------------------------------------------------------
# register / 命名空间共存（清单 38、39）
# ---------------------------------------------------------------------------


class TestRegister:
    def test_t38_qualified_key_conflict(self):
        """清单 38：同名同命名空间重复注册 → ToolNameConflictError，原条目不变。"""
        registry = ToolRegistry()
        first = _DummyTool("github")
        registry.register(first)
        with pytest.raises(ToolNameConflictError):
            registry.register(_DummyTool("github"))
        assert registry.get("github") is first

    def test_t39_default_shadows_builtin(self):
        """清单 39：builtin:: 已存在时插件以缺省 default:: 注册同名 →
        不报错；裸名解析到插件版本；builtin:: 仍可显式引用。"""
        registry = ToolRegistry()
        builtin = _DummyTool("web-search")
        registry.register(builtin, namespace="builtin")
        plugin = _DummyTool("web-search")
        registry.register(plugin)   # 缺省 default:: —— 不同命名空间允许共存
        assert registry.get("web-search") is plugin
        assert registry.get("builtin::web-search") is builtin
        # 限定名只查注册表精确键：未注册的限定名 → ToolNotFoundError
        with pytest.raises(ToolNotFoundError):
            registry.get("myplugin::web-search")


# ---------------------------------------------------------------------------
# 文件链慢路径（清单 40、41、42、45、46）
# ---------------------------------------------------------------------------


class TestFileChain:
    def test_t40_directory_fya_hit_and_derived_key_reuse(self, fixtures_dir):
        """清单 40：裸名 + source_dir 命中 ``make-payment/TOOL.fya`` →
        按目录派生键注册；再次同名调用短路复用（不重复实例化）。"""
        source_dir = fixtures_dir / "fya" / "tools"
        registry = ToolRegistry()
        tool = registry.get("make-payment", source_dir=source_dir)
        assert isinstance(tool, ScriptTool)
        assert tool.definition.name == "make-payment"
        # fya 显式 description 压过 callable docstring
        assert tool.definition.description == "对指定订单发起支付。"
        assert set(tool.definition.params_schema) == {"order_id", "amount"}
        assert registry.get("make-payment", source_dir=source_dir) is tool

    async def test_t40b_fixture_tool_executes(self, fixtures_dir):
        """清单 40 附：fixture 工具经 __call__ 真实执行（callable 通道产物）。"""
        tool = ToolRegistry().get(
            "make-payment", source_dir=fixtures_dir / "fya" / "tools")
        result = await tool({"order_id": "o1", "amount": 9.9})
        assert result.status == "completed"
        assert result.output == {"tx": "fake-tx", "order_id": "o1",
                                 "amount": 9.9}

    def test_t41_bare_name_without_source_dir_no_probing(self, tmp_path, monkeypatch):
        """清单 41：裸名 + source_dir=None → 只查 default::/builtin::，
        不命中即 ToolNotFoundError，不做文件探测。"""
        _write(tmp_path / "ghost.py", '''
from flowing.tool import flowing_tool


@flowing_tool
async def ghost(x: int) -> int:
    return x
''')
        monkeypatch.chdir(tmp_path)   # 同名文件就在 cwd——证明不以 cwd 探测
        with pytest.raises(ToolNotFoundError):
            ToolRegistry().get("ghost")

    def test_t42_explicit_path_empty_dir(self, tmp_path):
        """清单 42：显式路径指向空目录 → FormatError（不继续向下）。"""
        (tmp_path / "payment").mkdir()
        with pytest.raises(FormatError):
            ToolRegistry().get("./payment", source_dir=tmp_path)

    def test_bare_name_empty_dir_falls_through(self, tmp_path):
        """「继续向下」仅裸名语境：目录存在但无合法入口 → 继续目录外链。"""
        (tmp_path / "hollow").mkdir()   # 空目录
        _write(tmp_path / "hollow.py", '''
from flowing.tool import flowing_tool


@flowing_tool
async def hollow(x: int) -> int:
    return x
''')
        tool = ToolRegistry().get("hollow", source_dir=tmp_path)
        assert isinstance(tool, ScriptTool)
        assert tool.definition.name == "hollow"

    def test_t45_fya_preferred_over_py_with_warning(self, tmp_path):
        """清单 45：.fya 与同名 .py 并存 → 告警 + .fya 优先。"""
        _write(tmp_path / "payment.fya", """
type: script
description: fya 形态产物
callable: ./impl.py::payment
args:
  x: int
""")
        _write(tmp_path / "impl.py",
               "async def payment(x: int) -> int:\n    return x\n")
        _write(tmp_path / "payment.py", '''
from flowing.tool import flowing_tool


@flowing_tool
async def payment(x: int) -> int:
    return x
''')
        registry = ToolRegistry()
        with pytest.warns(UserWarning, match=r"\.fya 优先"):
            tool = registry.get("payment", source_dir=tmp_path)
        assert tool.definition.description == "fya 形态产物"

    def test_t46_generic_name_hit_dir_identity(self, fixtures_dir):
        """清单 46：TOOL.fya 通用名候选命中 → 身份名取目录名（snake→kebab）。"""
        registry = ToolRegistry()
        tool = registry.get("run-tests", source_dir=fixtures_dir / "fya" / "tools")
        assert isinstance(tool, CliTool)
        assert tool.definition.name == "run-tests"
        assert tool.shell == "sh"
        assert set(tool.definition.params_schema) == {"working_dir", "test_path"}
        assert tool.definition.output_schema is not None

    def test_t46b_fya_explicit_name_mismatch(self, fixtures_dir, tmp_path):
        """清单 46：TOOL.fya 显式 name 声明与目录推断名不符 → NameMismatchError。"""
        dst = tmp_path / "run-tests"
        shutil.copytree(fixtures_dir / "fya" / "tools" / "run-tests", dst)
        fya = dst / "TOOL.fya"
        fya.write_text(
            fya.read_text(encoding="utf-8").replace(
                "name: run-tests", "name: other-name"),
            encoding="utf-8")
        with pytest.raises(NameMismatchError):
            ToolRegistry().get("run-tests", source_dir=tmp_path)

    def test_explicit_path_to_file(self, tmp_path):
        """路径形态直指文件：./x.py 命中 → 实例化注册。"""
        _write(tmp_path / "direct_tool.py", '''
from flowing.tool import flowing_tool


@flowing_tool
async def direct_tool(x: int) -> int:
    return x
''')
        tool = ToolRegistry().get("./direct_tool.py", source_dir=tmp_path)
        assert isinstance(tool, ScriptTool)
        assert tool.definition.name == "direct-tool"


# ---------------------------------------------------------------------------
# get_tool_class / __contains__（清单 43、44）
# ---------------------------------------------------------------------------


class TestRegistryAccessors:
    def test_t43_get_tool_class(self, fixtures_dir):
        """清单 43：get_tool_class 薄委托 type(get(...))。"""
        source_dir = fixtures_dir / "fya" / "tools"
        registry = ToolRegistry()
        assert (registry.get_tool_class("make-payment", source_dir=source_dir)
                is type(registry.get("make-payment", source_dir=source_dir)))

    def test_t44_contains_qualified_only(self):
        """清单 44：__contains__ 仅认全限定键（P3-07）。"""
        registry = ToolRegistry()
        registry.register(_DummyTool("read"), namespace="builtin")
        assert "builtin::read" in registry
        assert "read" not in registry
