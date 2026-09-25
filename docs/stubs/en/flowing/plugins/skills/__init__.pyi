"""Public API for the built-in Skills extension.

The package exports the two-stage enablement API, the Runtime-wide registry,
the LLM-facing ``skill-load`` tool, catalog rendering support, naming rules,
and the data-model types used to define and bind Skills. Installing
``SkillPlugin`` registers the shared services; calling ``use_skill(agent)``
enables Skill support for an individual Agent. The extension is shipped with
Flowing but is not installed automatically.

Definitions are resolved by ``SkillRegistry`` and shared across Agents, while
``SkillEntry`` stores each Agent's alias, fixed arguments, description
override, and visibility. Catalog rendering and Skill loading are exposed by
the modules below; the data-model module documents the fields and their
individual contracts.

.. seealso:: :mod:`flowing.plugins.skills.skills` for enablement, lookup,
    catalog, and loading behavior; :mod:`flowing.plugins.skills.registry`
    for registry lookup and injection; and :mod:`flowing.plugins.skills.models`
    for the Skill data models.
"""

from flowing.plugins.skills.models import (
    SKILL_NAMING as SKILL_NAMING,
    CatalogTemplate as CatalogTemplate,
    Skill as Skill,
    SkillContent as SkillContent,
    SkillEntry as SkillEntry,
    SkillLoadContext as SkillLoadContext,
    SkillResult as SkillResult,
)
from flowing.plugins.skills.registry import (
    SkillRegistry as SkillRegistry,
    skill_registry_key as skill_registry_key,
)
from flowing.plugins.skills.skills import (
    DEFAULT_CATALOG_TEMPLATE as DEFAULT_CATALOG_TEMPLATE,
    LazySkillsPrompt as LazySkillsPrompt,
    SkillLoadTool as SkillLoadTool,
    SkillPlugin as SkillPlugin,
    use_skill as use_skill,
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
