"""Skill 扩展子包（``flowing.plugins.skills``）：SkillPlugin / Skill / SkillEntry / SkillRegistry / SkillLoadTool / use_skill。

本包由 N-02 拆分而来，对外 API 经 __init__ 再导出不变。
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
