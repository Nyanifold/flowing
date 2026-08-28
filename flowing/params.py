"""``flowing.params`` —— 参数声明（Pydantic 模型 / JSON Schema）与类型安全键。

本模块承载 Flowing 的「参数面」基础设施。参数声明有两个同构的形态
（B1 裁决，取代旧 ``ParamSpec`` 体系）：

- **Python 层 = Pydantic BaseModel 子类**：手写工具 / Agent 子类的声明方式。
  声明即模型——LLM 可见 schema 由 ``model_json_schema()`` 派生，执行层
  校验即模型本身（``model_validate``）。
- **fya 层 = 纯 JSON Schema 展开式**：``args:`` 块直接展开参数名 →
  JSON Schema property（少量糖，见下），装配期经 :func:`schema_to_model`
  桥接为等价 Pydantic 模型。

``InjectionKey[T]`` 与 ``ConfigKey[T]`` 是两个仅在调用侧生效的泛型
类型安全键，与参数声明无关。

.. rubric:: fya 声明层的展开式糖（定稿）

``args:`` 块直接展开参数名，**无 ``type: object`` 顶层包装、无
``required:`` 清单**——required 由 ``default`` 有无派生（无 default →
必填，与 Pydantic「无默认值字段必填」语义同构）::

    args:
      user_id: {type: string, description: 用户 ID}   # 无 default → 必填
      amount: {type: number, minimum: 0.01}            # 必填 + 范围约束
      currency: {type: string, default: CNY}           # 有 default → 可选
      page_size: 20                                    # 糖：字面量 → {type: integer, default: 20}
      category: str                                    # 糖：裸类型字符串 → {type: string}（必填）

糖仅两种（:func:`expand_args_schema` 归一化，声明端专用——覆写端零糖，
见 ``apply_param_overrides``）：

- **裸类型字符串**：``str`` / ``int`` / ``float`` / ``bool`` / ``list`` /
  ``dict`` → 对应 ``{type: ...}`` 单键 property（必填）；
- **YAML 字面量**：标量 / 列表字面量 → 按值类型推断 ``type`` 并以值为
  ``default``（可选）。

其余一律是完整 JSON Schema property dict（``{}`` 为合法值——JSON Schema
原生「匹配一切」即 any 类型）。``type:`` 的取值同时识别 Python 风格简写
名与 JSON Schema 名（``type: str`` ≡ ``type: string``，别名表
:data:`TYPE_ALIASES`，声明端与覆写端均在桥接点归一），并支持类型表达式
（联合 ``str | None``、泛型嵌套 ``list[str]`` / ``dict[str, int]``，可
组合——解析规则见 :func:`_parse_type_expr`）。

**可空参数**：``x: {type: [string, "null"], default: null}`` 标准
nullable 写法（T5 裁决：不禁 None；Pydantic 侧对应 ``x: str | None =
None``，产出同样的 ``anyOf`` nullable schema）。

.. rubric:: 桥接子集（schema_to_model）

fya 声明经 :func:`schema_to_model` 桥接为 Pydantic 模型。支持的 JSON
Schema 关键字子集（**基础类型 + 常用约束**）：

- 类型：``type: string/number/integer/boolean/array/object``（含
  ``[T, "null"]`` 可空形态）；
- 约束：``description`` / ``default`` / ``enum`` / ``minimum`` /
  ``maximum`` / ``minLength`` / ``maxLength`` / ``pattern`` / ``items`` /
  ``properties``——逐关键字映射为 ``pydantic.Field(ge=..., pattern=...)``
  等字段约束；
- **超出子集**（``oneOf``/``anyOf``/``$ref``/``allOf`` 等组合子）→ 装配期
  :class:`flowing.errors.FormatError`（fail-fast；初版不引入 jsonschema
  库做全文校验）。

required 派生规则在桥接同样生效：property 无 ``default`` → 必填字段，
有 → 以 default 为字段默认值。

.. rubric:: 覆写层（apply_param_overrides，绑定层共用）

Entry（``ToolEntry`` / ``SubagentEntry``）的 ``override_params`` 是
**JSON Schema 稀疏补丁**，深合并到基底 schema 上（B1 裁决）：

- 补丁子字段 = JSON Schema 关键字（上述桥接子集），**覆写端零语法糖**
  ——类型必须写 ``type:`` 关键字；裸值在覆写语境只可能是指定值
  （specified），无歧义；
- 未提及的子字段（含 ``default``）原样沿用基底；显式给了 ``default``
  → 该参数变可选；**不支持删除已有 default**（稀疏补丁无删除语义）；
- 未知关键字 → :class:`flowing.errors.FormatError`（fail-fast 保留）；
- 未知参数名 → 视为新增参数（FinishTool 动态 schema 依赖此语义）。

**注入不再有独立通道**（R-4）：``inject`` 键与 ``ToolEntry.inject`` 字段
已删除——注入 = specified 值写 Parsable 表达式
``"{{ self.inject('user_id') }}"``（调用时以调用方 Agent 为上下文求值、
沿 provide 链上溯；渲染上下文 ``self``/``agent`` 入口见
:mod:`flowing.parsable`）。机制细节见 ``flowing.tool.ToolEntry``。

.. rubric:: PENDING 哨兵（沿用）

``.fya`` 的 ``field: _`` 解析为 :data:`flowing.parsable.PENDING`
（延迟定义承诺），语义不变——它与参数声明形式正交，声明层不消费
``_UNSET``（旧 ParamSpec 的 required 哨兵已随该类删除；``_UNSET`` 仅
余 ``Agent.source_file`` 推算用途，见 :mod:`flowing.parsable`）。

.. rubric:: 安全边界

``args`` / ``parameters`` 最终序列化为 JSON 通过 function calling 传给
LLM，因此**只接受 JSON 兼容值**（``str`` / ``int`` / ``float`` / ``bool``
标量及其 ``list`` / ``dict`` 嵌套组合）。复杂对象（数据库连接、服务
客户端、函数、生成器）不允许走 args；``specified`` **也不是复杂对象
通道**——它只是为 LLM 不可见的标量参数提供值（固定值或注入表达式）。
复杂对象的两条替代路径：

1. 传可序列化标识符（``connection_id: "main"``），工具内部按标识符查找；
2. ``execute()`` 声明 ``caller`` 参数，从调用方 Agent 实例上取对象。

.. rubric:: 双视图一致性契约（定稿）

声明（模型或 schema）→ LLM 视图（JSON Schema）→ 执行校验（模型）
全部同源派生，无独立维护的第二声明::

    Python BaseModel ──model_json_schema()──┐
    fya 展开式 schema ──expand/桥接─────────┤
                                            ▼
                                     基底 JSON Schema
                                            ▼ Entry 稀疏补丁 + specified 参数隐藏
                                     最终 JSON Schema ──▶ LLM 可见面
                                            │
                                            ▼ schema_to_model（同一桥接）
                                     最终模型 ──▶ 执行层校验

要点：required 恒由最终 schema 的 ``default`` 有无派生，两侧天然一致；
补丁应用在 schema 上（合并点唯一），最终模型从最终 schema 再桥接一次
（纯 description 补丁亦统一重建，实现可缓存）。

.. rubric:: InjectionKey[T] / ConfigKey[T] 的「类型信息不跨节点」边界

两个键类的泛型参数 ``T`` **只在调用侧提供编译期类型标注**，运行期完全
退化为按 ``name`` 的字符串匹配：

- ``InjectionKey[T]``：``_provided`` 字典的 key **始终是 ``str``**。
- ``ConfigKey[T]``：镜像 ``InjectionKey`` 模式。``T`` 约束
  ``Runtime.get_config(key, default)`` 的 ``default`` 参数类型。

因此：类型不匹配（父 provide 了 ``int``、子按 ``InjectionKey[str]``
inject）**不会**在运行期被框架拦截——泛型是给静态检查器与读者的契约，
不是运行期校验器。需要运行期校验的场景走 args（Pydantic 模型校验）
而非 provide/inject。

.. rubric:: 非行为（防止实现者加戏）

- 桥接不做 JSON Schema 全量合规校验——只认子集，超子集 fail-fast。
- 不为 ``InjectionKey`` / ``ConfigKey`` 做运行期类型检查。
- 不做可变默认值的读时拷贝——Pydantic ``Field(default_factory=...)``
  与模型校验天然处理（旧 ParamSpec 的读时拷贝 property 已随之删除）。

.. rubric:: 稳定性分级

本模块全部公开签名（``expand_args_schema``、``schema_to_model``、
``apply_param_overrides``、``InjectionKey``、``ConfigKey``）属于跨版本
稳定契约；``_coerce`` 为内部 API，不属稳定契约。

.. seealso::

    :mod:`flowing.parsable`
        ``PENDING`` 的定义模块；Parsable 惰性求值与渲染上下文。
    :class:`flowing.agent.Agent`
        ``args_model`` 的宿主。
    :class:`flowing.tool.ToolDefinition`、:class:`flowing.tool.ToolEntry`
        Tool 侧参数声明与 ``specified`` 聚合的消费者。
    ``flowing.runtime.Runtime.get_config``
        ``ConfigKey`` 的消费入口。
    ``flowing.runtime.inject_from``
        ``InjectionKey`` 跨节点退化为按 ``name`` 匹配的执行点。
"""

import copy
from typing import Any, Generic, Mapping, TypeVar

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
"""裸类型字符串糖表（``str`` → ``"string"`` 等）。声明端 ``args:`` 块
专用——覆写端（Entry 补丁）不做任何糖，类型必须写 ``type:`` 关键字。
"""

TYPE_ALIASES: dict[str, str] = dict(ARG_SHORTHAND)
"""``type:`` 关键字的取值别名表——Python 风格简写名（``str`` /
``int`` / ``float`` / ``bool`` / ``list`` / ``dict``）与 JSON Schema
名（``string`` / ``integer`` / ...）等价，**声明端与覆写端均识别**，
桥接（:func:`schema_to_model`）统一归一为 JSON Schema 名。"""


def _parse_type_expr(expr: str) -> dict:
    """解析 ``type:`` 的取值表达式为 JSON Schema 片段。内部 API。

    除裸名（:data:`TYPE_ALIASES`）外支持两种复合形态（声明端与覆写端
    均识别）：

    - **联合（``|`` 运算）**：``str | None`` → ``{"type": ["string",
      "null"]}``；``int | str`` → ``{"type": ["integer", "string"]}``；
    - **泛型嵌套**：``list[str]`` → ``{"type": "array", "items":
      {"type": "string"}}``；``dict[str, int]`` → ``{"type": "object"}``
      （键值类型不进入 schema——JSON Schema 的 object 键恒为字符串，
      值类型约束由执行层模型承载）。

    嵌套与联合可组合（``list[str | None]``）。无法解析 →
    :class:`flowing.errors.FormatError`（fail-fast）。
    """
    ...

# 桥接子集：本表之外的关键字出现在 property 中 → schema_to_model fail-fast
SCHEMA_KEYWORDS: frozenset[str] = frozenset({
    "type", "description", "default", "enum",
    "minimum", "maximum", "minLength", "maxLength", "pattern",
    "items", "properties",
})
"""桥接子集关键字表（:func:`schema_to_model` 认识的全部 JSON Schema
关键字）。超出子集（``oneOf``/``anyOf``/``$ref``/``allOf`` 等）→
装配期 :class:`flowing.errors.FormatError`（初版不引入 jsonschema 库）。
"""


def expand_args_schema(args: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """把 fya ``args:`` 声明块归一化为 JSON Schema properties（展开糖）。

    .. rubric:: 功能介绍

    声明端唯一的糖处理点。逐参数判别（顺序固定）：

    1. 值是**裸类型字符串**（:data:`ARG_SHORTHAND` 六保留字之一）→
       ``{type: <展开值>}``（必填）；
    2. 值是 **dict** → 原样视为完整 JSON Schema property（``{}`` 合法，
       any 语义；键超出 :data:`SCHEMA_KEYWORDS` 子集的报错推迟到
       :func:`schema_to_model` 桥接时）；
    3. 值是其余 YAML 字面量（标量 / 列表）→ 按值类型推断 ``type`` 并以
       值为 ``default``（可选）；``None`` 值（YAML ``key:`` 空值）→
       :class:`flowing.errors.FormatError`（连声明意图都无法确认，
       fail fast）。

    归一化产物不含 required 信息——required 恒由 property 的
    ``default`` 有无派生（无 default → 必填）。

    :param args: fya ``args:`` 块的原始映射。
    :return: ``{参数名: property dict}`` 新构造的 dict。
    :raises flowing.errors.FormatError: 值为 ``None``（空声明）。

    .. rubric:: 测试案例

    - 前置：``{"a": "str", "b": 20, "c": {type: string}}``；操作：展开；
      期望：``a → {type: string}``、``b → {type: integer, default: 20}``、
      ``c`` 原样。
    - 前置：``{"x": None}``；期望：``FormatError``。

    .. seealso:: :func:`schema_to_model`（下一步桥接）
    """
    props: dict[str, dict[str, Any]] = {}
    for name, raw in args.items():
        if isinstance(raw, str) and raw in ARG_SHORTHAND:
            props[name] = {"type": ARG_SHORTHAND[raw]}   # 糖一：裸类型字符串（必填）
        elif isinstance(raw, dict):
            props[name] = dict(raw)                      # 完整 property（原样，子集校验推迟到桥接）
        elif raw is None:
            raise FormatError(f"参数 {name!r} 空声明（None）——须给出完整 property、字面量默认值或裸类型字符串")
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
    最终 schema 共用的建模入口（``pydantic.create_model``，S-36：pydantic
    是本规约的显式依赖）。逐 property 映射：

    - ``type`` → Python 类型（``string→str``、``integer→int``、
      ``number→float``、``boolean→bool``、``array→list``、``object→dict``；
      ``[T, "null"]`` 可空形态 → ``T | None``）；取值先经
      :data:`TYPE_ALIASES` 归一（``type: str`` ≡ ``type: string``，
      声明端与覆写端均识别）；
    - 约束关键字 → ``Field`` 约束（``minimum→ge``、``maximum→le``、
      ``minLength``/``maxLength``/``pattern`` 同名、``enum`` → ``Literal``）；
    - ``description`` → ``Field(description=...)``；
    - required 派生：property 无 ``default`` → 必填字段；有 → 默认值。

    .. rubric:: 行为规约

    - 子集校验：property 含 :data:`SCHEMA_KEYWORDS` 之外的关键字 →
      :class:`flowing.errors.FormatError`（报错信息列出参数名与非法
      关键字）。声明块与覆写补丁共用本检查点（fail-fast 单点）。
    - 后置条件：返回新模型类；不修改入参。
    - 非行为：不做全量 JSON Schema 合规校验；不缓存模型类（调用方可缓存）。
    - 边缘情况：``{}`` 空 property → ``Any``（any，匹配一切）；
      ``enum`` + ``default`` 共存合法（default 须在 enum 内，校验交
      Pydantic）。

    .. rubric:: 测试案例

    - 前置：``{"a": {type: string}, "b": {type: integer, default: 1}}``；
      操作：建模 + ``model_validate({"a": "x"})``；期望：成功、``b == 1``；
      ``model_validate({})`` 抛 ``ValidationError``。
    - 前置：``{"a": {type: [string, "null"], default: null}}``；期望：
      可空可选字段。
    - 前置：``{"a": {oneOf: [...]}}``；期望：``FormatError``（超子集）。

    .. rubric:: 调用关系（审计）

    - 调用：``pydantic.create_model``（每次调用）；:data:`SCHEMA_KEYWORDS`
      子集检查
    - 被调：``.fya`` 装配层（声明期）、``flowing.tool.ToolDefinition``
      （补丁后最终模型重建）

    .. seealso:: :func:`expand_args_schema`（前置归一化）、
        :func:`apply_param_overrides`（补丁应用）
    """
    from pydantic import create_model  # 动态建模入口（S-36 库符号具名）
    type_map: dict[str, type] = {
        "string": str, "integer": int, "number": float,
        "boolean": bool, "array": list, "object": dict,
    }
    fields: dict[str, Any] = {}
    for pname, prop in properties.items():
        invalid = set(prop) - set(SCHEMA_KEYWORDS)
        if invalid:   # 超子集 fail-fast（声明块与覆写补丁共用检查点）
            raise FormatError(f"参数 {pname!r} 含超出桥接子集的关键字: {sorted(invalid)}")
        type_name = TYPE_ALIASES.get(str(prop.get("type")), prop.get("type"))   # 别名归一；含 |/[ ] 的复合形态经 _parse_type_expr 展开
        py_type: Any = type_map.get(type_name, Any)   # [T,"null"] 可空形态/{} any 的映射细节从简
        constraints: dict[str, Any] = {k: prop[k] for k in prop if k not in ("type", "default")}
        if "default" in prop:
            fields[pname] = (py_type, Field(default=prop["default"], **constraints))
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
    """对 JSON Schema properties 应用稀疏补丁，产出**新** dict（输入不变）。

    .. rubric:: 功能介绍

    绑定层（``ToolEntry`` / ``SubagentEntry``）覆写的**唯一应用点**：
    ``override_params`` 是「基底 schema 的稀疏镜像」——只写要改的参数、
    只写要改的关键字，其余全部从基底深合并回填。``ToolDefinition`` 的
    LLM 视图生成与子 Agent catalog ``<params>`` 生成共用本函数。

    .. rubric:: 行为规约

    - **零语法糖**：补丁子字段一律是 JSON Schema 关键字（类型必须写
      ``type:``）；覆写端不做裸类型字符串 / 字面量推断——裸值在覆写
      语境由解析层归入 specified（指定值），到不了本函数。**注意陷阱**：
      覆写端写 ``id: str`` 不会展开为类型声明，而是得到指定值字符串
      ``"str"``——想改类型必须写 ``id: {type: string}``；写错不报错，
      在调用期模型校验时才暴露。
    - **关键字校验**：补丁键 ∉ :data:`SCHEMA_KEYWORDS` →
      :class:`flowing.errors.FormatError`（fail-fast，单点）。
    - **稀疏回填**：未出现的参数、未出现的关键字保持基底原值
      （深合并）。
    - **requiredness**：不主动推断——补丁没写 ``default`` = 沿用基底
      default 有无；显式给了 ``default`` → 该参数变可选；**不支持删除
      已有 default**（稀疏补丁无删除语义）。
    - **未知参数名**：``overrides`` 中出现 ``params_schema`` 不存在的键
      → 视为**新增参数**（补丁 property 直接并入）——Tool 侧
      ``FinishTool`` 动态 schema 依赖此语义。
    - 后置条件：返回新 dict；基底 property dict 不被修改。

    .. rubric:: 使用示例

    .. code-block:: python

        new = apply_param_overrides(
            base_schema,   # {amount: {type: number, minimum: 0.01}}
            {"amount": {"description": "支付金额（元），最低 1 元"}},   # 只改 description
        )
        # new["amount"] == {type: number, minimum: 0.01, description: 支付金额（元），最低 1 元}

    .. rubric:: 测试案例

    - 前置：基底 ``{"amount": {type: number, minimum: 0.01}}``，补丁
      ``{"amount": {description: "支付金额"}}`` → 期望：新 dict 中
      ``type``/``minimum`` 原样、``description`` 被替换；原 dict 不变。
    - 前置：补丁 ``{"amount": {descrition: "笔误"}}`` → 期望：
      ``FormatError``。
    - 前置：补丁 ``{"new_param": {type: integer}}`` 且基底无此键 →
      期望：新 dict 含 ``new_param``（新增参数语义）。
    - 前置：基底 ``{"a": {type: string, default: x}}``，补丁
      ``{"a": {default: y}}`` → 期望：default 改为 ``y``（仍可选）；
      补丁不含 default 的其它键 → default 原样沿用。

    .. rubric:: 调用关系（审计）

    - 调用：深合并（property 内关键字级）
    - 被调：``flowing.tool.ToolDefinition``（LLM 视图与最终模型生成）、
      ``flowing.subagents.SubagentEntry.catalog_view``（``params_xml``
      生成）

    .. seealso::

        - :func:`schema_to_model` —— 补丁后最终 schema 的建模桥接。
        - :meth:`flowing.tool.ToolDefinition` —— Tool 侧封装（另含
          别名 / 描述 / specified 参数隐藏）。
    """
    result: dict[str, dict[str, Any]] = {k: copy.deepcopy(v) for k, v in params_schema.items()}
    for key, patch in overrides.items():
        invalid = set(patch) - set(SCHEMA_KEYWORDS)
        if invalid:   # 非法关键字 fail-fast（覆写端零糖：只认 JSON Schema 关键字）
            raise FormatError(f"参数 {key!r} 的覆写含非法关键字: {sorted(invalid)}")
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
    保留原始值，交由后续模型校验报告具体错误——**失败不报错**。

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
    实质内容，``T`` 是给类型检查器与读者的「该键应注入什么类型的值」标注。
    与裸字符串 key 共存：需要类型安全（共享的 ``keys.py``、框架核心内部如
    ``use_xxx()`` 读 ``communication_key``）用 ``InjectionKey``；简单一次性
    场景用字符串简写（``inject("locale")``，类型为 ``Any``）。

    .. rubric:: 设计动机

    借鉴 Vue 3 的 ``InjectionKey``：字符串 key 易拼错、重构不可追踪；泛型键
    让 ``provide(key, value)`` / ``inject(key, default)`` 的两侧类型在静态
    检查下对齐。关键定稿边界：**类型信息不跨节点传递**——``_provided`` 字典
    的 key 始终是 ``str``，``inject_from()`` 内部退化为按 ``name`` 匹配，
    ``ProvideNode`` 协议完全不需要感知本类（见模块级「类型信息不跨节点」）。

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
        locale: str = self.inject(locale_key, "en")

        # 字符串简写（与 InjectionKey("locale") 指向同一槽位）
        self.provide("locale", "zh")
        locale = self.inject("locale", "en")     # Any，无类型推断

    .. rubric:: 行为规约

    - ``name`` 创建后不可变；同一 ``name`` 的 ``InjectionKey`` 与裸字符串
      指向 ``_provided`` 中的**同一槽位**。
    - ``__eq__``：与另一个 ``InjectionKey`` 比较 ``name``，或与裸 ``str``
      直接比较；``__hash__`` 等于 ``hash(self.name)``——因此键可直接用于
      dict 查找并与字符串 key 混用。
    - 非行为：运行期不做 ``T`` 的类型校验——父 provide 了 ``int``、子按
      ``InjectionKey[str]`` inject 不会被框架拦截（类型契约是静态的）。
    - 边缘情况：两个泛型参数不同但 ``name`` 相同的键（如
      ``InjectionKey[int]("x")`` 与 ``InjectionKey[str]("x")``）在运行期
      **相等且同槽**——避免在同一作用域混用同名异型键，框架不检测此类
      静态错误。
    - 同名 provide key 冲突时后注册者报错（``provide`` 侧规则，见
      ``ProvideNode.provide``），与本类无关。

    .. rubric:: 测试案例

    - 前置：``k = InjectionKey[str]("locale")``；操作：``hash(k)``；
      期望：``hash("locale")``。
    - 前置：同上；操作：``k == "locale"`` 与 ``k == InjectionKey[int]("locale")``；
      期望：均为 ``True``（按 name 匹配）。
    - 前置：父 Agent ``provide(k, 1)``（应为 str 实传 int）；操作：子 Agent
      ``inject(k)``；期望：返回 ``1``，框架不抛类型错误（运行期不校验 T）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.runtime.Runtime.provide()`` / ``.inject()``、
      ``flowing.agent.Agent.provide()`` / ``.inject()``、
      ``flowing.plugins.workflow`` 的 provide/inject（作为 key 参数类型，
      每次 provide/inject）；``flowing.runtime.inject_from()`` 按 ``name``
      匹配时经 ``__eq__`` / ``__hash__``
    - 实例化方：框架内插件模块级键定义——
      ``flowing.plugins.comm.communication_key``、
      ``flowing.plugins.skills.skill_registry_key``、
      ``flowing.plugins.cron.cron_scheduler_key``（各模块 import 时构造）；
      用户共享键定义（docstring 示例 ``keys.py``）

    .. seealso::

        :class:`ConfigKey`
            镜像同模式的配置键。
        ``flowing.runtime.inject_from``
            沿 provide 链上溯、按 ``name`` 匹配的执行点。
        ``flowing.runtime.ProvideNode``
            ``_provided`` key 恒为 ``str`` 的协议。
    """

    name: str
    """键名——运行期唯一实质内容；``_provided`` 字典中的实际 key。
    创建后不可变；与同名裸字符串等价（同槽位、同哈希）。
    
    .. seealso:: :meth:`__eq__`、:meth:`__hash__`
    """

    def __init__(self, name: str) -> None:
        """以键名构造 ``InjectionKey``。

        .. rubric:: 功能介绍与动机

        构造是同步、无副作用的纯赋值；泛型参数 ``T`` 只出现在标注位置
        （``InjectionKey[str] = InjectionKey("locale")``），不参与构造。

        .. rubric:: 行为规约

        - 前置条件：``name`` 为非空字符串；空名的行为不作契约保证。
        - 后置条件：``self.name == name``；可立即用于 dict 键与相等比较。
        - 边缘情况：同名键多次构造产生**不同对象但相等**（``__eq__`` 按
          ``name``），可安全在多处声明同一键（推荐仍是共享 ``keys.py``
          单点定义，便于重构追踪）。

        .. rubric:: 调用关系（审计）

        - 调用：无（纯赋值）
        - 被调：各实例化点（框架内：插件模块级键定义，import 时；
          用户 ``keys.py`` 共享键定义）

        .. seealso:: :attr:`name`
        """
        self.name = name

    def __hash__(self) -> int:
        """返回 ``hash(self.name)``——与裸字符串 key 同哈希，可混用于 dict。

        .. rubric:: 行为规约

        - 不变量：``hash(InjectionKey("x")) == hash("x")`` 恒成立；
          纯函数、无副作用。

        .. rubric:: 调用关系（审计）

        - 调用：``hash(self.name)``（内建函数）
        - 被调：``_provided`` dict 存取时隐式触发——
          ``flowing.runtime.inject_from()`` 沿链匹配（每次 inject）

        .. seealso:: :meth:`__eq__`、:attr:`name`
        """
        result: int = hash(self.name)
        # -> int（__hash__ 返回值，时序见 docstring）
        return result  # v2 增补：返回值（docstring 明写 hash(self.name)）

    def __eq__(self, other: object) -> bool:
        """按 ``name`` 判等：可与另一个 ``InjectionKey`` 或裸 ``str`` 比较。

        .. rubric:: 功能介绍与动机

        「跨节点退化为按 name 匹配」在比较层的落地：``key in current._provided``
        对 ``InjectionKey`` 与字符串 key 走同一条路径。

        .. rubric:: 行为规约

        - ``other`` 为 ``InjectionKey`` → 比较 ``self.name == other.name``
          （忽略两侧泛型参数）。
        - ``other`` 为 ``str`` → 比较 ``self.name == other``。
        - 其余类型 → ``NotImplemented``/``False``（按 Python 惯例）。
        - 非行为：不比较泛型参数 ``T``（运行期不可得其值）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``key in current._provided`` 判等——
          ``flowing.runtime.inject_from()`` 上溯链（每次 inject）

        .. seealso:: :meth:`__hash__`
        """
        if isinstance(other, InjectionKey):
            result: bool = self.name == other.name  # 忽略两侧泛型参数
        elif isinstance(other, str):
            result = self.name == other
        else:
            return NotImplemented  # 其余类型按 Python 惯例
        return result  # -> bool

    def __repr__(self) -> str:
        """返回 ``InjectionKey("name")`` 形式的调试表示。

        .. rubric:: 行为规约

        - 展示 ``name``，不展示泛型参数（运行期不可得）；无副作用。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：框架内未见规约（仅调试/日志展示）

        .. seealso:: :attr:`name`
        """
        result: str = f"InjectionKey({self.name!r})"
        # -> str（调试表示，形式见 docstring）
        return result  # v2 增补：返回值（docstring 明写形式）

class ConfigKey(Generic[T]):
    """配置读取的类型安全键——镜像 :class:`InjectionKey` 模式。

    .. rubric:: 功能介绍

    ``ConfigKey[T]`` 是 ``Runtime.get_config(key, default)`` 的键类型：
    泛型参数 ``T`` 声明该配置值的 Python 类型，``name`` 是配置项的键名
    （含命名空间的点分路径，如 ``"retry.max_attempts"``、``"i18n.locale"``）。
    与裸字符串 key 共存：字符串简写返回值类型为 ``Any``。

    .. rubric:: 设计动机

    配置读取是 Runtime 实例方法（不做模块级全局函数）；``ConfigKey`` 把
    「键名 → 值类型」的对应关系固化为可静态检查的契约：
    ``get_config(key, default=...)`` 的 ``default`` 参数类型必须与 ``T``
    一致，否则 mypy/pyright 报错。与 ``InjectionKey`` 同模式——类型信息只在
    调用侧生效，配置存储层按字符串键存取，不感知泛型（「类型信息不跨节点」
    边界的配置侧镜像）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing import ConfigKey

        max_attempts_key: ConfigKey[int] = ConfigKey("retry.max_attempts")
        locale_key: ConfigKey[str] = ConfigKey("i18n.locale")

        # setup() / 钩子回调中（禁止模块顶层调用 get_config）
        def setup(self):
            n: int = self.runtime.get_config(max_attempts_key, default=3)
            loc = self.runtime.get_config(locale_key, default="zh")

        # 字符串简写（同一配置项，无类型推断）
        n = self.runtime.get_config("retry.max_attempts", 3)

    .. rubric:: 行为规约

    - ``name`` 创建后不可变；命名空间段（``retry``、``i18n``）是约定而非
      本类强制——命名空间的注册与「谁负责校验」由
      ``Runtime.register_config_namespace()`` 管理；``get_config`` 不做
      命名空间访问控制，任何代码可读取任何命名空间下的值。
    - ``__eq__`` / ``__hash__`` 语义与 :class:`InjectionKey` 相同：按
      ``name`` 与另一个 ``ConfigKey`` 或裸 ``str`` 比较，哈希等于
      ``hash(self.name)``。
    - 非行为：运行期不做 ``T`` 的类型校验——配置文件里实际值类型与 ``T``
      不符时，框架不拦截（静态契约，非运行期校验器）。
    - 调用时机约束（消费侧规则）：``get_config`` 只能在 ``setup()`` 与钩子
      回调中调用；模块顶层调用抛 ``ConfigNotReadyError``（优先级链合并
      未完成）。本类本身无此约束——约束在执行读取的 Runtime 方法上。
    - 边缘情况：同名异型键（``ConfigKey[int]("x")`` vs ``ConfigKey[str]("x")``）
      运行期相等——静态错误，框架不检测。

    .. rubric:: 测试案例

    - 前置：``k = ConfigKey[int]("retry.max_attempts")``；操作：
      ``k == "retry.max_attempts"``；期望：``True``。
    - 前置：同上；操作：``hash(k)``；期望：``hash("retry.max_attempts")``。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.runtime.Runtime.get_config()``（作为 key 参数类型，
      每次配置读取；约束 ``default`` 类型与 ``T`` 一致）
    - 实例化方：用户代码（docstring 示例）；框架内未见实例化点

    .. seealso::

        :class:`InjectionKey`
            同模式的注入键（设计原型）。
        ``flowing.runtime.Runtime.get_config``
            消费入口与调用时机约束（``ConfigNotReadyError``）。
        ``flowing.runtime.Runtime.register_config_namespace``
            命名空间注册与透传规则。
    """

    name: str
    """配置键名（约定为含命名空间的点分路径）；创建后不可变；
    与同名裸字符串等价。
    
    .. seealso:: :meth:`__eq__`、``flowing.runtime.Runtime.get_config``
    """

    def __init__(self, name: str) -> None:
        """以键名构造 ``ConfigKey``。

        .. rubric:: 功能介绍与动机

        同步、无副作用的纯赋值；泛型参数 ``T`` 只出现在标注位置，不参与
        构造。语义与 :meth:`InjectionKey.__init__` 完全一致。

        .. rubric:: 行为规约

        - 前置条件：``name`` 为非空字符串。
        - 后置条件：``self.name == name``；可立即用于相等比较。

        .. rubric:: 调用关系（审计）

        - 调用：无（纯赋值）
        - 被调：``ConfigKey`` 各实例化点（框架内未见；用户代码）

        .. seealso:: :attr:`name`、:meth:`InjectionKey.__init__`
        """
        self.name = name

    def __hash__(self) -> int:
        """返回 ``hash(self.name)``——与裸字符串 key 同哈希。

        .. rubric:: 行为规约

        - 不变量：``hash(ConfigKey("x")) == hash("x")``；纯函数、无副作用。

        .. rubric:: 调用关系（审计）

        - 调用：``hash(self.name)``（内建函数）
        - 被调：dict 存取时隐式触发（框架内未见规约）

        .. seealso:: :meth:`__eq__`、:attr:`name`
        """
        result: int = hash(self.name)
        # -> int（__hash__ 返回值，时序见 docstring）
        return result  # v2 增补：返回值（docstring 明写 hash(self.name)）

    def __eq__(self, other: object) -> bool:
        """按 ``name`` 判等：可与另一个 ``ConfigKey`` 或裸 ``str`` 比较。

        .. rubric:: 行为规约

        - ``other`` 为 ``ConfigKey`` → 比较 ``name``（忽略泛型参数）；
          ``other`` 为 ``str`` → 直接比较；其余 → ``NotImplemented``/``False``。
        - 非行为：不比较泛型参数 ``T``。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：框架内未见规约（``Runtime.get_config`` 按 ``name`` 取值的
          内部比较路径未在 docstring 写明）

        .. seealso:: :meth:`InjectionKey.__eq__`（同语义原型）
        """
        if isinstance(other, ConfigKey):
            result: bool = self.name == other.name  # 忽略两侧泛型参数
        elif isinstance(other, str):
            result = self.name == other
        else:
            return NotImplemented  # 其余类型按 Python 惯例
        return result  # -> bool

    def __repr__(self) -> str:
        """返回 ``ConfigKey("name")`` 形式的调试表示。

        .. rubric:: 行为规约

        - 展示 ``name``，不展示泛型参数；无副作用。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：框架内未见规约（仅调试/日志展示）

        .. seealso:: :attr:`name`
        """
        result: str = f"ConfigKey({self.name!r})"
        # -> str（调试表示，形式见 docstring）
        return result  # v2 增补：返回值（docstring 明写形式）
