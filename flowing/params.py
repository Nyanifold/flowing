"""``flowing.params`` —— 参数声明（Pydantic 模型 / JSON Schema）与类型安全键。

.. rubric:: 功能介绍

本模块承载工具与子 Agent 的参数声明基础设施，以及两个与参数声明无关
的类型安全键（:class:`InjectionKey` / :class:`ConfigKey`）。参数声明有
两种同构形态，由同一份声明派生出 LLM 可见的 JSON Schema 与执行层校验
模型，没有需要独立维护的第二份声明：

- Python 层：Pydantic ``BaseModel`` 子类——手写工具 / Agent 子类的
  声明方式。LLM 可见 schema 由 ``model_json_schema()`` 派生，执行层
  校验即模型本身（``model_validate``）；
- fya 层：``args:`` 块的展开式 JSON Schema——装配期经
  :func:`schema_to_model` 桥接为等价 Pydantic 模型。

fya 声明层（``args:`` 块）的书写规则：逐参数展开，无 ``type: object``
顶层包装、无 ``required:`` 清单——必填性由 ``default`` 有无派生（无
``default`` → 必填，与 Pydantic「无默认值字段必填」语义同构）::

    args:
      user_id: {type: string, description: 用户 ID}   # 无 default → 必填
      amount: {type: number, minimum: 0.01}            # 必填 + 范围约束
      currency: {type: string, default: CNY}           # 有 default → 可选
      page_size: 20                                    # 字面量 → {type: integer, default: 20}
      category: str                                    # 裸类型字符串 → {type: string}（必填）

两种简写糖（:func:`expand_args_schema` 归一化，声明端专用；覆写端零糖）：

- 裸类型字符串：``str`` / ``int`` / ``float`` / ``bool`` / ``list`` /
  ``dict`` → 对应 ``{type: ...}`` 单键 property（必填）；
- YAML 字面量：标量 / 列表字面量 → 按值类型推断 ``type`` 并以值为
  ``default`` （可选）。

其余值一律是完整 JSON Schema property dict（``{}`` 合法，表示匹配一切
的 any 类型）。``type:`` 的取值同时识别 Python 风格简写名与 JSON Schema
名（``type: str`` ≡ ``type: string``，别名表 :data:`TYPE_ALIASES`），并
支持类型表达式：联合（``str | None``）与泛型嵌套（``list[str]`` /
``dict[str, int]``）可组合。可空参数写标准 nullable 形态
``{type: [string, "null"], default: null}`` （Pydantic 侧对应
``str | None = None``）。

桥接（:func:`schema_to_model`）认识的 JSON Schema 关键字子集：``type`` /
``description`` / ``default`` / ``enum`` / ``minimum`` / ``maximum`` /
``minLength`` / ``maxLength`` / ``pattern`` / ``items`` / ``properties``
——逐关键字映射为 Pydantic 字段约束；超出子集（``oneOf`` / ``anyOf`` /
``$ref`` / ``allOf`` 等组合子）在装配期抛
:class:`flowing.errors.FormatError` （fail-fast：出错即刻抛异常、不静默
降级）。

覆写（:func:`apply_param_overrides`）供绑定层（``ToolEntry`` /
``SubagentEntry``）的 ``override_params`` 使用：对基底 JSON Schema
properties 做稀疏深合并，只写要改的参数与关键字；覆写端零语法糖（类型
必须写 ``type:`` 关键字），未知关键字抛 ``FormatError``，未提及的
关键字原样沿用基底，未知参数名视为新增参数。

安全边界：``args`` / ``parameters`` 最终序列化为 JSON 经 function
calling 传给 LLM，因此只接受 JSON 兼容值（``str`` / ``int`` / ``float`` /
``bool`` 标量及其 ``list`` / ``dict`` 嵌套组合）。复杂对象（数据库连接、
服务客户端、函数、生成器）不允许走参数声明；``specified`` 也不是复杂
对象通道——它只是为 LLM 不可见的标量参数提供值（固定值或注入表达式）。
复杂对象的两条替代路径：

1. 传可序列化标识符（``connection_id: "main"``），工具内部按标识符查找；
2. ``execute()`` 声明 ``caller`` 参数，从调用方 Agent 实例上取对象。

类型安全键：:class:`InjectionKey` 与 :class:`ConfigKey` 是两个仅在调用
侧生效的泛型类型安全键：泛型参数 ``T`` 只提供编译期类型标注，运行期
完全退化为按 ``name`` 的字符串匹配（注入存储的 key 恒为 ``str``），框架
不做运行期类型校验。因此类型不匹配（亲节点 provide 了 ``int``、子按
``InjectionKey[str]`` inject）不会被框架拦截——需要运行期校验的场景走
参数声明（Pydantic 模型校验），而非 provide / inject。

本模块的公开面为下列符号：常量 ``ARG_SHORTHAND`` / ``TYPE_ALIASES`` /
``SCHEMA_KEYWORDS``，函数 ``expand_args_schema`` / ``schema_to_model`` /
``apply_param_overrides``，类 :class:`InjectionKey` / :class:`ConfigKey`
——属跨版本稳定契约；其余符号（类型变量 ``T`` 与下划线前缀符号）为内部
实现。

.. seealso::

    :mod:`flowing.parsable`
        Parsable 惰性求值、渲染上下文与 ``PENDING`` 哨兵的定义模块。
    :class:`flowing.agent.Agent`
        ``args_model`` 的宿主。
    :class:`flowing.tool.ToolDefinition`、:class:`flowing.tool.ToolEntry`
        Tool 侧参数声明与 ``specified`` 聚合的消费者。
    :meth:`flowing.runtime.Runtime.get_config`
        ``ConfigKey`` 的消费入口。
    :func:`flowing.runtime.inject_from`
        ``InjectionKey`` 跨节点退化为按 ``name`` 匹配的执行点。
"""

import copy
import re
from typing import Any, Generic, Literal, Mapping, TypeVar

from pydantic import BaseModel, Field

from flowing.errors import FormatError

T = TypeVar("T")

# ---------------------------------------------------------------------------
# fya 声明块归一化
# ---------------------------------------------------------------------------

# 裸类型字符串糖 → JSON Schema type（fya 声明端专用；覆写端零糖）
ARG_SHORTHAND: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}
"""裸类型字符串简写表（``str`` → ``"string"`` 等）：fya ``args:`` 声明
块里写 ``category: str`` 时按本表展开为 ``{type: "string"}``。声明端
专用——覆写端（Entry 补丁）不做任何糖，类型必须写 ``type:`` 关键字。
"""

TYPE_ALIASES: dict[str, str] = dict(ARG_SHORTHAND)
"""``type:`` 关键字取值的别名表：Python 风格简写名（``str`` / ``int`` /
``float`` / ``bool`` / ``list`` / ``dict``）与 JSON Schema 名
（``string`` / ``integer`` / ...）等价。声明端与覆写端均识别，桥接
（:func:`schema_to_model`）统一归一为 JSON Schema 名。"""


def _parse_type_expr(expr: str) -> dict:
    """解析 ``type:`` 的取值表达式为 JSON Schema 片段。内部 API。

    除裸名（:data:`TYPE_ALIASES`）外支持两种复合形态（声明端与覆写端
    均识别）：

    - 联合（``|`` 运算）：``str | None`` → ``{"type": ["string",
      "null"]}``；``int | str`` → ``{"type": ["integer", "string"]}``；
    - 泛型嵌套：``list[str]`` → ``{"type": "array", "items":
      {"type": "string"}}``；``dict[str, int]`` → ``{"type": "object"}``
      （键值类型不进入 schema——JSON Schema 的 object 键恒为字符串，
      值类型约束由执行层模型承载）。

    嵌套与联合可组合（``list[str | None]``）。无法解析 →
    :class:`flowing.errors.FormatError` （fail-fast）。
    """
    # 微型递归下降解析器。两处行为约束：
    # - 联合成员仅限裸名——JSON Schema 的 type 数组只接受类型名，泛型
    #   schema（array/object 片段）进不了数组，联合中出现泛型成员属
    #   超子集 → FormatError；
    # - 未知裸名同样 fail-fast（typo 必须死在装配期，不静默降级 Any）。
    text = expr.strip()
    if not text:
        raise FormatError("type expression is empty")
    members = _split_top_level(text, "|")
    if len(members) > 1:
        names: list[str] = []
        for member in members:
            frag = _parse_type_expr(member)
            t = frag.get("type")
            if not isinstance(t, str):  # 泛型成员不能进 JSON Schema type 数组
                raise FormatError(f"union members must be bare type names, got: {member!r}")
            names.append(t)
        return {"type": names}
    m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(.*)\])?", text, flags=re.S)
    if not m:
        raise FormatError(f"cannot parse type expression: {expr!r}")
    base, args_src = m.group(1), m.group(2)
    if base in ("None", "null"):
        return {"type": "null"}
    base_norm = TYPE_ALIASES.get(base, base)
    if args_src is None:
        if base_norm in ("string", "integer", "number", "boolean", "array", "object"):
            return {"type": base_norm}
        raise FormatError(f"unknown type name: {base!r}")
    args = _split_top_level(args_src, ",")
    if base_norm == "array":
        if len(args) != 1:
            raise FormatError(f"list generic requires exactly one argument: {expr!r}")
        return {"type": "array", "items": _parse_type_expr(args[0])}
    if base_norm == "object":
        if len(args) != 2:
            raise FormatError(f"dict generic requires exactly two arguments: {expr!r}")
        _parse_type_expr(args[0])  # 键值类型只校验可解析性，不进入 schema
        _parse_type_expr(args[1])
        return {"type": "object"}
    raise FormatError(f"type {base!r} does not support generic arguments: {expr!r}")


def _split_top_level(text: str, sep: str) -> list[str]:
    """按顶层分隔符切分（``[...]`` 嵌套内的分隔符不切）。内部 API。"""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth < 0:
                raise FormatError(f"unbalanced brackets in type expression: {text!r}")
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if depth != 0:
        raise FormatError(f"unbalanced brackets in type expression: {text!r}")
    parts.append("".join(current))
    if any(not p.strip() for p in parts):
        raise FormatError(f"type expression contains an empty segment: {text!r}")
    return parts

# 桥接子集：本表之外的关键字出现在 property 中 → schema_to_model fail-fast
SCHEMA_KEYWORDS: frozenset[str] = frozenset({
    "type", "description", "default", "enum",
    "minimum", "maximum", "minLength", "maxLength", "pattern",
    "items", "properties",
})
"""桥接子集关键字集合：:func:`schema_to_model` 认识的 JSON Schema
关键字。property 中出现子集之外的关键字（``oneOf`` / ``anyOf`` /
``$ref`` / ``allOf`` 等）→ 桥接期 :class:`flowing.errors.FormatError`。
"""


def expand_args_schema(args: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """把 fya ``args:`` 声明块归一化为 JSON Schema properties（展开两种简写糖）。

    .. rubric:: 功能介绍

    声明端唯一的糖处理点，逐参数按固定顺序判别：

    1. 值是裸类型字符串（:data:`ARG_SHORTHAND` 六个保留字之一）→
       ``{type: <展开值>}`` （必填）；
    2. 值是 dict → 原样作为完整 JSON Schema property（``{}`` 合法，
       any 语义；键超出 :data:`SCHEMA_KEYWORDS` 子集的报错推迟到
       :func:`schema_to_model` 桥接时）；
    3. 值是其余 YAML 字面量（标量 / 列表）→ 按值类型推断 ``type`` 并
       以值为 ``default`` （可选）；值是 ``None`` （YAML ``key:`` 空值）
       → :class:`flowing.errors.FormatError` （连声明意图都无法确认，
       fail-fast）。

    归一化产物不含 required 信息——必填性恒由 property 的 ``default``
    有无派生（无 ``default`` → 必填）。

    :param args: fya ``args:`` 块的原始映射（参数名 → 值）。
    :return: ``{参数名: property dict}``，新构造的 dict（输入不被修改）。
    :raises flowing.errors.FormatError: 某参数值为 ``None`` （空声明）时。

    .. seealso:: :func:`schema_to_model` —— 下一步桥接。
    """
    props: dict[str, dict[str, Any]] = {}
    for name, raw in args.items():
        if isinstance(raw, str) and raw in ARG_SHORTHAND:
            props[name] = {"type": ARG_SHORTHAND[raw]}   # 糖一：裸类型字符串（必填）
        elif isinstance(raw, dict):
            props[name] = dict(raw)                      # 完整 property（原样，子集校验推迟到桥接）
        elif raw is None:
            raise FormatError(f"parameter {name!r} has an empty declaration (None) — give a full property, a literal default, or a bare type string")
        else:
            inferred = type(raw).__name__                # 糖二：YAML 字面量 → 类型 + 默认值
            props[name] = {"type": {"int": "integer", "float": "number", "bool": "boolean",
                                     "str": "string", "list": "array", "dict": "object"}[inferred],
                           "default": raw}
    return props


# ---------------------------------------------------------------------------
# schema → Pydantic 模型桥接
# ---------------------------------------------------------------------------

def schema_to_model(name: str, properties: Mapping[str, dict[str, Any]]) -> type[BaseModel]:
    """把 JSON Schema properties 桥接为 Pydantic 模型（基础子集，fail-fast）。

    .. rubric:: 功能介绍

    fya 声明（经 :func:`expand_args_schema` 归一化）与 Entry 补丁后的
    最终 schema 共用同一个建模入口（经 ``pydantic.create_model`` 动态
    建模）。逐 property 映射：

    - ``type`` → Python 类型（``string``→``str``、``integer``→``int``、
      ``number``→``float``、``boolean``→``bool``、``array``→``list``、
      ``object``→``dict``；``[T, "null"]`` 可空形态 → ``T | None``）；
      取值先经 :data:`TYPE_ALIASES` 归一（``type: str`` ≡
      ``type: string``）；含 ``|`` 或方括号的复合类型表达式（如
      ``str | None``、``list[str]``）经 :func:`_parse_type_expr` 展开；
    - 约束关键字 → ``pydantic.Field`` 约束（``minimum``→``ge``、
      ``maximum``→``le``、``minLength``→``min_length``、
      ``maxLength``→``max_length``、``pattern`` 同名）；
      ``description`` → ``Field(description=...)``；``enum`` → ``Literal``；
    - 必填性派生：property 无 ``default`` → 必填字段；有 → 以
      ``default`` 为字段默认值。

    :param name: 生成的模型类名。
    :param properties: ``{参数名: JSON Schema property dict}``。
    :return: 新的 Pydantic 模型类。

    :raises flowing.errors.FormatError: property 含超出
        :data:`SCHEMA_KEYWORDS` 子集的关键字时（报错信息列出参数名与
        非法关键字）。

    .. rubric:: 行为要点

    - ``{}`` 空 property → 字段类型为 ``Any`` （匹配一切）。
    - ``enum`` 与 ``default`` 共存合法（``default`` 须在 ``enum`` 内，
      校验交 Pydantic）。
    - 返回新模型类，不修改入参；每次调用都创建新模型类，不缓存
      （调用方可自行缓存）。
    - 不做 JSON Schema 全量合规校验：只认上述子集，超子集 fail-fast。
    - 多成员非 null 联合（如 ``[string, integer]``）超出 Python 单类型
      表达精度，桥接为 ``Any`` （子集只承诺 ``[T, "null"]`` 可空形态）。

    .. seealso:: :func:`expand_args_schema` —— 前置归一化；
        :func:`apply_param_overrides` —— 补丁应用。
    """
    from pydantic import create_model  # 动态建模入口

    type_map: dict[str, type] = {
        "string": str, "integer": int, "number": float,
        "boolean": bool, "array": list, "object": dict,
    }

    def _fragment_py_type(fragment: dict) -> Any:
        """``_parse_type_expr`` 产物（或原生 property 的 type 段）→ Python 类型。"""
        t = fragment.get("type")
        if t is None:
            return Any
        if isinstance(t, list):  # [T, "null"] 可空形态 → T | None
            # 成员同样过别名表归一（type: ["str", "null"] ≡ ["string", "null"]），
            # 未知成员 fail-fast（与 _parse_type_expr 口径一致）
            normalized = [TYPE_ALIASES.get(str(x), x) for x in t]
            for x in normalized:
                if x != "null" and x not in type_map:
                    raise FormatError(f"unknown type name: {x!r}")
            rest = [x for x in normalized if x != "null"]
            # 多成员非 null 联合（如 [string, integer]）超出 Python 侧
            # 表达精度 → Any（子集只承诺 [T, "null"] 形态）
            base = type_map.get(rest[0], Any) if len(rest) == 1 else Any
            if len(rest) != len(normalized) and base is not Any:
                return base | None
            return base
        return type_map.get(t, Any)

    # 约束子集 → pydantic.Field 关键字映射（items/properties 只影响类型
    # 层面（array/object），不进 Field；enum → Literal 在类型层面处理）
    _FIELD_KW: dict[str, str] = {
        "minimum": "ge", "maximum": "le",
        "minLength": "min_length", "maxLength": "max_length",
        "pattern": "pattern", "description": "description",
    }
    fields: dict[str, Any] = {}
    for pname, prop in properties.items():
        invalid = set(prop) - set(SCHEMA_KEYWORDS)
        if invalid:   # 超子集 fail-fast（声明块与覆写补丁共用检查点）
            raise FormatError(f"parameter {pname!r} contains keywords outside the bridge subset: {sorted(invalid)}")
        raw_type = prop.get("type")
        if raw_type is None:
            py_type: Any = Any   # {} 空 property → Any（匹配一切）
        elif isinstance(raw_type, str) and ("|" in raw_type or "[" in raw_type):
            py_type = _fragment_py_type(_parse_type_expr(raw_type))   # 复合类型表达式展开
        else:
            # 裸名（含 [T, "null"] 可空列表形态）：先别名归一（type: str ≡ type: string）
            fragment = {"type": raw_type} if isinstance(raw_type, list) else {
                "type": TYPE_ALIASES.get(str(raw_type), raw_type)
            }
            py_type = _fragment_py_type(fragment)
        if "enum" in prop:
            py_type = Literal[tuple(prop["enum"])]   # enum → Literal（校验交 Pydantic）
        constraints: dict[str, Any] = {dst: prop[src] for src, dst in _FIELD_KW.items() if src in prop}
        if "default" in prop:
            fields[pname] = (py_type, Field(default=prop["default"], **constraints))
        elif constraints:
            fields[pname] = (py_type, Field(**constraints))   # 必填 + 约束
        else:
            fields[pname] = (py_type, ...)   # 无 default → 必填
    Model: type[BaseModel] = create_model(name, **fields)
    return Model


# ---------------------------------------------------------------------------
# 稀疏覆写（Tool / 子 Agent 绑定层共用）
# ---------------------------------------------------------------------------

def apply_param_overrides(
    params_schema: Mapping[str, dict[str, Any]],
    overrides: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """对 JSON Schema properties 应用稀疏补丁，产出新 dict（输入不变）。

    .. rubric:: 功能介绍

    绑定层（``ToolEntry`` / ``SubagentEntry``）覆写参数声明的唯一应用
    点：``override_params`` 是基底 schema 的稀疏镜像——只写要改的参数、
    只写要改的关键字，未提及的从基底深合并回填。``ToolDefinition`` 的
    LLM 视图生成与子 Agent catalog 的 ``<params>`` 生成共用本函数。

    :param params_schema: 基底 ``{参数名: JSON Schema property dict}``。
    :param overrides: 补丁 ``{参数名: {JSON Schema 关键字: 值}}``。
    :return: 合并后的新 dict；基底及其 property dict 不被修改。

    :raises flowing.errors.FormatError: 补丁含超出
        :data:`SCHEMA_KEYWORDS` 子集的关键字时（fail-fast）。

    .. rubric:: 行为要点

    - 零语法糖：补丁子字段一律是 JSON Schema 关键字，类型必须写
      ``type:``；覆写端不做裸类型字符串 / 字面量推断——裸值在覆写语境
      由解析层归入 specified（指定值），到不了本函数。注意陷阱：覆写端
      写 ``id: str`` 不会展开为类型声明，而是得到指定值字符串 ``"str"``
      ——想改类型必须写 ``id: {type: string}``。
    - 稀疏回填：未出现的参数、未出现的关键字保持基底原值。
    - 必填性：补丁没写 ``default`` → 沿用基底（含基底无 ``default`` 的
      必填性）；显式给了 ``default`` → 该参数变可选；不支持删除已有
      ``default`` （稀疏补丁无删除语义）。
    - 未知参数名：``overrides`` 中出现 ``params_schema`` 不存在的键 →
      视为新增参数（补丁 property 直接并入）——Tool 侧 ``FinishTool``
      动态 schema 依赖此语义。

    .. rubric:: 使用示例

    .. code-block:: python

        new = apply_param_overrides(
            {"amount": {"type": "number", "minimum": 0.01}},
            {"amount": {"description": "支付金额（元），最低 1 元"}},   # 只改 description
        )
        # new["amount"] == {"type": "number", "minimum": 0.01,
        #                    "description": "支付金额（元），最低 1 元"}

    .. seealso:: :func:`schema_to_model` —— 补丁后最终 schema 的建模
        桥接；:class:`flowing.tool.ToolDefinition` —— Tool 侧封装（另含
        别名 / 描述 / specified 参数隐藏）。
    """
    result: dict[str, dict[str, Any]] = {k: copy.deepcopy(v) for k, v in params_schema.items()}
    for key, patch in overrides.items():
        invalid = set(patch) - set(SCHEMA_KEYWORDS)
        if invalid:   # 非法关键字 fail-fast（覆写端零糖：只认 JSON Schema 关键字）
            raise FormatError(f"override for parameter {key!r} contains invalid keywords: {sorted(invalid)}")
        if key in result:   # 稀疏合并：未出现关键字从基底回填
            merged: dict[str, Any] = dict(result[key])
            merged.update(patch)
            result[key] = merged
        else:   # 未知参数名 -> 新增参数（FinishTool 动态 schema 依赖）
            result[key] = dict(patch)
    return result


def _coerce(value: Any, prop_schema: Mapping[str, Any]) -> Any:
    """按目标 property schema 做兼容类型转换。内部 API，不属稳定契约。

    ``ToolEntry.resolve()`` 聚合 ``specified`` 值（Parsable 求值结果）时
    调用：模板 / 表达式求值出的值类型常与声明不一致（如 ``"42"`` vs
    ``number``），本函数按目标 ``type`` 做一次预转换；无法兼容转换时
    保留原始值，交由后续模型校验报告具体错误——失败不报错。

    纯函数；时序：``specified`` 惰性求值之后、模型校验之前
    （``ToolEntry.resolve`` 第 2 步）。

    .. seealso:: ``flowing.tool.ToolEntry.resolve``
    """
    target: Any = prop_schema.get("type")
    try:  # 兼容则转；否则保留原值（失败不报错）
        if target == "string":
            result: Any = str(value)
        elif target == "integer":
            result = int(value)
        elif target == "number":
            result = float(value)
        elif target == "boolean":
            result = bool(value)
        else:
            result = value  # array/object/可空/无 type 时不做预转换
    except (TypeError, ValueError):
        result = value  # 保留原值，交后续 Pydantic 校验报错
    return result


# ---------------------------------------------------------------------------
# 类型安全键
# ---------------------------------------------------------------------------

class InjectionKey(Generic[T]):
    """provide/inject 的类型安全注入键——仅在调用侧提供编译期类型标注。

    .. rubric:: 功能介绍

    ``InjectionKey[T]`` 是一个带泛型参数的具名键：``name`` 是运行期唯一
    实质内容，``T`` 是给类型检查器与读者的「该键应注入什么类型的值」
    标注。与裸字符串 key 共存：需要类型安全（共享的 ``keys.py``、框架
    插件内部的键定义如 ``communication_key``）用 ``InjectionKey``；
    简单一次性场景用字符串简写（``inject("locale")``，类型为 ``Any``）。
    字符串 key 容易拼错、重构不可追踪；泛型键让 ``provide`` / ``inject``
    两侧的值类型在静态检查下对齐。类型信息不跨节点传递：注入存储的 key
    恒为 ``str``，``inject_from()`` 内部退化为按 ``name`` 匹配，框架
    运行期不做任何类型校验。

    .. rubric:: 使用示例

    .. code-block:: python

        # keys.py —— 共享键定义
        from flowing import InjectionKey

        locale_key:   InjectionKey[str] = InjectionKey("locale")
        trace_id_key: InjectionKey[str] = InjectionKey("trace_id")
        user_id_key:  InjectionKey[int] = InjectionKey("user_id")

        # 提供方（Agent.setup() / Composable 中）
        self.provide(locale_key, "zh")

        # 消费方（类型推断为 str）
        locale: str = self.inject(locale_key)

        # 字符串简写（与 InjectionKey("locale") 指向同一槽位）
        self.provide("locale", "zh")
        locale = self.inject("locale")     # Any，无类型推断

    .. rubric:: 行为要点

    - ``name`` 创建后不可变；同一 ``name`` 的 ``InjectionKey`` 与裸
      字符串指向注入存储中的同一槽位（同哈希、可互作 dict 键）。
    - ``__eq__``：与另一个 ``InjectionKey`` 按 ``name`` 比较，或与裸
      ``str`` 直接比较；``__hash__`` 等于 ``hash(self.name)``。
    - ``__str__`` 返回 ``name``；``__repr__`` 返回 ``InjectionKey(...)``
      调试形态。
    - 运行期不做 ``T`` 的类型校验：亲节点 provide 了 ``int``、子按
      ``InjectionKey[str]`` inject 不会被框架拦截（类型契约是静态的）。
      两个泛型参数不同但 ``name`` 相同的键（``InjectionKey[int]("x")``
      与 ``InjectionKey[str]("x")``）在运行期相等且同槽——避免在同一
      作用域混用同名异型键，框架不检测这类静态错误。
    - 同 key 重复 ``provide`` 是覆盖更新（后者生效）——冲突处置属
      ``provide`` 侧规则，与键类本身无关。

    .. seealso:: :class:`ConfigKey` —— 镜像同模式的配置键；
        :func:`flowing.runtime.inject_from` —— 沿 provide 链上溯、按
        ``name`` 匹配的执行点；:class:`flowing.provide.ProvideNode` ——
        注入存储的 key 恒为 ``str`` 的节点协议。
    """

    name: str
    """键名——运行期唯一实质内容，也是注入存储中的实际 key。创建后不可
    变；与同名裸字符串等价（同槽位、同哈希、相等比较一致）。
    """

    def __init__(self, name: str) -> None:
        """以键名构造 ``InjectionKey``。

        :param name: 键名（字符串）。

        .. rubric:: 行为要点

        - 构造是同步、无副作用的纯赋值；泛型参数 ``T`` 只出现在类型
          标注位置（``InjectionKey[str] = InjectionKey("locale")``），
          不参与构造。
        - ``name`` 应为非空字符串；空名的行为不作契约保证。
        - 同名键多次构造产生不同对象但相等（``__eq__`` 按 ``name``），
          可安全在多处声明同一键；推荐仍是共享 ``keys.py`` 单点定义，
          便于重构追踪。

        .. seealso:: :attr:`name`
        """
        self.name = name

    def __hash__(self) -> int:
        """返回 ``hash(self.name)``——与同名裸字符串 key 同哈希。

        :return: ``hash(self.name)``。

        .. rubric:: 行为要点

        因此键可直接用于 dict 查找，并与字符串 key 混用（同槽位）。
        纯函数、无副作用。

        .. seealso:: :meth:`__eq__`、:attr:`name`
        """
        result: int = hash(self.name)
        return result

    def __eq__(self, other: object) -> bool:
        """按 ``name`` 判等：可与另一个 ``InjectionKey`` 或裸 ``str`` 比较。

        :param other: 要比较的对象。
        :return: ``other`` 为 ``InjectionKey`` 时比较两侧 ``name``
            （忽略泛型参数）；为 ``str`` 时比较 ``self.name == other``；
            为其它类型时返回 ``NotImplemented`` （Python 按默认规则
            处理，通常结果为 ``False``）。

        .. rubric:: 行为要点

        与字符串 key 的判等走同一条路径：键可直接用于以字符串为键的
        字典查找，与同名裸字符串指向同一槽位。运行期无法取得泛型参数
        ``T``，因此不参与比较。

        .. seealso:: :meth:`__hash__`
        """
        if isinstance(other, InjectionKey):
            result: bool = self.name == other.name  # 忽略两侧泛型参数
        elif isinstance(other, str):
            result = self.name == other
        else:
            return NotImplemented  # 其余类型按 Python 惯例
        return result

    def __str__(self) -> str:
        """返回键名本身（``self.name``）。

        :return: ``self.name``。

        .. rubric:: 行为要点

        键对象经 ``str()`` 归一后落键名，与「键与同名裸字符串同槽位」
        契约一致；调试形态由 :meth:`__repr__` 承担。纯函数、无副作用。

        .. seealso:: :attr:`name`、:meth:`__repr__`
        """
        return self.name

    def __repr__(self) -> str:
        """返回 ``InjectionKey('locale')`` 形式的调试表示。

        :return: ``InjectionKey(<name 的 repr>)``。

        .. rubric:: 行为要点

        展示 ``name``，不展示泛型参数（运行期不可得）；无副作用。

        .. seealso:: :attr:`name`
        """
        result: str = f"InjectionKey({self.name!r})"
        return result

class ConfigKey(Generic[T]):
    """配置读取的类型安全键——镜像 :class:`InjectionKey` 模式。

    .. rubric:: 功能介绍

    ``ConfigKey[T]`` 是 :meth:`flowing.runtime.Runtime.get_config` 的键
    类型：泛型参数 ``T`` 声明该配置值的 Python 类型，``name`` 是配置项
    的键名（约定为含命名空间的点分路径，如 ``"retry.max_attempts"`` /
    ``"i18n.locale"``）。``get_config(key, default=...)`` 的 ``default``
    参数类型须与 ``T`` 一致，否则静态检查报错——这把「键名 → 值类型」
    的对应关系固化为可静态检查的契约。与裸字符串 key 共存：字符串简写
    返回值类型为 ``Any``。与 ``InjectionKey`` 同模式——类型信息只在
    调用侧生效，配置存储层按字符串键存取，不感知泛型。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing import ConfigKey

        max_attempts_key: ConfigKey[int] = ConfigKey("retry.max_attempts")
        locale_key: ConfigKey[str] = ConfigKey("i18n.locale")

        # setup() 中读取（配置就绪后任何时机可调用）
        def setup(self):
            n = self.runtime.get_config(max_attempts_key, default=3)
            loc = self.runtime.get_config(locale_key, default="zh")

        # 字符串简写（同一配置项，无类型推断）
        n = self.runtime.get_config("retry.max_attempts", 3)

    .. rubric:: 行为要点

    - ``name`` 创建后不可变；命名空间段（``retry``、``i18n``）是约定
      而非本类强制——命名空间的注册与「谁负责校验」由
      :meth:`flowing.runtime.Runtime.register_config_namespace` 管理；
      ``get_config`` 不做命名空间访问控制，任何代码可读取任何命名空间
      下的值。
    - ``__eq__`` / ``__hash__`` 语义与 :class:`InjectionKey` 相同：按
      ``name`` 与另一个 ``ConfigKey`` 或裸 ``str`` 比较，哈希等于
      ``hash(self.name)``；``__str__`` 同样返回 ``name``。
    - 运行期不做 ``T`` 的类型校验：配置文件里实际值类型与 ``T`` 不符
      时框架不拦截（静态契约，非运行期校验器）。
    - 调用时机约束（消费侧规则）：``get_config`` 在配置就绪前调用抛
      :class:`flowing.errors.ConfigNotReadyError` （典型即模块顶层 import
      期）；就绪后任何时机可调用（``setup()``、钩子回调、工具 callable、
      ``main()`` 后续代码）。本类本身无此约束——约束在读取配置的
      Runtime 方法上。
    - 同名异型键（``ConfigKey[int]("x")`` vs ``ConfigKey[str]("x")``）
      运行期相等——静态错误，框架不检测。

    .. seealso:: :class:`InjectionKey` —— 同模式的注入键；
        :meth:`flowing.runtime.Runtime.get_config` —— 消费入口与调用
        时机约束；:meth:`flowing.runtime.Runtime.register_config_namespace`
        —— 命名空间注册。
    """

    name: str
    """配置键名（约定为含命名空间的点分路径）；创建后不可变；与同名裸
    字符串等价（同哈希、相等比较一致）。
    """

    def __init__(self, name: str) -> None:
        """以键名构造 ``ConfigKey``。

        :param name: 键名（字符串，约定为含命名空间的点分路径）。

        .. rubric:: 行为要点

        构造是同步、无副作用的纯赋值；泛型参数 ``T`` 只出现在类型标注
        位置，不参与构造。语义与 :meth:`InjectionKey.__init__` 一致。
        ``name`` 应为非空字符串。

        .. seealso:: :attr:`name`、:meth:`InjectionKey.__init__`
        """
        self.name = name

    def __hash__(self) -> int:
        """返回 ``hash(self.name)``——与同名裸字符串 key 同哈希。

        :return: ``hash(self.name)``。

        .. rubric:: 行为要点

        纯函数、无副作用；与字符串 key 混用作 dict 键时同槽位。

        .. seealso:: :meth:`__eq__`、:attr:`name`
        """
        result: int = hash(self.name)
        return result

    def __eq__(self, other: object) -> bool:
        """按 ``name`` 判等：可与另一个 ``ConfigKey`` 或裸 ``str`` 比较。

        :param other: 要比较的对象。
        :return: ``other`` 为 ``ConfigKey`` 时比较两侧 ``name``
            （忽略泛型参数）；为 ``str`` 时比较 ``self.name == other``；
            为其它类型时返回 ``NotImplemented`` （Python 按默认规则
            处理，通常结果为 ``False``）。

        .. rubric:: 行为要点

        运行期无法取得泛型参数 ``T``，因此不参与比较。

        .. seealso:: :meth:`InjectionKey.__eq__` —— 同语义原型；
            :meth:`__hash__`
        """
        if isinstance(other, ConfigKey):
            result: bool = self.name == other.name  # 忽略两侧泛型参数
        elif isinstance(other, str):
            result = self.name == other
        else:
            return NotImplemented  # 其余类型按 Python 惯例
        return result

    def __str__(self) -> str:
        """返回键名本身（``self.name``）。

        :return: ``self.name``。

        .. rubric:: 行为要点

        与 :meth:`InjectionKey.__str__` 同语义：键对象经 ``str()`` 归一
        后落键名，与「与同名裸字符串等价」契约一致；调试形态由
        :meth:`__repr__` 承担。纯函数、无副作用。

        .. seealso:: :attr:`name`、:meth:`__repr__`、:meth:`InjectionKey.__str__`
        """
        return self.name

    def __repr__(self) -> str:
        """返回 ``ConfigKey('retry.max_attempts')`` 形式的调试表示。

        :return: ``ConfigKey(<name 的 repr>)``。

        .. rubric:: 行为要点

        展示 ``name``，不展示泛型参数（运行期不可得）；无副作用。

        .. seealso:: :attr:`name`
        """
        result: str = f"ConfigKey({self.name!r})"
        return result
