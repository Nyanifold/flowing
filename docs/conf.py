"""Shared Sphinx configuration for the bilingual Flowing documentation site."""

from __future__ import annotations

import os
import sys
from pathlib import Path

CONF_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CONF_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

project = "Flowing"
author = "Flowing"
copyright = "2026, Flowing"
release = "0.1.1"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.viewcode",
    "myst_parser",
]

# The source code keeps the Chinese contract. The English build overlays the
# English docstrings from the mirrored .pyi files.
if tags.has("zh_cn"):
    language = "zh_CN"
    html_title = "Flowing 文档"
else:
    language = "en"
    html_title = "Flowing documentation"
    sys.path.insert(0, str(CONF_DIR / "_ext"))
    extensions.append("pyi_docstrings")
    pyi_docstring_roots = [str(CONF_DIR / "stubs" / "en")]


def _doc_languages() -> list[dict[str, str]]:
    raw = os.environ.get("SPHINX_DOC_LANGUAGES", f"{language}:{language}")
    result = []
    for entry in raw.split(","):
        code, _, label = entry.partition(":")
        result.append({"code": code, "label": label or code})
    return result


html_context = {
    "current_language": language,
    "doc_languages": _doc_languages(),
    # Light only. The theme reads this from the page context (it is not a
    # declared theme option), and the color-mode switch is not rendered at all
    # — see _templates/article-header-buttons.html.
    "default_mode": "light",
}

templates_path = [str(CONF_DIR / "_templates")]
html_static_path = [str(CONF_DIR / "_static")]
html_css_files = ["book-theme.css"]

html_theme = "sphinx_book_theme"
html_logo = "_static/logo.svg"
html_favicon = "_static/favicon-32.png"
html_theme_options = {
    "show_toc_level": 2,
    "use_repository_button": False,
    "use_issues_button": False,
    "use_download_button": False,
    "use_fullscreen_button": False,
}


def _skip_redeclared_provider_name(app, what, name, obj, skip, options):
    """Hide FakeProvider.name, whose inherited Provider.name contract repeats on the same page."""
    current = app.env.current_document
    if (
        what == "class"
        and name == "name"
        and current.autodoc_module == "flowing.providers.provider"
        and current.autodoc_class == "FakeProvider"
    ):
        return True
    return None


def setup(app):
    app.connect("autodoc-skip-member", _skip_redeclared_provider_name)

autodoc_member_order = "bysource"
autosummary_generate = True
# Keep autosummary-generated API pages in the document tree. Example READMEs,
# prompt assets, and chapter assertion suites are supporting files rather than
# standalone documentation pages.
exclude_patterns = [
    "_build",
    "**/examples/**",
    "**/_tests/**",
    "**/skills/**",
    "**/target/**",
    "Thumbs.db",
    ".DS_Store",
]
