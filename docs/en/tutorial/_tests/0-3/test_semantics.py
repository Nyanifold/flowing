# 0-3 semantic assertions: against the transcript and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# -- project file completeness -----------------------------------------

def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_root_fya_declares_orchestrator():
    root_fya = read("root.fya")
    assert "subagent-invoke" in root_fya, \
        "the orchestrator must declare subagent-invoke explicitly"
    assert "explore-agent" in root_fya, \
        "subagents: should declare explore-agent"
    # declarations use bare names (namespace omitted; comments excluded)
    for line in root_fya.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if stripped.startswith("- "):
            assert not stripped.removeprefix("- ").startswith("builtin::"), \
                f"declarations should use bare names: {line}"


# -- transcript completeness --------------------------------------------

def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, \
            f"transcript missing or empty: {name}"


# -- semantic key points -------------------------------------------------

def test_first_task_delegated_and_reported():
    out = read("repl_output.txt")
    assert out.count("[tool_call] subagent-invoke") >= 2, \
        "both tasks should be dispatched via subagent-invoke"
    assert "使用说明.md" in out and "路线图.md" in out, \
        "the explore agent's report should cover both actual files"


def test_write_task_refused_honestly():
    out = read("repl_output.txt")
    assert "capability boundary" in out and "read-only" in out, \
        "the write task should be reported honestly as beyond the capability boundary"
    assert "no file was created" in out or "not fabricate" in out, \
        "the report should state explicitly that no file was created and no result was fabricated"


def test_no_file_was_written():
    assert not (ROOT / "notes" / "总结.md").exists(), \
        "the explore agent is read-only; 总结.md must not be created"
