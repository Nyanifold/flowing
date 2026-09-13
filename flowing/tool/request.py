"""``flowing.tool.request`` —— ``RequestTool``：``.fya`` 声明驱动的 HTTP/HTTPS 请求工具。

零代码工具形态之一（``.fya`` 声明 ``type: request``）。公开符号经
``flowing.tool`` re-export。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import base64
import json
import os

from types import MappingProxyType
from typing import Any, Literal

import jinja2
import jinja2.meta

from flowing.errors import FormatError, MissingSchemaError
from flowing.params import schema_to_model

from flowing.tool._env import _ENV_JINJA, _render_env_templates
from flowing.tool.core import Tool, ToolDefinition, _strip_unsupported_background

def _auth_headers(auth: dict[str, Any]) -> dict[str, str]:
    """``auth`` 语法糖 → 请求头 dict（``basic`` / ``bearer`` / ``api_key``）。

    内部 API。未知类型 → ``FormatError`` （声明期 fail fast）。
    """
    auth_type = auth.get("type")
    if auth_type == "basic":
        cred = base64.b64encode(
            f"{auth.get('username', '')}:{auth.get('password', '')}".encode()
        ).decode()
        return {"Authorization": f"Basic {cred}"}
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {auth.get('value', '')}"}
    if auth_type == "api_key":
        return {str(auth["header"]): str(auth.get("value", ""))}
    raise FormatError(f"unknown auth type: {auth_type!r} (must be basic / bearer / api_key)")




_ENV_URL_JINJA = jinja2.Environment(autoescape=False, undefined=jinja2.DebugUndefined)
"""RequestTool URL 的装配期 env 渲染环境。内部 API。

URL 是两阶段模板：装配期先渲染 ``{{ env.X }}`` （DebugUndefined 把非 env
的占位原样保留为 ``{{ name }}`` 文本），执行期再以 args 渲染路径参数
（StrictUndefined）。env 引用缺失时在执行期暴露为渲染错误（error 结果）
——headers / auth 的 env 引用才是装配期 fail fast（`_ENV_JINJA`）。
"""




class RequestTool(Tool):
    
    """HTTP/HTTPS 请求工具实例——按 ``args`` 构造请求，零代码。

    .. rubric:: 功能介绍

    ``type: request`` 的实例类。``url`` 与 ``args`` 必填；参数到请求的
    映射自动完成，可用 ``body`` / ``query`` 显式覆盖。与 `CliTool` 同理：
    把「调一个 HTTP API」降为纯声明；凭证只经 ``{{ env.X }}`` 模板进入
    请求头，不进消息、不落盘。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # create-order/TOOL.fya
        name: create-order
        type: request
        description: 创建新订单，在用户确认购物车内容后调用。
        url: https://api.example.com/v1/orders
        method: POST                       # 默认 POST
        headers:
          Authorization: "Bearer {{ env.SHOP_API_KEY }}"
        auth:                              # headers 的语法糖；冲突时 auth 优先
          type: api_key                    # basic / bearer / api_key
          header: X-API-Key
          value: "{{ env.API_KEY }}"
        args:
          user_id:
            type: string
            description: 用户 ID
          items:
            type: array
            description: 购物车商品列表
          currency: CNY                  # 糖：字面量 → {type: string, default: CNY}
        output:
          type: object
          properties:
            order_id:
              type: string
            total_amount:
              type: number
        timeout: 30                        # 秒，默认 30
        expected_status: [200, 201]        # 默认 [200, 201]

    .. rubric:: 行为要点

    - URL 模板中出现的 ``{{ arg_name }}`` 识别为路径参数，自动从
      body / query 排除；
    - 非路径参数按 ``method`` 决定去向：``POST`` / ``PUT`` / ``PATCH``
      → JSON body；``GET`` / ``DELETE`` → query string；``body`` /
      ``query`` 声明可显式覆盖；
    - ``auth`` 与 ``headers`` 同时声明时，``auth`` 生成的头优先；
    - 响应默认按 JSON 解析作为返回值；声明 ``output`` 时按 schema 提取
      字段，无关字段忽略（非 JSON 响应回退为文本）；
    - 响应状态码不在 ``expected_status`` 内 → ``status="error"`` 结果
      （含状态码与响应摘要），不抛异常。

    :raises flowing.errors.MissingSchemaError: 未声明 ``args`` 时。

    .. seealso::

        - :class:`flowing.tool.CliTool` —— 零代码工具的另一形态。
    """


    def __init__(
        self,
        *,
        definition: ToolDefinition,
        url: str,
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST",
        headers: dict[str, str] | None = None,
        auth: dict[str, Any] | None = None,
        query: list[str] | None = None,
        body: list[str] | None = None,
        expected_status: list[int] | None = None,
        timeout: float = 30.0,
    ) -> None:
        """构造 Request 工具实例。

        :param definition: 工具声明；``params`` 必填，``output_schema``
          对应 ``.fya`` 的 ``output:``。
        :param url: 端点 URL，支持 Jinja2 模板（路径参数占位）。
        :param method: HTTP 方法，默认 ``POST``。
        :param headers: 静态请求头，值支持模板。
        :param auth: 认证语法糖（``basic`` / ``bearer`` / ``api_key``）；
          与 ``headers`` 冲突时优先。
        :param query: 强制走 query string 的参数名列表。
        :param body: 强制走 JSON body 的参数名列表。
        :param expected_status: 预期成功状态码；``None`` 等价 ``[200, 201]``。
        :param timeout: 超时秒数，默认 30。
        """
        if not definition.params_schema:
            raise MissingSchemaError("request tools must declare args")
        self.definition = definition
        # URL 两阶段渲染：装配期先渲染 {{ env.X }}（非 env
        # 占位原样保留），执行期再渲染路径参数（见 execute）
        self.url = _ENV_URL_JINJA.from_string(url).render(
            env=MappingProxyType(os.environ))
        self.method = method
        # {{ env.X }} 模板装配期一次性渲染：凭证只经模板进
        # 请求头，不进消息、不落盘；缺失变量 FormatError fail fast
        self.headers = _render_env_templates(headers)
        self.auth = _render_env_templates(auth)
        # auth 语法糖在构造期展开为请求头（未知类型 FormatError fail fast）；
        # 执行期与 headers 冲突时本表优先（见类 docstring 行为要点）
        self._auth_headers: dict[str, str] = (
            _auth_headers(self.auth) if self.auth else {})
        self.query = query
        self.body = body
        self.expected_status = [200, 201] if expected_status is None else expected_status
        self.timeout = timeout
        self._execution = None
        # URL 模板的路径参数名在创建期提取（jinja2 AST 静态分析）——执行期
        # 这些参数从 body/query 排除（见类 docstring 行为要点第 1 条）
        self._path_params: frozenset[str] = frozenset(
            jinja2.meta.find_undeclared_variables(_ENV_JINJA.parse(self.url)))
        # 创建时定内部校验模型（fya 声明经 params.schema_to_model
        # 桥接——definition.params_schema 即桥接产物 schema，再建最终模型）
        self._args_model = schema_to_model("Args", self.definition.params_schema)
        _strip_unsupported_background(self)   # background 仅 script 型受支持：告警 + 强制 False

    async def execute(self, **kwargs: Any) -> Any:
        """按 args 构造 HTTP 请求并取回响应（args → 请求映射规则的执行体）。

        .. rubric:: 行为要点

        - URL 模板以 args 渲染（路径参数不转义——URL 结构由声明方负责），
          路径参数自动从 body / query 排除；
        - 其余参数去向：``query`` / ``body`` 声明显式覆盖优先；否则按
          ``method``——``POST`` / ``PUT`` / ``PATCH`` → JSON body，
          ``GET`` / ``DELETE`` → query string；
        - ``auth`` 展开的请求头与 ``headers`` 冲突时 auth 优先；
        - 响应状态码不在 ``expected_status`` 内 → 抛异常（含状态码与响应
          摘要），由 ``__call__`` 包装为 ``status="error"`` 结果，不向
          调用方抛；
        - 响应默认按 JSON 解析；声明了 ``output`` （``definition.output_schema``）时按 schema 的 properties 提取字段，无关字段
          忽略；非 JSON 响应回退为文本。
        """
        import httpx

        url = _ENV_JINJA.from_string(self.url).render(**kwargs)   # 路径参数渲染（不转义）
        headers = dict(self.headers or {})
        headers.update(self._auth_headers)   # auth 与 headers 冲突时 auth 优先
        query_names = set(self.query or ())
        body_names = set(self.body or ())
        body_methods = self.method in ("POST", "PUT", "PATCH")
        query_params: dict[str, Any] = {}
        json_body: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key in self._path_params:
                continue   # URL 路径参数自动从 body/query 排除
            if key in query_names:   # query/body 显式覆盖优先
                query_params[key] = value
            elif key in body_names:
                json_body[key] = value
            elif body_methods:   # 未显式指定：按 method 默认去向
                json_body[key] = value
            else:
                query_params[key] = value   # GET/DELETE → query string
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(
                self.method, url, headers=headers,
                params=query_params or None, json=json_body or None)
        if resp.status_code not in self.expected_status:
            raise RuntimeError(
                f"request to {url} returned unexpected status {resp.status_code}"
                f" (expected {self.expected_status}): {resp.text[:500]}")
        try:
            data = resp.json()
        except json.JSONDecodeError:
            return resp.text   # 非 JSON 响应回退为文本
        output_schema = self.definition.output_schema
        if output_schema and isinstance(data, dict):
            fields = output_schema.get("properties")
            if fields:   # 声明 output 时按 schema 提取字段，无关字段忽略
                data = {k: data[k] for k in fields if k in data}
        return data


