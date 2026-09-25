# 1-6 semantic assertions: against the recorded transcripts and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/demo/TOOL.fya",
                 "tools/demo_server.py", "demo_env_failfast.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_mcp_declaration_shape():
    decl = read("tools/demo/TOOL.fya")
    assert "type: mcp" in decl
    assert "command:" in decl, "this chapter uses the stdio (command) form"
    assert "url:" not in decl.split("---")[0], "command and url are mutually exclusive"


def test_declarative_group_reference():
    root_fya = read("root.fya")
    assert "./tools/demo" in root_fya.split("---")[0], "tools: references the group declaration declaratively"
    assert "$script" not in root_fya, "the binding phase expands automatically; no manual setup needed"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "demo_env_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"recorded transcript missing or empty: {name}"


def test_synthesized_tool_names_used():
    out = read("repl_output.txt")
    assert "demo--list-prs" in out and "demo--create-issue" in out, \
        "the agent should call both server-side tools by synthesized name"


def test_mcp_results_returned():
    out = read("repl_output.txt")
    assert "issue_id" in out and "42" in out, "the structured result of create-issue should return to the turn"
    assert "fix typo" in out, "the server data of list-prs should appear in the answer"


def test_env_template_fail_fast():
    out = read("demo_env_output.txt")
    assert "FormatError" in out and "NO_SUCH_VAR" in out, \
        "a missing environment variable should fail fast at assembly time"
