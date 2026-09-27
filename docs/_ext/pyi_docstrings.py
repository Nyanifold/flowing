"""用镜像 ``.pyi`` 桩文件中的 docstring 覆盖 autodoc 的输出。

设计动机
--------
autodoc 通过导入真实模块读取 ``__doc__``，而 ``.pyi`` 桩文件不参与运行期
导入，Sphinx 原生看不见桩里的 docstring。本扩展在构建期补上这一环：

1. 扫描 ``pyi_docstring_roots`` 下的每个 ``*.pyi``，用 :mod:`ast` 解析出
   “autodoc 对象全名 -> docstring”映射（模块 / 类 / 函数 / 方法 / 属性）；
2. 挂在 ``autodoc-process-docstring`` 事件上，把 autodoc 取到的 docstring
   整体替换为桩中对应条目；桩里没有的条目维持源语言原样（优雅降级）。

桩文件全程只被 ``ast.parse``，从不被导入执行，因此可以放心使用任何桩专属
写法（``...`` 函数体、裸注解、不求值的表达式）。

conf.py 用法::

    sys.path.insert(0, str(CONF_DIR / "_ext"))
    extensions = ["sphinx.ext.autodoc", "pyi_docstrings"]
    pyi_docstring_roots = ["../stubs/zh_CN"]  # 相对 conf.py 所在目录
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sphinx.application import Sphinx

# “autodoc 对象全名 -> 桩 docstring”，每次 builder-inited 时重建。
_MAPS: dict[str, str] = {}


def _walk_scopes(body: list[ast.stmt], prefix: str, out: dict[str, str]) -> None:
    """遍历一个作用域，收集带 docstring 的定义与属性注解。

    函数体内的局部赋值不收集；类体/模块体中的 ``AnnAssign`` / ``Assign``
    后面紧跟字符串字面量的写法视为属性 docstring（与 autodoc 的取值规则
    一致）。桩里必须用这种字面量写法——``#:`` 注释对 ast 不可见。
    """
    for idx, stmt in enumerate(body):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            qual = f"{prefix}.{stmt.name}" if prefix else stmt.name
            if (doc := ast.get_docstring(stmt)) is not None:
                out[qual] = doc
            if isinstance(stmt, ast.ClassDef):
                _walk_scopes(stmt.body, qual, out)
        elif isinstance(stmt, (ast.AnnAssign, ast.Assign)) and idx + 1 < len(body):
            nxt = body[idx + 1]
            if not (
                isinstance(nxt, ast.Expr)
                and isinstance(nxt.value, ast.Constant)
                and isinstance(nxt.value.value, str)
            ):
                continue
            target = stmt.target if isinstance(stmt, ast.AnnAssign) else stmt.targets[0]
            if isinstance(target, ast.Name):
                qual = f"{prefix}.{target.id}" if prefix else target.id
                out.setdefault(qual, nxt.value.value)


def _module_name(pyi: Path, root: Path) -> str:
    """``root/mylib/core/__init__.pyi`` -> ``mylib.core``。"""
    parts = list(pyi.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_docstring(tree: ast.Module) -> str | None:
    """Read a module string placed immediately after future imports.

    Stub files commonly put a future import before the translated module
    string. Although Python does not treat that string as the runtime module
    docstring, it remains the intended documentation text.
    """
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
            continue
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            return stmt.value.value
        return None
    return None


def load_stub_docstrings(root: Path) -> dict[str, str]:
    """扫描 ``root`` 下所有 ``*.pyi``，返回“对象全名 -> docstring”映射。"""
    out: dict[str, str] = {}
    for pyi in sorted(root.rglob("*.pyi")):
        module = _module_name(pyi, root)
        tree = ast.parse(pyi.read_text(encoding="utf-8"), filename=str(pyi))
        if (doc := _module_docstring(tree)) is not None:
            out[module] = doc
        local: dict[str, str] = {}
        _walk_scopes(tree.body, "", local)
        out.update({f"{module}.{qual}": text for qual, text in local.items()})
    return out


def _on_builder_inited(app: Sphinx) -> None:
    _MAPS.clear()
    for raw in app.config.pyi_docstring_roots:
        path = Path(raw)
        root = path if path.is_absolute() else (Path(app.confdir) / path).resolve()
        _MAPS.update(load_stub_docstrings(root))


def _inherited_stub_docstring(name: str, obj: object) -> str | None:
    """Use the defining base object's English stub docstring for inherited members.

    Sphinx reports inherited members under the subclass name, while their
    implementation object still points at the defining base class. Attribute
    documentation needs a separate MRO lookup because the runtime attribute
    value (for example, a string class variable) does not retain its docstring.
    """
    implementation = getattr(obj, "__func__", obj)
    module = getattr(implementation, "__module__", None)
    qualname = getattr(implementation, "__qualname__", None)
    if module and qualname:
        defining_name = f"{module}.{qualname}"
        if defining_name != name and (doc := _MAPS.get(defining_name)) is not None:
            return doc

    # Resolve the owning class from the already-imported module. Do not import
    # anything here: autodoc has loaded the target module and its bases already.
    parts = name.split(".")
    for split in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:split])
        current: object | None = sys.modules.get(module_name)
        if current is None:
            continue
        try:
            for component in parts[split:-1]:
                current = getattr(current, component)
        except AttributeError:
            continue
        if not isinstance(current, type):
            continue
        attribute = parts[-1]
        # The documented path may be a re-export (for example,
        # ``flowing.runtime.ProvideNode`` is defined in ``flowing.provide``).
        # Try the runtime class's defining path before searching its bases.
        for owner in current.__mro__:
            owner_name = f"{owner.__module__}.{owner.__qualname__}.{attribute}"
            if (doc := _MAPS.get(owner_name)) is not None:
                return doc
        return None
    return None


def _on_process_docstring(app, what, name, obj, options, lines):
    doc = _MAPS.get(name) or _inherited_stub_docstring(name, obj)
    if doc is not None:
        lines[:] = doc.splitlines()


def setup(app: Sphinx) -> dict[str, object]:
    app.add_config_value("pyi_docstring_roots", [], "env")
    app.connect("builder-inited", _on_builder_inited)
    app.connect("autodoc-process-docstring", _on_process_docstring)
    return {"version": "1.0", "parallel_read_safe": True, "parallel_write_safe": True}
