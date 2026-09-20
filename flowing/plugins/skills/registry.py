"""``flowing.plugins.skills.registry`` —— Skill 注册表与注入键。

本模块定义 :data:`skill_registry_key` （provide 注入键）与
:class:`SkillRegistry` （``ns::name`` 全限定键 → 已解析 ``Skill`` 的
注册表）。Skill 定义文件的解析（三种形式统一解析为 :class:`Skill`
实例）也在这里完成（``_parse_skill_*`` 系列内部函数）。

.. seealso:: :mod:`flowing.plugins.skills` （扩展的启用方式与整体契约）、
    :mod:`flowing.plugins.skills.models` （数据对象）
"""


import types
import warnings
from pathlib import Path
from typing import Any

from flowing.errors import (
    FormatError,
    MissingFieldError,
    NameMismatchError,
    SkillNameConflictError,
    SkillNotFoundError,
)
from flowing.params import InjectionKey, expand_args_schema
from flowing.parsable import PENDING, Parsable
from flowing.parser import load_fya_yaml, split_fya
from flowing.paths import (
    classify_ref,
    fnmatch_keys,
    infer_name,
    probe_candidates,
    resolve_path,
    to_project_path,
)
from .models import SKILL_NAMING, CatalogTemplate, Skill


skill_registry_key: InjectionKey["SkillRegistry"] = InjectionKey("skill_registry")
"""全局 ``SkillRegistry`` 实例在 provide 链上的注入键。

``SkillPlugin.install()`` 以该键把注册表 provide 到 Runtime 根（键名
为 ``"skill_registry"``）；``use_skill()`` 经
``agent.inject(skill_registry_key)`` 消费（沿亲代链上溯，命中 Runtime
根级存储）。

.. rubric:: 行为要点

- 同 key 重复 provide 是覆盖更新（后者生效）——插件间靠键名前缀
  约定避免冲突（provide 机制的全局约定）。
- 未安装 ``SkillPlugin`` 时 inject 该键抛
  :class:`flowing.errors.MissingProvideError`。

.. seealso:: :class:`flowing.params.InjectionKey`、:class:`SkillPlugin`、
    :func:`use_skill`、:meth:`flowing.agent.Agent.inject`
"""


class SkillRegistry:
    """Skill 注册表——``ns::name`` 全限定键 → ``Skill`` 的声明期一次性解析。

    .. rubric:: 功能介绍

    Runtime 级单例（``SkillPlugin.install()`` 创建并经
    :data:`skill_registry_key` provide）。持有「规范名 → Skill」缓存；
    ``use_skill()`` 在声明期把 Agent 声明的全部条目（含 disabled）
    一次性解析入缓存，此后 catalog 渲染与 ``skill_load()`` 均为纯内存
    操作（文件发现优先级见模块 docstring「Skill 定义文件与查找规则」）。
    读取与渲染分离：读取不惰性（所有声明的定义文件在声明期一次
    读入，catalog 渲染永不触发文件 IO）；渲染保持动态
    （``description`` / ``content`` 是 Parsable，每次使用时以调用方
    Agent 为上下文现场求值，共享实例上不缓存渲染结果）。

    .. rubric:: 使用示例

    .. code-block:: python

        registry = agent.inject(skill_registry_key)
        skill = registry.get("summarize", source_dir=agent.source_dir())

    通常不直接调用——catalog 渲染与 ``skill_load()`` 内部经它解析。

    .. rubric:: 行为要点

    - ``name`` 已在缓存 → 直接返回共享实例；否则按定向查找优先级
      定位并解析定义文件，缓存后返回。常规路径下声明期
      （``use_skill()``）已把全部声明条目预解析入缓存；未命中分支
      只服务「声明外的编程式按需解析」。
    - 不做目录扫描预热；不做文件变更监听（解析一次即缓存，运行期
      内文件变化不生效）。
    - 同一规范名的并发解析不会发生（解析是同步文件 IO）。
    - 定义文件解析失败 → 异常上抛且不写入缓存（下次引用重试）。
    - ``source_dir`` 为定向查找根：引用方 Agent 定义文件所在目录
      本身（不设 ``skills/`` 默认子目录，与 Agent / Tool 查找根
      对齐）；显式路径 / glob 条目由 ``use_skill`` 在声明期解析为
      规范名与查找根。
    - 不变量：缓存 key 是 ``ns::规范名``；同一 Runtime 内同一全键
      永远解析到同一个 ``Skill`` 实例（跨 Agent 共享）。

    .. seealso:: :class:`Skill`、:meth:`get`、:data:`skill_registry_key`
    """

    _skills: dict[str, Skill]
    """内部存储：``ns::规范名`` → 已解析 / 已注册 ``Skill``。内部 API，
    不属稳定契约。
    """

    catalog_template: CatalogTemplate | None = None
    """Runtime 级默认渲染模板（``SkillPlugin.install()`` 构造注册表时
    注入，即 ``SkillPlugin`` 构造参数的透传落点——三级解析链
    「``use_skill()`` 参数 > 本属性 > 内置 :data:`DEFAULT_CATALOG_TEMPLATE`」
    的中段通道；``None`` 表示未设 Runtime 级默认）。运行期只读。
    """

    def __init__(self, *, catalog_template: CatalogTemplate | None = None,
                 project_root: "Path | None" = None) -> None:
        """构造空注册表（可携带 Runtime 级默认渲染模板）。

        :param catalog_template: Runtime 级默认渲染模板（缺省 ``None``）。
        :param project_root: ``@/`` 引用的解析基准（SkillPlugin.install 时
            传入 Runtime 固化的 ``project_root``，终身有效）；裸注册表
            （测试）为 ``None``——``@/`` 引用报错、其余形态退化。
        """
        self._skills = {}
        self.catalog_template = catalog_template
        self._project_root = project_root

    def register(self, skill: Skill, *, namespace: str | None = None) -> None:
        """编程式注册 Skill 实例（插件随身携带技能的通道）。

        .. rubric:: 功能介绍

        与文件解析通道（:meth:`get`）并列的注册入口：注册表 key 为
        ``ns::规范名`` （``skill.name``），``namespace`` 缺省落入
        ``default::``——裸名引用的注册表视图依次查 ``default::``、
        ``builtin::`` （``default`` 优先，即覆盖通道）；自定义命名空间的
        技能只能以 ``ns::name`` 全限定名引用（只查注册表，不走文件
        查找链）。

        .. rubric:: 行为要点

        - ``ns::name`` 全键冲突 → 抛 :class:`flowing.errors.SkillNameConflictError`
          （后注册者报错，与工具 / agent 类型注册同口径）。
        - 注册只在插件 ``install()`` 发生（运行时不增删）。
        - 不做文件 IO；不校验字段完整性（字段契约是 :class:`Skill`
          构造方的责任）。

        :param skill: 已构造的 ``Skill`` 实例。
        :param namespace: 命名空间；``None`` → ``"default"``。

        .. seealso:: :meth:`get`
        """
        key = f"{namespace or 'default'}::{skill.name}"
        if key in self._skills:
            raise SkillNameConflictError(key)  # 全键重名永远不允许
        self._skills[key] = skill
        skill.registry_key = key   # 落账时回写全键（与 get 的解析通道同口径）

    def glob(self, pattern: str) -> list[str]:
        """注册表键的名字 glob（``skills:`` 条目名字模式的匹配域）。

        委托 :func:`flowing.paths.fnmatch_keys`：裸名模式匹配
        ``default::`` / ``builtin::`` 视图的裸名部分；含 ``::`` 的限定
        模式匹配完整键。返回键排序后的完整键列表（稳定序），零命中返回
        空列表。匹配域是调用时点的注册表快照。
        """
        return fnmatch_keys(pattern, self._skills.keys())

    def get(self, name: str, source_dir: Path | None = None) -> Skill:
        """按规范名取 Skill，未命中缓存时现场解析定义文件。

        .. rubric:: 功能介绍

        注册表唯一入口。``source_dir`` 缺省时为纯注册表查询（裸名
        只查 ``default::`` / ``builtin::``）；提供时裸名先走定向文件
        查找链（文件覆盖注册表）。文件链按定向查找优先级定位（目录内
        ``SKILL.fya`` > ``skill.fya`` > ``<name>.skill.fya`` >
        ``<name>.fya`` > ``SKILL.md`` > ``skill.md`` → 目录外
        ``<name>.skill.fya`` → ``<name>.fya`` → ``<name>.md``，首个
        存在者生效）；定位后先按命中文件所在目录算派生键查注册表
        短路复用，未注册才解析、落账（回写 ``skill.registry_key``）
        后返回。同名 ``.fya`` 系与 ``.md`` 并存 → 告警且 ``.fya`` 系
        优先；命中通用名候选时规范名取目录名；链上顺序只是确定性判定
        规则，不推荐同一链路真的同时存在多个候选文件。

        命名空间：``name`` 含 ``::`` 时为限定名，只查注册表精确键
        （插件注册通道），不走文件查找链；文件解析产物以目录派生命名
        空间落账（``@/`` 下相对、根外绝对、文件夹式取上层目录；规范名
        推断经 :func:`flowing.paths.infer_name`，规则表
        :data:`SKILL_NAMING`）。

        .. rubric:: 行为要点

        - 返回的 ``Skill`` 是注册表共享实例；调用方不得修改。
        - 全部候选位置都不存在合法定义文件 → 抛
          :class:`flowing.errors.SkillNotFoundError` （``detail`` 字段含
          已尝试的查找路径）；定义文件存在但解析失败（缺 ``description``、
          YAML 语法错误等）→ 解析异常上抛，不写入缓存。
        - 不校验 ``name`` 是否被任何 Agent 声明——注册表对声明无感知
          （声明是 ``SkillEntry`` 层的事）。

        :param name: 规范名（裸名 / ``ns::name`` 限定名 / 路径引用）。
        :param source_dir: 定向查找根；``None`` → 纯注册表查询。
        :return: 共享的 ``Skill`` 实例。
        :raises flowing.errors.SkillNotFoundError: 按 ``name`` 在
            ``source_dir`` 下找不到任何合法定义文件（含 ``source_dir``
            缺省时注册表不命中）时。
        :raises flowing.errors.FormatError: 定义文件存在但缺少必填
            字段或格式非法时。

        .. seealso:: :class:`Skill`、:func:`use_skill`
        """
        # 精确键短路（先于形态判别）：文件派生限定键的命名空间含路径特征
        # （目录派生，如 "@/skills::sum"），过不了 classify_ref 的限定名判别
        # ——注册表在场证据优先于词法分流，「catalog 渲染 / skill_load 热路径
        # 必命中缓存」的 docstring 承诺靠此成立（与 ToolRegistry.get 同口径）
        if name in self._skills:
            return self._skills[name]
        if "::" in name:  # 限定名:只查注册表精确键(插件注册通道),不走文件查找链
            raise SkillNotFoundError(name)
        if classify_ref(name) == "path" or source_dir is not None:
            # 路径形态恒走文件定位（@/ 无需 source_dir；./ ../ 缺省时报错，
            # resolve_path 现有口径——与 ToolRegistry.get 同姿态）；裸名 +
            # source_dir 提供：先走定向文件查找链（文件覆盖注册表）——探测
            # 定位（目录内 SKILL.fya > skill.fya > <name>.skill.fya >
            # <name>.fya > SKILL.md > skill.md；目录外 <name>.skill.fya >
            # <name>.fya > <name>.md）后，先按命中文件所在目录算派生键查
            # 注册表：已注册 -> 短路复用现有实例；未注册才解析
            located = _locate_skill_file(name, source_dir,
                                         project_root=self._project_root)
            if located is not None:
                hit, folder_form, _base_dir, _candidates = located
                identity = infer_name(hit, naming=SKILL_NAMING)
                ns_dir = hit.parent.parent if folder_form else hit.parent   # 文件夹式取上层目录
                derived_ns = _derive_namespace(ns_dir, self._project_root)
                derived_key = f"{derived_ns}::{identity}"
                if derived_key in self._skills:
                    return self._skills[derived_key]   # 派生键短路复用（文件解析是声明期行为）
                skill = _parse_skill_file(name, source_dir,
                                          project_root=self._project_root)  # 定位并解析（首个存在者生效）
                # 解析成功才写缓存；解析失败异常上抛且不写入（下次重试）
                key = f"{derived_ns}::{skill.name}"
                self._skills[key] = skill
                skill.registry_key = key   # 回写全键（use_skill 预解析落账 SkillEntry.name_ori 的依据）
                return skill
        # 注册表裸名视图：default:: 优先于 builtin::（default 优先 = 覆盖通道）；
        # source_dir 缺省时跳过文件链、不命中即报错，不做文件探测
        for key in (f"default::{name}", f"builtin::{name}"):
            if key in self._skills:
                return self._skills[key]
        if classify_ref(name) == "path":
            # 路径形态：文件定位未命中（不存在或后缀非法）——name 字段含原引用串
            raise SkillNotFoundError(name, detail="definition file does not exist or has an invalid form")
        attempted = _attempted_paths(name, source_dir)
        raise SkillNotFoundError(
            name, detail="no lookup chain hit (tried: "
            + (", ".join(str(p) for p in attempted) if attempted else "registry bare-name view only")
            + ")")


def _dir_candidates(name: str) -> list[str]:
    """``<name>/`` 目录内的定向查找链（首个存在者生效）：
    ``SKILL.fya > skill.fya > <name>.skill.fya > <name>.fya > SKILL.md >
    skill.md``。内部 API。"""
    return ["SKILL.fya", "skill.fya", f"{name}.skill.fya", f"{name}.fya",
            "SKILL.md", "skill.md"]


def _derive_namespace(ns_dir: Path, project_root: "Path | None" = None) -> str:
    """所在目录 → 派生命名空间字符串（``@/`` 下根相对、根外绝对；无项目根
    （裸注册表）时退化为绝对路径，与「根外绝对」一致）。内部 API。"""
    if project_root is not None:
        return to_project_path(ns_dir, project_root=project_root)
    return str(ns_dir)


def _attempted_paths(name: str, source_dir: "Path | None") -> list[Path]:
    """拼装裸名查找链的已尝试路径列表（供 fail-fast 报文）。内部 API。"""
    if source_dir is None or classify_ref(name) == "path":
        return []
    directory = source_dir / name
    return ([directory / c for c in _dir_candidates(name)]
            + [source_dir / f"{name}{suffix}" for suffix in (".skill.fya", ".fya", ".md")])


def _locate_skill_file(
    ref: str, source_dir: "Path | None", *,
    project_root: "Path | None" = None,
) -> "tuple[Path, bool, Path, list[str]] | None":
    """按定向查找优先级定位 Skill 定义文件（内部 API）。

    返回 ``(命中路径, 是否文件夹式命中, 探测基准目录, 候选名列表)``，
    全部未命中 → ``None`` （裸名语境调用方继续查注册表裸名视图）。

    - 裸名：``<name>/`` 目录内 6 候选（目录存在但无合法定义文件 → 继续
      向下，仅裸名语境）→ 目录外 ``<name>.skill.fya > <name>.fya >
      <name>.md``；
    - 路径形态：``@/`` 锚注册表固化的项目根（无需 ``source_dir``，裸
      注册表无项目根时显式 ``ValueError``——与 ``ToolRegistry.get``
      同姿态）；目录 → 目录内 6 候选，无候选直接 ``FormatError``
      （定点引用的目录为空几乎必为笔误）；直指文件 → 命中即返回。
    """
    if classify_ref(ref) == "path":
        root = project_root
        if root is None and ref.replace("\\", "/").startswith("@/"):
            raise ValueError("@/ path resolution requires a project root (bare SkillRegistry without project_root)")
        resolved = resolve_path(
            ref, project_root=root if root is not None else Path.cwd(),   # 哑根：@/ 已在上方拒绝
            source_dir=source_dir)
        if resolved.is_dir():
            dir_name = infer_name(resolved, naming=SKILL_NAMING)   # 目录：basename 即目录名
            candidates = _dir_candidates(dir_name)
            hit = probe_candidates(resolved, candidates)
            if hit is None:
                # 显式路径语境：目录无候选 -> 直接报错，不继续向下
                raise FormatError(f"explicit path directory has no valid skill entry: {resolved}")
            return hit, True, resolved, candidates
        if resolved.is_file() and (
                resolved.name.endswith(".fya") or resolved.name.endswith(".md")):
            return resolved, False, resolved.parent, [resolved.name]
        return None
    # 裸名（调用方保证 source_dir 非 None）
    assert source_dir is not None
    directory = source_dir / ref
    if directory.is_dir():
        candidates = _dir_candidates(ref)
        hit = probe_candidates(directory, candidates)
        if hit is not None:
            return hit, True, directory, candidates
        # 裸名语境：目录存在但无合法定义文件 -> 继续链上下一项
    candidates = [f"{ref}.skill.fya", f"{ref}.fya", f"{ref}.md"]
    hit = probe_candidates(source_dir, candidates)
    if hit is not None:
        return hit, False, source_dir, candidates
    return None


def _parse_skill_file(name: str, source_dir: Path, *,
                      project_root: "Path | None" = None) -> Skill:
    """按定向查找优先级定位并解析 Skill 定义文件。

    内部 API，不属稳定契约。仅在 :meth:`SkillRegistry.get` 未
    命中缓存时调用；承担三种定义形式（``.md`` frontmatter / ``.skill.fya``
    块结构 / 目录形式）到统一 :class:`Skill` 实例的解析，含 ``$script``
    中 ``on_load`` 的提取与 ``_extra_fields`` 的收纳。
    .. rubric:: 行为要点

    - 定向查找：按规范名 ``name`` 在 ``source_dir`` 下首个存在者生效——
      ``<name>/`` 目录内 ``SKILL.fya > skill.fya > <name>.skill.fya >
      <name>.fya > SKILL.md > skill.md`` （裸名语境目录无合法定义文件时
      继续向下；显式路径目录无候选 → ``FormatError``，定位层分流）→
      目录外 ``<name>.skill.fya > <name>.fya > <name>.md``；``.fya`` 系
      与 ``.md`` 并存 → 告警且 ``.fya`` 系优先；通用名命中 → 规范名取
      目录名。全部不存在 → ``SkillNotFoundError`` （``detail`` 字段含已尝试路径）。
    - 解析：``.md`` 形式为 YAML frontmatter（``description`` 必填，缺 →
      ``MissingFieldError``；``name`` 仅作一致性断言，不符 →
      ``NameMismatchError``）和 Markdown 正文（即 ``content``）；
      ``.skill.fya`` 形式为块结构（``args:`` 经 ``expand_args_schema`` 归一、
      ``content`` / ``description`` 包装 ``Parsable``、未知字段收
      ``_extra_fields``）以及 ``$script`` 提取 ``on_load`` （经
      ``types.MethodType`` 绑定，``self`` 即本 Skill 实例）；目录形式
      正文可 ``{% include %}`` 目录内资料（渲染期经 Parsable include
      加载器；``content`` 的 ``$`` / ``{% include %}`` 基准 = SKILL 定义文件
      所在目录，而非调用方 Agent 的 ``source_dir``）。

    .. seealso:: :meth:`SkillRegistry.get`
    """
    located = _locate_skill_file(name, source_dir, project_root=project_root)
    if located is None:
        attempted = _attempted_paths(name, source_dir)
        raise SkillNotFoundError(
            name, detail="no valid skill definition file found (tried: "
            + (", ".join(str(p) for p in attempted) if attempted else str(name))
            + ")")
    hit, _folder_form, base_dir, candidates = located
    identity = infer_name(hit, naming=SKILL_NAMING)   # 通用名命中 -> 规范名取目录名
    if hit.name.endswith(".fya"):
        # .fya 系与同名 .md 并存 -> 告警且 .fya 系优先（链上顺序已保证优先，
        # 此处只补告警——与 Tool 的「.fya 优先于同名 .py 并告警」同口径）
        coexisting = [c for c in candidates
                      if c.endswith(".md") and (base_dir / c).exists()]
        if coexisting:
            warnings.warn(
                f"a same-name .fya family and .md coexist; the .fya family wins: {hit}"
                f" (coexisting: {', '.join(coexisting)})")
        return _parse_skill_fya(hit, identity)
    return _parse_skill_md(hit, identity)


def _split_frontmatter(text: str) -> "tuple[str | None, str]":
    """``.md`` 文本 → ``(frontmatter YAML 文本, Markdown 正文)``。

    内部 API。首行必须是 ``---`` 独占行（仅 rstrip CR/LF——与
    ``flowing.parser.split_fya`` 的分隔符口径一致），否则视为无
    frontmatter（``(None, 原文)``——``description`` 必填检查随后自然
    fail-fast）。
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:]).strip("\n")
    return None, text   # 无闭合 ---：视为无 frontmatter（缺 description 报错）


def _parse_skill_md(path: Path, identity: str) -> Skill:
    """``.md`` 形式解析：YAML frontmatter 与 Markdown 正文。内部 API。

    ``description`` 必填（缺 → ``MissingFieldError``）；``name`` 写了仅作
    一致性断言（不符 → ``NameMismatchError``）；``_`` （PENDING）值视同
    未声明；其余字段收 ``_extra_fields``；``.md`` 形式无 ``args_schema``
    与 ``on_load`` （前者为空 dict，后者为 None）。
    """
    frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    fields = load_fya_yaml(frontmatter) if frontmatter is not None else {}
    explicit_name = fields.get("name")
    if (explicit_name is not None and explicit_name is not PENDING
            and explicit_name != identity):
        raise NameMismatchError(explicit_name, identity, str(path))
    description = fields.get("description")
    if description is None or description is PENDING:
        # MissingFieldError 的第二参按契约是「所属 Agent 类型名」；Skill 无
        # Agent 类型语境，以定义文件路径充当场定位（spec 未规定 skill 语境的取值）
        raise MissingFieldError("description", str(path))
    extra = {k: v for k, v in fields.items() if k not in ("name", "description")}
    return Skill(name=identity, description=Parsable(description),
                 # content 以 SKILL 定义文件目录为 $/include 基准（F2）：
                 # `content: $./notes.md` 解析到同目录 notes.md
                 content=Parsable(body, source_dir=path.parent),
                 _extra_fields=extra)


_SKILL_FYA_RESERVED = frozenset({"name", "description", "content", "args"})
"""``.skill.fya`` 保留字段集——其余字段原样收 ``_extra_fields`` （与
Agent ``_extra`` 的宽松解析策略同构）。"""


def _parse_skill_fya(path: Path, identity: str) -> Skill:
    """``.skill.fya`` 形式解析：块结构与 ``$script`` 提取 ``on_load``。内部 API。

    ``args:`` 块经 ``expand_args_schema`` 归一化为 JSON Schema properties
    （值为 ``_``/PENDING 的参数归一为 ``{}``——any 类型、无默认值、必填，
    覆盖义务原样保留）；具名块（``$script`` 以外）不支持——Skill 正文经
    ``content`` 字段声明，写具名块几乎必为笔误，fail-fast。
    """
    raw = split_fya(path.read_text(encoding="utf-8"))
    if raw.blocks:
        raise FormatError(
            f"{path} contains named blocks unsupported by Skill definitions: {sorted(raw.blocks)}"
            " (Skill body is declared via the content field; $script is only for defining on_load)")
    fields = load_fya_yaml(raw.yaml_text)
    explicit_name = fields.get("name")
    if (explicit_name is not None and explicit_name is not PENDING
            and explicit_name != identity):
        raise NameMismatchError(explicit_name, identity, str(path))
    description = fields.get("description")
    if description is None or description is PENDING:
        raise MissingFieldError("description", str(path))
    content = fields.get("content")
    if content is None or content is PENDING:
        raise MissingFieldError("content", str(path))
    raw_args = fields.get("args")
    if raw_args is not None and not isinstance(raw_args, dict):
        raise FormatError(f"args field of {path} must be a mapping: {raw_args!r}")
    args_schema = expand_args_schema(
        {k: ({} if v is PENDING else v) for k, v in (raw_args or {}).items()})
    on_load = None
    if raw.script is not None:
        namespace: dict[str, Any] = {}
        # $script 是声明式定义面，与 Agent .fya 的 $script 同一信任级
        exec(compile(raw.script, str(path), "exec"), namespace)  # noqa: S102
        fn = namespace.get("on_load")
        if fn is not None and not callable(fn):
            raise FormatError(f"on_load in $script of {path} is not callable")
        on_load = fn
    extra = {k: v for k, v in fields.items() if k not in _SKILL_FYA_RESERVED}
    skill = Skill(name=identity, description=Parsable(description),
                  # content 以 SKILL 定义文件目录为 $/include 基准（F2）：
                  # `content: $./notes.md` 解析到同目录 notes.md，而不是调用方
                  # Agent 目录
                  content=Parsable(content, source_dir=path.parent),
                  args_schema=args_schema,
                  _extra_fields=extra)
    if on_load is not None:
        # 描述符绑定：self 即本 Skill 实例，框架以 (agent, args) 调用
        skill.on_load = types.MethodType(on_load, skill)
    return skill
