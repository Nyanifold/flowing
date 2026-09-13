"""``flowing.tool.mcp`` —— ``McpTool``：MCP 服务器（本地 stdio 进程 / 远程端点）声明驱动的工具。

每个声明独立实例；env 映射的 Jinja 渲染与 RequestTool 共用
``flowing.tool._env``。公开符号经 ``flowing.tool`` re-export。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import os

from contextlib import asynccontextmanager
from types import MappingProxyType
from typing import Any

import jinja2

from flowing.errors import (
    AmbiguousMcpSourceError,
    FormatError,
    MissingMcpSourceError,
)
from flowing.params import apply_param_overrides, schema_to_model

from flowing.tool._env import _ENV_JINJA, _render_env_templates
from flowing.tool.core import Tool, ToolDefinition, _strip_unsupported_background

def _mcp_input_schema_to_params(input_schema: "dict[str, Any] | None") -> dict[str, dict[str, Any]]:
    """MCP ``inputSchema`` （完整 JSON Schema object）→ 框架 properties 映射。

    内部 API。两点归一：

    - property 键裁剪到 :data:`flowing.params.SCHEMA_KEYWORDS` 子集——
      MCPServer 等服务端生成的 inputSchema 带 ``title`` 等超子集键，
      不裁剪会在 ``schema_to_model`` 桥接时炸 ``FormatError``；
    - required 口径对齐（框架以 ``default`` 有无派生）：``required``
      列表之外的 property 若无 ``default`` 补 ``default=None``——否则
      可选参数会被 ``schema_to_model`` 建成必填字段。
    """
    from flowing.params import SCHEMA_KEYWORDS

    schema = input_schema or {}
    props = {k: {kk: vv for kk, vv in v.items() if kk in SCHEMA_KEYWORDS}
             for k, v in schema.get("properties", {}).items()}
    required = set(schema.get("required") or ())
    for key, prop in props.items():
        if key not in required and "default" not in prop:
            prop["default"] = None
    return props


class McpTool(Tool):
    
    """MCP 工具实例——连接 MCP 服务器并代理其暴露的工具 schema。

    .. rubric:: 功能介绍

    ``type: mcp`` 的实例类。两种来源互斥：``command`` （本地 stdio 进程）
    或 ``url`` （远程端点，``/sse`` 结尾走 SSE、其余走 streamable HTTP）。
    默认 `ToolDefinition` 来自 MCP 服务器 ``list_tools()`` 返回的 schema，
    可经 ``overrides`` 局部覆写。MCP 工具无用户代码，是纯参数化配置——
    每个声明独立实例（区别于 script 单例）。

    命名规则：MCP 声明块代理的是一组服务端工具，注册 / 解析时每个实际
    工具的规范名 = ``<fya 声明名>-<server 暴露工具名>`` （如声明
    ``name: github``、服务端暴露 ``create-issue`` → 注册规范名
    ``github-create-issue``）——服务端工具名空间天然带声明名前缀，不同
    MCP 来源的同名工具不撞名。Agent 侧引用（``tools:`` 条目 /
    ``add_tool``）按合成名引用（可照常 ``as`` 别名）。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # 本地 stdio 进程
        name: search
        type: mcp
        description: 搜索互联网内容。在需要查找最新信息时调用。
        command: npx
        args: ["-y", "@modelcontextprotocol/server-brave-search"]
        env:
          BRAVE_API_KEY: "{{ env.BRAVE_API_KEY }}"

        # 远程端点
        name: github
        type: mcp
        url: https://mcp.example.com/github/sse
        headers:
          Authorization: "Bearer {{ env.GITHUB_TOKEN }}"
        tools: [create-issue, list-prs]     # 服务端工具子集
        overrides:
          create-issue:
            args:
              title: {description: "Issue 标题，不超过 80 字符。"}

    模板中的 ``env`` 是渲染上下文顶层对象（绑定 ``os.environ``），凭证经
    ``{{ env.X }}`` 注入，不硬编码、不进消息、不落盘；缺失变量在装配期
    fail fast（``FormatError``）。

    .. rubric:: 行为要点

    - 来源识别：存在 ``command`` → stdio；存在 ``url`` → 远程；两者都有
      → `AmbiguousMcpSourceError`；两者都无 → `MissingMcpSourceError`。
    - 同名冲突：同命名空间规范名重名注册永远抛 `ToolNameConflictError`
      ——注册名为合成名 ``<声明名>-<server 暴露名>``，撞名即声明名重复，
      须换声明名（或注册到不同命名空间）。
    - MCP 服务器的 ``outputSchema`` 自动填入 `ToolDefinition.output_schema`
      （随声明携带，adapter 白名单不映射；当前实现不据此做结果校验，也
      不喂模型）。
    - 连接失败 / 服务端 ``isError`` → ``status="error"`` 结果；无连接重试
      策略。

    .. seealso::

        - :class:`flowing.tool.ToolRegistry` —— 重名约束的执行者。
    """


    def __init__(
        self,
        *,
        definition: ToolDefinition,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        tools: list[str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        """按来源声明构造 MCP 工具实例。

        :param definition: 工具声明（通常来自 ``list_tools()`` schema 经
          ``overrides`` 覆写后的产物）。
        :param command: stdio 模式启动命令；与 ``url`` 互斥。
        :param args: 启动命令参数列表。
        :param env: 子进程环境变量（值支持 ``{{ env.X }}`` 模板）。
        :param url: 远程端点 URL；与 ``command`` 互斥。
        :param headers: 远程模式请求头（值支持模板）。
        :param tools: 只暴露的服务端工具名子集；``None`` 全量暴露。
        :param overrides: 对服务端 schema 的局部覆写
          （``{工具名: {description/args: ...}}``）。
        :raises flowing.errors.AmbiguousMcpSourceError: ``command`` 与
          ``url`` 同时给出。
        :raises flowing.errors.MissingMcpSourceError: 两者均未给出。
        """
        if command is not None and url is not None:
            raise AmbiguousMcpSourceError("both command and url were given")  # 来源互斥
        if command is None and url is None:
            raise MissingMcpSourceError("neither command nor url was given")
        self.definition = definition
        self.command = command
        self.args = args
        # {{ env.X }} 模板装配期一次性渲染：缺失变量 FormatError
        # fail fast；渲染产物只进连接配置，不进消息、不落盘
        self.env = _render_env_templates(env)
        # url 无路径参数阶段（区别于 RequestTool 的两阶段），装配期一次渲染；
        # 缺失 env 变量 FormatError fail fast（与 _render_env_templates 同口径）
        if url is not None:
            try:
                self.url = _ENV_JINJA.from_string(url).render(env=MappingProxyType(os.environ))
            except jinja2.UndefinedError as exc:
                raise FormatError(f"environment variable referenced by template {url!r} is missing: {exc}") from exc
        else:
            self.url = None
        self.headers = _render_env_templates(headers)
        self.tools = tools
        self.overrides = overrides
        self._execution = None
        # 声明实例（组代理）为 None，不可执行；list_tools() 展开产物置为
        # 服务端工具名。内部 API。
        self._server_tool_name: str | None = None
        # 创建时定内部校验模型（fya 声明经 params.schema_to_model
        # 桥接——definition.params_schema 即桥接产物 schema，再建最终模型）
        self._args_model = schema_to_model("Args", self.definition.params_schema)
        _strip_unsupported_background(self)   # background 仅 script 型受支持：告警 + 强制 False

    @asynccontextmanager
    async def _connect(self) -> "Any":
        """建立一次 MCP 会话（惰性连接：``list_tools`` / ``execute`` 各连
        一次，用后关闭；无连接池、无重试策略）。内部 API，不属稳定契约。

        连接失败异常上抛（``execute`` 内由 ``__call__`` 包装为
        ``status="error"`` 结果）。url 形态的传输判别：URL 路径以
        ``/sse`` 结尾 → SSE；其余 → streamable HTTP。stdio 的 ``env``
        直传 ``StdioServerParameters`` （SDK 内与默认环境合并）。
        """
        from mcp import ClientSession, StdioServerParameters   # 函数内 import：mcp SDK 重，非 MCP 用户不付 import 成本

        if self.command is not None:
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=self.command, args=self.args or [], env=self.env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        elif (self.url or "").rstrip("/").endswith("/sse"):
            from mcp.client.sse import sse_client

            async with sse_client(self.url, headers=self.headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        else:
            from mcp.client import streamable_http as _streamable_http

            # mcp 2.x：headers 不再作 transport 级参数，须配置在自建的
            # httpx2.AsyncClient 上（SDK 文档口径：follow_redirects=True，
            # 长活 GET 流需 Timeout(30, read=300)）；无 headers 时省略
            # http_client，用 SDK 内置默认客户端（同口径超时）。
            if self.headers:
                import httpx2

                async with httpx2.AsyncClient(
                        headers=self.headers, follow_redirects=True,
                        timeout=httpx2.Timeout(30, read=300)) as http_client:
                    async with _streamable_http.streamable_http_client(
                            self.url, http_client=http_client) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
            else:
                async with _streamable_http.streamable_http_client(
                        self.url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session

    async def list_tools(self) -> "list[McpTool]":
        """连接服务器拉取 ``list_tools()`` schema，产出逐工具代理实例列表。

        .. rubric:: 功能介绍

        装配期 schema 拉取的唯一入口：``ToolRegistry.get`` 命中 mcp 型
        TOOL.fya 只产声明实例（骨架 definition）；装配层在 get 解析完成
        后调用本方法一次——每个服务端工具产一个独立 `McpTool` 代理实例，
        规范名 = ``<fya 声明名>-<server 暴露工具名>``，由装配层经
        ``ToolRegistry.register`` 按合成名注册（撞名 →
        ``ToolNameConflictError``，注册表层承载）。``execute`` 不依赖本
        方法（惰性连接），但 LLM 可见声明必须由本方法的产物承载。

        .. rubric:: 行为要点

        - ``tools`` 子集过滤：声明了 ``tools`` 时仅展开子集内的服务端
          工具；``None`` 全量展开。
        - ``overrides`` 应用：``{工具名: {description/args: ...}}``——
          ``description`` 整体替换；``args`` 经
          :func:`flowing.params.apply_param_overrides` 对 inputSchema
          派生的 properties 稀疏覆写（非法关键字 fail-fast）。
        - 服务端 ``outputSchema`` 自动填入
          :attr:`ToolDefinition.output_schema`——存储、随声明携带；当前
          实现不据此做结果校验，也不喂模型。
        - MCP ``inputSchema`` → 框架 properties 映射的 required 口径对齐
          ：框架以 ``default`` 有无派生 requiredness，服务端 schema 的
          ``required`` 列表之外的 property 若无 ``default`` 补
          ``default=None``——否则可选参数会被 ``schema_to_model`` 建成
          必填字段。
        """
        async with self._connect() as session:
            result = await session.list_tools()
        produced: list[McpTool] = []
        for server_tool in result.tools:
            if self.tools is not None and server_tool.name not in self.tools:
                continue   # tools 子集之外的工具不暴露
            override = (self.overrides or {}).get(server_tool.name) or {}
            params = _mcp_input_schema_to_params(server_tool.input_schema)
            if "args" in override:
                params = apply_param_overrides(params, override["args"])
            definition = ToolDefinition(
                name=f"{self.definition.name}-{server_tool.name}",   # 合成名 <声明名>-<server 名>
                description=override.get("description",
                                         server_tool.description or ""),
                params_schema=params,
                output_schema=server_tool.output_schema)   # 自动填入（存储、随声明携带）
            tool = McpTool(
                definition=definition, command=self.command, args=self.args,
                env=self.env, url=self.url, headers=self.headers)
            tool._server_tool_name = server_tool.name
            produced.append(tool)
        return produced

    async def execute(self, **kwargs: Any) -> Any:
        """代理调用服务端工具：惰性连接 → ``call_tool`` → 取回产物。

        .. rubric:: 行为要点

        - 本方法只存在于 ``list_tools()`` 展开产物（``_server_tool_name``
          已置位）上；声明实例（组代理）直接执行 → ``RuntimeError``
          （按合成名 ``<声明名>-<server 名>`` 引用，属声明笔误）。
        - 连接失败 / 服务端 ``isError`` 返回 → 抛异常，由 ``__call__``
          包装为 ``status="error"`` 结果（无连接重试）。
        - 返回形态：服务端 ``structuredContent`` 优先；否则文本块拼合
          （单块 → str，多块 → list[str]，无 → ``None``）。
        """
        if self._server_tool_name is None:
            raise RuntimeError(
                f"MCP declaration {self.definition.name!r} is a tool-group proxy and cannot be executed directly"
                " — reference its expanded products by the synthetic name <decl-name>-<server tool name>")
        async with self._connect() as session:
            result = await session.call_tool(self._server_tool_name,
                                             arguments=kwargs)
        if result.is_error:
            text = "".join(getattr(block, "text", "") for block in result.content)
            raise RuntimeError(
                f"MCP tool {self._server_tool_name} got a server error: {text}")
        if result.structured_content is not None:
            return result.structured_content
        texts = [block.text for block in result.content
                 if getattr(block, "text", None) is not None]
        if not texts:
            return None
        return texts[0] if len(texts) == 1 else texts


