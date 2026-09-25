"""``flowing.tool.mcp`` — ``McpTool``, a tool declared by a local or remote MCP server.

An MCP declaration can launch a local stdio process or connect to a remote
endpoint. Each declaration has its own instance. Environment-template
rendering is shared with :class:`flowing.tool.RequestTool`. The public type is
re-exported from ``flowing.tool``.
"""
from typing import Any
from flowing.tool.core import Tool, ToolDefinition

def _mcp_input_schema_to_params(input_schema: dict[str, Any] | None) -> dict[str, dict[str, Any]]: ...

class McpTool(Tool):
    """Connect to an MCP server and proxy the tools that it exposes.

    .. rubric:: Overview

    A ``type: mcp`` declaration selects exactly one source: ``command`` for a
    local stdio process, or ``url`` for a remote endpoint. A URL ending in
    ``/sse`` uses SSE; other remote URLs use streamable HTTP. The default
    :class:`ToolDefinition` is derived from the server's ``list_tools()``
    schema and can be partially overridden with ``overrides``. Each MCP
    declaration creates its own configured instance; unlike script tools,
    these instances do not share user-defined code.

    A declaration represents a group of server tools. Each exposed tool is
    registered under the synthesized canonical name
    ``<declaration-name>--<server-tool-name>``. For example, a declaration
    named ``github`` that exposes ``create-issue`` registers
    ``github--create-issue``. The declaration name keeps same-named tools from
    different MCP sources distinct. Names containing ``--`` are not
    recommended for ordinary tools or declaration names. The first ``--`` is
    treated as the group separator, so a declaration name containing it can
    produce a mismatched group prefix. An ordinary tool name containing
    ``--`` is still resolved literally when it exactly matches a registry key.
    Agent references in ``tools:`` or
    ``add_tool`` use the synthesized name and may still define an alias with
    ``as``. The ``.fya`` assembly path expands the group before binding through
    :meth:`flowing.tool.registry.ToolRegistry.expand_mcp`. Because
    ``add_tool`` is synchronous, programmatic callers must first await
    ``runtime.tool_registry.expand_mcp(...)`` before referring to a synthesized
    name.

    .. rubric:: Local stdio configuration

    .. code-block:: yaml

        # Local stdio process
        name: search
        type: mcp
        description: "Search the web when current information is needed."
        command: npx
        args: ["-y", "@modelcontextprotocol/server-brave-search"]
        env:
          BRAVE_API_KEY: "{{ env.BRAVE_API_KEY }}"

    .. rubric:: Remote endpoint configuration

    .. code-block:: yaml

        name: github
        type: mcp
        url: https://mcp.example.com/github/sse
        headers:
          Authorization: "Bearer {{ env.GITHUB_TOKEN }}"
        tools: [create-issue, list-prs]
        overrides:
          create-issue:
            args:
              title: {description: "Issue title, up to 80 characters."}

    Values in ``env`` are rendered with the top-level ``env`` object backed by
    ``os.environ``. Credentials are supplied through ``{{ env.X }}`` templates;
    they are not hard-coded, included in messages, or persisted. A missing
    variable raises :class:`flowing.errors.FormatError` during assembly.

    .. rubric:: Behavior

    - Providing both ``command`` and ``url`` raises
      :class:`flowing.errors.AmbiguousMcpSourceError`. Providing neither raises
      :class:`flowing.errors.MissingMcpSourceError`.
    - A duplicate synthesized name in the same namespace raises
      :class:`flowing.errors.ToolNameConflictError`. Change the declaration
      name or register the tool in another namespace to avoid the conflict.
    - The server's ``outputSchema`` is copied to
      :attr:`ToolDefinition.output_schema`. The current implementation stores
      it with the declaration but does not validate results against it or pass
      it to the model.
    - A connection failure or a server response marked ``isError`` becomes an
      ``error`` result. The tool does not retry failed connections.
    .. seealso:: :class:`flowing.tool.ToolRegistry` for MCP name registration.
    """
    def __init__(self, *, definition: ToolDefinition, command: str | None = None, args: list[str] | None = None, env: dict[str, str] | None = None, url: str | None = None, headers: dict[str, str] | None = None, tools: list[str] | None = None, overrides: dict[str, Any] | None = None) -> None:
        """Create an MCP tool declaration for a local or remote server.

        :param definition: The tool declaration, usually derived from the
            server's ``list_tools()`` schema and then modified by ``overrides``.
        :param command: The command to launch in stdio mode. It is mutually
            exclusive with ``url``.
        :param args: Arguments passed to the local process.
        :param env: Environment variables for the local process. Values
            support ``{{ env.X }}`` templates.
        :param url: Remote endpoint URL. It is mutually exclusive with
            ``command``.
        :param headers: Headers for remote requests. Values support templates.
        :param tools: Names of the server tools to expose. ``None`` exposes all
            server tools.
        :param overrides: Partial schema overrides in the form
            ``{tool_name: {description/args: ...}}``.
        :raises flowing.errors.AmbiguousMcpSourceError: Both ``command`` and
            ``url`` are provided.
        :raises flowing.errors.MissingMcpSourceError: Neither ``command`` nor
            ``url`` is provided.
        """
        ...

    async def _connect(self) -> Any: ...

    async def list_tools(self) -> list[McpTool]:
        """Fetch server schemas and create one proxy instance per exposed tool.

        This is the assembly-time schema-loading entry point. A
        :meth:`flowing.tool.registry.ToolRegistry.get` call for an MCP
        ``TOOL.fya`` declaration creates only the group declaration. During
        declaration-time expansion,
        :meth:`flowing.tool.registry.ToolRegistry.expand_mcp` calls this method
        once. Each returned proxy represents one server tool and receives the
        synthesized canonical name ``<declaration-name>--<server-tool-name>``.
        The registry registers those proxies in ``default::``. A collision
        raises :class:`flowing.errors.ToolNameConflictError`.

        ``execute`` can connect lazily without calling this method, but the
        LLM-visible tool declarations are carried by the proxy instances
        returned here.

        .. rubric:: Behavior

        - If ``tools`` is set, only the named server tools are expanded; if it
          is ``None``, every server tool is expanded.
        - ``overrides`` uses the form
          ``{tool_name: {description/args: ...}}``. ``description`` replaces
          the server description. ``args`` sparsely overrides properties
          derived from ``inputSchema`` through
          :func:`flowing.params.apply_param_overrides`; unsupported keywords
          fail during assembly.
        - The server's ``outputSchema`` is stored in
          :attr:`ToolDefinition.output_schema`. It is carried with the
          declaration, but the current implementation does not validate
          results against it or pass it to the model.
        - Requiredness follows the framework's schema convention: a parameter
          without a ``default`` is required. For properties omitted from the
          server's ``required`` list, the adapter supplies ``default=None``
          when needed so they remain optional in the generated model.
        """
        ...

    async def execute(self, **kwargs: Any) -> Any:
        """Call the corresponding server tool and return its result.

        The connection is established lazily, followed by ``call_tool``.

        .. rubric:: Behavior

        - This method is valid only on a proxy produced by ``list_tools()``,
          whose server tool name has been set. Calling it on the group
          declaration raises ``RuntimeError``. The group declaration is
          expanded during ``.fya`` binding; see
          :meth:`flowing.tool.registry.ToolRegistry.expand_mcp`.
        - A connection failure or a server-side ``isError`` response raises an
          exception. ``Tool.__call__`` wraps it as a result with
          ``status="error"``. The tool does not retry connections.
        - The server's ``structuredContent`` takes precedence. If it is absent,
          text blocks are combined: one block returns as a string, multiple
          blocks as a list of strings, and no blocks as ``None``.
        """
        ...
