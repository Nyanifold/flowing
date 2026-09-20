"""``flowing.agent_registry`` —— Runtime 全局 Agent 类型注册表（``ns::name`` 全限定键 → Agent 类）。

.. rubric:: 功能介绍

框架核心层。承载 Agent 类型的注册与解析：

- :class:`AgentRegistry`：每个 Runtime 持有一个实例（``runtime.agent_registry``），
  是 Agent 类型的唯一注册与解析权威——与 :class:`flowing.tool.ToolRegistry` /
  :class:`flowing.plugins.skills.registry.SkillRegistry` 同构的三注册表体系。
- :data:`AGENT_NAMING`：Agent 资源的路径形态身份名推断规则表。

注册时间线：无启动扫描（框架没有默认扫描目录）——内置类型（``ExploreAgent``）
随 ``Runtime.__init__`` 注册；插件类型在阶段一 ``install()`` 注册；文件形态
类型由 :meth:`AgentRegistry.get` 引用触发惰性解析并注册（两级惰性：亲代
Agent 实例化时只记元信息，创建 / invoke 时才加载类）。

.. seealso::

    :class:`flowing.tool.ToolRegistry` —— 同构的 Tool 注册表。
    :meth:`flowing.runtime.Runtime.get_agent_class` —— 公开解析入口（薄委托）。
    :meth:`flowing.runtime.Runtime.register_agent_type` —— 公开注册入口（薄委托）。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import importlib.util
import warnings

from pathlib import Path
from typing import TYPE_CHECKING

from flowing.errors import (
    AgentTypeConflictError,
    AgentTypeNotFoundError,
    FormatError,
    NameMismatchError,
)
from flowing.paths import NamingRules
from flowing.paths import classify_ref as _classify_ref
from flowing.paths import infer_name as _infer_name
from flowing.paths import kebab_to_snake as _kebab_to_snake
from flowing.paths import pascal_to_kebab as _pascal_to_kebab
from flowing.paths import path_to_module_name as _path_to_module_name
from flowing.paths import fnmatch_keys as _fnmatch_keys
from flowing.paths import probe_candidates as _probe_candidates
from flowing.paths import resolve_path as _paths_resolve_path
from flowing.paths import to_project_path as _paths_to_project_path

if TYPE_CHECKING:
    from flowing.agent import Agent

__all__ = ["AGENT_NAMING", "AgentRegistry"]

AGENT_NAMING = NamingRules(
    suffixes=(".agent.fya", ".fya", ".py"),
    generic_names=frozenset({"agent.fya", "AGENT.fya"}),
)
"""Agent 资源的路径形态身份名推断规则表（:class:`flowing.paths.NamingRules`）。

紧邻候选链声明（:meth:`AgentRegistry.get`）：命中通用名候选
（``agent.fya`` / ``AGENT.fya``）→ 身份名取目录名；否则文件名去首个
匹配后缀、snake→kebab。供 agent 装配层调
:func:`flowing.parser.parse_fya` 时传入 ``naming=AGENT_NAMING``，以及
name 断言的推断侧。兼容出口：``flowing.runtime.AGENT_NAMING`` 为本常量的
re-export。
"""


def _agent_glob_accept(path: Path) -> bool:
    """``subagents:`` 字段 glob 命中的过滤（``glob_accept`` 回调）。内部 API。

    与 :func:`flowing.tool.registry._tool_glob_accept` 同构。纯名字分析，
    不做任何语义嗅探：

    - 目录：探测目录内候选链（``AGENT.fya > agent.fya > <名>.agent.fya >
      <名>.fya``，只含 ``.fya``——与解析口径一致），有合法入口 →
      ``True``；无 → ``False``（杂项子目录跳过）；
    - 显式标记只纳入 ``*.agent.fya`` 一种（带其它显式标记的，如
      ``*.tool.fya``，不属智能体面）；
    - 其余 ``.fya`` / ``.py`` 文件（裸 ``foo.fya``、``agent.fya`` 等
      通用名、``foo.py``）→ ``True`` 直接纳入——是否合法 Agent 资源
      留给创建期的 eager 解析 fail-fast。通用名文件的设计用途是独居
      叶目录（目录形态资源），glob 命中的本来就是目录本身，无需特判；
    - 其它后缀（``.md`` 等）→ ``False``。
    """
    if path.is_dir():
        name = _infer_name(path, naming=AGENT_NAMING)   # 目录：basename 即目录名
        return _probe_candidates(
            path, ["AGENT.fya", "agent.fya", f"{name}.agent.fya", f"{name}.fya"]
        ) is not None
    if path.name.endswith(".tool.fya"):
        return False
    return path.suffix in (".fya", ".py")


class AgentRegistry:
    """Runtime 全局 Agent 类型注册表——``ns::name`` 全限定键 → Agent 类。

    .. rubric:: 功能介绍

    每个 Runtime 持有一个实例（``runtime.agent_registry``）。注册表条目
    惰性——:meth:`get` 在 invoke / 创建时才解析类。重名约束按 ``ns::name``
    全限定键判定：同一命名空间内重名 → 后注册者抛
    :class:`flowing.errors.AgentTypeConflictError`；不同命名空间的同名类型
    允许共存。裸名引用的注册表视图依次查 ``default::``、``builtin::``
    （``default`` 优先 = 插件覆盖原生行为的通道）；自定义命名空间的类型只能
    以 ``ns::name`` 全限定名引用。

    .. rubric:: 使用示例

    .. code-block:: python

        runtime.agent_registry.register(PaymentAgent)                  # name 缺省 → 类名推断 payment-agent
        cls = runtime.agent_registry.get("payment-agent")              # 按规范名取
        "default::payment-agent" in runtime.agent_registry             # 纯存在性检查

    .. rubric:: 行为要点

    - 不变量：任意时刻一个 ``ns::name`` 全限定键至多映射一个 Agent 类
      （不同命名空间同名允许共存）。
    - 不提供 unregister（销毁随 Runtime 生命周期）。
    - 身份名一律推断（路径文件名 / 目录名、注册名、类名 ``__name__``）；
      类体写了 ``name`` 仅作一致性断言——与推断值不符抛
      :class:`flowing.errors.NameMismatchError`。

    .. seealso::

        :attr:`flowing.runtime.Runtime.agent_registry` —— 挂载点。
        :class:`flowing.tool.ToolRegistry` —— 同构的 Tool 注册表。
    """

    _agents: dict[str, type[Agent]]
    """``ns::规范名`` → Agent 类（命名空间规则见 :meth:`get`）。
    内部 API，不属稳定契约。
    """

    def __init__(self, *, project_root: "Path | None" = None) -> None:
        # 空注册表（无启动扫描——「无默认扫描目录」基调）
        self._agents = {}
        # @/ 引用的解析基准：Runtime 构造时传入固化值（project_root），
        # 终身有效；裸注册表（测试）为 None——@/ 引用报错、其余形态退化
        self._project_root = project_root

    def register(self, agent_class: type[Agent], *, name: str | None = None,
                 namespace: str | None = None) -> None:
        """注册 Agent 类。

        :param agent_class: Agent 子类。
        :param name: 注册名覆写；``None`` 时从类名推断
          （``__name__``，PascalCase → kebab-case，经
          :func:`flowing.paths.pascal_to_kebab`；类名非合法 PascalCase →
          :class:`flowing.errors.FormatError`）。注册表 key 为 ``ns::name``。
        :param namespace: 命名空间；``None`` → ``"default"``。核心内置类型归
          ``builtin::``；裸名引用的注册表视图依次查 ``default::``、
          ``builtin::`` （``default`` 优先 = 插件覆盖原生行为的通道），自定义
          命名空间只能以 ``ns::name`` 全限定名引用。
        :raises flowing.errors.AgentTypeConflictError: ``ns::name`` 完整键
          （含命名空间）已存在（不同命名空间的同名类型允许共存）。
        :raises flowing.errors.NameMismatchError: 类体显式 ``name`` 与注册名
          （显式参数或类名推断值）不符——``name`` 非机制字段，写了仅作一致性
          断言。
        """
        bare = name if name is not None else _pascal_to_kebab(agent_class.__name__)
        explicit_name = agent_class.__dict__.get("name")   # name 非机制字段：写了仅作一致性断言
        if explicit_name is not None and explicit_name != bare:
            raise NameMismatchError(explicit_name, bare, agent_class.__name__)
        ns = namespace or "default"
        key = f"{ns}::{bare}"
        if key in self._agents:
            raise AgentTypeConflictError(key)   # 全键重名永远不允许
        self._agents[key] = agent_class   # 条目惰性：get 在 invoke/创建时才解析
        agent_class.registry_key = key   # 回写（与 Tool/Skill.registry_key 同构；文件派生注册点同律）

    def glob(self, pattern: str) -> list[str]:
        """注册表键的名字 glob（``subagents:`` 条目名字模式的匹配域）。

        委托 :func:`flowing.paths.fnmatch_keys`：裸名模式匹配
        ``default::`` / ``builtin::`` 视图的裸名部分；含 ``::`` 的限定
        模式匹配完整键。返回键排序后的完整键列表（稳定序），零命中返回
        空列表。匹配域是调用时点的注册表快照——未被引用过的文件态 Agent
        类型不在册（它们由路径 glob 覆盖）。
        """
        return _fnmatch_keys(pattern, self._agents.keys())

    def get(self, agent_type: str, *,
            source_dir: Path | None = None) -> type[Agent]:
        """Agent 类型的唯一解析入口——注册表快路径 + 文件链慢路径合一。

        .. rubric:: 功能介绍

        与 :meth:`flowing.tool.ToolRegistry.get` /
        :meth:`flowing.plugins.skills.registry.SkillRegistry.get` 同构。形态
        判别委托 :func:`flowing.paths.classify_ref` （词法唯一来源），三种
        引用形态：

        - 限定名（含 ``::``，如 ``myplugin::payment-agent``）：只查注册表
          精确键，不走文件查找链；
        - 裸名（如 ``payment``）：``source_dir`` 提供时先走文件查找链
          （相对 ``source_dir``——文件覆盖注册表）；``source_dir`` 缺省时
          跳过文件链。之后查注册表裸名视图——``default::`` 优先于
          ``builtin::`` （插件覆盖原生行为的通道）。需要文件上下文的调用
          走 :meth:`flowing.agent.Agent.get_agent_class` （自动携带
          ``source_dir``）；
        - 路径形态（``./`` / ``@/`` / 绝对路径 / ``文件::类名``）经
          ``resolve_path`` 定位 ``.fya`` 或手写 ``.py`` 后编译 / 加载
          （``@/`` 锚 ``project_root`` 无需 ``source_dir``；``./`` / ``../``
          缺省 ``source_dir`` 报错）。不做 glob 展开（``subagents:`` 条目
          的 glob 在装配层 ``_expand_glob_entries`` 展开——命中先经
          :func:`_agent_glob_accept` 形态过滤——后逐条进本方法）。

        路径形态细则：

        - 目录形态候选链 ``AGENT.fya`` > ``agent.fya`` > ``<name>.agent.fya``
          > ``<name>.fya``，探测循环委托 :func:`flowing.paths.probe_candidates`、
          首个存在者生效（目录存在但无任一候选 →
          :class:`flowing.errors.AgentTypeNotFoundError`；链上顺序只是确定性
          的消歧规则，不推荐同一链路真的同时存在多个候选文件）；同名
          ``.fya`` 单文件与文件夹并存时文件夹优先；``.fya`` 与手写子类同名
          并存时 ``.fya`` 优先并告警。
        - 指向手写 ``.py`` 文件时，模块内需恰好一个 Agent 子类（与
          Workflow 定义文件的约定同构）；零个 →
          :class:`flowing.errors.FormatError`；多个 → 用 ``路径::ClassName``
          形态消歧（左段含路径特征——``/`` / 反斜杠 / ``.py`` 结尾——时
          按「文件::类名」解析，绕开「恰好一个子类」限制；与命名空间
          限定名 ``ns::name`` 的区分在 ``flowing.paths.classify_ref`` 词法
          层完成）。
        - 目录候选链只含 ``.fya``——不接管手写类的目录组织（手写类的
          目录组织走标准 Python 包机制 + :meth:`register`）。
        - 文件解析产物的命名空间从所在目录派生（``@/`` 下相对、根外绝对，
          文件夹式取上层目录），仅作内部身份标识，引用写法不变。
        - 两级惰性：亲代 Agent 实例化时只记元信息，创建 / invoke 时才加载类。

        .. rubric:: 行为要点

        - 解析失败抛 :class:`flowing.errors.AgentTypeNotFoundError`。
        - 身份名一律推断（路径文件名 / 目录名、注册名、类名 ``__name__``）；
          ``.fya`` 或手写子类中写了 ``name`` 仅作一致性断言——与推断值
          不符抛 :class:`flowing.errors.NameMismatchError`；``class_name``
          推断规则见 :class:`flowing.agent.Agent`。
        - 裸名目录外候选链 ``<name>.agent.fya`` > ``<name>.fya`` >
          ``<name_snake>.py``（首个存在者生效）；目录存在但无合法入口 →
          继续链上下一项（「继续向下」仅裸名语境；显式路径语境下目录无
          候选 → 直接报错，定点引用的目录为空几乎必为笔误）。

        :param agent_type: 类型名字符串（限定名 / 裸名 / 路径形态）。
        :param source_dir: 裸名与 ``./`` / ``../`` 路径形态的基准目录。
        :return: Agent 类。
        :raises flowing.errors.AgentTypeNotFoundError: 注册表与文件链均无法
            解析时。
        :raises flowing.errors.FormatError: 手写 ``.py`` 模块内 Agent 子类
            数量不为恰好一个、且未用 ``路径::ClassName`` 消歧时。
        :raises flowing.errors.NameMismatchError: 声明 ``name`` 与推断身份
            名不符时。
        :raises ValueError: ``@/`` 路径无项目根上下文（裸注册表）时。

        .. seealso::

            - :meth:`flowing.runtime.Runtime.get_agent_class` —— 公开入口
              （薄委托本方法）。
            - :meth:`flowing.tool.ToolRegistry.get` —— 同构的 Tool 解析入口。
        """
        # 精确键短路（先于形态判别）：文件派生限定键的命名空间含路径特征
        # （目录派生，如 "@/order-agent::payment"），过不了 classify_ref 的
        # 限定名判别（左段含 / 判成路径形态）——注册表在场证据优先于词法分流
        if agent_type in self._agents:
            return self._agents[agent_type]
        # 形态判别委托 classify_ref（词法唯一来源）——注意不能用
        # `"::" in agent_type` 粗判：「文件::类名」（消歧形态）也含 ::，
        # 但属路径形态（左段含路径特征时 classify_ref 判 "path"）
        form = _classify_ref(agent_type)
        if form == "qualified":
            # 限定名（ns::name）：只查注册表精确键，不走文件查找链
            raise AgentTypeNotFoundError(agent_type)
        if form == "bare":
            # 裸名：source_dir 提供时先走文件查找链（相对 source_dir——文件
            # 覆盖注册表）；缺省时跳过文件链
            if source_dir is not None:
                loaded = self._load_agent_from_name_chain(agent_type, source_dir)
                if loaded is not None:
                    return loaded
            # 注册表裸名视图：default:: 优先于 builtin::（插件覆盖原生行为通道）
            for key in (f"default::{agent_type}", f"builtin::{agent_type}"):
                if key in self._agents:
                    return self._agents[key]
            raise AgentTypeNotFoundError(agent_type)
        # 路径形态（./ @/ 绝对路径 / 含分隔符的相对路径 / 文件::类名）：
        # @/ 锚 project_root 无需 source_dir；./ ../ 缺省 source_dir 报错
        # （resolve_path 现有口径）。裸注册表（无项目根）时 @/ 无法锚定 →
        # ValueError（编程错误）
        path_part, sep, class_name = agent_type.partition("::")
        root = self._project_root
        if root is None and path_part.replace("\\", "/").startswith("@/"):
            raise ValueError("@/ path resolution requires a project root (bare AgentRegistry without project_root)")
        resolved = _paths_resolve_path(
            path_part,
            project_root=root if root is not None else Path.cwd(),   # 哑根：@/ 已在上方拒绝
            source_dir=source_dir)
        return self._load_agent_from_path(
            resolved, class_name if sep else None, ref=agent_type)

    def __contains__(self, key: str) -> bool:
        """纯存在性检查（仅认全限定键 ``ns::name``，不做文件探测）。"""
        return key in self._agents

    def _load_agent_from_name_chain(
        self, name: str, source_dir: Path
    ) -> "type[Agent] | None":
        """裸名的定向文件查找链（内部 API）。

        相对 ``source_dir`` 探测：目录形态 ``<name>/`` 优先（候选链只含
        ``.fya``：``AGENT.fya > agent.fya > <name>.agent.fya > <name>.fya``，
        首个存在者生效）；目录外依次 ``<name>.agent.fya`` >
        ``<name>.fya`` 单文件、``<name_snake>.py`` 手写文件。``.fya`` 命中经
        :meth:`_load_agent_from_fya` 编译装配；``.fya`` 与同名 ``.py`` 并存
        → 告警且 ``.fya`` 优先。全部未命中 → ``None`` （调用方继续查注册表
        裸名视图）。
        """
        directory = source_dir / name
        if directory.is_dir():
            hit = _probe_candidates(
                directory,
                ["AGENT.fya", "agent.fya", f"{name}.agent.fya", f"{name}.fya"])
            if hit is not None:
                return self._load_agent_from_fya(hit, ref=name)
            # 裸名语境：目录存在但无合法入口 → 继续链上下一项
        py_file = source_dir / f"{_kebab_to_snake(name)}.py"
        hit = _probe_candidates(
            source_dir, [f"{name}.agent.fya", f"{name}.fya", py_file.name])
        if hit is None:
            return None
        if hit.name.endswith(".fya"):
            if py_file.exists():
                warnings.warn(
                    f"a same-name .fya and a handwritten .py coexist; the .fya wins: {hit} / {py_file}")
            return self._load_agent_from_fya(hit, ref=name)
        return self._load_agent_from_py(hit, None, ref=name)

    def _load_agent_from_path(
        self, resolved: Path, class_name: "str | None", *, ref: str
    ) -> "type[Agent]":
        """路径形态的编译 / 加载（内部 API）。

        目录 → 候选链探测（只含 ``.fya``；无任一候选 →
        ``AgentTypeNotFoundError``）；``.fya`` 文件 →
        :meth:`_load_agent_from_fya` 编译装配；手写 ``.py`` →
        :meth:`_load_agent_from_py`；不存在 / 其它后缀 →
        ``AgentTypeNotFoundError`` （解析失败的统一口径）。
        """
        if resolved.is_dir():
            name = _infer_name(resolved, naming=AGENT_NAMING)   # 目录：basename 即目录名
            hit = _probe_candidates(
                resolved,
                ["AGENT.fya", "agent.fya", f"{name}.agent.fya", f"{name}.fya"])
            if hit is None:
                raise AgentTypeNotFoundError(ref)   # 目录存在但无任一候选 → 解析失败
            return self._load_agent_from_fya(hit, ref=ref)
        if not resolved.exists():
            raise AgentTypeNotFoundError(ref)
        if resolved.suffix == ".fya":
            return self._load_agent_from_fya(resolved, class_name, ref=ref)
        if resolved.suffix == ".py":
            return self._load_agent_from_py(resolved, class_name, ref=ref)
        raise AgentTypeNotFoundError(ref)

    def _load_agent_from_fya(
        self, path: Path, class_name: "str | None" = None, *, ref: str
    ) -> "type[Agent]":
        """``.fya`` 命中的编译装配与派生注册（内部 API）。

        经 :func:`flowing.compiler.compile_fya_class` 现场合成 Agent 子类
        （``name`` 一致性断言在合成层完成）。派生注册与
        :meth:`_load_agent_from_py` 同范式：派生键
        ``to_project_path(dir)::infer_name`` + ``registry_key`` 回写 +
        派生键已在注册表 → 短路复用（不重复合成）。``class_name``
        （``路径::ClassName`` 形态）对 ``.fya`` 仅作一致性断言——单文件
        只合成一个类，不符 → :class:`flowing.errors.FormatError`。
        """
        from flowing.compiler import compile_fya_class   # 局部 import：模块头依赖图保持单向

        name = _infer_name(path, naming=AGENT_NAMING)   # 身份名推断（通用名取目录名）
        derived_key = f"{self._derived_namespace(path.parent)}::{name}"   # 目录派生命名空间
        if derived_key in self._agents:
            cls = self._agents[derived_key]   # 派生键短路复用（文件解析是声明期行为）
            # 短路同样过 class_name 一致性断言——不得静默返回不符的已注册类
            if class_name is not None and cls.__name__ != class_name:
                raise FormatError(
                    f"registered synthesized class of {path} is {cls.__name__}, not {class_name!r}"
                    " (.fya files synthesize exactly one class per file; :: disambiguation is the mechanism for handwritten .py multi-class files)")
            return cls
        cls = compile_fya_class(path, project_root=self._project_root)   # 运行时编译自带项目根
        if class_name is not None and cls.__name__ != class_name:
            raise FormatError(
                f"synthesized class of {path} is {cls.__name__}, not {class_name!r}"
                " (.fya files synthesize exactly one class per file; :: disambiguation is the mechanism for handwritten .py multi-class files)")
        cls.registry_key = derived_key   # 回写（与 _load_agent_from_py 同构）
        self._agents[derived_key] = cls
        return cls

    def _load_agent_from_py(
        self, path: Path, class_name: "str | None", *, ref: str
    ) -> "type[Agent]":
        """加载手写 ``.py`` 中的 Agent 子类（内部 API）。

        模块内需恰好一个本文件定义的 Agent 子类（``__module__`` 过滤掉
        import 进来的）；零个 → ``FormatError``；多个 → ``FormatError``
        （消息指明用 ``路径::ClassName`` 消歧）；``class_name`` 指定时
        直接按名取（绕开「恰好一个」限制）。命中后注册到派生键（命名空间
        从所在目录派生：``@/`` 下根相对、根外绝对——``to_project_path``
        形式，仅作内部身份标识）并回写 ``cls.registry_key``；派生键已在
        注册表 → 短路复用（不重复加载）。``::ClassName`` 消歧形态的派生键
        含类名（``<目录键>::<身份名>::<类名>``——每类一键，否则同一多类
        文件先注册 ``::A`` 后 ``::B`` 会误短路返回 A）；无 ``class_name``
        时为 ``<目录键>::<身份名>``。类体写了 ``name`` 仅作一致性断言：
        与推断值不符抛 ``NameMismatchError``。
        """
        from flowing.agent import Agent   # 局部 import：模块头依赖图保持单向

        name = _infer_name(path, naming=AGENT_NAMING)   # 身份名推断（文件名去后缀、snake→kebab）
        base_key = f"{self._derived_namespace(path.parent)}::{name}"   # 目录派生命名空间（内部身份标识）
        # ::ClassName 消歧形态的派生键含类名（每类一键）——同一多类文件
        # 先 ::A 后 ::B 时，B 不得误命中 A 的短路
        derived_key = f"{base_key}::{class_name}" if class_name is not None else base_key
        if derived_key in self._agents:
            return self._agents[derived_key]   # 派生键短路复用（文件解析是声明期行为）
        spec = importlib.util.spec_from_file_location(
            _path_to_module_name(path, project_root=self._project_root,
                                 prefix="flowing_agent_file_"), path)
        module = importlib.util.module_from_spec(spec)   # type: ignore[union-attr]
        spec.loader.exec_module(module)   # type: ignore[union-attr]
        if class_name is not None:
            cls = getattr(module, class_name, None)
            if not (isinstance(cls, type) and issubclass(cls, Agent)):
                raise FormatError(
                    f"no Agent subclass {class_name} in {path} (file::ClassName disambiguation failed)")
        else:
            candidates = [
                obj for obj in vars(module).values()
                if isinstance(obj, type) and issubclass(obj, Agent)
                and obj is not Agent and obj.__module__ == module.__name__
            ]
            if not candidates:
                raise FormatError(
                    f"no Agent subclass in {path} — handwritten .py files must define exactly one")
            if len(candidates) > 1:
                raise FormatError(
                    f"{path} contains multiple Agent subclasses"
                    f"（{', '.join(c.__name__ for c in candidates)}）——"
                    " — disambiguate with the path::ClassName form")
            cls = candidates[0]
        explicit_name = cls.__dict__.get("name")   # name 非机制字段：写了仅作一致性断言
        if explicit_name is not None and explicit_name != name:
            raise NameMismatchError(explicit_name, name, str(path))
        cls.registry_key = derived_key   # 回写（与 register 同构）
        self._agents[derived_key] = cls
        return cls

    def _derived_namespace(self, ns_dir: Path) -> str:
        """所在目录 → 派生命名空间字符串（``@/`` 下根相对、根外绝对——
        :func:`flowing.paths.to_project_path` 口径；无项目根（裸注册表）
        时退化为绝对路径，与「根外绝对」一致）。内部 API，不属稳定契约。"""
        if self._project_root is not None:
            return _paths_to_project_path(ns_dir, project_root=self._project_root)
        return str(ns_dir)
