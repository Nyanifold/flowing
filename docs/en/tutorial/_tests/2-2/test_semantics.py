# 2-2 semantic assertions: against the recorded transcript and project files,
# without calling a real provider.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "agents/assistant/agent.fya"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_orchestrator_prompt_declares_name_resume():
    root_fya = read("root.fya")
    assert 'name "memo"' in root_fya
    assert 'resume="memo"' in root_fya


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, \
            f"recorded transcript missing or empty: {name}"


def test_named_creation_then_resume():
    out = read("repl_output.txt")
    assert out.count("[tool_call] subagent-invoke") >= 2, \
        "expected a named invocation followed by a resume of the same instance (two invocations)"


def test_memory_survives_resume():
    out = read("repl_output.txt")
    # the user's second message never restates the number; only the memo
    # instance's context can supply the 42
    assert "42" in out.split('"resume": "memo"')[-1], \
        "after resuming the same instance, the assistant should repeat the number 42 it remembered"


def test_confirmation_relayed():
    out = read("repl_output.txt")
    assert "confirmed" in out, \
        "after the named invocation the orchestrator should relay the assistant's confirmation"
