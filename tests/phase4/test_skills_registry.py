"""阶段 4 skills 注册表与解析测试（W02–W09）：测试清单 T05–T15。

纯注册表层测试（不建 Agent）：缓存语义、注册通道、定向文件查找链
（目录优先 / 空目录继续向下 / .fya 系优先告警 / 通用名取目录名 /
全不命中 fail-fast / 解析失败不写缓存）与 ``Skill.__getattr__`` 回退。
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from flowing.errors import (
    FlowingError,
    MissingFieldError,
    NameMismatchError,
    SkillNameConflictError,
    SkillNotFoundError,
)
from flowing.parsable import Parsable
from flowing.plugins.skills import Skill, SkillRegistry

FIXTURES_SKILLS = Path(__file__).parent.parent / "fixtures" / "skills"


@pytest.fixture
def registry() -> SkillRegistry:
    return SkillRegistry()


# ---------------------------------------------------------------------------
# T05：缓存——连续两次 get 返回同一对象，定义文件只读一次
# ---------------------------------------------------------------------------


def test_t05_get_caches_parsed_skill(registry, monkeypatch):
    read_calls: list[str] = []
    real_read_text = Path.read_text

    def _spy(self: Path, *args, **kwargs):
        read_calls.append(str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _spy)
    s1 = registry.get("sum", FIXTURES_SKILLS)
    s2 = registry.get("sum", FIXTURES_SKILLS)
    assert s1 is s2
    assert sum("SKILL.md" in p for p in read_calls) == 1  # 定义文件只读一次


# ---------------------------------------------------------------------------
# T07：注册表命中裸名视图（default::），不走文件查找链
# ---------------------------------------------------------------------------


def test_t07_register_and_bare_name_hit(registry):
    report = Skill(name="report", description="报告技能。", content="正文")
    registry.register(report)   # namespace 缺省落 default::
    assert report.registry_key == "default::report"   # 落账时回写全键
    # 裸名 + source_dir（目录下没有任何 report 定义文件）→ 命中注册表
    assert registry.get("report", FIXTURES_SKILLS) is report
    # source_dir 缺省：纯注册表查询同样命中
    assert registry.get("report") is report


# ---------------------------------------------------------------------------
# T08：同 ns::name 再注册 → SkillNameConflictError（字段含全键）
# ---------------------------------------------------------------------------


def test_t08_duplicate_register_raises(registry):
    registry.register(Skill(name="report", content="x"))
    with pytest.raises(SkillNameConflictError, match="default::report"):
        registry.register(Skill(name="report", content="y"))
    # 不同命名空间的同名技能允许共存
    registry.register(Skill(name="report", content="z"), namespace="myplugin")
    assert registry.get("myplugin::report").content.source == "z"


# ---------------------------------------------------------------------------
# T09：目录优先——sum/（含 SKILL.md）与 sum.skill.fya 并存时解析目录内文件
# ---------------------------------------------------------------------------


def test_t09_directory_priority_over_sibling_file(registry):
    skill = registry.get("sum", FIXTURES_SKILLS)
    assert "DIR_MD_BODY" in skill.content.source          # 目录内 SKILL.md
    assert "FILE_FYA_BODY" not in skill.content.source    # 目录外 .fya 未命中


# ---------------------------------------------------------------------------
# T10：裸名语境目录无合法定义文件 → 继续向下解析目录外文件
# ---------------------------------------------------------------------------


def test_t10_empty_directory_falls_through(registry):
    skill = registry.get("nope", FIXTURES_SKILLS / "empty-dir")
    assert "NOPE_FYA_BODY" in skill.content.source
    assert skill.name == "nope"


# ---------------------------------------------------------------------------
# T11：.fya 系与 .md 并存 → 解析 .fya 并发出并存告警
# ---------------------------------------------------------------------------


def test_t11_fya_priority_with_coexistence_warning(registry):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        skill = registry.get("dup", FIXTURES_SKILLS)
    assert "DUP_FYA_BODY" in skill.content.source
    assert any(".fya" in str(w.message) and "coexist" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# T12：通用名命中规范名取目录名；frontmatter name 不符 → NameMismatchError
# ---------------------------------------------------------------------------


def test_t12_generic_name_takes_directory_name(registry):
    skill = registry.get("sum", FIXTURES_SKILLS)
    assert skill.name == "sum"   # sum/SKILL.md 命中通用名 → 取目录名
    with pytest.raises(NameMismatchError):
        registry.get("bad-name", FIXTURES_SKILLS)   # frontmatter name: other


# ---------------------------------------------------------------------------
# T13：全部候选位置无合法定义文件 → SkillNotFoundError（含规范名与已尝试路径）
# ---------------------------------------------------------------------------


def test_t13_not_found_error_lists_attempted_paths(registry):
    with pytest.raises(SkillNotFoundError) as exc_info:
        registry.get("ghost", FIXTURES_SKILLS)
    message = str(exc_info.value)
    assert "ghost" in message
    assert "ghost.skill.fya" in message   # 已尝试路径入报文


# ---------------------------------------------------------------------------
# T14：.md 缺 description → MissingFieldError，且不写入缓存（下次重试）
# ---------------------------------------------------------------------------


def test_t14_missing_description_not_cached(registry, tmp_path):
    # 在 tmp 副本上验证（fixtures 原件保持只读）
    md = tmp_path / "no-desc.md"
    md.write_text((FIXTURES_SKILLS / "no-desc.md").read_text(encoding="utf-8"),
                  encoding="utf-8")
    with pytest.raises(MissingFieldError):
        registry.get("no-desc", tmp_path)
    assert not any("no-desc" in key for key in registry._skills)   # 不写入缓存
    # 补上 description 后重试成功（解析失败不写缓存 → 下次引用重试）
    md.write_text("---\ndescription: 补齐后的描述。\n---\n\n正文。\n",
                  encoding="utf-8")
    skill = registry.get("no-desc", tmp_path)
    assert skill.description.source == "补齐后的描述。"


# ---------------------------------------------------------------------------
# T15：Skill.__getattr__——_extra_fields 原样返回（不做 Parsable 求值）
# ---------------------------------------------------------------------------


def test_t15_getattr_extra_fields_passthrough():
    marker = Parsable("{{ 1 + 1 }}")
    skill = Skill(name="x", content="c", _extra_fields={"custom": 1, "tpl": marker})
    assert skill.custom == 1
    assert skill.tpl is marker   # 原样返回，不做 Parsable 求值
    with pytest.raises(AttributeError):
        skill.missing
