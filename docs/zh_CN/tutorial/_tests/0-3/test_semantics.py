# 0-3 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ── 工程文件齐全性 ──────────────────────────────────────────

def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_root_fya_declares_orchestrator():
    root_fya = read("root.fya")
    assert "subagent-invoke" in root_fya, "编排者必须显式声明 subagent-invoke"
    assert "explore-agent" in root_fya, "subagents: 应声明 explore-agent"
    # 声明使用裸名（命名空间省略；注释除外）
    for line in root_fya.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if stripped.startswith("- "):
            assert not stripped.removeprefix("- ").startswith("builtin::"), \
                f"声明应使用裸名: {line}"


# ── 留档齐全性 ──────────────────────────────────────────────

def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


# ── 语义关键点 ──────────────────────────────────────────────

def test_first_task_delegated_and_reported():
    out = read("repl_output.txt")
    assert out.count("[tool_call] subagent-invoke") >= 2, \
        "两个任务都应经 subagent-invoke 派单"
    assert "使用说明.md" in out and "路线图.md" in out, \
        "探索智能体的汇报应覆盖两个实际文件"


def test_write_task_refused_honestly():
    out = read("repl_output.txt")
    assert "超出" in out and "能力边界" in out, \
        "写文件任务应被如实汇报为超出能力边界"
    assert "没有伪造" in out or "未落盘" in out or "未能创建" in out, \
        "汇报应明确说明没有伪造结果、文件未创建"


def test_no_file_was_written():
    assert not (ROOT / "notes" / "总结.md").exists(), \
        "探索智能体只读，总结.md 不应被创建"
