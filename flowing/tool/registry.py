"""``flowing.tool.registry`` —— ``ToolRegistry``（``ns::name`` 全限定键 → Tool 实例）与 ``.fya`` / ``.py`` 文件加载。

工具注册与解析的唯一权威（与 ``flowing.agent_registry.AgentRegistry`` /
``flowing.plugins.skills.registry.SkillRegistry`` 同构的三注册表体系）。
公开符号经 ``flowing.tool`` re-export。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import warnings

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from flowing.errors import (
    AmbiguousToolError,
    FormatError,
    NameMismatchError,
    ToolNameConflictError,
    ToolNotFoundError,
)
from flowing.params import expand_args_schema, schema_to_model
from flowing.paths import (
    classify_ref,
    infer_name,
    kebab_to_pascal,
    kebab_to_snake,
    path_to_module_name,
    probe_candidates,
    resolve_path,
    to_project_path,
)

from flowing.tool.cli import CliTool
from flowing.tool.core import (
    TOOL_NAMING,
    Tool,
    ToolDefinition,
    _infer_from_execute,
    _whole_doc,
)
from flowing.tool.mcp import McpTool
from flowing.tool.request import RequestTool
from flowing.tool.script import ScriptTool, _auto_generate_tool


def _dir_candidates(name: str) -> list[str]:
    """``<name>/`` 目录内的定向查找链（首个存在者生效）：
    ``TOOL.fya > <name>.tool.fya > <name>.fya > TOOL.py > tool.py >
    <name_snake>.py``。内部 API。"""
    snake = kebab_to_snake(name)
    return ["TOOL.fya", f"{name}.tool.fya", f"{name}.fya",
            "TOOL.py", "tool.py", f"{snake}.py"]


_TOOL_FYA_RESERVED = frozenset({
    "type", "name", "description", "args", "output", "callable", "command",
    "shell", "url", "method", "headers", "auth", "query", "body",
    "expected_status", "timeout", "env", "tools", "overrides", "background",
})
"""TOOL.fya 保留字段集——其余字段原样落为 tool 实例的普通属性（如
``requires_approval``，见 `Tool` 行为要点「实例属性开放」；框架不解析、
不据此做任何自动行为）。``background`` 仅 script 型合法；cli / request /
mcp 声明 → ``FormatError`` （见 `_tool_from_fya`）。内部 API。"""


def _tool_glob_accept(path: Path) -> bool:
    """``tools:`` 字段 glob 命中的过滤（``glob_accept`` 回调）。内部 API。

    与 :func:`flowing.agent_registry._agent_glob_accept` 同构。纯名字
    分析，不做任何语义嗅探：

    - 目录：探测目录内工具候选链（``TOOL.fya`` / ``<名>.tool.fya`` /
      …），有合法入口 → ``True``；无 → ``False``（杂项子目录跳过）；
    - 显式标记只纳入 ``*.tool.fya`` 一种（带其它显式标记的，如
      ``*.agent.fya``，不属工具面）；
    - 其余 ``.fya`` / ``.py`` 文件（裸 ``foo.fya``、``TOOL.fya`` 等
      通用名、``impl.py``）→ ``True`` 直接纳入——是否合法工具资源
      留给创建期的 eager 解析 fail-fast。通用名文件的设计用途是独居
      叶目录（目录形态资源），glob 命中的本来就是目录本身，无需特判；
    - 其它后缀（``.md`` 等）→ ``False``。
    """
    if path.is_dir():
        return probe_candidates(
            path, _dir_candidates(infer_name(path, naming=TOOL_NAMING))) is not None
    if path.name.endswith(".agent.fya"):
        return False
    return path.suffix in (".fya", ".py")


def _tool_from_fya(path: Path, identity: str, *,
                   project_root: "Path | None" = None) -> Tool:
    """``.fya`` 命中的实例化分派：按 ``type:`` 构造四型工具实例。内部 API。

    .. rubric:: 行为要点

    - ``name:`` 显式声明仅作一致性断言（不符 → ``NameMismatchError``）；
    - ``args:`` 经 :func:`flowing.params.expand_args_schema` 归一为
      properties（``mcp`` 例外：其 ``args`` 是启动命令参数列表而非参数
      schema——MCP 的参数 schema 由 ``list_tools()`` 拉取填充，属
      `McpTool` 装配链，本层只落声明字段）；
    - ``output:`` → ``ToolDefinition.output_schema``；
    - ``type`` 缺失或非法 → ``FormatError``；各类型必填字段缺失 →
      ``FormatError`` （``cli`` 缺 ``args`` / ``request`` 缺 ``args`` 由
      构造器的 ``MissingSchemaError`` 承载）。
    """
    from flowing.parser import load_fya_yaml   # 模块头依赖图保持单向（parser 不 import tool）

    fields = load_fya_yaml(path.read_text(encoding="utf-8"))
    tool_type = fields.get("type")
    if tool_type not in ("script", "cli", "request", "mcp"):
        raise FormatError(
            f"type of {path} is missing or invalid: {tool_type!r} (must be script / cli / request / mcp)")
    explicit_name = fields.get("name")
    if explicit_name is not None and explicit_name != identity:
        raise NameMismatchError(explicit_name, identity, str(path))
    description = fields.get("description")
    output_schema = fields.get("output")
    params = ({} if tool_type == "mcp"   # mcp 的 args 是命令参数，非参数 schema
              else expand_args_schema(fields.get("args") or {}))
    definition = ToolDefinition(
        name=identity, description=description or "",
        params_schema=params, output_schema=output_schema)
    if tool_type == "script":
        tool = _script_tool_from_fya(path, identity, fields, params,
                                     project_root=project_root)
    elif tool_type == "cli":
        command = fields.get("command")
        if command is None:
            raise FormatError(f"cli tool at {path} is missing the command field")
        tool = CliTool(definition=definition, command=command,
                       shell=fields.get("shell", "sh"))
    elif tool_type == "request":
        url = fields.get("url")
        if url is None:
            raise FormatError(f"request tool at {path} is missing the url field")
        tool = RequestTool(
            definition=definition, url=url,
            method=fields.get("method", "POST"), headers=fields.get("headers"),
            auth=fields.get("auth"), query=fields.get("query"),
            body=fields.get("body"),
            expected_status=fields.get("expected_status"),
            timeout=fields.get("timeout", 30.0))
    else:  # mcp
        tool = McpTool(
            definition=definition, command=fields.get("command"),
            args=fields.get("args"), env=fields.get("env"),
            url=fields.get("url"), headers=fields.get("headers"),
            tools=fields.get("tools"), overrides=fields.get("overrides"))
    # 非保留字段原样落为实例普通属性（框架不解析、不做任何自动行为）
    for key, value in fields.items():
        if key not in _TOOL_FYA_RESERVED:
            setattr(tool, key, value)
    # background 仅 script 型合法——script 显式落属性（值须布尔），
    # 其他型声明 → FormatError（解析期 fail fast，不静默忽略）
    if "background" in fields:
        if tool_type == "script":
            if not isinstance(fields["background"], bool):
                raise FormatError(f"background of {path} must be a boolean")
            tool.background = fields["background"]   # 显式 False 与缺省等价，但为一致性落属性无害
        else:
            raise FormatError(
                f"the background field of {path} is only supported for script tools (current type: {tool_type})")
    return tool


def _script_tool_from_fya(
    path: Path, identity: str, fields: dict[str, Any],
    params: dict[str, dict[str, Any]], *,
    project_root: "Path | None" = None,
) -> Tool:
    """``type: script`` 的 TOOL.fya 装配：``callable: {路径}::{函数名或类名}``
    指针加载 → `ScriptTool` 子类实例化 / 裸函数提升。内部 API。

    .. rubric:: 行为要点

    - ``callable:`` 指向已打标函数 → ``FormatError`` （通道互斥：显式
      指针通道与自动提升通道二选一）；
    - fya ``args`` 声明存在时经 :func:`flowing.params.schema_to_model`
      桥接为校验模型（声明即模型）；缺省从 callable 签名构建
      （``_infer_from_execute``）；
    - fya 的显式 ``description`` / ``output`` 声明压过一切兜底来源
      （callable docstring 整体是函数路径的最后回退）。
    """
    import importlib.util

    callable_ref = fields.get("callable")
    if not callable_ref or "::" not in str(callable_ref):
        raise FormatError(
            f"script tool at {path} must declare callable: <path>::<function-or-class-name>")
    path_part, _, symbol = str(callable_ref).partition("::")
    root = project_root   # @/ 基准：ToolRegistry 构造时固化的项目根（None → cwd 哑根退化）
    impl_path = resolve_path(
        path_part, project_root=root if root is not None else Path.cwd(),
        source_dir=path.parent)   # callable 路径相对 TOOL.fya 所在目录
    spec = importlib.util.spec_from_file_location(
        path_to_module_name(impl_path, project_root=root,
                            prefix="flowing_tool_file_"), impl_path)
    module = importlib.util.module_from_spec(spec)   # type: ignore[union-attr]
    spec.loader.exec_module(module)   # type: ignore[union-attr]
    if not hasattr(module, symbol):
        raise FormatError(f"{symbol} does not exist in {impl_path} (callable: pointer missed)")
    target = getattr(module, symbol)
    description = fields.get("description")
    output_schema = fields.get("output")
    if isinstance(target, type) and issubclass(target, ScriptTool):
        # .fya callable 通道规范名 = 文件身份 identity（TOOL.fya
        # 目录/文件名，同 _tool_from_py）；类体显式 name 与身份不符报错；
        # 无 definition 也无显式 name 时以身份注入（不做类名推断）
        explicit_name = target.__dict__.get("name")
        if explicit_name is not None and explicit_name != identity:
            raise NameMismatchError(explicit_name, identity, str(path))
        if explicit_name is None and "definition" not in target.__dict__:
            target.name = identity
        tool = target()
    elif callable(target):
        if hasattr(target, "__flowing_tool_name__"):
            raise FormatError(
                f"callable: points to the already-marked function {symbol} — "
                "the explicit-pointer and auto-promotion channels are mutually exclusive (declaration channels conflict)")
        args_model = (schema_to_model(f"{kebab_to_pascal(identity)}Args", params)
                      if params else _infer_from_execute(target))
        cls = type(kebab_to_pascal(identity), (ScriptTool,), {
            "execute": staticmethod(target),
            "name": identity,
            "description": description or _whole_doc(target.__doc__) or "",
            "args_model": args_model,
        })
        tool = cls()
    else:
        raise FormatError(f"{symbol} of {impl_path} is neither a function nor a ScriptTool subclass")
    # fya 显式声明（description / output）压过一切兜底来源（优先级链头部）
    if description is not None or output_schema is not None:
        d = tool.definition
        tool.definition = ToolDefinition(
            name=d.name,
            description=description if description is not None else d.description,
            params_schema=d.params_schema,
            output_schema=output_schema if output_schema is not None else d.output_schema,
            strict=d.strict)
    return tool


class ToolRegistry:
    """Runtime 全局工具注册表——``ns::name`` 全限定键 → Tool 实例。

    .. rubric:: 功能介绍

    每个 Runtime 持有一个实例（``runtime.tool_registry``）。注册时间线：
    无启动扫描（框架没有默认扫描目录）——核心工具（``finish`` /
    ``subagent-invoke``）随 ``Runtime.__init__`` 注册；插件工具在阶段一
    ``install()`` 注册；文件形态工具由 :meth:`get` 引用触发惰性解析并
    注册；MCP / CLI / Request 在 Agent 解析 ``.fya`` 时创建实例并注册。

    重名约束按 ``ns::name`` 全限定键判定：同一命名空间内重名 → 后注册者
    抛 ``ToolNameConflictError``；不同命名空间的同名工具允许共存。需要
    同一 MCP 服务器不同配置时用不同命名空间或规范名，需要相同实例时复用
    已有注册。裸名引用的注册表视图依次查 ``default::``、``builtin::``
    （``default`` 优先 = 插件覆盖原生行为的通道）；自定义命名空间的资源
    只能以 ``ns::name`` 全限定名引用。别名冲突不存在——别名是 `ToolEntry`
    层（Agent 本地）的概念。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.tool_registry.register(MakePayment())          # script 单例
        tool = runtime.tool_registry.get("make-payment")       # 按规范名取

    .. rubric:: 行为要点

    - 不变量：任意时刻一个 ``ns::name`` 全限定键至多映射一个 Tool 实例
      （不同命名空间同名允许共存）。
    - 不提供别名查找（别名在 `ToolEntry` 层）；不提供 unregister（销毁
      随 Runtime 生命周期）。

    .. seealso::

        - :attr:`flowing.runtime.Runtime.tool_registry` —— 挂载点。
        - :class:`flowing.tool.ToolEntry` —— 按 ``name_ori`` 查本表。
    """

    _tools: dict[str, Tool]
    """``ns::规范名`` → Tool 实例（命名空间规则见 ``flowing.runtime``）。
    内部 API，不属稳定契约。
    """

    def __init__(self, *, project_root: "Path | None" = None) -> None:
        # spec 骨架无显式构造段：空注册表（无启动扫描——「无默认扫描目录」基调）
        self._tools = {}
        # @/ 引用的解析基准：Runtime 构造时传入固化值（project_root），
        # 终身有效；裸注册表（测试）为 None——@/ 引用报错、其余形态退化
        self._project_root = project_root

    def register(self, tool: Tool, *, name: str | None = None,
                 namespace: str | None = None) -> None:
        """注册工具实例。

        :param tool: 工具实例；script 类型应注册全局单例。
        :param name: 规范名覆写；``None`` 时取 ``tool.definition.name``。
        :param namespace: 命名空间；``None`` → ``"default"``。注册表 key 为
          ``ns::name``——核心内置工具归 ``builtin::``；裸名引用的注册表
          视图依次查 ``default::``、``builtin::`` （``default`` 优先 =
          插件覆盖原生行为的通道），自定义命名空间只能以 ``ns::name``
          全限定名引用。
        :raises flowing.errors.ToolNameConflictError: ``ns::name`` 完整键
          （含命名空间）已存在（不同命名空间的同名工具允许共存）。
        """
        ns = namespace or "default"
        bare = name if name is not None else tool.definition.name
        key = f"{ns}::{bare}"
        if key in self._tools:
            raise ToolNameConflictError(f"tool with the canonical name already registered: {key}")  # 全键重名永远不允许
        self._tools[key] = tool
        tool.registry_key = key   # 回写全键（Entry 装配对文件派生工具落账 name_ori 的依据）

    def get(self, name_or_path: str, *,
            source_dir: Path | None = None) -> Tool:
        """工具的唯一解析入口——注册表快路径 + 文件链慢路径合一。

        .. rubric:: 功能介绍

        形态判别委托 :func:`flowing.paths.classify_ref` （词法唯一来源），
        三分语义：

        - 限定名（含 ``::``，如 ``myplugin::web-search``）：只查注册表
          精确键，不走文件查找链（命名空间无法反向映射到文件）；
        - 裸名（如 ``payment``）：``source_dir`` 提供时先走定向文件查找
          链（相对 ``source_dir``——文件覆盖注册表）：命中后按所在目录
          派生键（``@/`` 下相对、根外绝对、文件夹式取上层目录，仅作内部
          身份标识）短路复用已注册实例，未注册才实例化并注册；
          ``source_dir`` 缺省时跳过文件链。之后查注册表裸名视图——
          ``default::`` 优先于 ``builtin::`` （插件覆盖原生行为的通道）；
        - 路径形态：``@/`` 经 ``project_root`` 定位（无需 ``source_dir``，
          无 launch 上下文时无法锚定 → ``ValueError``）；``./`` / ``../``
          需 ``source_dir``，缺省时报错。跳过注册表，定位后走候选链；
          命中后同样注册（命名空间派生规则同上）。

        定向查找链（按规范名 ``<name>``，``<name_snake>`` 为其 snake_case
        形式，转换经 :func:`flowing.paths.kebab_to_snake`；首个存在者生效，
        探测委托 :func:`flowing.paths.probe_candidates`）：:

            目录内（<name>/ 存在时）：
                TOOL.fya > <name>.tool.fya > <name>.fya
                > TOOL.py > tool.py > <name_snake>.py
            目录外：
                <name>.tool.fya > <name>.fya > <name_snake>.py

        链上顺序只是确定性的先后规则——不推荐同一链路真的同时存在多个候选
        文件（读者需回溯优先级才能确定生效者）。

        .. rubric:: 行为要点

        - 形参承载规范名 / 限定名 / 路径，非别名——别名到规范名的换算在
          Agent 绑定层（``ToolEntry``）。
        - 继续向下仅裸名语境：裸名查找时目录存在但无合法入口 → 继续链上
          下一项；显式路径语境下目录无候选 → 直接报错（定点引用的目录为
          空几乎必为笔误）。
        - fail-fast 口径：裸名未注册时，``source_dir`` 提供则先走文件
          查找链、均不命中才报错；``source_dir`` 缺省则只查注册表、不
          命中即报错，不做文件探测。纯存在性检查用 ``__contains__``
          （仅认全限定键）。
        - 热路径口径：``ToolEntry.llm_definition()`` （每轮上下文组装）
          与 ``before_tool_call`` 审批路径调本方法时必命中注册表快路径
          ——Entry 在装配期已解析落账（文件命中的落账派生限定键，注册表
          命中的落账裸名），文件解析是声明期行为，运行时不触发文件 IO。
        - ``.fya`` 与同名 ``.py`` 并存 → 告警 + ``.fya`` 优先。
        - ``.py`` 命中后：恰好一个 ``@flowing_tool`` 打标函数 →
          ``_auto_generate_tool`` 提升；或恰好一个 `ScriptTool` 子类 →
          实例化；两者并存 / 多个打标函数 →
          :class:`flowing.errors.AmbiguousToolError`；皆无 →
          :class:`flowing.errors.FormatError`。
        - name 断言：命中对象的显式 ``name`` 声明（``.fya`` 字段 / 类
          属性 / 装饰器参数）必须与 ``<name>`` 一致，不符抛
          :class:`flowing.errors.NameMismatchError`；``<name>`` 的推断
          本体为 :func:`flowing.paths.infer_name` （规则表
          :data:`TOOL_NAMING`）。
        - 不做 glob 展开（``tools:`` 条目的 glob 在装配层展开后逐条进本
          方法）。

        :param name_or_path: 规范名（裸名）、限定名（``ns::name``）或
          路径形态字符串。
        :param source_dir: 裸名文件链的查找根与 ``./`` / ``../`` 的相对
          基准。缺省（``None``）时：裸名只查注册表（``default::`` /
          ``builtin::``），相对路径报错。声明期调用点（``.fya`` 装配、
          ``Agent.add_tool``）义务性传入引用方 Agent 的 ``source_file``
          所在目录——推荐经 :meth:`flowing.agent.Agent.get_tool` 自动携带。
        :return: 已注册的 Tool 实例。
        :raises flowing.errors.ToolNotFoundError: 注册表与查找链均不命中。
        :raises flowing.errors.FormatError: 显式路径目录无候选、``.py``
          无任何合法定义、或 ``callable:`` 指向已装饰函数。
        :raises ValueError: ``@/`` 路径无 launch 上下文（无法锚定项目根）。

        .. seealso::

            - :func:`flowing.tool.flowing_tool` —— 打标通道。
            - :meth:`flowing.runtime.Runtime.get_agent_class` ——
              同构的 Agent 解析管线。
        """
        # 精确键短路（先于形态判别）：文件派生限定键的命名空间含路径特征
        # （目录派生，如 "@/order-agent::payment" / 绝对路径形态），过不了
        # classify_ref 的限定名判别（左段含 / 会判成路径形态）——注册表在场
        # 证据优先于词法分流，docstring 承诺的「llm_definition / 审批路径
        # 必命中注册表快路径」靠此成立
        if name_or_path in self._tools:
            return self._tools[name_or_path]
        form = classify_ref(name_or_path)
        if form == "qualified":
            # 限定名（ns::name）：只查注册表精确键，不走文件查找链
            # （命名空间无法反向映射到文件；命中已在上方精确键短路返回）
            raise ToolNotFoundError(f"tool not registered: {name_or_path}")
        if form == "bare":
            if source_dir is not None:
                # 裸名 + source_dir：先走定向文件查找链（相对 source_dir——
                # 文件覆盖注册表）；命中后按所在目录派生键短路复用/实例化注册
                probed = self._probe_name_chain(name_or_path, source_dir)
                if probed is not None:
                    return self._resolve_hit(*probed, ref=name_or_path)
            # 注册表裸名视图：default:: 优先于 builtin::（插件覆盖原生行为
            # 的通道）；source_dir 缺省时跳过文件链、不命中即报错，不做文件探测
            for key in (f"default::{name_or_path}", f"builtin::{name_or_path}"):
                if key in self._tools:
                    return self._tools[key]
            raise ToolNotFoundError(f"tool not registered and no lookup chain hit: {name_or_path}")
        # 路径形态（慢路径，声明期行为）：@/ 锚注册表固化的项目根（无需
        # source_dir）；./ ../ 需 source_dir（缺省 -> ValueError，resolve_path
        # 现有口径）。裸注册表（无项目根）时 @/ 无法锚定 -> ValueError（编程错误）
        root = self._project_root
        if root is None and name_or_path.replace("\\", "/").startswith("@/"):
            raise ValueError("@/ path resolution requires a project root (bare ToolRegistry without project_root)")
        resolved = resolve_path(
            name_or_path,
            project_root=root if root is not None else Path.cwd(),   # 哑根：@/ 已在上方拒绝
            source_dir=source_dir)
        if resolved.is_dir():
            dir_name = infer_name(resolved, naming=TOOL_NAMING)   # 目录：basename 即目录名
            candidates = _dir_candidates(dir_name)
            hit = probe_candidates(resolved, candidates)
            if hit is None:
                # 显式路径语境：目录无候选 -> 直接报错（定点引用的目录为空
                # 几乎必为笔误），不继续向下
                raise FormatError(f"explicit path directory has no valid tool entry: {resolved}")
            return self._resolve_hit(hit, True, resolved, candidates,
                                     ref=name_or_path)
        if not resolved.exists() or not (
                resolved.name.endswith(".fya") or resolved.suffix == ".py"):
            raise ToolNotFoundError(f"tool not registered and no lookup chain hit: {name_or_path}")
        # 直指文件的显式路径：候选列表仅服务于 .fya/.py 并存告警
        identity = infer_name(resolved, naming=TOOL_NAMING)
        candidates = [resolved.name, f"{kebab_to_snake(identity)}.py"]
        return self._resolve_hit(resolved, False, resolved.parent, candidates,
                                 ref=name_or_path)

    def _probe_name_chain(
        self, name: str, source_dir: Path
    ) -> "tuple[Path, bool, Path, list[str]] | None":
        """裸名的定向文件查找链。内部 API，不属稳定契约。

        返回 ``(命中路径, 是否文件夹式命中, 探测基准目录, 候选名列表)``，
        全部未命中 → ``None`` （调用方继续查注册表裸名视图）。

        目录内（``<name>/`` 存在时）：``TOOL.fya > <name>.tool.fya >
        <name>.fya > TOOL.py > tool.py > <name_snake>.py``；目录存在但无
        合法入口 → 继续链上下一项（「继续向下」仅裸名语境）。目录外：
        ``<name>.tool.fya > <name>.fya > <name_snake>.py``。
        """
        directory = source_dir / name
        if directory.is_dir():
            candidates = _dir_candidates(name)
            hit = probe_candidates(directory, candidates)
            if hit is not None:
                return hit, True, directory, candidates
            # 裸名语境：目录存在但无合法入口 -> 继续链上下一项
        snake = kebab_to_snake(name)
        candidates = [f"{name}.tool.fya", f"{name}.fya", f"{snake}.py"]
        hit = probe_candidates(source_dir, candidates)
        if hit is not None:
            return hit, False, source_dir, candidates
        return None

    def _resolve_hit(self, hit: Path, folder_form: bool, base_dir: Path,
                     candidates: list[str], *, ref: str) -> Tool:
        """候选命中 → 派生键短路 / 实例化注册。内部 API，不属稳定契约。

        派生键 = ``<所在目录派生命名空间>::<身份名>`` （文件夹式资源取上层
        目录；``@/`` 下根相对、根外绝对，仅作内部身份标识）——已注册则
        短路复用（不重复实例化）；未注册则按后缀分派实例化（``.fya`` →
        :func:`_tool_from_fya`；``.py`` → :meth:`_tool_from_py`）并落账、
        回写 ``tool.registry_key``。
        """
        identity = infer_name(hit, naming=TOOL_NAMING)
        if hit.name.endswith(".fya"):
            # .fya 与同名 .py 并存 -> 告警 + .fya 优先（链上顺序已保证优先，
            # 此处只补告警）
            coexisting = [c for c in candidates
                          if c.endswith(".py") and (base_dir / c).exists()]
            if coexisting:
                warnings.warn(
                    f"a same-name .fya and .py coexist; the .fya wins: {hit}"
                    f" (coexisting: {', '.join(coexisting)})")
        ns_dir = hit.parent.parent if folder_form else hit.parent
        derived_key = f"{self._derived_namespace(ns_dir)}::{identity}"
        if derived_key in self._tools:
            return self._tools[derived_key]   # 派生键短路复用（文件解析是声明期行为）
        tool = (_tool_from_fya(hit, identity, project_root=self._project_root)
                if hit.name.endswith(".fya") else self._tool_from_py(hit, identity))
        self._tools[derived_key] = tool
        tool.registry_key = derived_key   # 回写全键（Entry 装配落账 name_ori 的依据）
        return tool

    def _derived_namespace(self, ns_dir: Path) -> str:
        """所在目录 → 派生命名空间字符串（``@/`` 下根相对、根外绝对——
        :func:`flowing.paths.to_project_path` 口径；无项目根（裸注册表）
        时退化为绝对路径，与「根外绝对」一致）。内部 API，不属稳定契约。"""
        if self._project_root is not None:
            return to_project_path(ns_dir, project_root=self._project_root)
        return str(ns_dir)

    def _tool_from_py(self, path: Path, identity: str) -> Tool:
        """``.py`` 命中的实例化分派。内部 API，不属稳定契约。

        恰好一个 ``@flowing_tool`` 打标函数 → :func:`_auto_generate_tool`
        提升；恰好一个 `ScriptTool` 子类 → 实例化；两者并存 / 多个打标
        函数 / 多个子类 → ``AmbiguousToolError``；皆无 → ``FormatError``。
        子类的规范名 = 文件身份（``identity``）：类体显式 ``name`` 与身份
        不符 → ``NameMismatchError``（防错位）；类既无显式 ``name`` 也无
        ``definition`` 时以 ``identity`` 注入（不由类名推断）。
        打标函数的装饰器参数断言在 :func:`_auto_generate_tool` 内。
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            path_to_module_name(path, project_root=self._project_root,
                                prefix="flowing_tool_file_"), path)
        module = importlib.util.module_from_spec(spec)   # type: ignore[union-attr]
        spec.loader.exec_module(module)   # type: ignore[union-attr]
        # 只认本文件定义的符号（__module__ 过滤掉 import 进来的）
        marked = [
            obj for obj in vars(module).values()
            if callable(obj) and hasattr(obj, "__flowing_tool_name__")
            and getattr(obj, "__module__", None) == module.__name__
        ]
        subclasses = [
            obj for obj in vars(module).values()
            if isinstance(obj, type) and issubclass(obj, ScriptTool)
            and obj is not ScriptTool and obj.__module__ == module.__name__
        ]
        if marked and subclasses:
            raise AmbiguousToolError(
                f"{path} contains both a @flowing_tool-marked function and a ScriptTool subclass")
        if len(marked) > 1:
            raise AmbiguousToolError(f"{path} contains multiple @flowing_tool-marked functions")
        if len(subclasses) > 1:
            raise AmbiguousToolError(
                f"{path} contains multiple ScriptTool subclasses (the spec does not list the multi-subclass form; "
                "it is routed to the AmbiguousToolError channel)")
        if not marked and not subclasses:
            raise FormatError(f"no @flowing_tool-marked function or ScriptTool subclass in {path}")
        if marked:
            return _auto_generate_tool(marked[0])
        cls = subclasses[0]
        # ScriptTool 不做类名推断 name。.py 文件通道的规范名 =
        # 文件身份（文件名/目录名，声明面权威，与 @flowing_tool 通道同源）：
        # 类体显式 name 与文件身份不符 -> NameMismatchError（防错位）；类既无
        # definition 也无显式 name 时以文件身份注入（不回退类名推断）。
        explicit_name = cls.__dict__.get("name")
        if explicit_name is not None and explicit_name != identity:
            raise NameMismatchError(explicit_name, identity, str(path))
        if explicit_name is None and "definition" not in cls.__dict__:
            cls.name = identity
        return cls()

    def get_tool_class(self, name_or_path: str, *,
                       source_dir: Path | None = None) -> type[Tool]:
        """取已解析工具的类对象（与 ``Runtime.get_agent_class`` 对称）。

        .. rubric:: 功能介绍

        薄委托 ``type(self.get(...))``——不另开解析路径，单一解析权威仍是
        :meth:`get`；script 打标工具返回 ``_auto_generate_tool`` 提升出
        的 ``ScriptTool`` 子类。Agent 侧解析产物是类、Tool 侧注册产物是
        单例实例；编译发射、测试断言（``issubclass``）、子类化扩展等场景
        要的是类而非实例——薄委托保证类语义与实例语义永不漂移。

        .. rubric:: 行为要点

        - 解析 / 注册 / 缓存语义全部继承 :meth:`get` （含限定名只查注册
          表、命名空间派生、fail-fast 口径）。
        - 不绕过注册表直接加载文件；不实例化新对象（取的是已注册单例的
          类）。

        :param name_or_path: 同 :meth:`get`。
        :param source_dir: 同 :meth:`get`。
        :return: 已注册单例的类（``type(实例)``）。
        :raises flowing.errors.ToolNotFoundError: 同 :meth:`get`。

        .. seealso:: :meth:`get` —— 唯一解析入口；
            :meth:`flowing.runtime.Runtime.get_agent_class` —— 对称的
            Agent 侧入口。
        """
        return type(self.get(name_or_path, source_dir=source_dir))

    def __contains__(self, name: str) -> bool:
        """全限定键（``命名空间::规范名``）是否已注册。

        .. rubric:: 功能介绍

        ``x in registry`` 运算符的落点。只认全限定键：``"builtin::read"
        in registry`` 为真；裸名永不命中（``"read" in registry`` 恒为假，
        即便 ``builtin::read`` 在场）。裸名解析（``default::`` 优先、
        ``builtin::`` 兜底的优先级链）是 :meth:`get` 的专属职责——若
        ``in`` 也做裸名展开，同一对象上将存在两套语义重叠的查询通道，且
        ``in`` 的解析结果不可见（只回布尔值），排查更绕。保持 ``in``
        廉价、无歧义、零解析逻辑。

        .. rubric:: 使用示例

        .. code-block:: python

            "builtin::read" in runtime.tool_registry   # True（全限定键）
            "read" in runtime.tool_registry            # False——裸名请用 get()：
            runtime.tool_registry.get("read")          # 走命名空间优先级解析链
        """
        return name in self._tools

    def __iter__(self) -> Iterator[Tool]:
        """遍历全部已注册实例（顺序不保证）。"""
     
        return iter(self._tools.values())
