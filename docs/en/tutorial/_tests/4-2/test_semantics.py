# 4-2 semantic assertions: check the recorded transcripts and project files; no real provider is called.
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/prefs.py", "tools/todo.py",
                 "tools/app_info.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_tool_file_names_match_identity():
    # file channel: the file name (snake_case -> kebab-case) is the identity and must match the class name
    for fname, tool in (("tools/prefs.py", '"prefs"'),
                        ("tools/todo.py", '"todo"'),
                        ("tools/app_info.py", '"app-info"')):
        src = read(fname)
        assert f"name = {tool}" in src, f"{fname} must set name to {tool}"


def test_setup_registers_state():
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    for key in ("user_prefs", "todo_items", "visit_count"):
        assert f'self.state.register("{key}"' in script
    assert 'runtime.register_state("app")' in read("main.py")


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"transcript missing or empty: {name}"


def test_run1_set_values():
    out = read("repl_output.txt")
    assert "prefs" in out and "todo" in out and "app-info" in out
    assert "dark" in out and "write the weekly report" in out
    assert "launch #1" in out or "launch count" in out


def test_run2_restored_values():
    out = read("repl_output_run2.txt")
    assert "dark" in out, "preference should be restored across sessions (persisted value wins)"
    assert "write the weekly report" in out and "buy milk" in out, "todo list should come back exactly as saved"
    assert "2" in out, "launch count should be 2 (Runtime-global bag accumulates across sessions)"


def test_state_files_persisted():
    state = (ROOT / ".flowing" / "agent-main" / "state.jsonl").read_text(encoding="utf-8")
    assert '"key": "user_prefs"' in state
    assert '"key": "todo_items"' in state
    assert '"visit_count", "value": 2' in state or '"visit_count"' in state
    app = (ROOT / ".flowing" / "app.jsonl").read_text(encoding="utf-8")
    last = [json.loads(ln) for ln in app.splitlines() if ln.startswith('{"op"')][-1]
    assert last["key"] == "boots" and last["value"] >= 2
