"""``flowing.compiler`` —— ``.fya`` 显式编译器：构建期把声明式 Agent 编译为同目录 ``.py``。

.. rubric:: 功能介绍

本模块是「``.fya`` → Agent 类」的合成层本体（三层架构：parser 字面层
→ 装配层 → 本模块合成层）。两个入口同源：

- 内存形态（:func:`compile_fya_class`）：``.fya`` → 内存中的 Agent
  类——运行期的唯一合成点（``Runtime.get_agent_class`` 路径形态、
  ``mount()``、装配层资源引用全部经此）；
- 落盘形态（:func:`compile_fya_file`）：内存合成 + 写出同目录
  ``.py`` （去 ``.fya`` 后缀，兼容 ``from payment import PaymentAgent``），
  产物可被 linter / type checker / mypy 静态分析——生产部署与 CI 推荐。

运行期不需要 import 期钩子：Agent 类一律经 ``Runtime.get_agent_class``
按字符串名惰性解析；用户手写 Python 直接引用 fya 定义的类时走显式编译
产物。

.. rubric:: 全局约定（跨符号、影响使用的约定）

- 同目录产物：``payment.fya`` → ``payment.py``，标准 import 链无需
  任何 hook 即可工作。
- hash 防覆盖闸：编译元数据写在产物同目录的 ``.flowing.meta.yaml``
  （每目录一份，条目以 ``.fya`` 文件名为键），三字段含义见下表。编译
  产物可读但不应手改；语义被手改 → :class:`flowing.errors.ArtifactModifiedError`
  中止，不静默覆盖（要保留手改内容，请改写为手写子类）。

  .. list-table:: ``.flowing.meta.yaml`` 条目（每目录一份，条目以 ``.fya`` 文件名为键）
     :header-rows: 1

     * - 字段
       - 含义
       - 不匹配时
     * - ``fya_hash``
       - 源 ``.fya`` 解析结构的哈希（注释 / 空行变化不影响）
       - 与当前解析结果不同 → 重编译
     * - ``py_hash``
       - 产物 ``.py`` 的 AST 哈希（格式化改动不影响）
       - 与产物实际哈希不符 → ``ArtifactModifiedError`` 中止（不覆盖）
     * - ``compiler_version``
       - 编译器版本（:data:`COMPILER_VERSION`）
       - 与当前版本不同 → 强制重编译

- 编译是幂等的：``fya_hash`` 未变且 ``compiler_version`` 一致 →
  跳过；冲突中止后重跑可继续，已产出文件不回滚。
- 不允热重启：编译只发生在构建 / 启动阶段，运行期不监听文件变更。
- 声明途径等价：同一逻辑的 ``.fya`` 与手写 ``.py`` 是等价的两条声明
  途径；同名并存时 ``.fya`` 优先并告警（优先级判定在
  ``Runtime.get_agent_class`` 的解析侧）。
- 本模块只做编译：不删除无对应 ``.fya`` 的孤儿 ``.py``；不编译手写
  ``.py`` Agent；不拉起 Runtime、不执行 ``main``。

.. rubric:: 使用示例

.. code-block:: python

    from pathlib import Path
    from flowing.compiler import compile_fya_file

    product = compile_fya_file(Path("agents/payment.fya"))   # 生成同目录 payment.py

.. seealso::

    - :func:`flowing.interfaces.cli.cmd_compile` —— 本模块的 CLI 壳。
    - :mod:`flowing.parser` —— 字面层前端（``FyaDocument`` 来源）。
    - :mod:`flowing.errors` —— ``CompileError`` / ``ArtifactModifiedError``。
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import io
import json
import types
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ruamel.yaml import YAML   # 全项目统一 ruamel（meta 读写与 parser 同库）

from flowing.errors import (
    ArtifactModifiedError,
    CompileError,
    FormatError,
    NameMismatchError,
)
from flowing.params import bridge_properties, expand_args_schema
from flowing.parser import EntryRef, FyaDocument, parse_fya, split_as
from flowing.parsable import PENDING, Parsable
from flowing.paths import infer_name, kebab_to_pascal
from flowing.subagents import _expand_glob_entries

__all__ = [
    "COMPILER_VERSION",
    "compile_fya_class",
    "compile_fya_file",
    "compile_project",
]

COMPILER_VERSION: str = "0.1.3"
"""编译器版本——写入 meta 的 ``compiler_version`` 字段。

版本是缓存键的一部分：meta 中记录的 ``compiler_version`` 与当前值不同
→ 视同 ``fya_hash`` 变化，强制重编译（防发射格式漂移后旧产物被静默
沿用）。框架升级的代价是全项目 ``.fya`` 首次编译时一次性集体重跑，
幂等且廉价。
"""

_ENTRY_FIELDS = ("tools", "subagents")
"""Agent ``.fya`` 的资源列表字段（glob 展开 + EntryRef 规范化的对象）。"""

_META_FILENAME = ".flowing.meta.yaml"
"""产物同目录的 meta 文件名（每目录一份，条目以 .fya 文件名为键）。"""

_USER_SETUP_NAME = "_fya_user_setup"
"""``$script`` 中用户 ``setup`` 在装配包装下的改名（生成 setup 的委托目标）。"""

_MISSING: Any = object()
"""字段缺失哨兵（与 PENDING 区分：缺失 = 未声明，PENDING = 声明了 ``_``）。"""


# ---------------------------------------------------------------------------
# 装配层：具名块填回（四条导航规则，规则文本见 subagents.SubagentEntry 行为要点）
# ---------------------------------------------------------------------------


def _find_mapping_key(mapping: Mapping[str, Any], segment: str) -> str | None:
    """dict 段寻址（规则 2）：精确 key 优先；``as`` 键仅以别名段寻址。

    含 ``as`` 的 key（``working_dir as cwd``）不匹配其规范名段——有了别名
    就不允许规范名段寻址（与列表段「声明了 ``as`` 必须写别名」同一原则）。
    块路径段字符集不含空白（词法层保证），精确命中不可能落在 as 键上。
    """
    if segment in mapping:
        return segment
    for key in mapping:
        if isinstance(key, str):
            _, alias = split_as(key)
            if alias is not None and alias == segment:
                return key
    return None


def _merge_named_blocks(fields: dict[str, Any], blocks: Mapping[str, str]) -> None:
    """把具名块填回顶层字段映射（装配层导航四条规则的唯一落点）。

    内部 API，不属稳定契约。原地修改 ``fields`` （含 EntryRef 条目的
    ``body`` 覆写映射）。四条规则（规则文本的权威出处是
    :class:`flowing.subagents.SubagentEntry` 行为要点）：

    1. 列表段（``tools`` / ``subagents`` 等 entry 列表）：按条目
       别名精确匹配（``EntryRef.alias``），未命中 →
       :class:`FormatError`；无下标语法（词法层已禁止）；命中后进入该
       条目的覆写声明空间（``body``）；
    2. dict 段：精确 key；key 含 ``as`` 时仅以别名段寻址
       （``$tools.pay.args.cwd.description:`` 命中 ``working_dir as cwd``
       键，``...args.working_dir...`` 不命中）；中间段缺失 →
       :class:`FormatError` （末端缺失才算「目标缺失」，见规则 4）；
    3. PENDING 槽：其后还有路径段 → 物化为空映射继续深入
       （override 位 ``_`` = 空补丁语义）；即末端 → 按规则 4 写入；
    4. 末端：目标缺失或为 ``PENDING`` → 写入；已有实际值 → 冲突
       :class:`FormatError`。

    块体写入前剥掉尾部换行（块体原文以 ``---``/EOF 收尾，必带行尾换行；
    该换行是容器格式而非内容）。
    末端落在列表别名段（路径在条目处耗尽）→ :class:`FormatError`
    （块只能写进映射键，不能整体替换条目）。
    """
    for block_path, body in blocks.items():
        segments = block_path.split(".")
        node: Any = fields
        for depth, segment in enumerate(segments):
            last = depth == len(segments) - 1
            if isinstance(node, list):
                # 规则 1：列表段按条目别名精确匹配，命中后进入 body 覆写空间
                ref = next(
                    (r for r in node
                     if isinstance(r, EntryRef) and r.alias == segment),
                    None)
                if ref is None:
                    raise FormatError(
                        f"named block ${block_path}: segment {segment!r} does not match any entry alias")
                if last:
                    raise FormatError(
                        f"named block ${block_path}: the last segment lands on entry {segment!r}"
                        " — blocks can only be written into mapping keys, not replace whole entries")
                node = ref.body
                continue
            if not isinstance(node, dict):
                raise FormatError(
                    f"named block ${block_path}: container of segment {segment!r} is not a mapping/entry list")
            key = _find_mapping_key(node, segment)
            if key is None:
                if last:
                    node[segment] = body.rstrip("\r\n")   # 规则 4：末端缺失 → 写入
                    break
                raise FormatError(   # 规则 2：中间段缺失（非 PENDING 槽）→ 不命中
                    f"named block ${block_path}: middle segment {segment!r} did not match"
                    " (keys with as are addressed by alias segment only; missing outside PENDING slots is not materialized)")
            value = node[key]
            if last:
                # 规则 4：末端为 PENDING → 写入；已有实际值 → 冲突
                if value is PENDING:
                    node[key] = body.rstrip("\r\n")
                else:
                    raise FormatError(
                        f"named block ${block_path}: last segment {segment!r} already has a concrete value; conflict")
                break
            if value is PENDING:
                value = {}   # 规则 3：PENDING 槽物化为空映射继续深入
                node[key] = value
            node = value


# ---------------------------------------------------------------------------
# 装配层：$script 处理与 args 桥接
# ---------------------------------------------------------------------------


def _rename_user_setup(script: str) -> str:
    """``$script`` 顶层 ``def setup`` 文本级改名 ``_fya_user_setup`` （保留原文格式）。

    改名只动 ``def`` 行（AST 定位后切片替换），注释/格式/其余成员原样
    保留；无顶层 ``setup`` → 原样返回。仅在装配层需要生成包装 ``setup``
    （有 ``_extra`` / 条目绑定工作）时调用——无装配工作时用户 ``setup``
    保持原名直接进类体。
    """
    tree = ast.parse(script)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "setup":
            lines = script.splitlines(keepends=True)
            line = lines[node.lineno - 1]
            def_at = line.index("def", node.col_offset)
            name_at = line.index("setup", def_at + 3)
            lines[node.lineno - 1] = line[:name_at] + _USER_SETUP_NAME + line[name_at + 5:]
            return "".join(lines)
    return script


def _split_script(script: str) -> tuple[str, str, list[str]]:
    """``$script`` 二分为模块级段与类体段（各自保留原相对顺序）。

    - 类体段：顶层 ``def`` / ``async def`` 且首个位置参数名为 ``self``
      的函数（连同装饰器行）——它们是方法（``setup`` / ``@on``
      handler / 实例方法）；
    - 模块级段：其余**一切**顶层语句（import、无 ``self`` 的函数、
      类声明、赋值、``if`` / ``try`` 等），发射到产物 ``.py`` 的类体
      上方——类体层的非 def 语句与无 self 函数对方法体不可见（方法体
      按模块级 globals 解析），提升后才是用户直觉语义；注意这意味着
      ``$script`` 顶层赋值是**模块级常量**而非类属性；
    - ``from __future__ import ...`` 只能出现在脚本顶端（否则原脚本
      即 SyntaxError），单独抽出返回，发射到产物文件最前（语法硬性
      要求：须先于一切其他语句）。

    顶层 ``@on`` 装饰的无 ``self`` 函数 →
    :class:`flowing.errors.FormatError`——``@on`` 的收集只扫类 MRO，
    提升到模块级会静默丢失注册（作者笔误，编译期必须报错）。

    行区间按 AST 节点切分（函数段含装饰器行）；节点间的前导空隙行
    （注释 / 空行）归入随后节点的段，末尾空隙归入最后节点的段。

    :return: ``(模块级段文本, 类体段文本, future import 语句列表)``。
    """
    tree = ast.parse(script)
    lines = script.splitlines(keepends=True)

    def _is_on_decorator(dec: ast.expr) -> bool:
        # @on(...) / @on(...)[pattern] / 裸 @on 三形态，剥 Call/Subscript 取底名
        target = dec
        while isinstance(target, (ast.Call, ast.Subscript)):
            target = target.func if isinstance(target, ast.Call) else target.value
        return isinstance(target, ast.Name) and target.id == "on"

    module_parts: list[str] = []
    class_parts: list[str] = []
    future_imports: list[str] = []
    last_is_method = False
    prev_end = 0   # 已消费到的行号（1-based 行号即已消费行数）
    for node in tree.body:
        end = node.end_lineno or node.lineno
        start = node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
            start = min(start, *(d.lineno for d in node.decorator_list))
        begin = min(start, prev_end + 1)   # 前导空隙（注释 / 空行）归入本节点段
        text = "".join(lines[begin - 1:end])
        prev_end = end
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            future_imports.append(text.rstrip("\r\n"))
            continue
        is_method = False
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            positional = node.args.posonlyargs + node.args.args
            is_method = bool(positional) and positional[0].arg == "self"
            if not is_method and any(_is_on_decorator(d) for d in node.decorator_list):
                raise FormatError(
                    f"@on handler {node.name!r} in $script must take self — "
                    "top-level functions without self are hoisted to module level "
                    "where @on registration is never collected")
        (class_parts if is_method else module_parts).append(text)
        last_is_method = is_method
    tail = "".join(lines[prev_end:])
    if tail.strip():   # 末尾空隙归入最后节点的段（全文无节点 → 模块级段）
        (class_parts if last_is_method else module_parts).append(tail)
    return ("".join(module_parts).rstrip("\r\n"),
            "".join(class_parts).rstrip("\r\n"),
            future_imports)


def _script_setup_fn(script: str, name: str, fya_path: Path) -> Any:
    """临时 exec ``$script`` 取用户 setup 函数对象（签名探测用；类合成时还会再 exec）。

    无该成员 → ``None``。exec 失败（语法/导入错误）→
    :class:`flowing.errors.CompileError`。
    """
    ns: dict[str, Any] = {}
    try:
        exec(compile(script, str(fya_path), "exec"), ns)
    except Exception as exc:
        raise CompileError(f"$script of {fya_path} failed: {exc}") from exc
    return ns.get(name)


def _setup_signature(setup_fn: Any, *, class_name: str) -> tuple[dict[str, Any] | None, frozenset[str] | None]:
    """用户 setup 签名 → ``(args properties, 透传参数集)``.

    - ``**kwargs`` / ``*args`` 吸收形态 → ``(None, None)`` （无
      args_model；透传参数集 ``None`` = 全量透传）；无参形态 →
      ``(None, frozenset())`` （无 args_model；包装 setup 不透传任何
      参数，避免空参 setup 收到 kwargs 炸 TypeError）；
    - 参数缺类型标注 → 内置 ``ValueError`` （构造期抛——作者笔误，
      编程错误通道）；默认值须 JSON 可序列化（要发射进产物
      .py 与桥接进 schema）；
    - 注解解析与工具侧同源：字符串注解（``from __future__
      import annotations``）经 ``$script`` 命名空间求值后，统一交给
      pydantic ``create_model``——支持 ``Annotated`` + ``Field`` 元数据
      （``description`` / 约束）与任意 pydantic 类型；产物 properties
      过 ``bridge_properties`` 收敛到桥接子集（去 ``title`` 等）；
    - 正常形态 → ``(properties, frozenset(参数名))``——透传参数集供
      装配层生成的包装 setup 过滤 ``kwargs`` （编译期定死，产物运行期
      不做签名探测）。
    """
    sig = inspect.signature(setup_fn)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    if any(p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL) for p in params):
        return None, None
    if not params:
        return None, frozenset()
    # 缺注解（原始形态）先报——作者笔误通道，优先于 hints 求值
    for p in params:
        if p.annotation is p.empty:
            raise ValueError(
                f"setup parameter {p.name!r} of {class_name} lacks a type annotation — "
                "cannot derive args_model (add an annotation or declare args: in the .fya)")
    # 字符串注解经 $script 命名空间求值（globals = exec 该 $script 的 dict，
    # 含其 import 的名字，如 pydantic.Field / typing.Annotated）
    def _resolve(p: Any) -> Any:
        ann = p.annotation
        if isinstance(ann, str):
            try:
                return eval(ann, setup_fn.__globals__)  # noqa: S307 — $script 作者自己的注解
            except Exception as exc:
                raise FormatError(
                    f"setup parameter {p.name!r} of {class_name}: cannot evaluate "
                    f"type annotation {ann!r}") from exc
        return ann

    from pydantic import create_model

    fields: dict[str, Any] = {}
    for p in params:
        if p.default is not p.empty:
            fields[p.name] = (_resolve(p), p.default)
        else:
            fields[p.name] = (_resolve(p), ...)
    model = create_model(f"{class_name}SetupArgs", **fields)
    props = bridge_properties(model.model_json_schema()["properties"])
    # default 可序列化检查（产物 .py 发射与 schema 桥接都需要）
    for pname, prop in props.items():
        if "default" in prop:
            try:
                json.dumps(prop["default"])
            except TypeError as exc:
                raise FormatError(
                    f"default value of setup parameter {pname!r} of {class_name} is not JSON-serializable"
                    " (it must be bridged into the args schema)") from exc
    return props, frozenset(props)


def _emit_value(value: Any) -> str:
    """Python 字面量发射（产物 .py 的右值）。

    ``PENDING`` → ``PENDING`` 引用；``Parsable`` → ``Parsable(<source>)``
    重构；映射/列表/元组递归；其余标量走 ``repr``。
    """
    if value is PENDING:
        return "PENDING"
    if isinstance(value, Parsable):
        return f"Parsable({_emit_value(value.source)})"
    if isinstance(value, Mapping):
        items = ", ".join(f"{_emit_value(k)}: {_emit_value(v)}" for k, v in value.items())
        return "{" + items + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_emit_value(v) for v in value) + "]"
    return repr(value)


def _emit_entry_ref(ref: EntryRef) -> str:
    """``EntryRef`` 的发射（原始引用串 + 别名 + 已合并的覆写映射体）。"""
    return (f"EntryRef(raw={_emit_value(ref.raw)}, alias={_emit_value(ref.alias)}, "
            f"body={_emit_value(dict(ref.body))})")


@dataclass
class _Assembly:
    """``_build`` 的产物：发射源码 + 类名 + fya_hash（内存/落盘两形态共享）。"""

    source: str
    """发射的 ``.py`` 源码全文。"""

    class_name: str
    """合成的 Agent 子类名。"""

    fya_hash: str
    """解析结构口径的 ``.fya`` hash（带算法名前缀）。"""


def _project_root() -> Path | None:
    """launch 上下文的项目根（无上下文 → ``None``；局部 import 破环）。"""
    from flowing.runtime import _current_project_root   # 局部 import：runtime 不反向依赖本模块头部

    return _current_project_root.get()


def _agent_naming():
    """``AGENT_NAMING`` 规则表（局部 import：本模块头部不反向拉 runtime）。"""
    from flowing.runtime import AGENT_NAMING

    return AGENT_NAMING


def _source_file_value(fya_path: Path,
                       project_root: "Path | None" = None) -> str:
    """``source_file`` 类属性注入值：根内 ``@/`` 相对形式，根外/无上下文 → 绝对路径。

    项目根取显式 ``project_root``（运行时编译由 Runtime 传入，F3），否则
    launch 上下文（:func:`_project_root`）；两者皆无时 ``@/`` 不可表达，
    绝对路径同样被 ``resolve_path`` 原样接受。
    """
    resolved = fya_path.resolve()
    root = project_root if project_root is not None else _project_root()
    if root is not None:
        try:
            return "@/" + str(resolved.relative_to(Path(root).resolve()))
        except ValueError:
            pass   # 根外：无 "@/" 表达
    return str(resolved)


def _build(fya_path: Path, *,
           project_root: "Path | None" = None) -> _Assembly:
    """``.fya`` → 发射源码（解析 → 装配 → 合成三步一体；内存/落盘两形态共享）。

    - 字面层：``parse_fya`` （不带 ``entry_fields``——glob 条目过不了
      ``normalize_entries``，资源列表由本层先经 ``_expand_glob_entries``
      分流展开，展开产物即 ``list[EntryRef]``）；
    - 装配：具名块填回（``_merge_named_blocks`` 四条导航规则）、``name``
      一致性断言、``class_name`` 推断（kebab→Pascal + 保证 ``Agent``
      结尾的后缀策略）、``args`` 经 ``expand_args_schema`` 归一化
      （桥接为 ``args_model`` 发生在产物 import 期的
      ``schema_to_model`` 调用——声明即模型）；缺省从 ``$script``
      用户 ``setup`` 签名推导；
    - 合成：发射 .py 源码（文件头注释 → ``from __future__`` 与按需
      import → ``$script`` 模块级段（一切非 self 方法的顶层语句，保序）
      → 模块级 ``_FYA_*`` 数据常量 → 类属性赋值 → ``$script`` 类体段
      （self 方法）→ 装配层生成的 ``setup``）。

    :param project_root: 项目根（``@/`` 展开与 ``source_file`` 换算基准）。
        运行时编译由 :func:`compile_fya_class` 从 Runtime 传入（F3）；缺省
        回落 launch 上下文（:func:`_project_root`）。
    """
    fya_path = Path(fya_path)
    try:
        text = fya_path.read_text(encoding="utf-8")
        doc = parse_fya(text)   # entry_fields 留空：glob 分流后再规范化（见 docstring）
    except FormatError:
        raise
    except Exception as exc:
        raise CompileError(f"failed to parse {fya_path}: {exc}") from exc
    fya_hash = _fya_hash(doc)
    fields = doc.fields
    root = project_root if project_root is not None else _project_root()   # @/ 基准（F3）

    # 资源列表字段：形态校验 + glob 展开（先显式后 glob、同资源跳过——
    # _expand_glob_entries 入参是规范化之前的原始 YAML 列表项）；
    # tools / subagents 字段各接资源形态过滤（非本字段形态的 glob 命中
    # 跳过 + 告警，如共享 impl.py / 工具定义 .tool.fya / 无入口子目录），
    # 别名推断的 naming 规则表同样按字段分派（.tool.fya 需 TOOL_NAMING
    # 才能剥出合法别名——用 AGENT_NAMING 会残留 .tool 段报 FormatError）
    from flowing.agent_registry import _agent_glob_accept   # 函数内 import：模块头依赖图保持单向
    from flowing.tool.core import TOOL_NAMING   # 同上
    from flowing.tool.registry import _tool_glob_accept   # 同上
    _FIELD_NAMING = {"tools": TOOL_NAMING, "subagents": _agent_naming()}
    _GLOB_ACCEPT = {"tools": _tool_glob_accept, "subagents": _agent_glob_accept}
    for field_name in _ENTRY_FIELDS:
        if field_name in fields:
            items = fields[field_name]
            if not isinstance(items, list):
                raise FormatError(f"resource list field {field_name!r} must be a YAML list")
            fields[field_name] = _expand_glob_entries(
                items, naming=_FIELD_NAMING[field_name],
                source_dir=fya_path.parent, project_root=root,
                glob_accept=_GLOB_ACCEPT[field_name])

    # 装配：具名块填回（四条导航规则）
    _merge_named_blocks(fields, doc.blocks)

    # 身份名与类名
    identity = infer_name(fya_path, naming=_agent_naming())
    declared_name = fields.pop("name", None)
    if declared_name is not None and declared_name != identity:
        raise NameMismatchError(str(declared_name), identity, str(fya_path))
    class_name = fields.pop("class_name", None)
    if class_name is None:
        class_name = kebab_to_pascal(identity)
        if not class_name.endswith("Agent"):   # 后缀策略：始终保证以 Agent 结尾
            class_name += "Agent"

    # 已知字段分流（未知字段落 _extra）
    description = fields.pop("description", _MISSING)
    system_prompt = fields.pop("system_prompt", _MISSING)
    model_tag = fields.pop("model_tag", _MISSING)
    metadata = fields.pop("metadata", _MISSING)
    if metadata is not _MISSING and not isinstance(metadata, Mapping):
        raise FormatError(f"metadata field of {fya_path} must be a mapping")
    tools_refs: list[EntryRef] = fields.pop("tools", None) or []
    subagent_refs: list[EntryRef] = fields.pop("subagents", None) or []
    args_field = fields.pop("args", _MISSING)
    if args_field is not _MISSING and not isinstance(args_field, Mapping):
        raise FormatError(f"args field of {fya_path} must be a mapping")
    extra = fields   # 其余字段全部落 _extra（框架不解释，扩展自行 resolve）

    # $script：有装配工作（_extra / 条目绑定）时改名用户 setup 并生成包装；
    # 探测 exec 用未拆分的原文（注解求值依赖其顶层 import 的名字），
    # 发射前再二分：self 方法留类体段，其余语句归模块级段
    script = doc.script or ""
    need_wrapper = bool(extra or tools_refs or subagent_refs)
    user_setup_fn = None
    setup_params: frozenset[str] | None = None
    module_script = ""
    future_imports: list[str] = []
    if script.strip():
        if need_wrapper:
            script = _rename_user_setup(script)
        user_setup_fn = _script_setup_fn(
            script, _USER_SETUP_NAME if need_wrapper else "setup", fya_path)
        module_script, script, future_imports = _split_script(script)

    # args 桥接：显式 args: 优先；缺省从用户 setup 签名推导
    args_props: dict[str, Any] | None
    if args_field is not _MISSING:
        args_props = expand_args_schema(args_field)
        if user_setup_fn is not None:
            derived_props, setup_params = _setup_signature(user_setup_fn, class_name=class_name)
            if derived_props is not None:
                # 校验对照（参数必须有对应字段；类型兼容检查未实现）
                for pname in derived_props:
                    if pname not in args_props:
                        raise FormatError(
                            f"{fya_path}: setup parameter {pname!r} has no corresponding field in the args: declaration")
    elif user_setup_fn is not None:
        args_props, setup_params = _setup_signature(user_setup_fn, class_name=class_name)
    else:
        args_props = None

    # description 缺省：.fya 未写 description 字段时，取用户 setup 方法的
    # docstring **整体**（cleandoc 全文）作为 agent 描述（与工具侧
    # docstring 整体回退同口径；无 setup 或无 docstring → 维持缺省）
    if description is _MISSING:
        if user_setup_fn is not None and user_setup_fn.__doc__:
            import inspect
            description = inspect.cleandoc(user_setup_fn.__doc__).strip()

    source = _emit_source(
        fya_path,
        project_root=root, class_name=class_name,
        description=description, system_prompt=system_prompt,
        model_tag=model_tag, metadata=metadata,
        args_props=args_props, script=script,
        module_script=module_script, future_imports=future_imports,
        tools_refs=tools_refs, subagent_refs=subagent_refs, extra=extra,
        has_user_setup=user_setup_fn is not None,
        need_wrapper=need_wrapper, setup_params=setup_params)
    return _Assembly(source=source, class_name=class_name, fya_hash=fya_hash)


def _emit_source(
    fya_path: Path,
    *,
    project_root: "Path | None" = None,
    class_name: str,
    description: Any,
    system_prompt: Any,
    model_tag: Any,
    metadata: Any,
    args_props: dict[str, Any] | None,
    script: str,
    module_script: str,
    future_imports: list[str],
    tools_refs: list[EntryRef],
    subagent_refs: list[EntryRef],
    extra: dict[str, Any],
    has_user_setup: bool,
    need_wrapper: bool,
    setup_params: frozenset[str] | None,
) -> str:
    """发射产物 .py 源码（固定模板）。

    模板：文件头注释（来源与「勿手改」提示）→ ``$script`` 拆出的
    ``from __future__`` import（语法要求最前）→ 按需 import
    （``PENDING`` 永在首行：条目覆写常量也可能引用它，按需判定的
    扫描面曾漏过它们）→
    ``$script`` 模块级段（无 self 函数 / 类声明 / 赋值等一切非方法
    顶层语句，保序）→ 模块级 ``_FYA_*`` 数据常量（刻意不放类体——
    ``_check_pending`` 会扫类 MRO，条目覆写里的 PENDING 空补丁会被
    误判为未兑现字段）→ ``class <Name>(Agent):`` 类属性赋值 →
    ``$script`` 类体段（self 方法）→ 装配层生成的 ``setup`` （前置段：
    ``_extra`` 合入 + 条目预处理（MCP 合成名展开与名字 glob 的运行期
    展开，经 ``_prepare_tool_refs`` / ``_prepare_agent_refs``）+ 条目
    绑定 → 按编译期定死的透传参数集委托用户
    setup）。
    """
    imports = ["from flowing import Agent, PENDING"]
    body_lines: list[str] = []

    body_lines.append(f"source_file = {_emit_value(_source_file_value(fya_path, project_root))}")
    for attr, value in (("description", description), ("system_prompt", system_prompt)):
        if value is _MISSING:
            continue   # 未声明：description 缺省 None；system_prompt 缺省由创建管线决定
        if value is None:
            body_lines.append(f"{attr} = None")
        elif value is PENDING:
            # 声明了 _ 且未被具名块填回——原样发射，创建管线
            # _check_pending 兑现检查（MissingFieldError）
            body_lines.append(f"{attr} = PENDING")
        elif isinstance(value, str):
            body_lines.append(f"{attr} = Parsable({_emit_value(value)})")
        else:
            raise FormatError(f"{attr} field of {fya_path} must be a string/_/null")
    if model_tag is not _MISSING:
        body_lines.append(f"model_tag = {_emit_value(model_tag)}")
    if metadata is not _MISSING:
        body_lines.append(f"metadata = {_emit_value(metadata)}")
    if args_props is not None:
        body_lines.append(
            f"args_model = schema_to_model({_emit_value(class_name + 'Args')}, "
            f"{_emit_value(args_props)})")

    if any("Parsable(" in line for line in body_lines):
        imports[0] = "from flowing import Agent, PENDING, Parsable"
    if args_props is not None:
        imports.append("from flowing.params import schema_to_model")
    if tools_refs or subagent_refs:
        imports.append("from flowing.parser import EntryRef")
    if need_wrapper and has_user_setup:
        imports.append("import inspect")

    constants: list[str] = []
    if tools_refs:
        constants.append("_FYA_TOOLS = [\n"
                         + "".join(f"    {_emit_entry_ref(r)},\n" for r in tools_refs) + "]")
    if subagent_refs:
        constants.append("_FYA_SUBAGENTS = [\n"
                         + "".join(f"    {_emit_entry_ref(r)},\n" for r in subagent_refs) + "]")
    if extra:
        constants.append(f"_FYA_EXTRA = {_emit_value(extra)}")

    if script.strip():
        body_lines.append("")
        body_lines.extend(script.rstrip("\r\n").splitlines())
    if need_wrapper:
        body_lines.append("")
        body_lines.append("async def setup(self, **kwargs):")
        body_lines.append(
            '    """装配层生成的 setup：_extra 合入 + 条目绑定 → 委托用户 setup。"""')
        if extra:
            body_lines.append("    self._extra.update(_FYA_EXTRA)")
        if tools_refs:
            body_lines.append("    for _ref in await self._prepare_tool_refs(_FYA_TOOLS):")
            body_lines.append("        self.add_tool(_ref)")
        if subagent_refs:
            body_lines.append("    for _ref in await self._prepare_agent_refs(_FYA_SUBAGENTS):")
            body_lines.append("        self.add_agent(_ref)")
        if has_user_setup:
            if setup_params is None:
                body_lines.append(f"    _result = self.{_USER_SETUP_NAME}(**kwargs)")
            else:
                body_lines.append(
                    f"    _result = self.{_USER_SETUP_NAME}("
                    "**{k: v for k, v in kwargs.items()"
                    f" if k in {_emit_value(sorted(setup_params))}}})")
            body_lines.append("    if inspect.isawaitable(_result):")
            body_lines.append("        await _result")

    parts: list[str] = [
        f"# 本文件由 flowing 编译器自动生成（compiler_version={COMPILER_VERSION}），"
        f"来源：{fya_path.name}",
        "# 请勿手工修改（py_hash 闸会拒绝覆盖外部修改）；如需手改请转正为手写子类。",
        *future_imports,   # $script 拆出，语法要求先于一切其他语句
        *imports,
    ]
    if module_script:
        # $script 模块级段（无 self 函数 / 类声明 / 赋值 / import 等，
        # 保序）——方法体经模块 globals 可见；跟在编译器 import 之后，
        # 可引用其中的 Agent / Parsable 等名字
        parts += module_script.splitlines() + ["", ""]
    else:
        parts += ["", ""]
    parts.extend(constants)
    if constants:
        parts += ["", ""]
    parts.append(f"class {class_name}(Agent):")
    parts.append(textwrap_indent(body_lines))
    parts.append("")
    return "\n".join(parts)


def textwrap_indent(body_lines: list[str]) -> str:
    """类体缩进（空行不缩进，避免行尾空白）。"""
    return "\n".join(("    " + line) if line.strip() else "" for line in body_lines)


# ---------------------------------------------------------------------------
# hash 与 meta（防覆盖闸）
# ---------------------------------------------------------------------------


def _normalize_for_hash(value: Any) -> Any:
    """hash 前规范化：消除不稳定 repr（PENDING 哨兵、EntryRef），dict 键排序。"""
    if value is PENDING:
        return "<PENDING>"
    if isinstance(value, EntryRef):
        return ("EntryRef", value.raw, value.alias,
                _normalize_for_hash(dict(value.body)))
    if isinstance(value, Mapping):
        return {k: _normalize_for_hash(v)
                for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_hash(v) for v in value]
    return value


def _fya_hash(doc: FyaDocument) -> str:
    """``fya_hash``：解析结构口径——``repr`` 规范化后的
    ``FyaDocument`` （注释/空行不进解析产物，天然不影响 hash）。

    块体与 ``$script`` 原文在 hash 前剥尾部换行——与发射侧口径一致
    （块体以 ``---`` / EOF 收尾必带行尾换行，属容器格式而非内容；
    文件末尾增删空行不应触发重编译）。
    """
    blocks = {k: v.rstrip("\r\n") for k, v in doc.blocks.items()}
    script = doc.script.rstrip("\r\n") if doc.script is not None else None
    payload = ("FyaDocument", _normalize_for_hash(doc.fields),
               _normalize_for_hash(blocks), script)
    return "sha256:" + hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def _py_hash(source: str) -> str:
    """``py_hash``：AST 口径——``ast.parse`` 后规范化转储
    （格式化改动不影响；语义改动才变）。"""
    tree = ast.parse(source)
    return "sha256:" + hashlib.sha256(ast.dump(tree).encode("utf-8")).hexdigest()


def _load_meta(meta_path: Path) -> dict[str, Any]:
    """读同目录 meta（不存在/空 → 空表）。"""
    if not meta_path.exists():
        return {}
    data = YAML(typ="safe").load(meta_path.read_text(encoding="utf-8"))
    return dict(data) if isinstance(data, dict) else {}


def _save_meta(meta_path: Path, meta: Mapping[str, Any]) -> None:
    """写同目录 meta（每目录一份，条目以 ``.fya`` 文件名为键；稳定键序）。"""
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump({k: meta[k] for k in sorted(meta, key=str)}, buf)
    meta_path.write_text(buf.getvalue(), encoding="utf-8")


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------


def compile_fya_class(fya_path: Path, *,
              project_root: "Path | None" = None) -> type:
    """``.fya`` → 内存中的 Agent 类（运行期唯一合成点）。

    .. rubric:: 功能介绍

    流水：``parser.parse_fya`` （字面层，文本 → ``FyaDocument``）→ 装配
    （具名块填回、EntryRef 判别、``args`` 经
    :func:`flowing.params.expand_args_schema` 归一化 +
    :func:`flowing.params.schema_to_model` 桥接为 ``args_model``）→
    合成（生成 Agent 子类：类属性注入 ``source_file``/``description``/
    ``system_prompt`` 等；``$script`` 按「self 签名」二分——带 self 的
    顶层函数（``setup`` / ``@on`` handler / 实例方法）入类体，其余一切
    顶层语句（import / 无 self 函数 / 类声明 / 赋值等）按原序提升到
    模块级——类体层语句对方法体不可见，提升后才是用户直觉语义）。
    产物是普通 Python 类，与手写子类同属一个类模型（二者是
    等价的两条声明途径，同目录并存时 ``.fya`` 优先）。

    .. rubric:: 行为要点

    - 同步、纯内存：不写任何文件（落盘形态是 :func:`compile_fya_file`
      = 本函数 + 写出 ``.py``）。
    - 每次调用现场合成，无缓存（调用方的注册表 / 惰性解析层负责去重）。
    - ``.fya`` 与同名手写 ``.py`` 并存时的优先级由
      ``Runtime.get_agent_class`` 的解析侧决定（``.fya`` 优先并告警），
      本函数不处理。
    - 声明错误（``FormatError`` 族）原样上抛；其余合成失败包装为
      :class:`flowing.errors.CompileError`。

    :param fya_path: 源 ``.fya`` 路径。
    :param project_root: 项目根（``@/`` 展开与 ``source_file`` 换算
        基准）。运行时编译由 ``Runtime`` 传入（F3：惰性建 Agent 不依赖
        launch 上下文）；缺省回落 launch 上下文。
    :return: 合成的 Agent 子类。
    :raises flowing.errors.CompileError: 解析或合成失败。

    .. seealso:: :func:`compile_fya_file` —— 落盘形态。
    """
    assembly = _build(Path(fya_path), project_root=project_root)
    module = types.ModuleType(f"flowing_fya_{assembly.class_name}")
    module.__file__ = str(fya_path)   # traceback 定位到源 .fya
    try:
        exec(compile(assembly.source, str(fya_path), "exec"), module.__dict__)
    except Exception as exc:
        raise CompileError(f"failed to synthesize the Agent class of {fya_path}: {exc}") from exc
    cls = module.__dict__.get(assembly.class_name)
    if not isinstance(cls, type):
        raise CompileError(f"synthesis failed for {fya_path}: {assembly.class_name} is not a class")
    return cls


def compile_fya_file(fya_path: Path, *,
             project_root: "Path | None" = None) -> Path:
    """编译单个 ``.fya`` 为同目录 ``.py``，含 meta hash 校验。

    .. rubric:: 功能介绍

    时序四步：

    1. 合成：经 :func:`compile_fya_class` 同源的 ``_build`` 得到发射
       源码（字面层 + 装配 + 合成一步完成）；
    2. 发射：生成 ``.py`` 源码——Parsable 值以类体赋值形式写出
       （如 ``system_prompt = Parsable('$./system-prompt.md')``），
       ``$script`` 按「self 签名」二分：self 方法入类体，其余顶层
       语句按原序提升到模块级；
    3. meta 校验：读同目录 ``.flowing.meta.yaml`` （每目录一份，
       条目以 ``fya_path.name`` 为键）中本文件的条目——产物 ``.py``
       已存在且记录的 ``py_hash`` （AST 口径）与实际不符 →
       抛 :class:`flowing.errors.ArtifactModifiedError` （不覆盖）；
       ``fya_hash`` （解析结构口径）未变且 ``compiler_version`` 一致 →
       跳过发射，直接返回产物路径（幂等）；版本不同 → 强制重编译
       （见 :data:`COMPILER_VERSION`）；
    4. 写盘：写产物 ``.py`` 并更新 meta 条目三字段
       （``fya_hash`` / ``py_hash`` / ``compiler_version``）。

    :param fya_path: 源 ``.fya`` 路径。
    :param project_root: 项目根（同 :func:`compile_fya_class`，缺省回落
        launch 上下文）。
    :return: 产物 ``.py`` 的路径（无论本次是否重编译）。
    :raises flowing.errors.ArtifactModifiedError: 产物被外部修改
      （``py_hash`` 不匹配，或无 meta 记录的既有产物）——报错中止，
      不静默覆盖。
    :raises flowing.errors.CompileError: 解析或发射失败。

    .. seealso:: :func:`compile_project` —— 项目级入口。
    """
    fya_path = Path(fya_path)
    product = fya_path.with_suffix(".py")   # 同目录去 .fya 后缀
    # 合成与发射（与 compile_fya_class 同源的 _build；Parsable 以类体赋值写出）
    assembly = _build(fya_path, project_root=project_root)
    # meta 校验：py_hash 不匹配 -> ArtifactModifiedError；fya_hash 未变且
    # compiler_version 一致 -> 幂等跳过
    meta_path = fya_path.parent / _META_FILENAME
    meta = _load_meta(meta_path)
    entry = meta.get(fya_path.name)
    if product.exists():
        actual = _py_hash(product.read_text(encoding="utf-8"))
        if entry is None or entry.get("py_hash") != actual:
            # 产物被外部修改（或无 meta 记录的既有产物）→ 报错中止，不覆盖
            raise ArtifactModifiedError(product)
        if (entry.get("fya_hash") == assembly.fya_hash
                and entry.get("compiler_version") == COMPILER_VERSION):
            return product   # 幂等 no-op
    # 写产物 + 更新 meta 三字段
    product.write_text(assembly.source, encoding="utf-8")
    meta[fya_path.name] = {
        "fya_hash": assembly.fya_hash,
        "py_hash": _py_hash(assembly.source),
        "compiler_version": COMPILER_VERSION,
    }
    _save_meta(meta_path, meta)
    return product


_TOOL_SKILL_FYA_GENERIC = frozenset({"TOOL.fya", "tool.fya"})
"""``compile_project`` 扫描时排除的 Tool 通用名（工具资源不是 Agent 合成对象）。"""


def _is_agent_fya(path: Path) -> bool:
    """``.fya`` 是否 Agent 声明（排除 tool/skill 形态——它们不是本编译器的对象）。

    工具（``TOOL.fya`` / ``*.tool.fya``）与 skill（``*.skill.fya``）有
    各自命名形态与装配层，误当 Agent 合成必炸——按命名形态排除。
    """
    name = path.name
    if name in _TOOL_SKILL_FYA_GENERIC:
        return False
    return not (name.endswith(".tool.fya") or name.endswith(".skill.fya"))


def compile_project(path: Path) -> list[Path]:
    """项目级编译：递归扫描 ``*.fya`` 并逐文件编译。

    .. rubric:: 功能介绍

    ``flowing compile <path>`` 的唯一编译入口。meta 随产物走——每个
    ``.fya`` 的 hash 条目写入其所在目录的 ``.flowing.meta.yaml``。
    逐个调用 :func:`compile_fya_file`；任一文件抛
    ``ArtifactModifiedError`` 则中止（已产出文件不回滚——编译幂等，
    重跑可继续）。

    .. rubric:: 行为要点

    - 边缘情况：无 ``.fya`` 文件 → 返回空列表（调用方 CLI 打印
      「无可编译文件」并正常退出）。
    - 重复执行：无变更时全量命中 ``fya_hash``，等价 no-op。
    - 不编译 tool / skill 形态的 ``.fya`` （``TOOL.fya`` /
      ``*.tool.fya`` / ``*.skill.fya``——见 :func:`_is_agent_fya`）。

    :param path: 项目根目录。
    :return: 全部产物 ``.py`` 路径（按扫描序，``sorted`` 保证稳定）。
    :raises flowing.errors.ArtifactModifiedError: 首个 hash 冲突处中止。

    .. seealso:: :func:`flowing.interfaces.cli.cmd_compile` —— CLI 壳与退出码映射。
    """
    fya_files = [p for p in sorted(Path(path).rglob("*.fya")) if _is_agent_fya(p)]
    products: list[Path] = []
    for fya in fya_files:
        products.append(compile_fya_file(fya))   # meta 随产物同目录
    return products
