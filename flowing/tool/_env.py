"""``flowing.tool._env`` —— 声明式工具的 env 映射 Jinja 渲染助手（内部 API，不属稳定契约）。

``env:`` 映射值中的 ``{{ ... }}`` 模板在实例化期以 ``os.environ`` 为
上下文求值（StrictUndefined，缺变量即报错）。McpTool 与 RequestTool
共用。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import os

from types import MappingProxyType

import jinja2

from flowing.errors import FormatError

_ENV_JINJA = jinja2.Environment(autoescape=False, undefined=jinja2.StrictUndefined)
"""``{{ env.X }}`` 凭证模板的一次性渲染环境。

顶层 ``env`` 绑定 ``os.environ`` 只读视图；``StrictUndefined`` 使缺失变量
fail fast（渲染方就地包成 ``FormatError``），不静默降级为空串。渲染产物
不进消息、不落盘。内部 API。
"""


def _render_env_templates(values: "dict[str, str] | None") -> "dict[str, str] | None":
    """把映射的每个字符串值按 ``{{ env.X }}`` 模板一次性渲染。内部 API。

    求值时点：装配期（工具实例构造，连接 / 请求之前一次完成）。缺失变量 →
    :class:`flowing.errors.FormatError` （fail fast，不静默降级）。
    """
    if values is None:
        return None
    rendered: dict[str, str] = {}
    for key, value in values.items():
        try:
            rendered[key] = _ENV_JINJA.from_string(str(value)).render(
                env=MappingProxyType(os.environ))
        except jinja2.UndefinedError as exc:
            raise FormatError(f"environment variable referenced by template {value!r} is missing: {exc}") from exc
    return rendered


