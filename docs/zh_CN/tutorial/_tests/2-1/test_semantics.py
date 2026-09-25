# 2-1 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "agents/coder/agent.fya",
                 "agents/reviewer/agent.fya", "agents/auditor/agent.fya",
                 "demo_enabled.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_subagents_declared_as_paths():
    root_fya = read("root.fya")
    head = root_fya.split("---", 1)[0]
    assert "subagents:" in head
    assert "./agents/coder" in head and "./agents/reviewer" in head
    assert "subagent-invoke" in head, "唤起工具须显式声明（注册 ≠ 可见）"
    assert "visible: false" in head, "auditor 以不可见形态声明"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "demo_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_orchestration_dispatched_both():
    out = read("repl_output.txt")
    assert out.count("[tool_call] subagent-invoke") >= 2, \
        "编排者应先后派单 coder 与 reviewer"
    assert "def fib" in out, "coder 产出的代码应进入汇总"
    assert "审查" in out or "LGTM" in out or "意见" in out, \
        "reviewer 的审查意见应被汇总交付"


def test_invisible_entry_not_in_catalog_but_invocable():
    out = read("demo_output.txt")
    assert "auditor: visible=False" in out
    assert "不进 catalog" in out
    assert "审计通道已激活" in out and "status=completed" in out, \
        "visible=False 的条目仍应可编程式唤起（可见性与可执行性分离）"
