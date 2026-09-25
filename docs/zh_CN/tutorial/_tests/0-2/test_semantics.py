# 0-2 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ── 工程文件齐全性 ──────────────────────────────────────────

def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_root_fya_declares_tools_bare():
    root_fya = read("root.fya")
    assert "tools:" in root_fya
    # 裸名声明：tools 列表项不带 builtin:: 前缀（命名空间省略；注释除外）
    for line in root_fya.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if stripped.startswith("- "):
            assert not stripped.removeprefix("- ").startswith("builtin::"), \
                f"tools 声明应使用裸名: {line}"


def test_notes_fixture_present():
    assert (ROOT / "notes" / "使用说明.md").is_file()
    assert (ROOT / "notes" / "路线图.md").is_file()


# ── 留档齐全性 ──────────────────────────────────────────────

def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


# ── 语义关键点 ──────────────────────────────────────────────

def test_tool_calls_happened():
    out = read("repl_output.txt")
    assert "[tool_call]" in out, "Agent 应发起工具调用（glob/read）"
    assert "[tool:completed]" in out, "工具调用应有完成回执"


def test_agent_read_project_content():
    out = read("repl_output.txt")
    # Agent 经 glob/read 读到真实文件内容后作答（语义关键点）
    assert "使用说明.md" in out or "路线图" in out, "回答应提到实际读到的文件"
    assert "Python" in out, "使用说明.md 的安装要点（Python 3.13）应被概括到"


def test_answer_not_from_thin_air():
    out = read("repl_output.txt")
    # 项目问答应基于读到的内容，而非拒绝回答
    assert "无法" not in out.split("(tool:completed)")[-1][:50], \
        "Agent 不应在成功读文件后声称无法回答"
