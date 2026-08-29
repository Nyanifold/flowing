"""Skill 注册表与注入键（skill_registry_key / SkillRegistry / _parse_skill_file）。"""


import types
import warnings
from pathlib import Path
from typing import Any

from flowing.errors import (
    FlowingError,
    FormatError,
    MissingFieldError,
    NameMismatchError,
)
from flowing.params import InjectionKey, expand_args_schema
from flowing.parsable import PENDING, Parsable
from flowing.parser import load_fya_yaml, split_fya
from flowing.paths import classify_ref, infer_name, probe_candidates, resolve_path
from .models import SKILL_NAMING, CatalogTemplate, Skill


skill_registry_key: InjectionKey["SkillRegistry"] = InjectionKey("skill_registry")
"""功能/动机：全局 ``SkillRegistry`` 实例在 provide 链上的注入键。
``SkillPlugin.install()`` 以该键 provide 注册表；``use_skill()`` 经
``agent.inject(skill_registry_key)`` 消费（插件约定 R2：协作不查询，
安装顺序无关）。

行为边界：键名为 ``"skill_registry"``；同名 provide key 冲突时后注册者
报错（框架全局约定）。未安装 ``SkillPlugin`` 时 inject 该键抛
``MissingProvideError``。

.. seealso:: :class:`flowing.params.InjectionKey`、:class:`SkillPlugin`、
:func:`use_skill`、:meth:`flowing.agent.Agent.inject`
"""


class SkillRegistry:
    """Skill 注册表——``ns::name`` 全限定键 → ``Skill``，声明期一次性解析（M-98 最终裁决）。

    .. rubric:: 功能介绍

    Runtime 级单例（``SkillPlugin.install()`` 创建并经
    :data:`skill_registry_key` provide）。持有「规范名 → Skill」缓存；
    ``use_skill()`` 在声明期把 Agent 声明的**全部**条目（含 disabled）
    一次性解析入缓存，此后 catalog 渲染与 ``skill_load()`` 均为纯内存操作
    （文件发现优先级见模块 docstring 专属角度一）。

    .. rubric:: 设计动机

    M-98 最终裁决：读取时间与渲染时间分离——**读取不惰性**（所有声明的
    Skill 定义文件在 ``use_skill()`` 时一次读入；Skill 文件都是小文本，
    惰性省下的 IO 可忽略，换来的是 catalog 渲染永不触发文件 IO）；
    **渲染保持动态**（``description`` / ``content`` 是 Parsable，每次
    使用时以调用方 Agent 为上下文现场求值，共享实例上不缓存渲染结果）。
    ``disabled`` 纯为渲染时概念（与 Tool / Subagent 条目一致）：只影响
    catalog 与 LLM 可见性，不影响文件读取与编程式加载。

    .. rubric:: 使用示例

    .. code-block:: python

        registry = agent.inject(skill_registry_key)
        skill = registry.get("summarize", source_dir=agent_source_dir)

    通常不直接调用——catalog 渲染与 ``skill_load()`` 内部经它解析。

    .. rubric:: 行为规约

    - 期待行为：``name`` 已在缓存 → 直接返回共享实例；否则按定向查找
      优先级定位并解析定义文件，缓存后返回。常规路径下声明期
      （``use_skill()``）已把全部声明条目预解析入缓存，本方法命中缓存；
      未命中分支只服务「声明外的编程式按需解析」。
    - 非行为：不做目录扫描预热；不做文件变更监听（解析一次即缓存，
      运行期内文件变化不生效）。
    - 边缘情况：同一规范名并发解析（单事件循环下不会发生——解析是同步
      文件 IO）；定义文件解析失败 → 异常上抛且**不写入缓存**（下次引用
      重试）。
    - 前置条件：``source_dir`` 为定向查找根（约定：引用方 Agent 定义文件
      所在目录**本身**——不设 ``skills/`` 默认子目录，与 Agent/Tool 查找根
      对齐；显式路径/glob 条目由 ``use_skill`` 在声明期解析为规范名 +
      查找根）。
    - 不变量：缓存 key 是 ``ns::规范名``；同一 Runtime 内同一全键永远
      解析到同一个 ``Skill`` 实例（跨 Agent 共享）。

    .. rubric:: 测试案例

    - 前置：空注册表。操作：连续两次 ``get("sum", d)``。期望：
      两次返回**同一对象**（``is`` 相等），定义文件只被读取一次。
    - 前置：``use_skill()`` 声明 ``skills: [a, b]``（一 enabled 一
      disabled）完成。操作：检查注册表。期望：``a`` / ``b`` 均已解析入
      缓存（M-98：读取不惰性，disabled 是渲染时概念，不影响读取）。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.plugins.skills._parse_skill_file()``（时机：
      ``get()`` 缓存未命中分支）
    - 被调：``flowing.plugins.skills.use_skill()``（声明期预解析全部
      声明条目）；``flowing.plugins.skills._load_skill()`` 契约第 2 步
      （时机：每次 skill_load）；``LazySkillsPrompt.resolve()``（时机：
      每次 catalog 渲染）——均经 ``get()``；实例经
      ``agent.inject(skill_registry_key)`` 取得（``use_skill()`` 第 1 步）
    - 实例化方：``flowing.plugins.skills.SkillPlugin.install()``（时机：
      ``runtime.use()`` 阶段一，每次安装）

    .. seealso::

        - :class:`Skill`、:meth:`get`、:data:`skill_registry_key`
    """

    _skills: dict[str, Skill]
    """内部存储：``ns::规范名`` → 已解析/已注册 ``Skill``（命名空间规则见
    ``flowing.runtime`` 模块 docstring §7a）。内部 API，不属稳定契约；
    禁止插件/应用层直接读写（插件约定 R2/R3）。
    """

    catalog_template: CatalogTemplate | None = None
    """Runtime 级默认渲染模板（``SkillPlugin.install()`` 构造注册表时
    注入，即 ``SkillPlugin`` 构造参数的透传落点——三级解析链
    「``use_skill()`` 参数 > 本属性 > 内置 :data:`DEFAULT_CATALOG_TEMPLATE`」
    的中段通道；``None`` = 未设 Runtime 级默认）。运行期只读。
    """

    def __init__(self, *, catalog_template: CatalogTemplate | None = None) -> None:
        """构造空注册表（可携带 Runtime 级默认渲染模板）。

        .. rubric:: 调用关系（审计）

        - 调用：无（初始化 ``_skills`` 空表 + 保存 ``catalog_template``）
        - 被调：``SkillPlugin.install()``（时机：阶段一安装，每次一次）
        """
        self._skills = {}
        self.catalog_template = catalog_template

    def register(self, skill: Skill, *, namespace: str | None = None) -> None:
        """编程式注册 Skill 实例（插件随身携带技能的通道）。

        .. rubric:: 功能介绍

        与文件解析通道（:meth:`get`）并列的注册入口，对齐
        ``ToolRegistry.register`` / ``Runtime.register_agent_type``：
        注册表 key 为 ``ns::规范名``（``skill.name``），``namespace`` 缺省
        落入 ``default::``——裸名引用的注册表视图依次查 ``default::``、
        ``builtin::``（``default`` 优先 = 覆盖通道）；自定义命名空间的
        技能只能以 ``ns::name`` 全限定名引用（只查注册表，不走文件查找链）。

        .. rubric:: 行为规约

        - ``ns::name`` 全键冲突 → 抛 :class:`flowing.errors.FlowingError`
          （消息含全键；后注册者报错，与工具/agent 类型注册同口径）。
        - 注册只在插件 ``install()``（R3：运行时不增删）。
        - 非行为：不做文件 IO；不校验 ``skill.description`` 之外的字段
          完整性（字段契约是 :class:`Skill` 构造方的责任）。

        :param skill: 已构造的 ``Skill`` 实例。
        :param namespace: 命名空间；``None`` → ``"default"``。

        .. rubric:: 测试案例

        - 前置：插件 ``install()`` 注册 ``Skill(name="report")`` → 操作：
          任意 Agent ``skills: [report]`` → 期望：``get`` 裸名
          视图命中 ``default::report``，不走文件查找链。
        - 前置：同 ``ns::name`` 再注册 → 期望：抛 ``FlowingError``。

        .. rubric:: 调用关系（审计）

        - 调用：无（写 ``_skills``）
        - 被调：``flowing.runtime.Runtime.register_skill()``（委托入口，
          时机：插件 ``install()`` 阶段一注册）
        """
        key = f"{namespace or 'default'}::{skill.name}"
        if key in self._skills:
            raise FlowingError(f"技能重名注册：{key}")  # 全键重名永远不允许
        self._skills[key] = skill
        skill.registry_key = key   # 落账时回写全键（与 get 的解析通道同口径）

    def get(self, name: str, source_dir: Path | None = None) -> Skill:
        """按规范名取 Skill，未命中缓存时现场解析定义文件。

        三资源解析 API 统一为 ``get*`` 单入口（用户裁决）：本方法与
        ``ToolRegistry.get`` / ``Runtime.get_agent_class`` 同构——
        注册表（缓存）快路径与文件链慢路径合一，无 ``*_or_*`` 形态
        （原名 ``get_or_create``）。运行时调用（catalog 渲染 /
        ``skill_load``）必命中缓存快路径——声明期 ``use_skill()``
        已预解析全部条目并**落账解析键**（文件派生技能记派生限定键，
        见 ``Skill.registry_key``；M-98），文件 IO 是声明期行为。

        .. rubric:: 功能介绍

        注册表唯一入口。``source_dir`` 缺省时为**纯注册表查询**
        （裸名只查 ``default::`` / ``builtin::``）；提供时裸名**先走
        定向文件查找链**（文件覆盖注册表，与 ``ToolRegistry.get``
        同口径）。文件链按模块 docstring 的定向查找优先级（目录内
        ``SKILL.fya`` > ``skill.fya`` > ``<name>.skill.fya`` >
        ``<name>.fya`` > ``SKILL.md`` > ``skill.md`` → 目录外
        ``<name>.skill.fya`` → ``<name>.fya`` → ``<name>.md``，首个
        存在者生效）定位；**定位后先算目录派生键查注册表短路复用**，
        未注册才解析、落账（回写 ``skill.registry_key``）后返回。
        同名 ``.fya`` 系与 ``.md`` 并存 → 告警 + ``.fya`` 系优先；
        命中通用名候选时规范名取目录名；链上顺序只是确定性裁决规则，
        不推荐同一链路真的同时存在多个候选文件。

        命名空间（§7a）：``name`` 的形态判别经
        :func:`flowing.paths.classify_ref`——含 ``::`` 时为限定名，
        **只查注册表**精确键（插件注册通道），不走文件查找链；文件
        解析产物以目录派生命名空间落账（``@/`` 下相对、根外绝对、
        文件夹式取上层目录；规范名推断经 :func:`flowing.paths.infer_name`，
        规则表 :data:`SKILL_NAMING`）。

        .. rubric:: 行为规约

        - 期待行为：返回的 ``Skill`` 是注册表共享实例；调用方不得修改。
        - 边缘情况：全部候选位置都不存在合法定义文件 → 抛
          :class:`flowing.errors.FlowingError`（消息含规范名与已尝试的
          查找路径）；定义文件存在但解析失败（缺 ``description``、YAML
          语法错误等）→ 解析异常上抛，不写入缓存。
        - 非行为：不校验 ``name`` 是否被任何 Agent 声明——注册表对声明
          无感知（声明是 ``SkillEntry`` 层的事）。

        :raises flowing.errors.FlowingError: —— 按 ``name`` 在
           ``source_dir`` 下找不到任何合法定义文件（含 ``source_dir``
           缺省时注册表不命中）时。
        :raises flowing.errors.FormatError: —— 定义文件存在但缺少
           必填字段或格式非法时。

        .. rubric:: 测试案例

        - 前置：``source_dir`` 下同时存在 ``sum/`` 目录（含 ``SKILL.md``）
          与 ``sum.skill.fya``。操作：``get("sum", source_dir)``。
          期望：解析的是 ``sum/SKILL.md``（目录优先）。
        - 前置：目录 ``sum/`` 存在但无任何合法定义文件，目录外存在
          ``sum.fya``。操作：同上（裸名语境）。期望：解析 ``sum.fya``
          （裸名语境目录空则继续向下；**显式路径**语境目录无候选由
          ``use_skill()`` 声明期抛 ``FormatError``，不进本方法）。
        - 前置：``sum/SKILL.fya`` 与 ``sum/SKILL.md`` 并存。期望：解析
          ``SKILL.fya`` 并发出并存告警（``.fya`` 系优先于 ``.md``）。
        - 前置：仅有 ``sum/SKILL.md``。期望：规范名取目录名 ``sum``；
          frontmatter 写了 ``name: other`` → 抛 ``NameMismatchError``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.skills._parse_skill_file()``（时机：
          缓存未命中时现场解析——声明期预解析或编程式按需）
        - 被调：``flowing.plugins.skills.use_skill()`` 声明期预解析
          （时机：每次 ``use_skill()``）；
          ``flowing.plugins.skills._load_skill()`` 契约第 2 步（时机：
          每次 skill_load，命中缓存的纯内存查找）；
          ``LazySkillsPrompt.resolve()``（时机：每次 catalog 渲染取已
          解析条目）

        .. seealso:: :class:`Skill`、:func:`use_skill`
        """
        # 精确键短路（先于形态判别）：文件派生限定键的命名空间含路径特征
        # （目录派生，如 "@/skills::sum"），过不了 classify_ref 的限定名判别
        # ——注册表在场证据优先于词法分流，「catalog 渲染 / skill_load 热路径
        # 必命中缓存」的 docstring 承诺靠此成立（与 ToolRegistry.get 同口径）
        if name in self._skills:
            return self._skills[name]
        if "::" in name:  # 限定名:只查注册表精确键(插件注册通道),不走文件查找链
            raise FlowingError(f"技能未注册：{name}")
        if classify_ref(name) == "path" or source_dir is not None:
            # 路径形态恒走文件定位（@/ 无需 source_dir；./ ../ 缺省时报错，
            # resolve_path 现有口径——与 ToolRegistry.get 同姿态）；裸名 +
            # source_dir 提供：先走定向文件查找链（文件覆盖注册表）——探测
            # 定位（目录内 SKILL.fya > skill.fya > <name>.skill.fya >
            # <name>.fya > SKILL.md > skill.md；目录外 <name>.skill.fya >
            # <name>.fya > <name>.md）后，先按命中文件所在目录算派生键查
            # 注册表：已注册 -> 短路复用现有实例；未注册才解析
            located = _locate_skill_file(name, source_dir)
            if located is not None:
                hit, folder_form, _base_dir, _candidates = located
                identity = infer_name(hit, naming=SKILL_NAMING)
                ns_dir = hit.parent.parent if folder_form else hit.parent   # 文件夹式取上层目录（§7a）
                derived_ns = _derive_namespace(ns_dir)
                derived_key = f"{derived_ns}::{identity}"
                if derived_key in self._skills:
                    return self._skills[derived_key]   # 派生键短路复用（文件解析是声明期行为）
                skill = _parse_skill_file(name, source_dir)  # 定位并解析（首个存在者生效）
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
            # 路径形态：文件定位未命中（不存在或后缀非法）——报文含原引用串
            raise FlowingError(f"技能定义文件不存在或形态非法：{name}")
        attempted = _attempted_paths(name, source_dir)
        raise FlowingError(
            f"技能未注册且查找链不命中：{name}（已尝试: "
            + (", ".join(str(p) for p in attempted) if attempted else "仅注册表裸名视图")
            + "）")


def _dir_candidates(name: str) -> list[str]:
    """``<name>/`` 目录内的定向查找链（首个存在者生效）：
    ``SKILL.fya > skill.fya > <name>.skill.fya > <name>.fya > SKILL.md >
    skill.md``。内部 API。"""
    return ["SKILL.fya", "skill.fya", f"{name}.skill.fya", f"{name}.fya",
            "SKILL.md", "skill.md"]


def _derive_namespace(ns_dir: Path) -> str:
    """所在目录 → 派生命名空间字符串（``@/`` 下根相对、根外绝对，§7a 口径）。

    内部 API。与 Tool/Agent 注册表的同名逻辑保持**同一份实现**——委托
    ``ToolRegistry._derived_namespace``（单一来源，不另造轮子）。
    """
    from flowing.tool import ToolRegistry   # 局部 import：注册表层对工具层只借这一个符号

    return ToolRegistry._derived_namespace(ns_dir)


def _launch_project_root() -> "Path | None":
    """读 launch 上下文的项目根（委托 ``flowing.tool._launch_project_root``，
    同一 ContextVar 来源）。内部 API。"""
    from flowing.tool import _launch_project_root as _impl   # 局部 import：单一来源复用

    return _impl()


def _attempted_paths(name: str, source_dir: "Path | None") -> list[Path]:
    """拼装裸名查找链的已尝试路径列表（供 fail-fast 报文）。内部 API。"""
    if source_dir is None or classify_ref(name) == "path":
        return []
    directory = source_dir / name
    return ([directory / c for c in _dir_candidates(name)]
            + [source_dir / f"{name}{suffix}" for suffix in (".skill.fya", ".fya", ".md")])


def _locate_skill_file(
    ref: str, source_dir: "Path | None"
) -> "tuple[Path, bool, Path, list[str]] | None":
    """按定向查找优先级定位 Skill 定义文件（**内部 API**）。

    返回 ``(命中路径, 是否文件夹式命中, 探测基准目录, 候选名列表)``，
    全部未命中 → ``None``（裸名语境调用方继续查注册表裸名视图）。

    - 裸名：``<name>/`` 目录内 6 候选（目录存在但无合法定义文件 → 继续
      向下，仅裸名语境）→ 目录外 ``<name>.skill.fya > <name>.fya >
      <name>.md``；
    - 路径形态：``@/`` 锚 launch 上下文项目根（无需 ``source_dir``，无
      上下文时显式 ``ValueError``——与 ``ToolRegistry.get`` 同姿态）；
      目录 → 目录内 6 候选，无候选直接 ``FormatError``（定点引用的目录
      为空几乎必为笔误）；直指文件 → 命中即返回。
    """
    if classify_ref(ref) == "path":
        root = _launch_project_root()
        if root is None and ref.replace("\\", "/").startswith("@/"):
            raise ValueError("@/ 路径解析需要 launch 上下文（_current_project_root 未登记）")
        resolved = resolve_path(
            ref, project_root=root if root is not None else Path.cwd(),   # 哑根：@/ 已在上方拒绝
            source_dir=source_dir)
        if resolved.is_dir():
            dir_name = infer_name(resolved, naming=SKILL_NAMING)   # 目录：basename 即目录名
            candidates = _dir_candidates(dir_name)
            hit = probe_candidates(resolved, candidates)
            if hit is None:
                # 显式路径语境：目录无候选 -> 直接报错，不继续向下
                raise FormatError(f"显式路径目录无合法技能入口: {resolved}")
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


def _parse_skill_file(name: str, source_dir: Path) -> Skill:
    """按定向查找优先级定位并解析 Skill 定义文件。

    内部 API，不属稳定契约。仅在 :meth:`SkillRegistry.get` 未
    命中缓存时调用；承担三种定义形式（``.md`` frontmatter / ``.skill.fya``
    块结构 / 目录形式）到统一 :class:`Skill` 实例的解析，含 ``$script``
    中 ``on_load`` 的提取与 ``_extra_fields`` 的收纳。

    .. rubric:: 行为规约

    - 定向查找：按规范名 ``name`` 在 ``source_dir`` 下首个存在者生效——
      ``<name>/`` 目录内 ``SKILL.fya > skill.fya > <name>.skill.fya >
      <name>.fya > SKILL.md > skill.md``（裸名语境目录无合法定义文件时
      继续向下；显式路径目录无候选 → ``FormatError``，定位层分流）→
      目录外 ``<name>.skill.fya > <name>.fya > <name>.md``；``.fya`` 系
      与 ``.md`` 并存 → 告警 + ``.fya`` 系优先；通用名命中 → 规范名取
      目录名。全部不存在 → ``FlowingError``（消息含规范名与已尝试路径）。
    - 解析：``.md`` = YAML frontmatter（``description`` 必填，缺 →
      ``MissingFieldError``；``name`` 仅作一致性断言，不符 →
      ``NameMismatchError``）+ Markdown 正文（即 ``content``）；
      ``.skill.fya`` = 块结构（``args:`` 经 ``expand_args_schema`` 归一、
      ``content`` / ``description`` 包装 ``Parsable``、未知字段收
      ``_extra_fields``）+ ``$script`` 提取 ``on_load``（经
      ``types.MethodType`` 绑定，``self`` 即本 Skill 实例）；目录形式
      正文可 ``{% include %}`` 目录内资料（渲染期经 Parsable include
      加载器，基准为调用方 Agent 的 ``source_dir``）。

    .. rubric:: 调用关系（审计）

    - 调用：``_locate_skill_file()``（定位）；``flowing.parser.split_fya``
      / ``load_fya_yaml``（字面解析）；``flowing.params.expand_args_schema``
      （``args:`` 归一）；``flowing.paths.infer_name``（规范名推断）
    - 被调：``flowing.plugins.skills.SkillRegistry.get()``
      缓存未命中分支（时机：``use_skill()`` 声明期预解析，或声明外
      编程式按需解析）

    .. seealso:: :meth:`SkillRegistry.get`
    """
    located = _locate_skill_file(name, source_dir)
    if located is None:
        attempted = _attempted_paths(name, source_dir)
        raise FlowingError(
            f"按 {name!r} 找不到任何合法技能定义文件（已尝试: "
            + (", ".join(str(p) for p in attempted) if attempted else str(name))
            + "）")
    hit, _folder_form, base_dir, candidates = located
    identity = infer_name(hit, naming=SKILL_NAMING)   # 通用名命中 -> 规范名取目录名
    if hit.name.endswith(".fya"):
        # .fya 系与同名 .md 并存 -> 告警 + .fya 系优先（链上顺序已保证优先，
        # 此处只补告警——与 Tool 的「.fya 优先于同名 .py 并告警」同口径）
        coexisting = [c for c in candidates
                      if c.endswith(".md") and (base_dir / c).exists()]
        if coexisting:
            warnings.warn(
                f"同名 .fya 系与 .md 并存，.fya 系优先: {hit}"
                f"（并存: {', '.join(coexisting)}）")
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
    """``.md`` 形式解析：YAML frontmatter + Markdown 正文。内部 API。

    ``description`` 必填（缺 → ``MissingFieldError``）；``name`` 写了仅作
    一致性断言（不符 → ``NameMismatchError``）；``_``（PENDING）值视同
    未声明；其余字段收 ``_extra_fields``；``.md`` 形式无 ``args_schema``
    （空 dict）与 ``on_load``（None）。
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
        # Agent 类型语境，以定义文件路径充当场定位（spec 未规定 skill 语境
        # 的取值，就地裁决）
        raise MissingFieldError("description", str(path))
    extra = {k: v for k, v in fields.items() if k not in ("name", "description")}
    return Skill(name=identity, description=Parsable(description),
                 content=Parsable(body), _extra_fields=extra)


_SKILL_FYA_RESERVED = frozenset({"name", "description", "content", "args"})
"""``.skill.fya`` 保留字段集——其余字段原样收 ``_extra_fields``（与
Agent ``_extra`` 的宽松解析策略同构）。"""


def _parse_skill_fya(path: Path, identity: str) -> Skill:
    """``.skill.fya`` 形式解析：块结构 + ``$script`` 提取 ``on_load``。内部 API。

    ``args:`` 块经 ``expand_args_schema`` 归一化为 JSON Schema properties
    （值为 ``_``/PENDING 的参数归一为 ``{}``——any 类型、无默认值、必填，
    覆盖义务原样保留）；具名块（``$script`` 以外）不支持——Skill 正文经
    ``content`` 字段声明，写具名块几乎必为笔误，fail-fast。
    """
    raw = split_fya(path.read_text(encoding="utf-8"))
    if raw.blocks:
        raise FormatError(
            f"{path} 含 Skill 定义不支持的具名块: {sorted(raw.blocks)}"
            "（Skill 正文经 content 字段声明；$script 只用于定义 on_load）")
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
        raise FormatError(f"{path} 的 args 字段必须是映射: {raw_args!r}")
    args_schema = expand_args_schema(
        {k: ({} if v is PENDING else v) for k, v in (raw_args or {}).items()})
    on_load = None
    if raw.script is not None:
        namespace: dict[str, Any] = {}
        # $script 是声明式定义面，与 Agent .fya 的 $script 同一信任级
        exec(compile(raw.script, str(path), "exec"), namespace)  # noqa: S102
        fn = namespace.get("on_load")
        if fn is not None and not callable(fn):
            raise FormatError(f"{path} 的 $script 中 on_load 不是可调用对象")
        on_load = fn
    extra = {k: v for k, v in fields.items() if k not in _SKILL_FYA_RESERVED}
    skill = Skill(name=identity, description=Parsable(description),
                  content=Parsable(content), args_schema=args_schema,
                  _extra_fields=extra)
    if on_load is not None:
        # 描述符绑定：self 即本 Skill 实例，框架以 (agent, args) 调用
        skill.on_load = types.MethodType(on_load, skill)
    return skill
