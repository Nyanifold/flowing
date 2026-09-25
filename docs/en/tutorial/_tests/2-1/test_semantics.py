# 2-1 semantic assertions: against the recorded transcripts and project
# files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "agents/coder/agent.fya",
                 "agents/reviewer/agent.fya", "agents/auditor/agent.fya",
                 "demo_enabled.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_subagents_declared_as_paths():
    root_fya = read("root.fya")
    head = root_fya.split("---", 1)[0]
    assert "subagents:" in head
    assert "./agents/coder" in head and "./agents/reviewer" in head
    assert "subagent-invoke" in head, \
        "the invocation tool must be declared explicitly (registration != visibility)"
    assert "visible: false" in head, \
        "auditor must be declared in invisible form"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "demo_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, \
            f"recorded transcript missing or empty: {name}"


def test_orchestration_dispatched_both():
    out = read("repl_output.txt")
    assert out.count("[tool_call] subagent-invoke") >= 2, \
        "the orchestrator should dispatch coder and reviewer in sequence"
    assert "def fib" in out, "the code produced by coder should appear in the summary"
    assert "LGTM" in out or "review" in out.lower(), \
        "the reviewer's findings should be summarized and delivered"


def test_invisible_entry_not_in_catalog_but_invocable():
    out = read("demo_output.txt")
    assert "auditor: visible=False" in out
    assert "not in catalog" in out
    assert "Audit channel activated" in out and "status=completed" in out, \
        "a visible=False entry must remain programmatically invocable (visibility is separated from executability)"
