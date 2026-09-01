"""``flowing.plugins.skills`` —— Skill 扩展子包。

本包承载 Skill 扩展的全部公开符号：:class:`SkillPlugin`（阶段一插件）、
:func:`use_skill`（阶段二启用）、:class:`Skill` / :class:`SkillEntry`
（可执行对象与 Agent 级绑定）、:class:`SkillRegistry`（注册表）、
:class:`SkillLoadTool`（``skill-load`` 工具）、
:class:`LazySkillsPrompt`（catalog 动态块）以及 :data:`skill_registry_key`
（provide 注入键）等；对外 API 经本 ``__init__`` 统一再导出。

.. seealso:: :mod:`flowing.plugins.skills.skills`（插件主模块：启用方式
    与注册面清单）、:mod:`flowing.plugins.skills.models`（数据对象）、
    :mod:`flowing.plugins.skills.registry`（注册表）
"""

from flowing.plugins.skills.models import (
    SKILL_NAMING,
    CatalogTemplate,
    Skill,
    SkillContent,
    SkillEntry,
    SkillLoadContext,
    SkillResult,
)
from flowing.plugins.skills.registry import (
    SkillRegistry,
    skill_registry_key,
)
from flowing.plugins.skills.skills import (
    DEFAULT_CATALOG_TEMPLATE,
    LazySkillsPrompt,
    SkillLoadTool,
    SkillPlugin,
    use_skill,
)

__all__ = [
    "SkillPlugin",
    "Skill",
    "SkillEntry",
    "SkillRegistry",
    "SkillLoadContext",
    "SkillContent",
    "SkillResult",
    "LazySkillsPrompt",
    "SkillLoadTool",
    "CatalogTemplate",
    "DEFAULT_CATALOG_TEMPLATE",
    "skill_registry_key",
    "SKILL_NAMING",
    "use_skill",
]
