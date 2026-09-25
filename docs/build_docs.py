#!/usr/bin/env python3
"""多语言 Sphinx 文档构建脚本（在 uv 项目根目录下执行）。

用法::

    uv run python build_docs.py                 # 构建全部语言
    uv run python build_docs.py --lang zh_CN    # 只构建中文
    uv run python build_docs.py --clean --strict
    uv run python build_docs.py --check         # 源码/桩一致性检查，不构建

产物::

    docs/_build/html/index.html         # 多语言入口页，默认跳进 DEFAULT_LANGUAGE 站
    docs/_build/html/en/index.html      # 英文站：docstring 取自英文 .pyi 桩
    docs/_build/html/zh_CN/index.html   # 中文站：docstring 取自 flowing/ 源码

等价的裸 sphinx-build 命令（conf.py 共享一份，源码目录按语言切换）::

    sphinx-build -b html -c docs docs/en    docs/_build/html/en
    sphinx-build -b html -c docs -t zh_cn docs/zh_CN docs/_build/html/zh_CN
                                       ^^^^^^^^ 触发 conf.py 启用 pyi_docstrings 扩展

语言登记（提前登记，数目不定）：LANGUAGES 表新增一行即完成一次接入——
构建循环、侧边栏切换菜单（经环境变量 SPHINX_DOC_LANGUAGES 传给 conf.py）、
多语言入口页三处全部自动跟随。
"""

from __future__ import annotations

import argparse
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
BUILD = DOCS / "_build"
SRC = ROOT / "flowing"
STUBS = DOCS / "stubs" / "en" / "flowing"

# 语言登记处：label 用于侧边栏切换菜单与入口页显示。
LANGUAGES: dict[str, dict] = {
    "en": {"srcdir": DOCS / "en", "extra_args": (), "label": "English"},
    "zh_CN": {"srcdir": DOCS / "zh_CN", "extra_args": ("-t", "zh_cn"), "label": "中文"},
}

# 站点根入口页的默认跳转目标：与 docstring 的源语言无关，仅是访问根路径时
# 的落地语言。该目录不存在时入口页退化为纯语言列表（不跳转）。
DEFAULT_LANGUAGE = "en"


def build(lang: str, *, strict: bool) -> int:
    cfg = LANGUAGES[lang]
    registered = ",".join(f"{code}:{c['label']}" for code, c in LANGUAGES.items())
    env = {**os.environ, "SPHINX_DOC_LANGUAGES": registered}
    cmd = [
        sys.executable, "-m", "sphinx",
        "-b", "html",
        "-c", str(DOCS),  # 语言无关的共享 conf.py
        "-d", str(BUILD / "doctrees" / lang),
        *cfg["extra_args"],  # zh_CN：打开桩 docstring 注入
        *(("-W", "--keep-going") if strict else ()),
        str(cfg["srcdir"]),
        str(BUILD / "html" / lang),
    ]
    print(f"[{lang}] $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


def write_landing_page() -> None:
    """自动扫描产物目录，写多语言入口页 ``docs/_build/html/index.html``。

    任何含 ``index.html`` 的一级子目录都会列出——以后加入第三种语言
    （无论走不走 LANGUAGES 登记），入口页都无需改动。若 ``DEFAULT_LANGUAGE``
    对应的产物存在，入口页额外带 meta refresh 直接跳进该语言站，语言列表
    作为不刷新环境（或想换语言）时的回退。
    """
    html_root = BUILD / "html"
    if not html_root.is_dir():
        return
    roots = sorted(p for p in html_root.iterdir() if (p / "index.html").is_file())
    if not roots:
        return
    items = "\n".join(
        f'    <li><a href="{p.name}/index.html">'
        f'{LANGUAGES.get(p.name, {}).get("label", p.name)}</a>'
        f' <span class="code">({p.name})</span></li>'
        for p in roots
    )
    default_href = f"{DEFAULT_LANGUAGE}/index.html"
    redirect = ""
    note = ""
    if (html_root / DEFAULT_LANGUAGE / "index.html").is_file():
        redirect = f'<meta http-equiv="refresh" content="0; url={default_href}">\n'
        note = (f'<p><small>正在进入默认语言：'
                f'<a href="{default_href}">{DEFAULT_LANGUAGE}</a>……</small></p>\n')
    page = f"""<!DOCTYPE html>
<html lang="{DEFAULT_LANGUAGE}">
<head>
<meta charset="utf-8">
{redirect}<title>Flowing Documentation</title>
<style>
  body {{ font-family: sans-serif; max-width: 40em; margin: 4em auto; line-height: 1.6; }}
  li {{ margin: 0.4em 0; }}
  .code {{ color: #666; font-size: 0.9em; }}
</style>
</head>
<body>
<h1>Flowing Documentation</h1>
{note}<p>Choose a language / 选择语言以浏览文档：</p>
<ul>
{items}
</ul>
</body>
</html>
"""
    (html_root / "index.html").write_text(page, encoding="utf-8")
    names = ", ".join(p.name for p in roots)
    print(f"[landing] {html_root / 'index.html'}（自动扫描到 {len(roots)} 种语言：{names}）")


def _public_names(path: Path) -> set[str]:
    """收集模块级/类级的公开符号名（函数、类、赋值目标），用于比对桩。

    不深入函数体：局部变量不属于公开 API。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    stack: list[tuple[list[ast.stmt], str]] = [(tree.body, "")]
    while stack:
        body, prefix = stack.pop()
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                if not stmt.name.startswith("_"):
                    names.add(prefix + stmt.name)
                stack.append((stmt.body, prefix + stmt.name + "."))
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not stmt.name.startswith("_"):
                    names.add(prefix + stmt.name)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if not stmt.target.id.startswith("_"):
                    names.add(prefix + stmt.target.id)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        names.add(prefix + target.id)
    return names


def _init_instance_names(path: Path) -> set[str]:
    """Collect public ``self`` fields assigned directly within class initializers.

    These may be declared in a ``.pyi`` even when the implementation only
    creates them in ``__init__`` rather than annotating them in the class body.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()

    class _InitializerVisitor(ast.NodeVisitor):
        def __init__(self, root: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.root = root
            self.fields: set[str] = set()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is self.root:
                self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node is self.root:
                self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if (
                isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and not node.attr.startswith("_")
            ):
                self.fields.add(node.attr)
            self.generic_visit(node)

    def visit_scope(body: list[ast.stmt], prefix: str) -> None:
        for stmt in body:
            if not isinstance(stmt, ast.ClassDef):
                continue
            class_prefix = f"{prefix}{stmt.name}."
            for member in stmt.body:
                if (
                    isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and member.name == "__init__"
                ):
                    visitor = _InitializerVisitor(member)
                    visitor.visit(member)
                    names.update(f"{class_prefix}{name}" for name in visitor.fields)
            visit_scope(stmt.body, class_prefix)

    visit_scope(tree.body, "")
    return names


def _exported_names(path: Path) -> set[str]:
    """Read literal module exports from ``__all__``, when present."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, (ast.List, ast.Tuple)):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in stmt.targets):
            continue
        names.update(
            item.value
            for item in stmt.value.elts
            if isinstance(item, ast.Constant)
            and isinstance(item.value, str)
            and not item.value.startswith("_")
        )
    return names


def _imported_names(path: Path) -> set[str]:
    """Collect explicit imported names so stub re-exports can mirror ``__all__``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom):
            names.update(
                alias.asname or alias.name
                for alias in stmt.names
                if alias.name != "*" and not (alias.asname or alias.name).startswith("_")
            )
        elif isinstance(stmt, ast.Import):
            names.update(
                alias.asname or alias.name.split(".")[0]
                for alias in stmt.names
                if not (alias.asname or alias.name.split(".")[0]).startswith("_")
            )
    return names


def check() -> int:
    """比对源码与桩：公开符号一致，源码中的公开 docstring 均有英文镜像。"""
    sys.path.insert(0, str(DOCS / "_ext"))
    from pyi_docstrings import _walk_scopes

    failures = 0
    for py in sorted(SRC.rglob("*.py")):
        parts = list(py.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = ".".join(["flowing", *parts])
        pyi = STUBS.joinpath(*py.relative_to(SRC).parts).with_suffix(".pyi")

        if not pyi.exists():
            print(f"✗ {module}: 缺少镜像桩 {pyi}")
            failures += 1
            continue

        src_tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        stub_tree = ast.parse(pyi.read_text(encoding="utf-8"), filename=str(pyi))
        src_names = _public_names(py) | _exported_names(py)
        init_fields = _init_instance_names(py)
        stub_names = (
            _public_names(pyi)
            | _exported_names(pyi)
            | (_imported_names(pyi) & src_names)
        )
        missing = sorted(src_names - stub_names)
        extra = sorted(stub_names - src_names - init_fields)

        source_docstrings: set[str] = set()
        if ast.get_docstring(src_tree) is not None:
            source_docstrings.add("")
        local_docs: dict[str, str] = {}
        _walk_scopes(src_tree.body, "", local_docs)
        source_docstrings.update(local_docs)

        translated_docstrings: set[str] = set()
        if ast.get_docstring(stub_tree) is not None:
            translated_docstrings.add("")
        stub_local_docs: dict[str, str] = {}
        _walk_scopes(stub_tree.body, "", stub_local_docs)
        translated_docstrings.update(stub_local_docs)

        def is_public_doc(name: str) -> bool:
            return not name or all(not part.startswith("_") for part in name.split("."))

        required_docs = {
            name for name in source_docstrings if is_public_doc(name)
        }
        translated_docs = {
            name
            for name in translated_docstrings
            if name in stub_names and is_public_doc(name)
        }
        if "" in translated_docstrings:
            translated_docs.add("")
        untranslated = sorted(required_docs - translated_docs)
        extra_docs = sorted(translated_docs - required_docs - init_fields)
        if missing or extra or untranslated or extra_docs:
            failures += 1
            print(f"✗ {module}")
            for name in missing:
                print(f"    桩缺少符号: {name}")
            for name in extra:
                print(f"    桩多出符号: {name}")
            for name in untranslated:
                print(f"    桩缺 docstring: {name or '(module)'}")
            for name in extra_docs:
                print(f"    桩多出 docstring: {name or '(module)'}")
        else:
            print(
                f"✓ {module}: {len(src_names)} 个公开符号，"
                f"{len(required_docs)} 个源码 docstring 均有桩镜像"
            )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="构建多语言 Sphinx 文档 / 检查桩覆盖",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--lang", choices=[*LANGUAGES, "all"], default="all",
                        help="只构建指定语言（默认全部）")
    parser.add_argument("--clean", action="store_true", help="构建前清空 docs/_build")
    parser.add_argument("--strict", action="store_true",
                        help="警告视为错误（-W --keep-going）")
    parser.add_argument("--check", action="store_true",
                        help="只做源码/桩一致性检查，不构建")
    args = parser.parse_args()

    if args.check:
        return check()
    if args.clean:
        shutil.rmtree(BUILD, ignore_errors=True)

    returncode = 0
    for lang in [*LANGUAGES] if args.lang == "all" else [args.lang]:
        returncode |= build(lang, strict=args.strict)
    if returncode == 0:
        write_landing_page()
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
