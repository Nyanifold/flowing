"""``flowing.parser`` —— ``.fya`` 纯字面前端：把文本切分为结构化声明文档。

.. rubric:: 功能介绍

本模块是 ``.fya`` 处理三层流水（字面层 → 装配层 → 合成层）中的
字面层：只做「文本 → 结构化数据」的转换，产出 :class:`FyaDocument`
（或中间的 :class:`RawFya`）。:class:`FyaDocument` 分三部分：

- ``fields``：顶层 YAML 映射（``_`` 已映射为 :data:`flowing.parsable.PENDING`；
  ``entry_fields`` 声明的资源列表已规范化为 ``list[EntryRef]``）；
- ``blocks``：具名块（``$<点分路径>:``）块体原文，原样持有、不填回——
  填回（merge）属装配层；
- ``script``：``$script`` 块原文。

本模块与资源无关：不内置 "agent" / "tool" / "skill" 等任何关键词；
「哪些字段是资源列表」由调用方经 ``entry_fields`` 声明，「路径形态怎么
推断名字」由调用方经 :class:`flowing.paths.NamingRules` 提供。

.. rubric:: ``.fya`` 词法规则（本模块管辖的全部语法）

- 文件 = 顶层 YAML 段 + 0..n 个块；块分隔符 ``---`` 独占一行；
  块头为分隔后次行 ``$<点分路径>:`` （路径段为纯标识符，无下标）；
  块体为到下一个 ``---`` 或 EOF 的原文；
- ``$script`` 是保留块名：内容单独取出，不进 ``blocks``；
- YAML 标量恰为 ``_`` → :data:`~flowing.parsable.PENDING` （递归进嵌套
  dict/list）；``$"_"`` 是普通字符串不受影响；非字符串 YAML 值保持原生
  类型（``Parsable`` 包装是装配层职责）。

.. rubric:: 边界

- 不 merge 具名块（填回规则与导航在装配层）；
- 不做引用解析：``EntryRef.raw`` 保留原始字符串，形态判别经
  :func:`flowing.paths.classify_ref`，实际查找在各 registry；
- 不识别任何具体字段名（``tools`` / ``args`` 等对 parser 无意义）。

.. seealso::

    - :mod:`flowing.paths` —— 引用形态与命名词汇。
    - :mod:`flowing.compiler` —— 合成层（内存类 / 落盘 ``.py`` 两形态
      同源），共享本前端。
"""

from __future__ import annotations   # 注解延迟求值：配合 TYPE_CHECKING 破注解级循环边

import re
from dataclasses import dataclass, field
from typing import Any, Collection, Mapping

from ruamel.yaml import YAML   # 全项目统一 ruamel（与 flowing.model 同库）
from ruamel.yaml.error import YAMLError

from flowing.errors import FormatError
from flowing.parsable import PENDING
from flowing.paths import NamingRules, classify_ref, infer_name

__all__ = [
    "EntryRef",
    "FyaDocument",
    "RawFya",
    "parse_fya",
    "split_fya",
    "normalize_entries",
    "split_as",
    "load_fya_yaml",
]

# ---------------------------------------------------------------------------
# 产物类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryRef:
    """资源列表条目的字面规范化产物。

    .. rubric:: 功能介绍

    ``tools:`` / ``subagents:`` / ``skills:`` 列表项的统一形态：原始引用
    串不拆、别名落定、覆写映射原样持有。本类型只携带「指向谁、叫什么、
    临时附言」三件字面事实——解析到目标（注册表查找 / 候选链探测）不在
    此层。

    .. rubric:: 使用示例

    ``- ./payment as pay: {args: [...]}`` 产出：

    .. code-block:: python

        EntryRef(raw="./payment", alias="pay",
                 body={"args": [...]})

    .. rubric:: 行为要点

    - ``raw`` 从不被本模块内部解析——路径内的 ``::``、限定名的 ``ns::``
      都原样保留；消费方按需再调 :func:`flowing.paths.classify_ref`
      分流。
    - ``alias`` 在规范化后永远有值（推断规则见
      :func:`normalize_entries`），是后续一切匹配（装配层深层块导航、
      冲突检测、LLM 可见名）的唯一键。

    .. seealso:: :func:`normalize_entries` —— 唯一构造通道。
    """

    raw: str
    """``as`` 前的原始字符串：路径 / 裸名 / ``ns::name``，不拆。"""

    alias: str
    """最终别名：显式 ``as`` 值 > 限定名 name 段 > 裸名本身 > 路径形态
    经 :func:`flowing.paths.infer_name` 推断。"""

    body: Mapping[str, Any] = field(default_factory=dict)
    """单键映射项的值（如 ``{args: [...]}``）；字符串项
    为空映射。内容对本模块不透明——命名取中性的「映射体」，不预设
    「覆写」语义（语义解释归各资源装配层 / ``Agent.add_tool`` 等消费方）。"""


@dataclass(frozen=True)
class RawFya:
    """:func:`split_fya` 的中间产物：文本切分结果，YAML 尚未加载。

    .. rubric:: 功能介绍

    公开供调试与测试；正常调用方应使用 :func:`parse_fya` 一次走完。
    """

    yaml_text: str
    """顶层 YAML 段原文（可能为空串）。"""

    blocks: dict[str, str]
    """具名块路径 → 块体原文（不含 ``$script``）。"""

    script: str | None
    """``$script`` 块体原文；无则 ``None``。"""


@dataclass(frozen=True)
class FyaDocument:
    """``.fya`` 文件的字面解析终产物（:func:`parse_fya` 的返回）。

    .. rubric:: 功能介绍

    「已规范化、未求值、未合并」的声明文档：``fields`` 中 ``_`` 已是
    :data:`~flowing.parsable.PENDING`、声明为资源列表的字段已是
    ``list[EntryRef]``；``blocks`` 原样持有等待装配层填回；``script``
    等待装配层编译为类体方法。

    .. rubric:: 行为要点

    - 产出值都是「生的」：具名块是原始字符串，YAML 值是原生类型；
      不构造 ``Parsable``、不做文件查找、不校验字段语义。
    - 不变量：``blocks`` 的 key 唯一（重复路径在切分期已报错）且
      不含 ``$script``。
    """

    fields: dict[str, Any]
    """顶层 YAML（``_`` → ``PENDING`` 已映射；``entry_fields`` 列表
    已规范化为 ``list[EntryRef]``）。"""

    blocks: dict[str, str]
    """具名块（路径 → 原文），未填回。"""

    script: str | None
    """``$script`` 块原文；无则 ``None``。"""


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def parse_fya(
    text: str,
    *,
    entry_fields: Collection[str] = (),
    naming: NamingRules | None = None,
) -> FyaDocument:
    """``.fya`` 文本 → :class:`FyaDocument` （字面层流水一次走完）。

    .. rubric:: 功能介绍

    主管线（顺序不变量）：

    1. :func:`split_fya` 切分为顶层 YAML 文本、具名块、``$script``；
    2. :func:`load_fya_yaml` 加载 YAML（``_`` → ``PENDING`` 递归映射）；
    3. 对 ``entry_fields`` 声明的字段调 :func:`normalize_entries`
       规范化资源列表；其余字段原样不动。

    不 merge：具名块填回（含深层导航、PENDING 替换规则）是装配层
    职责。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.runtime import AGENT_NAMING

        # agent 装配层（有资源列表字段）
        doc = parse_fya(text, entry_fields={"subagents", "tools"},
                        naming=AGENT_NAMING)
        # tool / skill 装配层（无资源列表）
        doc = parse_fya(text)

    .. rubric:: 行为要点

    - ``entry_fields`` 中声明但文件里不存在的字段：不报错（缺省视为
      无声明——字段缺失语义由装配层按「必填 / 可选」各自决定）。
    - 声明字段的值不是列表 → :class:`FormatError` （资源列表必须是
      YAML 列表）。
    - ``naming`` 仅在条目规范化遇路径形态时需要；其余形态可缺省。
    - 边缘情况：空文件 / 仅 YAML 无块 / 仅块无 YAML——均合法，
      ``fields`` 与 ``blocks`` 相应为空。

    :param text: ``.fya`` 文件全文。
    :param entry_fields: 声明为资源列表的字段名集合（值会被规范化
        为 ``list[EntryRef]``）。
    :param naming: 路径形态条目的别名推断规则表。
    :return: 规范化后的声明文档。
    :raises flowing.errors.FormatError: 声明字段的值不是列表；或切分 /
        YAML 加载阶段发现非法结构（块路径重复、块头不合法、顶层非映射、
        YAML 语法错误等）。

    .. seealso:: :class:`FyaDocument` —— 产物契约。
    """
    raw = split_fya(text)
    fields = load_fya_yaml(raw.yaml_text)
    for name in entry_fields:
        if name in fields:
            items = fields[name]
            if not isinstance(items, list):
                raise FormatError(f"resource list field {name!r} must be a YAML list")
            fields[name] = normalize_entries(items, naming=naming)
    return FyaDocument(fields=fields, blocks=raw.blocks, script=raw.script)


# ---------------------------------------------------------------------------
# 第 1 层：文本切分
# ---------------------------------------------------------------------------

_BLOCK_HEADER_PATTERN = re.compile(r"^\$([A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*):$")
"""块头模式：``$<点分路径>:`` 独占一行；段字符集不含 ``[``，下标段天然失配。"""


def split_fya(text: str) -> RawFya:
    """``.fya`` 文本 → 顶层 YAML 文本 + 具名块 + ``$script``。

    .. rubric:: 功能介绍

    纯文本切分，不触碰 YAML 语义。规则：

    - 第一个 ``---`` 独占一行之前的全部内容为顶层 YAML 段；文件以
      ``---`` 开头则 YAML 段为空；
    - 每个块：``---`` 独占一行，次行必须是块头 ``$<点分路径>:``，
      块体为块头行之后到下一个 ``---`` 或 EOF 的原文（不去缩进、
      不去尾部换行——内容整形由消费方决定）；
    - 块路径：``$`` 后点分标识符序列，段字符集 ``[A-Za-z0-9_-]+``；
      不允许 ``[n]`` 下标段（列表一律按别名寻址，见装配层导航规则）；
    - ``$script`` 保留：其块体进 :attr:`RawFya.script`，不进
      ``blocks``。

    .. rubric:: 行为要点

    - 块路径重复（含大小写差异视为不同 key——不做归一化，笔误由装配层
      未命中报错暴露）→ :class:`FormatError`；``$script`` 出现多次 →
      :class:`FormatError`。
    - 块头不合法（``---`` 后次行不匹配 ``$...:`` 形式、路径含空段或
      非法字符、含 ``[``）→ :class:`FormatError`。
    - 块体允许为空串（如 ``$field:`` 后无内容）——空串也是「值」，
      与「未声明」不同。
    - 本函数不解析 YAML、不识别 ``_`` （那是 :func:`load_fya_yaml` 的
      职责）；不校验路径段对应的字段是否真实存在（装配层导航时才检查）。

    .. rubric:: 使用示例

    .. code-block:: python

        raw = split_fya("a: 1\\n---\\n$system_prompt:\\n你好")
        # raw.yaml_text == "a: 1\\n"
        # raw.blocks == {"system_prompt": "你好"}

    :param text: ``.fya`` 文件全文。
    :return: :class:`RawFya` （YAML 段原文 / 具名块 / ``$script``）。
    :raises flowing.errors.FormatError: 块分隔后缺块头、块头非法、块路径
        重复或 ``$script`` 重复。

    .. seealso:: :func:`load_fya_yaml` —— 紧接着的 YAML 加载层。
    """
    lines = text.splitlines(keepends=True)
    sep = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == "---"]
    yaml_text = "".join(lines[: sep[0]]) if sep else text
    blocks: dict[str, str] = {}
    script: str | None = None
    for idx, sep_i in enumerate(sep):
        body_start = sep_i + 2   # 块头行之后
        if sep_i + 1 >= len(lines):
            raise FormatError(f"block header missing after the --- separator (line {sep_i + 1})")
        header = lines[sep_i + 1].rstrip("\r\n")
        m = _BLOCK_HEADER_PATTERN.match(header)
        if m is None:
            raise FormatError(
                f"invalid block header (line {sep_i + 2}): {header!r}"
                " — must be $<dotted path>: (segment charset [A-Za-z0-9_-], no index segments)")
        path = m.group(1)
        body_end = sep[idx + 1] if idx + 1 < len(sep) else len(lines)
        body = "".join(lines[body_start:body_end])   # 原文保留（不去缩进/尾部换行）
        if path == "script":
            if script is not None:
                raise FormatError("the $script block appears more than once")
            script = body
        else:
            if path in blocks:
                raise FormatError(f"duplicate block path: ${path}:")
            blocks[path] = body
    return RawFya(yaml_text=yaml_text, blocks=blocks, script=script)


# ---------------------------------------------------------------------------
# YAML 加载(含 _ → PENDING 映射)
# ---------------------------------------------------------------------------


def load_fya_yaml(text: str) -> dict[str, Any]:
    """YAML 文本 → dict，标量 ``_`` 递归映射为 ``PENDING``。

    .. rubric:: 功能介绍

    ``.fya`` 顶层段与 skill ``.md`` frontmatter 共用的 YAML 加载器——
    保证 ``_`` → ``PENDING`` 语义全框架只有这一处实现。

    .. rubric:: 行为要点

    - 映射规则：YAML 标量值恰为字符串 ``"_"`` →
      :data:`~flowing.parsable.PENDING`，递归作用于嵌套 dict/list
      （``args: {description: _}`` 内同样生效）；``$"_"`` 等其它字符串
      原样（RAW 语义无涉本层）。
    - 列表项位置的 ``PENDING`` 在本层不报错：``tools: [payment, _]``
      可以加载；但 :func:`normalize_entries` 会拒绝（列表项无别名
      寻址通道，``_`` 无法兑现——见该函数）。
    - 非字符串 YAML 值保持原生类型（``max_turns: 10`` → ``int``）；
      ``Parsable`` 包装属装配层。
    - 空文本 → 空 dict；顶层不是映射（如标量 / 列表）→
      :class:`FormatError` （报文含首个非注释非空行的行号）；YAML 语法
      错误 → :class:`FormatError` （包装原始异常，报文含行号）。
    - 本函数不做环境变量展开、不做 ``$`` 引用解析（那是 Parsable
      求值期）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.parsable import PENDING

        load_fya_yaml("a: _")["a"] is PENDING          # True

    :param text: YAML 文本。
    :return: 顶层映射 dict。
    :raises flowing.errors.FormatError: 顶层不是映射，或 YAML 语法错误。

    .. seealso:: :data:`flowing.parsable.PENDING` —— 哨兵语义本体。
    """
    # safe_load 语义经 ruamel YAML(typ="safe") 落实（全项目统一 ruamel）；
    # 语法错误包装为 FormatError，ruamel 报文自带行号
    if not text.strip():
        return {}
    try:
        data = YAML(typ="safe").load(text)
    except YAMLError as exc:
        raise FormatError(f".fya top-level YAML syntax error:\n{exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        # 行号取首个非注释非空行（safe_load 成功路径无异常 mark，就地扫描合成）
        first = next(
            (i + 1 for i, ln in enumerate(text.splitlines())
             if ln.strip() and not ln.lstrip().startswith("#")),
            1,
        )
        raise FormatError(
            f".fya top level must be a mapping, got "
            f"{type(data).__name__} (starting at line {first})")
    return _map_pending(data)


def _map_pending(value: Any) -> Any:
    """递归把 YAML 标量值 ``"_"`` 映射为 ``PENDING`` （嵌套 dict/list 内同样生效）。"""
    if isinstance(value, dict):
        return {k: _map_pending(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_map_pending(v) for v in value]
    if value == "_" and isinstance(value, str):
        return PENDING
    return value


# ---------------------------------------------------------------------------
# 第 2 层:列表条目规范化
# ---------------------------------------------------------------------------

_AS_PATTERN = re.compile(r"\s+as\s+")
"""``as`` 切分模式:两侧至少一空白;全框架唯一定义(条目与覆写参数共用)。"""


def split_as(s: str) -> tuple[str, str | None]:
    """``"<ref> as <alias>"`` → ``(ref, alias)``；无 ``as`` → ``(s, None)``。

    .. rubric:: 功能介绍

    ``as`` 语法的唯一切分实现。两个消费点：本模块的条目规范化
    （资源列表项）与装配层的覆写参数项判别（``working_dir as cwd``
    → ``param_aliases``）。

    .. rubric:: 行为要点

    - 按两侧至少一个空白的 ``as`` 切分；出现多个 ``as`` →
      :class:`FormatError` （嵌套引用无意义，几乎必为笔误）；
    - ``as`` 任一侧为空串 → :class:`FormatError`；
    - 返回的两段均 ``strip``；内部空白原样保留（``ref`` 可能是含
      目录段的路径，但不会含首尾空白）。
    - 本函数不校验 ref / alias 的字符集与形态（各属
      :func:`flowing.paths.classify_ref` 与装配层）。

    .. rubric:: 使用示例

    .. code-block:: python

        split_as("payment as pay") == ("payment", "pay")
        split_as("payment") == ("payment", None)
        split_as("a  as  b") == ("a", "b")   # 空白宽容

    :param s: 引用串（可含 ``as``）。
    :return: ``(ref, alias)`` 二元组；无 ``as`` 时 ``alias`` 为 ``None``。
    :raises flowing.errors.FormatError: 出现多个 ``as``，或任一侧为空。
    """
    parts = _AS_PATTERN.split(s)
    if len(parts) > 2:
        raise FormatError(f"multiple as in one entry: {s!r}")
    ref = parts[0].strip()
    alias = parts[1].strip() if len(parts) == 2 else None
    if not ref or (alias is not None and not alias):
        raise FormatError(f"both sides of as must be non-empty: {s!r}")
    return ref, alias


def normalize_entries(
    items: list[Any],
    *,
    naming: NamingRules | None = None,
) -> list[EntryRef]:
    """资源列表 → ``list[EntryRef]`` （``as`` 切分 + 形态判别 + 别名推断）。

    .. rubric:: 功能介绍

    逐条处理顺序（不变量）：

    1. 形态校验：项须为字符串（纯引用）或单键映射
       （``{<引用>: <覆写映射>}``）；其余（含多键映射、标量）→
       :class:`FormatError`；
    2. :func:`split_as` 切分引用串；
    3. :func:`flowing.paths.classify_ref` 判别形态（路径前缀优先——
       路径内 ``::`` 不解析；否则按第一个 ``::`` 为限定名）；
    4. 别名推断（首个命中者生效），规则见下表。

    .. list-table:: 别名推断（按优先级，首个命中者生效）
       :header-rows: 1

       * - 情形
         - 别名取值
       * - 显式 ``as`` （``- payment as pay``）
         - ``as`` 右侧的别名
       * - 限定名（``ns::name``）
         - 第一个 ``::`` 后的 ``name`` 段
       * - 裸名
         - 引用串本身
       * - 路径形态（``./a/payment`` 等）
         - 经 :func:`flowing.paths.infer_name` 推断（需 ``naming``
           规则表，否则 :class:`FormatError`）

    .. rubric:: 行为要点

    - 列表项为 ``PENDING`` （YAML 写了 ``- _``）→ :class:`FormatError`：
      列表项没有别名无法被深层块寻址，``_`` 在此是永远无法兑现的
      承诺（``PENDING`` 只存在于可按名寻址的位置：标量字段与覆写值）。
    - 单键映射的值为 ``PENDING`` （``- payment: _``）→ 解析为空
      覆写 ``{}`` （空补丁语义：声明了覆写位、内容为空，可从原始定义
      全量回填）；值为标量 / 列表 → :class:`FormatError` （覆写集合必须
      是映射或 ``_``）。
    - 重复别名不在本层报错：撞名检测（``EntryNameConflictError``）
      是装配层职责（它需要目标身份做 glob 例外判定，本层没有）。
    - 本函数不解析 ``raw`` （不读文件、不查注册表）；不解释 ``body``
      内容。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.runtime import AGENT_NAMING

        normalize_entries(["payment as pay", "./a/payment",
                           {"builtin::web-search": {"visible": True}}],
                          naming=AGENT_NAMING)
        # → [EntryRef("payment", "pay", {}),
        #    EntryRef("./a/payment", "payment", {}),
        #    EntryRef("builtin::web-search", "web-search", {"visible": True})]

    :param items: 资源列表项（字符串或单键映射）。
    :param naming: 路径形态条目的别名推断规则表。
    :return: 规范化后的 ``EntryRef`` 列表。
    :raises flowing.errors.FormatError: 项形态非法、列表项为 ``PENDING``、
        覆写集合不是映射或 ``_``、路径形态条目缺 ``naming``。

    .. seealso:: :func:`parse_fya` —— 按 ``entry_fields`` 调用本函数。
    """
    refs: list[EntryRef] = []
    for item in items:
        # 1. 形态校验：字符串（纯引用）或单键映射；其余 -> FormatError
        if isinstance(item, str):
            ref_str, body = item, {}
        elif isinstance(item, Mapping):
            if len(item) != 1:
                raise FormatError(f"entry must be a single-key mapping: {item!r}")
            ref_str, body = next(iter(item.items()))
            if body is PENDING:
                body = {}   # 覆写位 PENDING = 空补丁语义
            elif not isinstance(body, Mapping):
                raise FormatError(f"override set must be a mapping or _ (PENDING): {item!r}")
        else:
            if item is PENDING:
                # 列表项 PENDING：无别名无法被深层块寻址，永远无法兑现
                raise FormatError("list items may not be _ (PENDING)")
            raise FormatError(f"entry must be a string or a single-key mapping: {item!r}")
        if not isinstance(ref_str, str):
            raise FormatError(f"entry reference must be a string: {ref_str!r}")
        # 2. as 切分
        raw, alias = split_as(ref_str)
        # 3. 形态判别（路径前缀优先；否则按第一个 :: 为限定名）
        kind = classify_ref(raw)
        # 4. 别名推断：显式 as > 限定名 name 段 > 裸名本身 > 路径形态 infer_name
        if alias is None:
            if kind == "qualified":
                alias = raw.split("::", 1)[1]
            elif kind == "bare":
                alias = raw
            else:   # 路径形态：经 infer_name 推断（naming 必传）
                if naming is None:
                    raise FormatError(
                        f"alias inference for path-form entries requires a naming rule table: {raw!r}")
                alias = infer_name(raw, naming=naming)
        refs.append(EntryRef(raw=raw, alias=alias, body=body))
    return refs
