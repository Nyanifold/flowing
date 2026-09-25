# 0-2 semantic assertions: target the recorded transcript and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ── project file completeness ─────────────────────────────────

def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_root_fya_declares_tools_bare():
    root_fya = read("root.fya")
    assert "tools:" in root_fya
    # bare-name declaration: tools list items carry no builtin:: prefix (namespace omitted; comments excepted)
    for line in root_fya.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if stripped.startswith("- "):
            assert not stripped.removeprefix("- ").startswith("builtin::"), \
                f"tools declaration should use bare names: {line}"


def test_notes_fixture_present():
    assert (ROOT / "notes" / "使用说明.md").is_file()
    assert (ROOT / "notes" / "路线图.md").is_file()


# ── transcript completeness ───────────────────────────────────

def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"transcript missing or empty: {name}"


# ── semantic key points ───────────────────────────────────────

def test_tool_calls_happened():
    out = read("repl_output.txt")
    assert "[tool_call]" in out, "agent should issue tool calls (glob/read)"
    assert "[tool:completed]" in out, "tool calls should have completion receipts"


def test_agent_read_project_content():
    out = read("repl_output.txt")
    # the agent answers after reading real file content via glob/read (semantic key point)
    assert "使用说明.md" in out or "路线图" in out, "answer should mention the actually-read files"
    assert "Python" in out, "the install point of 使用说明.md (Python 3.13) should be summarized"


def test_answer_not_from_thin_air():
    out = read("repl_output.txt")
    # the project Q&A answer should be based on the content read, not a refusal to answer
    assert "unable to" not in out.split("(tool:completed)")[-1][:50], \
        "agent should not claim it cannot answer after successfully reading the files"
