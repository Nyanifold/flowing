"""``flowing.paths`` —— 资源引用与路径词汇：前缀、形态判别、解析、命名格式。

.. rubric:: 功能介绍

本模块集中定义框架内「资源引用字符串」的路径词法规则，供 ``.fya`` 装配、
注册表查找等场景共用：

- 路径前缀表（:data:`PATH_PREFIXES`）：``./``、``../``、``@/`` 三类相对
  前缀，全框架唯一权威来源（``parsable`` 的 ``$`` 引用、各 registry 的
  候选链与 ``Runtime.resolve_path`` 共用同一组前缀）；
- 引用形态判别（:func:`classify_ref`）：把引用字符串判为路径 / 限定名
  （``ns::name``）/ 裸名三种形态之一——只回答「这个字符串长什么样」，
  不回答「去哪找」；
- 路径解析与表示（:func:`resolve_path` / :func:`to_project_path`）：
  前缀 → ``pathlib.Path`` 的纯函数，以及逆向的对外表示（根内 ``@/``
  形式）；
- 候选链探测（:func:`probe_candidates`）：按调用方给定的候选名列表逐个
  探测存在性，首个命中返回——链内容是各资源的规则（留在各模块），探测
  循环只有一份；
- 身份名推断（:class:`NamingRules` / :func:`infer_name`）：文件名/目录名
  → 规范身份名（去后缀、通用名取目录名、snake→kebab）；
- 命名格式互转：kebab / snake / Pascal 三格式的双向纯转换
  （:func:`kebab_to_snake` 等四个函数）。

本模块只做字符串与路径运算：不做文件读取、注册表查找或 glob 展开（这些
是调用方职责）；不含资源语义——候选链内容、后缀策略由各资源模块提供。

.. rubric:: 行为要点

- 路径形态的判定规则（:func:`classify_ref`）：任一资源引用字符串，只要
  不含 ``::`` 且含 ``/`` 或反斜杠字符，一律判为路径，以引用方提供的
  ``source_dir`` 为基准解析，不再要求必须写 ``./`` 前缀。判定顺序固定：
  路径前缀（``./`` / ``../`` / ``@/``）与绝对路径优先；其次含 ``::`` →
  看第一个 ``::`` 之前的左段：左段是纯标识符则判限定名（``ns::name``），
  左段含路径特征（含 ``/`` 或以 ``.py`` 结尾）则判路径（``文件::类名``
  形态，如 ``./agents.py::OrderAgent``，完整规则见 :func:`classify_ref`）；
  再次含 ``/`` 或反斜杠字符 → 路径；最后才是裸名。
- 因此 ``agents/order-agent`` 这类写法在 Agent 的 ``tools:`` /
  ``subagents:`` / ``skills:`` 引用中按路径解析，以该 Agent 定义文件
  所在目录为 ``source_dir`` 基准。
- ``@/`` 前缀指向 flowing 子项目目录（工程根）：:func:`resolve_path`
  以调用方显式传入的 ``project_root`` 为基准；框架运行期由
  :meth:`flowing.runtime.Runtime.resolve_path` 注入（``@`` 上下文按
  asyncio Task 隔离——一个进程可同时运行多个 Runtime，互不串扰）。

.. rubric:: 使用示例

.. code-block:: python

    from pathlib import Path

    from flowing.paths import classify_ref, resolve_path, to_project_path

    root = Path("/proj")
    classify_ref("payment")                # "bare"：裸名走注册表查找
    classify_ref("builtin::web-search")    # "qualified"：限定名
    classify_ref("./agents/order-agent")   # "path"：路径形态；由 resolve_path 按 source_dir 解析
    resolved = resolve_path("@/tools/search.py", project_root=root)
    # resolved == Path("/proj/tools/search.py")
    to_project_path(resolved, project_root=root)   # "@/tools/search.py"

.. seealso::

    :mod:`flowing.parser` —— 条目引用的形态分流与别名推断，消费
    :func:`classify_ref` / :func:`infer_name`。
    :meth:`flowing.runtime.Runtime.resolve_path` —— 运行期入口，注入
    ``project_root`` 与 ``source_dir`` 后委托本模块纯函数。
"""

from __future__ import annotations   # 注解延迟求值：类型注解字符串化，不参与 import 期求值

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from flowing.errors import FormatError

__all__ = [
    "PATH_PREFIXES",
    "NamingRules",
    "classify_ref",
    "resolve_path",
    "to_project_path",
    "probe_candidates",
    "infer_name",
    "kebab_to_snake",
    "snake_to_kebab",
    "kebab_to_pascal",
    "pascal_to_kebab",
]

# ---------------------------------------------------------------------------
# 路径前缀与形态判别
# ---------------------------------------------------------------------------

PATH_PREFIXES: tuple[str, ...] = ("./", "../", "@/")
"""相对路径前缀表：``"./"`` （以引用方提供的 ``source_dir`` 为基准）、
``"../"`` （``source_dir`` 的父目录，多级 ``"../../"`` 逐级向上）、
``"@/"`` （项目根，以调用方传入的 ``project_root`` 为基准）。

绝对路径不在表内，由前导 ``/`` （POSIX；Windows UNC 归一后为 ``//``
前缀）或 Windows 盘符（``C:/``）另行判定。``~`` 也是合法前缀，永远按
绝对路径触发：``"~"`` / ``"~/..."`` 经 ``os.path.expanduser`` 展开为
当前用户家目录（POSIX 为 ``$HOME``，Windows 为 ``%USERPROFILE%``），
之后的处理与绝对路径同规则。``~`` 写法受支持，日常引用推荐用 ``./`` /
``@/`` 或绝对路径，语义更直接。

前缀判定中反斜杠与斜杠等价：``".\\"`` 视同 ``"./"``、``"..\\"`` 视同
``"../"``、``"~\\"`` 视同 ``"~/"`` （多级同理）；实现先做分隔符归一。

本表是全框架路径前缀的唯一权威来源：``parsable`` 的 ``$`` 引用与
``{% include %}``、:func:`classify_ref` 的路径形态判定、
:meth:`flowing.runtime.Runtime.resolve_path` 共用本表。
"""

_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
"""Windows 盘符绝对路径判定（分隔符归一后的形态，如 ``C:/x``）。

POSIX 上 ``pathlib`` 不把盘符 / UNC 识别为绝对路径；按
:data:`PATH_PREFIXES` docstring「Windows 盘符 / UNC 均算绝对路径」的
跨平台约定，实现侧在归一化后用本正则显式判定（UNC 归一后为 ``//``
前缀，由前导 ``/`` 判定覆盖）。
"""


def classify_ref(raw: str) -> Literal["path", "qualified", "bare"]:
    """把一个资源引用字符串判为路径 / 限定名 / 裸名三种形态之一。

    .. rubric:: 功能介绍

    纯词法判定，不做通道选择：本函数只回答「这个字符串长什么样」，
    「去哪找」（注册表查找、路径定位）由各注册表与装配层负责。判定
    顺序固定：

    1. 命中 :data:`PATH_PREFIXES` 任一项、前导 ``/`` （绝对路径）、
       Windows 盘符（``C:/``）或 ``~`` 形态 → ``"path"``——整体视为
       路径字符串，内部不再做任何格式解析（路径中出现 ``::`` 只是
       路径字符，不参与切分）；
    2. 含 ``::`` → 看第一个 ``::`` 之前的左段：左段含路径特征（含
       ``/`` 或反斜杠，或以 ``.py`` 结尾）→ ``"path"`` （``文件::类名``
       形态，如 ``./agents.py::OrderAgent``——多 Agent 子类文件的
       消歧引用；切分与类名选择语义在调用方，见
       :meth:`flowing.runtime.Runtime.get_agent_class`）；左段为纯
       标识符 → ``"qualified"`` （限定名 ``ns::name``，按第一个 ``::``
       切分，见 :func:`flowing.parser.normalize_entries`）；
    3. 含 ``/`` 或反斜杠字符（且无 ``::``）→ ``"path"``——普通相对
       路径，解析时以引用方提供的 ``source_dir`` 为基准；
    4. 其余 → ``"bare"`` （裸名）。

    :param raw: 资源引用字符串。
    :return: ``"path"`` / ``"qualified"`` / ``"bare"`` 之一。

    .. rubric:: 行为要点

    - ``a::b::c`` 判为 ``"qualified"``：本函数只命名形态，多个 ``::``
      不报错——切分语义在调用方，后续查找必然不命中，由查找层报错。
    - ``a::b/c`` 含 ``::``，判为 ``"qualified"`` （不是路径）。
    - 裸 ``@`` （不带斜杠）不命中任何路径形态，落入 ``"bare"`` （几乎
      必为笔误）。
    - 本函数不校验裸名 / 限定名的字符集（名称校验在各资源装配层）。

    .. seealso:: :func:`resolve_path` —— ``"path"`` 形态的实际解析。
    """
    # 先做分隔符归一（\ → /），再按固定顺序执行形态判别
    s = raw.replace("\\", "/")
    if s == "~" or s.startswith("~/"):
        return "path"  # ~ 永远按绝对路径触发（见 PATH_PREFIXES docstring）
    # Windows 盘符 / UNC 在前缀判定中与 POSIX 绝对路径同列（先归一后判定）
    if s.startswith(PATH_PREFIXES) or s.startswith("/") or _DRIVE_RE.match(s):
        return "path"
    if "::" in s:
        # 文件::类名 分支：左段含路径特征（/ 或以 .py 结尾）→ path
        left = s.split("::", 1)[0]
        if "/" in left or left.endswith(".py"):
            return "path"
        return "qualified"
    if "/" in s:
        return "path"
    return "bare"


# ---------------------------------------------------------------------------
# 路径解析与表示（纯函数；Runtime 方法委托于此）
# ---------------------------------------------------------------------------


def resolve_path(
    path: str,
    *,
    project_root: Path,
    source_dir: Path | None = None,
) -> Path:
    """把带前缀的路径字符串解析为 ``pathlib.Path`` （``@/`` / ``./`` / ``../`` / 绝对路径）。

    .. rubric:: 功能介绍

    前缀语义：``@/`` → ``project_root``；``./`` → ``source_dir``；
    ``../`` → ``source_dir.parent``，多级 ``../../`` 逐级向上；绝对路径
    原样接受。本函数面向「路径形态」的引用：裸名（无前缀、无分隔符）
    的查找属注册表 / 装配层，不经本函数。

    .. rubric:: 使用示例

    .. code-block:: python

        from pathlib import Path

        resolve_path("@/tools/search.py", project_root=Path("/proj"))
        resolve_path("./subagents/", project_root=Path("/proj"), source_dir=Path("/proj/agents"))

    :param path: 带前缀的路径字符串（``@/`` / ``./`` / ``../`` / 绝对
        路径 / 含 ``/`` 或反斜杠的普通相对路径 / ``~`` 形态）。
    :param project_root: ``@/`` 前缀的解析基准（项目根）。
    :param source_dir: ``./`` / ``../`` 与普通相对路径的解析基准；不提供
        时遇到这些形态抛 :class:`ValueError`。
    :return: 解析出的 ``Path``。解析结果可能越出 ``project_root``
        （绝对路径或 ``../`` 逃逸均合法）。

    .. rubric:: 行为要点

    - 前缀判定中反斜杠与斜杠等价（``.\\`` 视同 ``./``、``..\\`` 视同
      ``../``，多级同理）；实现先做分隔符归一。
    - ``~`` / ``~/...`` 永远按绝对路径触发：经 ``os.path.expanduser``
      展开为当前用户家目录（POSIX ``$HOME`` / Windows
      ``%USERPROFILE%``）后按绝对路径规则处理。``~`` 写法受支持，日常
      引用推荐用 ``./`` / ``@/`` 或绝对路径。
    - ``@/`` 不带后续路径段表示根目录本身（``@/`` → ``project_root``）。
    - 适用面：仅 flowing 项目资源引用（``.fya`` 的 ``$`` /
      ``{% include %}``、``tools:`` / ``skills:`` / ``subagents:`` 等
      路径字段）；不涉及 flowing 自身配置读取（``providers.yaml`` 等
      宿主启动层配置不经本函数解析）。
    - 不做存在性检查（解析 ≠ 打开文件）；不做 glob 展开（展开由调用方）。

    .. seealso:: :func:`to_project_path` —— 逆向的对外表示。
    """
    # 先归一分隔符（\ → /），再做 ~ 检测与 expanduser（~\\ 视同 ~/）
    s = path.replace("\\", "/")
    if s == "~" or s.startswith("~/"):
        # ~ 永远按绝对路径触发：expanduser 展开结果即绝对路径，按绝对路径规则处理
        s = os.path.expanduser(s)
    if s.startswith("@/"):
        return project_root / s[2:]
    if s.startswith("/") or _DRIVE_RE.match(s):
        # POSIX 前导 /（含归一后 // 的 UNC）与 Windows 盘符均按绝对路径原样接受
        return Path(s)
    if source_dir is None:
        raise ValueError("相对路径需要 source_dir（./ ../ 或含 / 反斜杠字符的普通相对路径）")
    base = source_dir
    rest = s
    while rest.startswith("../"):
        base = base.parent
        rest = rest[3:]
    if rest.startswith("./"):
        rest = rest[2:]
    return base / rest


def to_project_path(path: Path, *, project_root: Path) -> str:
    """把 ``Path`` 转换为对外表示：项目根内用 ``@/`` 相对形式，根外保留绝对路径。

    :param path: 要表示的路径。
    :param project_root: 项目根，用于判定内外与计算相对路径。
    :return: ``path`` 位于 ``project_root`` 内时返回 ``"@/"`` 加相对
        路径（等于根本身时返回 ``"@/"``）；位于根外时返回绝对路径
        字符串原样。

    .. rubric:: 行为要点

    - 对象（消息 / 快照 / 日志 / 错误文本）中的路径字段统一经本函数
      表示：项目内容用根相对形式（跨机共享语义只对项目内容成立），
      宿主环境路径如实呈现。
    - 不做符号链接解析（``resolve()``），按纯路径分量判断内外；根外
      不做 ``../`` 相对化。

    .. seealso:: :func:`resolve_path` —— 正向解析。
    """
    try:
        rel = path.relative_to(project_root)
    except ValueError:
        return str(path)
    return "@/" + str(rel) if str(rel) != "." else "@/"


# ---------------------------------------------------------------------------
# 候选链探测与身份名推断
# ---------------------------------------------------------------------------


def probe_candidates(base_dir: Path, candidates: Iterable[str]) -> Path | None:
    """按候选名列表逐个探测存在性，返回首个命中者的完整路径；全部未命中返回 ``None``。

    .. rubric:: 功能介绍

    框架内三资源（Agent / Tool / Skill）的目录形态定向查找链共用本
    函数做探测循环：候选名及其顺序是各资源的规则，由调用方构造并
    传入（如 Agent 的 ``AGENT.fya > agent.fya > ...``、Tool 的
    ``TOOL.fya > ...``）；本函数只做「按传入顺序逐个检查 ``base_dir``
    下是否存在，首个命中返回」。

    :param base_dir: 候选名相对的基准目录。
    :param candidates: 候选文件名列表（相对 ``base_dir``），顺序即
        优先级。
    :return: 首个存在者的完整路径；全部未命中返回 ``None`` （不报错，
        「不命中」的处置是调用方职责）。

    .. rubric:: 行为要点

    - 不区分文件 / 目录形态：候选名本身由调用方决定（如 ``TOOL.fya``
      或 ``payment.tool.fya``）。
    - 不做并存告警：同名 ``.fya`` / ``.py`` 并存检测在调用方。

    .. seealso:: :func:`infer_name` —— 命中后的身份名推断。
    """
    for name in candidates:
        candidate = base_dir / name
        if candidate.exists():
            return candidate
    return None


@dataclass(frozen=True)
class NamingRules:
    """路径形态的身份名推断规则表：机制在 :func:`infer_name`，规则内容由各资源提供。

    .. rubric:: 功能介绍

    本类型只承载「哪些文件名是通用名、按什么顺序剥离后缀」这两张表，
    不含推断逻辑。框架内各资源模块在紧邻其候选链声明处定义自己的规则
    常量：Agent 的 ``AGENT_NAMING`` （见 ``flowing.runtime``）、Tool 的
    ``TOOL_NAMING`` （见 ``flowing.tool``）、Skill 的 ``SKILL_NAMING``
    （见 ``flowing.plugins.skills``）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.paths import NamingRules

        TOOL_NAMING = NamingRules(
            suffixes=(".tool.fya", ".fya", ".py"),
            generic_names=frozenset({"TOOL.fya", "TOOL.py", "tool.fya", "tool.py"}),
        )
    """

    suffixes: tuple[str, ...]
    """依次尝试剥离的后缀（排在前面的先匹配，如 ``.agent.fya`` 先于
    ``.fya``）。"""

    generic_names: frozenset[str]
    """通用文件名集合：basename 原样命中（未去后缀）时，身份名取所在
    目录名。"""


def infer_name(path: str | Path, *, naming: NamingRules) -> str:
    """从文件或目录路径推断规范身份名（kebab-case）。

    .. rubric:: 功能介绍

    推断顺序（首个命中者生效）：

    1. basename 命中 ``naming.generic_names`` → 取所在目录名（如
       ``agents/order-agent/agent.fya`` → ``order-agent``）；
    2. 否则按 ``naming.suffixes`` 顺序剥离首个匹配后缀，取剩余部分
       （如 ``pay_agent.py`` → ``pay_agent``）；
    3. 把 ``_`` 替换为 ``-`` 并按 kebab-case 规则校验（``pay_agent``
       → ``pay-agent``；与 :func:`snake_to_kebab` 的转换规则一致，
       但不要求输入是 snake 形态）。

    目录形态传入目录路径本身即可（basename 即目录名，无后缀可剥）。

    :param path: 文件或目录路径。
    :param naming: 规则表（:class:`NamingRules`）。
    :return: 规范身份名（kebab-case）。
    :raises flowing.errors.FormatError: 剥离后为空串（如文件全名恰为
        后缀），或结果不是合法 kebab-case 名时。

    .. rubric:: 行为要点

    - 不做存在性检查（纯字符串运算）。
    - 不判断路径形态：调用方先经 :func:`classify_ref` 判别为路径形态
      后再调用。

    .. seealso:: :class:`NamingRules` —— 规则表；显式 ``as`` 别名优先
        于本推断（``split_as`` 见 :func:`flowing.parser.split_as`）。
    """
    p = Path(path)
    base = p.name
    if base in naming.generic_names:
        stem = p.parent.name
    else:
        stem = base
        for suffix in naming.suffixes:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
    if not stem:
        raise FormatError(f"无法从路径推断身份名: {path}")
    kebab = stem.replace("_", "-")
    if not _KEBAB_RE.match(kebab):
        raise FormatError(f"路径推断结果不是合法身份名: {path}")
    return kebab


# ---------------------------------------------------------------------------
# 命名格式互转（纯格式转换，无资源策略）
# ---------------------------------------------------------------------------

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
"""合法 kebab-case 名：小写字母数字，单连字符分段。"""


def kebab_to_snake(name: str) -> str:
    """kebab-case → snake_case（``payment-agent`` → ``payment_agent``）。

    :param name: 待转换的 kebab-case 名。
    :return: 转换结果。
    :raises flowing.errors.FormatError: 输入不是合法 kebab-case（小写
        字母数字、单连字符分段）时——本函数同时是 kebab 名称校验的兜底。

    .. rubric:: 行为要点

    转换为机械替换（``-`` → ``_``），无其它规则。

    .. seealso:: :func:`snake_to_kebab` —— 逆转换。
    """
    if not _KEBAB_RE.match(name):
        raise FormatError(f"非法 kebab-case 名: {name!r}")
    return name.replace("-", "_")


def snake_to_kebab(name: str) -> str:
    """snake_case → kebab-case（``payment_agent`` → ``payment-agent``）。

    :param name: 待转换的 snake_case 名。
    :return: 转换结果。
    :raises flowing.errors.FormatError: 输入不是小写 snake_case（小写
        字母数字、单下划线分段）时。

    .. rubric:: 行为要点

    转换为机械替换（``_`` → ``-``），无其它规则。

    .. seealso:: :func:`kebab_to_snake` —— 逆转换。
    """
    if not re.match(r"^[a-z0-9]+(_[a-z0-9]+)*$", name):
        raise FormatError(f"非法 snake_case 名: {name!r}")
    return name.replace("_", "-")


def kebab_to_pascal(name: str) -> str:
    """kebab-case → PascalCase（``payment-agent`` → ``PaymentAgent``）。

    :param name: 待转换的 kebab-case 名。
    :return: 转换结果。
    :raises flowing.errors.FormatError: 输入不是合法 kebab-case 时
        （校验规则与 :func:`kebab_to_snake` 相同）。

    .. rubric:: 行为要点

    纯格式转换，不叠加资源后缀策略：「类名始终以 ``Agent`` 结尾」等
    规则由资源装配层在本函数结果上叠加 / 校验（如 ``flowing.compiler``
    的 ``class_name`` 推断：``pay`` → ``PayAgent`` 的补后缀发生在
    装配层，不在本函数）。

    .. seealso:: :func:`pascal_to_kebab` —— 逆转换。
    """
    if not _KEBAB_RE.match(name):
        raise FormatError(f"非法 kebab-case 名: {name!r}")
    return "".join(part.capitalize() for part in name.split("-"))


def pascal_to_kebab(name: str) -> str:
    """PascalCase → kebab-case（``PaymentAgent`` → ``payment-agent``）。

    :param name: 待转换的 PascalCase 名（首字符为大写字母，不含 ``-`` /
        ``_`` / 空白）。
    :return: 转换结果。
    :raises flowing.errors.FormatError: 输入不是合法 PascalCase 时。

    .. rubric:: 行为要点

    - 词边界规则：小写或数字 → 大写处切分；连续大写串（缩写词）在
      「大写 → 大写小写」的收尾处切分——``HTTPClient`` →
      ``http-client``，``PayAgent`` → ``pay-agent``。
    - 纯格式转换，不去除任何资源后缀：``PayAgent`` 转出 ``pay-agent``，
      含 ``agent`` 段（后缀语义属资源层）。

    .. seealso:: :func:`kebab_to_pascal` —— 逆转换。
    """
    if not re.match(r"^[A-Z][A-Za-z0-9]*$", name):
        raise FormatError(f"非法 PascalCase 名: {name!r}")
    kebab = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "-", name)
    return kebab.lower()
