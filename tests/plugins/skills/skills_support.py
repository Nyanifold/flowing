"""skills 插件测试的公共助手。

提供：``harness`` 助手转发（HarnessRuntime / make_runtime / FakeProvider
脚本回放）、skills fixtures 拷贝、测试 Agent 类（``source_file`` 显式指向
``@/skills/`` 使 ``source_dir`` 落在拷贝后的 fixture 目录）、以及
「装好 SkillPlugin 的 HarnessRuntime」工厂。

本模块为普通模块而非 conftest：测试文件直接 ``import skills_support``，
避免顶级模块名 ``conftest`` 遮蔽。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from harness import (
    add_fake_provider,
    HarnessRuntime,
    make_runtime,
    script_provider,
    SimpleAgent,
    text_response,
    tool_call_response,
)

FIXTURES_SKILLS = Path(__file__).parent.parent.parent / "fixtures" / "skills"


def copy_skills_fixtures(tmp_path: Path) -> Path:
    """把 ``tests/fixtures/skills/`` 整树拷到 ``tmp_path/skills/`` 并返回该目录。

    fixtures 保持只读语义（目录即契约），测试一律在 tmp_path 副本上操作。
    """
    dst = tmp_path / "skills"
    shutil.copytree(FIXTURES_SKILLS, dst)
    return dst


class SkillHostAgent(SimpleAgent):
    """``source_dir`` 落在 ``<project_root>/skills/`` 的测试 Agent。

    显式 ``source_file``（``__init_subclass__`` 尊重显式值）——只需目录
    定位，宿主文件本身不需要存在。
    """

    source_file = "@/skills/host.fya"


def make_skill_runtime(
    tmp_path: Path,
    *,
    catalog_template: str | None = None,
    with_fixtures: bool = True,
) -> Any:
    """HarnessRuntime + 已安装 SkillPlugin（注册表经 ``runtime.inject`` 可取）。

    ``with_fixtures=True`` 时把 skills fixtures 拷入 ``tmp_path/skills/``
    并注册 ``skill-host`` 类型（``source_dir`` 指向该目录）。
    """
    from flowing.plugins.skills import SkillPlugin

    if with_fixtures:
        copy_skills_fixtures(tmp_path)
    runtime = HarnessRuntime(tmp_path)
    SkillPlugin(catalog_template=catalog_template).install(runtime)
    if with_fixtures:
        runtime.register_agent_type(SkillHostAgent, name="skill-host")
    return runtime
