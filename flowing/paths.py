"""``flowing.paths`` —— 资源引用与路径词汇：前缀、形态判别、解析、命名格式。

.. rubric:: 功能介绍

本模块是框架的**叶子词汇模块**：集中定义「资源引用字符串」的全部词法规则——

- **路径前缀表**（:data:`PATH_PREFIXES`）：``./`` ``../`` ``@/`` 绝对路径，
  全框架唯一权威来源（``parsable`` 的 ``$`` 引用、各 registry 的候选链、
  ``Runtime.resolve_path`` 共用同一组前缀）；
- **引用形态判别**（:func:`classify_ref`）：路径 / 限定名（``ns::name``）/
  裸名 三分——只回答「这个字符串长什么样」，不回答「去哪找」；
- **路径解析与表示**（:func:`resolve_path` / :func:`to_project_path`）：
  前缀 → ``Path`` 的纯函数版（``Runtime.resolve_path`` 为薄方法委托）；
- **候选链探测**（:func:`probe_candidates`）：按调用方给定的候选名列表
  逐个探测存在性——链内容是各资源的规则（留各模块），探测循环只有一份；
- **身份名推断**（:class:`NamingRules` / :func:`infer_name`）：文件名/目录名
  → 规范身份名（去后缀、通用名取目录名、snake→kebab）；
- **命名格式互转**：kebab / snake / Pascal 三格式双向纯转换
  （:func:`kebab_to_snake` 等四函数）。

.. rubric:: 引用形态判定强调（重要）

- 任一资源引用字符串，只要**不含 ``::`` 且含 ``/`` 或反斜杠字符**，
  一律判为路径（``"path"``），以**引用方的 ``source_dir``** 为基准解析；
  不再要求必须写 ``./`` 前缀。
- 判定顺序固定：路径前缀（``./`` / ``../`` / ``@/``）与绝对路径优先；
  其次含 ``::`` → 限定名（``ns::name``）；再次含 ``/`` 或反斜杠字符 →
  路径；最后才是裸名。
- 因此 ``agents/live2d-controller`` 在 Agent 的 ``tools:`` / ``subagents:`` /
  ``skills:`` 中按“该 Agent 定义文件的亲路径”解析。

.. rubric:: 设计动机

路径前缀表曾同时出现在 ``parsable``（``$`` 引用）与 ``Runtime.resolve_path``
两处文档；候选链探测循环在 tool / skills / agent 三处近乎克隆；命名转换
（snake→kebab、kebab→Pascal）散见各资源推断规则。词汇一旦漂移，三资源
「对齐」的裁决就名存实亡。本模块把**词汇**收为一处；**策略**（链内容、
后缀策略、通道选择）仍留各资源模块——见「边界」。

.. rubric:: 模块定位与分层

- 叶子模块：仅依赖 :mod:`flowing.errors` 与 stdlib，不依赖任何其它
  flowing 模块；被 ``parser`` / ``runtime`` / ``tool`` / ``skills`` /
  ``parsable`` 向下引用。
- ``Runtime.resolve_path`` / ``Runtime.to_project_path`` 保留为公开方法，
  体内委托本模块纯函数并注入 ``project_root``——公开 API 不变。

.. rubric:: 边界（非目标）

- 不做文件读取、注册表查找、glob 展开（调用方职责）；
- 不做 Parsable 渲染（``parsable`` 辖区）；
- 不含资源语义：后缀策略（如类名必以 ``Agent`` 结尾）由资源层在本模块
  转换结果上自行叠加；
- 不涉及 flowing 自身配置读取（M-64 分层约定，见 :func:`resolve_path`）。

.. seealso::

    - :mod:`flowing.parser` —— ``.fya`` 文本字面层，本模块的直接上游消费者。
    - :meth:`flowing.runtime.Runtime.resolve_path` —— 委托方法（注入上下文）。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

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
"""相对路径前缀表（绝对路径经 ``PurePath.is_absolute()`` 另行判定——
POSIX 前导 ``/`` 与 Windows 盘符（``C:\\``）/ UNC（``\\\\``）均算，
不在表内）。

全框架唯一权威来源：``parsable`` 的 ``$`` 引用与 ``{% include %}``、
:func:`classify_ref` 的路径形态判定、``Runtime.resolve_path`` 共用本表。
``~`` 是合法前缀：**永远按绝对路径触发**——``~`` / ``~/...`` /
``~\\...`` 经 ``os.path.expanduser`` 展开为当前用户家目录（跨平台：
POSIX 展开为 ``$HOME``，Windows 展开为 ``%USERPROFILE%``；展开结果
即绝对路径，之后的处理与绝对路径同规则）。「不写 ``~``」是编码规范
层面的约定，不是框架的内置限制（P5 裁决）。

**Windows 分隔符兼容**：前缀判定中 ``\\`` 与 ``/`` 等价——``.\\``
视同 ``./``、``..\\`` 视同 ``../``（多级同理）、``~\\`` 视同
``~/``；实现侧先做分隔符归一再判定前缀。
"""

_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
"""Windows 盘符绝对路径判定（分隔符归一后的形态，如 ``C:/x``）。

POSIX 上 ``pathlib`` 不把盘符 / UNC 识别为绝对路径；按 PATH_PREFIXES
docstring「Windows 盘符 / UNC 均算绝对路径」的跨平台约定，实现侧在
归一化后用本正则显式判定（UNC 归一后为 ``//`` 前缀，由前导 ``/``
判定覆盖）。
"""


def classify_ref(raw: str) -> Literal["path", "qualified", "bare"]:
    """引用字符串的形态判别：路径 / 限定名 / 裸名。

    .. rubric:: 功能介绍

    纯词法判定，**不做通道选择**（「去哪找」是各 registry 与装配层的
    职责）。判定顺序固定，**路径前缀优先**：

    1. 命中 :data:`PATH_PREFIXES` 任一项或前导 ``/``（绝对路径）→
       ``"path"``——整体视为路径字符串，**内部不再做任何格式解析**
       （路径中出现 ``::`` 只是路径字符）；
    2. 含 ``::`` → 看左段（第一个 ``::`` 之前）：左段含路径特征
       （``/`` / 反斜杠 / 以 ``.py`` 结尾）→ ``"path"``——
       **``文件::类名`` 形态**（R21：多 Agent 子类文件的消歧引用，
       如 ``./agents.py::OrderAgent``；切分与类名选择语义在调用方，
       见 ``Runtime.get_agent_class``）；左段为纯标识符 →
       ``"qualified"``（限定名 ``ns::name``；切分语义见
       :func:`flowing.parser.normalize_entries`，按第一个 ``::`` 切）；
    3. 含 ``/`` 或反斜杠字符（且无 ``::``）→ ``"path"``——普通相对路径，
       解析时以引用方的 ``source_dir`` 为基准；
    4. 其余 → ``"bare"``（裸名）。

    .. rubric:: 设计动机

    形态判别规则必须单点维护：``parser.normalize_entries``（别名推断）
    与三处 registry（``ToolRegistry.get`` / ``SkillRegistry.get`` /
    ``Runtime.get_agent_class`` 的分流）消费同一条规则。本模块是最低层
    消费者，规则放这里，高层向下 import，依赖图无环。

    .. rubric:: 使用示例

    .. code-block:: python

        classify_ref("./a/payment")        # "path"
        classify_ref("@/shared/x.fya")     # "path"
        classify_ref("builtin::web-search")  # "qualified"
        classify_ref("./agents.py::OrderAgent")  # "path"（文件::类名，R21）
        classify_ref("payment")            # "bare"
        classify_ref("agents/live2d-controller")  # "path"（含 / 且无 ::）
        classify_ref("subagents\\live2d")        # "path"（含反斜杠且无 ::）

    .. rubric:: 行为规约

    - 边缘情况：``a::b::c`` 判为 ``"qualified"``（按第一个 ``::`` 切的
      语义在调用方；本函数只命名形态，多 ``::`` 不报错——后续查找必然
      不命中，由查找层报错）。
    - 边缘情况：``a::b/c`` 含 ``::``，判为 ``"qualified"``（不是路径）。
    - 边缘情况：裸 ``@``（不带斜杠）不命中路径形态，落入 ``"bare"``
      （几乎必为笔误）。
    - 非行为：不校验裸名/限定名的字符集（名称校验在各资源装配层）。

    .. rubric:: 测试案例

    - 前置：``"./x"`` / ``"../x"`` / ``"@/x"`` / ``"/abs/x"`` → 期望：
      均 ``"path"``；``"./a::b"`` → 期望：``"path"``（路径内 ``::``
      不解析）。
    - 前置：``"ns::name"`` → 期望：``"qualified"``；``"pay"`` →
      期望：``"bare"``。

    .. rubric:: 调用关系（审计）

    - 调用：无（纯字符串判定，读取 :data:`PATH_PREFIXES`）
    - 被调：``flowing.parser.normalize_entries``（别名推断前的形态
      判定）；``ToolRegistry.get`` /
      ``SkillRegistry.get`` / ``Runtime.get_agent_class``
      （引用分流，时机：各查找链入口）

    .. seealso:: :func:`resolve_path` —— ``"path"`` 形态的实际解析。
    """
    # X4：docstring 承诺而 spec 骨架缺失的三件事在此落实——
    # 先做分隔符归一（\ → /），再 ~ 前缀判定，再按固定顺序执行形态判别。
    s = raw.replace("\\", "/")
    if s == "~" or s.startswith("~/"):
        return "path"  # ~ 永远按绝对路径触发（PATH_PREFIXES docstring，P5 裁决）
    # Windows 盘符 / UNC 在前缀判定中与 POSIX 绝对路径同列（先归一后判定）
    if s.startswith(PATH_PREFIXES) or s.startswith("/") or _DRIVE_RE.match(s):
        return "path"
    if "::" in s:
        # 文件::类名 分支（X4）：左段含路径特征（/ 或以 .py 结尾）→ path
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
    """路径前缀规则的纯函数执行器：``@/`` ``./`` ``../`` 绝对路径 → ``Path``。

    .. rubric:: 功能介绍

    前缀语义：``@/`` → ``project_root``；``./`` → ``source_dir``；
    ``../`` → ``source_dir.parent``，多级 ``../../`` 逐级向上；绝对路径
    原样接受；**裸名不走本函数**（名称查找属注册表/装配层）。

    **根内相对不变量**（M-64 最终裁决）：解析结果越出 ``project_root``
    合法（绝对路径或 ``../`` 逃逸均可）；对外表示经 :func:`to_project_path`
    分两种——根内一律根相对形式（``@/a/b``），根外保留绝对路径。

    .. rubric:: 设计动机

    不引入 ``ProjectPath`` 类型——路径解析就是字符串前缀判断，避免与
    ``pathlib.Path`` 互操作复杂度。提取为纯函数是为了让 parser / 装配层
    在无 Runtime 实例的上下文（如显式编译期）也能执行同一规则；运行期
    入口仍是 ``Runtime.resolve_path`` 薄方法。

    .. rubric:: 使用示例

    .. code-block:: python

        resolve_path("@/tools/search.py", project_root=Path("/proj"))
        resolve_path("./subagents/", project_root=root, source_dir=agent_dir)

    .. rubric:: 行为规约

    - ``./`` / ``../`` 前缀且未提供 ``source_dir`` → :class:`ValueError`。
    - 适用面：**仅 flowing 项目资源引用**（``.fya`` 的 ``$``、
      ``{% include %}``、``tools:`` / ``skills:`` / ``subagents:`` 等
      路径字段）；**不涉及 flowing 自身配置读取**（``providers.yaml`` /
      XDG 用户级配置由宿主启动层直接读取，M-64 分层约定）。
    - 输入语法：``@/`` / ``./`` / ``../`` / 绝对路径（``is_absolute``
      判定，POSIX 前导 ``/`` 与 Windows 盘符 / UNC 均算），以及含 ``/``
      或反斜杠字符的普通相对路径（以 ``source_dir`` 为基准）；前缀判定
      中 ``\\`` 与 ``/`` 等价（``.\\`` 视同 ``./``、``..\\`` 视同
      ``../``，多级同理——实现侧先做分隔符归一）；``~`` /
      ``~/...`` / ``~\\...`` **永远按绝对路径触发**——经
      ``os.path.expanduser`` 展开为家目录（跨平台：POSIX ``$HOME`` /
      Windows ``%USERPROFILE%``）后按绝对路径规则处理（对象表示：
      根外保留绝对路径，M-64）。「不写 ``~``」是编码规范约定，不是
      内置限制（P5 裁决）。
    - 边缘情况：``@/`` 不带后续路径段表示**根目录本身**（``pathlib``
      吸收空段的自然结果，无需特判）。
    - 非行为：不做存在性检查（解析 ≠ 打开）；不做 glob 展开（展开由
      调用方）。

    .. rubric:: 测试案例

    - 前置：``project_root=/proj`` → 期望：
      ``resolve_path("@/a/b", project_root=...) == Path("/proj/a/b")``。
    - 前置：``source_dir=None`` → 操作：``resolve_path("./x", ...)`` →
      期望：``ValueError``。
    - 前置：``source_dir=/proj/ag`` → 期望：``resolve_path("../../x",
      project_root=..., source_dir=...) == Path("/x")``（越出根合法）。

    .. rubric:: 调用关系（审计）

    - 调用：无（字符串前缀判断 + ``pathlib`` 拼接）
    - 被调：``Runtime.resolve_path``（委托，注入 ``project_root``，
      时机：运行期一切路径解析）

    .. seealso:: :func:`to_project_path` —— 逆向的对外表示。
    """
    # X4：docstring 承诺而 spec 骨架缺失的两件事在此落实——
    # 实现侧先做分隔符归一（\ → /），再做 ~ 检测与 expanduser。
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
    """``Path`` → 对外表示：根内 ``@/a/b``，根外保留绝对路径。

    .. rubric:: 功能介绍

    M-64「根内相对不变量」的表示侧：对象（消息 / 快照 / 日志 / 错误
    文本）中的路径表示统一经本函数——项目内容用根相对形式（跨机共享
    语义只对项目内容成立），宿主环境路径如实呈现。

    .. rubric:: 行为规约

    - ``path`` 位于 ``project_root`` 内 → ``"@/" + 相对路径``；
      等于根本身 → ``"@/"``。
    - 根外 → 绝对路径字符串原样（不做 ``../`` 相对化）。
    - 非行为：不做符号链接解析（``resolve()``）；按纯路径分量判断
      内外。

    .. rubric:: 测试案例

    - 前置：``project_root=/proj`` → 期望：
      ``to_project_path(Path("/proj/a/b"), ...) == "@/a/b"``；
      ``to_project_path(Path("/etc/x"), ...) == "/etc/x"``。

    .. rubric:: 调用关系（审计）

    - 调用：无
    - 被调：``Runtime.to_project_path``（委托）；消息/快照/日志中
      路径字段的序列化点

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
    """按候选名列表逐个探测存在性，返回首个命中者的完整路径。

    .. rubric:: 功能介绍

    三资源候选链（Agent 的 ``AGENT.fya > agent.fya > ...``、Tool 的
    ``TOOL.fya > ...``、Skill 的 ``SKILL.fya > ...``）共享的**探测循环**。
    链内容（候选名列表及其优先级）是各资源的规则，由调用方构造并传入；
    本函数只做「逐个 ``exists()``，首个命中返回」。

    .. rubric:: 设计动机

    探测循环曾将在 tool / skills / agent 三处克隆；链内容仍然分散
    （它们是资源规则），但「按序探测、首个命中」的机械行为只有一份，
    杜绝「同优先级不同循环写法」的隐性漂移。

    .. rubric:: 行为规约

    - 候选名为**相对名**（相对 ``base_dir``）；按传入序探测，顺序即
      优先级（调用方负责稳定序）。
    - 全部未命中 → 返回 ``None``（不报错——「不命中」的处置是调用方
      职责：裸名继续走目录外链、显式路径抛 ``FormatError`` 等）。
    - 非行为：不区分文件/目录形态（调用方的候选名本身区分）；不做
      并存告警（同名 ``.fya``/``.py`` 并存检测在调用方）。

    .. rubric:: 测试案例

    - 前置：目录含 ``payment/TOOL.fya`` → 操作：
      ``probe_candidates(payment_dir, ["TOOL.fya", "payment.tool.fya"])``
      → 期望：返回 ``payment/TOOL.fya``。
    - 前置：无候选存在 → 期望：``None``。

    .. rubric:: 调用关系（审计）

    - 调用：``Path.exists``（逐候选）
    - 被调：``ToolRegistry.get`` / ``SkillRegistry.get``
      / ``Runtime.get_agent_class``（时机：目录形态候选链探测）

    .. seealso:: :func:`infer_name` —— 命中后的身份名推断。
    """
    for name in candidates:
        candidate = base_dir / name
        if candidate.exists():
            return candidate
    return None


@dataclass(frozen=True)
class NamingRules:
    """路径形态的身份名推断规则表——机制在 :func:`infer_name`，内容由各资源提供。

    .. rubric:: 功能介绍

    「通用文件名取目录名」的具体表是各资源的规则（Agent 的
    ``agent.fya``/``AGENT.fya``、Tool 的 ``TOOL.fya``/``tool.py``、
    Skill 的 ``SKILL.fya``/``skill.md`` 等），本类型只是承载：
    各资源模块在紧邻其候选链声明处定义常量（``AGENT_NAMING`` /
    ``TOOL_NAMING`` / ``SKILL_NAMING``），规约与实现同源。

    .. rubric:: 使用示例

    .. code-block:: python

        TOOL_NAMING = NamingRules(
            suffixes=(".tool.fya", ".fya", ".py"),
            generic_names=frozenset({"TOOL.fya", "TOOL.py", "tool.fya", "tool.py"}),
        )
    """

    suffixes: tuple[str, ...]
    """依次尝试剥离的后缀（排在前面的先匹配，如 ``.agent.fya`` 先于
    ``.fya``）。"""

    generic_names: frozenset[str]
    """通用文件名集合——basename 原样命中（**未去后缀**）时，身份名取
    所在目录名。"""


def infer_name(path: str | Path, *, naming: NamingRules) -> str:
    """路径 → 规范身份名（kebab-case）。

    .. rubric:: 功能介绍

    推断顺序（首个命中者生效）：

    1. basename 命中 ``naming.generic_names`` → 取**目录名**；
    2. 否则按 ``naming.suffixes`` 顺序剥离首个匹配后缀，取剩余部分；
    3. 结果经 :func:`snake_to_kebab` 规范化。

    目录形态传入目录路径本身（basename 即目录名，无后缀可剥）。

    .. rubric:: 行为规约

    - 剥离后为空串（如文件全名恰为后缀）→ :class:`FormatError`。
    - 非行为：不做存在性检查（纯字符串运算）；不判断路径形态
      （调用方经 :func:`classify_ref` 判定后调用）。

    .. rubric:: 测试案例

    - 前置：``naming = AGENT_NAMING`` → 期望：
      ``infer_name("agents/order-agent/agent.fya")`` == ``"order-agent"``
      （通用名取目录名）；``infer_name("pay_agent.py")`` == ``"pay-agent"``
      （去 ``.py`` + snake→kebab）。

    .. rubric:: 调用关系（审计）

    - 调用：无（规范化 = ``_``→``-`` 替换 + kebab 校验，与
      :func:`snake_to_kebab` 同规则但不限输入为 snake）
    - 被调：``flowing.parser.normalize_entries``（路径形态条目的别名
      推断）；各资源装配层的身份名推断（声明期）

    .. seealso:: :class:`NamingRules` —— 规则表；:func:`split_as` 见
      ``flowing.parser``（显式 ``as`` 别名优先于本推断）。
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

    .. rubric:: 行为规约

    - 输入须为合法 kebab-case（见 :data:`_KEBAB_RE`），否则
      :class:`FormatError`——本函数同时是名称校验的兜底。
    - 转换为机械替换（``-`` → ``_``），无其它规则。

    .. rubric:: 测试案例

    - ``kebab_to_snake("payment-agent") == "payment_agent"``；
      ``kebab_to_snake("Pay")`` → ``FormatError``。

    .. rubric:: 调用关系（审计）

    - 被调：候选链构造（``<name_snake>.py`` 候选名生成，声明期）

    .. seealso:: :func:`snake_to_kebab` —— 逆转换。
    """
    if not _KEBAB_RE.match(name):
        raise FormatError(f"非法 kebab-case 名: {name!r}")
    return name.replace("-", "_")


def snake_to_kebab(name: str) -> str:
    """snake_case → kebab-case（``payment_agent`` → ``payment-agent``）。

    .. rubric:: 行为规约

    - 输入须为小写 snake_case（``[a-z0-9]+(_[a-z0-9]+)*``），否则
      :class:`FormatError`。
    - 转换为机械替换（``_`` → ``-``）。

    .. rubric:: 测试案例

    - ``snake_to_kebab("payment_agent") == "payment-agent"``；
      ``snake_to_kebab("")`` → ``FormatError``。

    .. rubric:: 调用关系（审计）

    - 被调：:func:`infer_name`（文件名 → 身份名规范化的最后一步）；
      Tool 打标函数名推断（``tool.py`` 打标通道）

    .. seealso:: :func:`kebab_to_snake` —— 逆转换。
    """
    if not re.match(r"^[a-z0-9]+(_[a-z0-9]+)*$", name):
        raise FormatError(f"非法 snake_case 名: {name!r}")
    return name.replace("_", "-")


def kebab_to_pascal(name: str) -> str:
    """kebab-case → PascalCase（``payment-agent`` → ``PaymentAgent``）。

    .. rubric:: 行为规约

    - 输入校验同 :func:`kebab_to_snake`。
    - 纯格式转换：**后缀策略不在此**——「类名始终以 ``Agent`` 结尾」
      等规则由资源装配层在本函数结果上叠加/校验。

    .. rubric:: 测试案例

    - ``kebab_to_pascal("pay-agent") == "PayAgent"``；
      ``kebab_to_pascal("pay") == "Pay"``（补 ``Agent`` 后缀是调用方的事）。

    .. rubric:: 调用关系（审计）

    - 被调：Agent 装配层 ``class_name`` 推断（``.fya`` 未显式声明时）

    .. seealso:: :func:`pascal_to_kebab` —— 逆转换。
    """
    if not _KEBAB_RE.match(name):
        raise FormatError(f"非法 kebab-case 名: {name!r}")
    return "".join(part.capitalize() for part in name.split("-"))


def pascal_to_kebab(name: str) -> str:
    """PascalCase → kebab-case（``PaymentAgent`` → ``payment-agent``）。

    .. rubric:: 行为规约

    - 词边界规则：小写/数字 → 大写 处切分；连续大写串（缩写词）在
      「大写 → 大写小写」的收尾处切分——``HTTPClient`` →
      ``http-client``，``PayAgent`` → ``pay-agent``。
    - 输入首字符须为大写字母且不含 ``-``/``_``/空白，否则
      :class:`FormatError`。
    - 纯格式转换，不去除任何资源后缀（``PayAgent`` 转出
      ``pay-agent``，**含** ``agent`` 段——后缀语义属资源层）。

    .. rubric:: 测试案例

    - ``pascal_to_kebab("PaymentAgent") == "payment-agent"``；
      ``pascal_to_kebab("HTTPClient") == "http-client"``；
      ``pascal_to_kebab("paymentAgent")`` → ``FormatError``（非 Pascal）。

    .. rubric:: 调用关系（审计）

    - 被调：手写 Agent 子类省略 ``name`` 时的类名 kebab 化推断
      （定义期，见 ``flowing.agent.Agent`` 的 ``name`` 类属性）

    .. seealso:: :func:`kebab_to_pascal` —— 逆转换。
    """
    if not re.match(r"^[A-Z][A-Za-z0-9]*$", name):
        raise FormatError(f"非法 PascalCase 名: {name!r}")
    kebab = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "-", name)
    return kebab.lower()
