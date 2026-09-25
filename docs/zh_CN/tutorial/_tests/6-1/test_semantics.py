# 6-1 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "notes/scratch.fya"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_smoke_test_subcommand():
    out = read("demo_output.txt")
    first = out.split("$ echo")[0]
    assert "uv run flowing test ." in first
    assert "exit=0" in first, "test：冒烟拉起（launch + snapshot 断言 + shutdown）"


def test_repl_debug_eval():
    out = read("demo_output.txt")
    seg = out.split("repl-debug")[1].split("compile")[0]
    assert ">>>agent-main" in seg, "repl-debug 的 /eval 在 Agent 上下文求值"


def test_compile_subcommand():
    out = read("demo_output.txt")
    seg = out.split("compile")[1]
    assert "exit=0" in seg
    assert (ROOT / "notes" / "scratch.py").is_file(), "compile 落盘 .py 产物"
    py = read("notes/scratch.py")
    assert "由 flowing 编译器自动生成" in py
    assert "class ScratchAgent(Agent)" in py
