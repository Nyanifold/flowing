"""Internal Jinja renderer shared by declarative tools.

During tool construction, templates in values of the ``env:`` mapping are
rendered once with ``os.environ`` as their context. The renderer uses
``StrictUndefined``, so referencing a missing environment variable raises an
error. ``McpTool`` and ``RequestTool`` use this helper.
"""
from typing import Any
import jinja2

_ENV_JINJA: jinja2.Environment
def _render_env_templates(values: dict[str, str] | None) -> dict[str, str] | None: ...
