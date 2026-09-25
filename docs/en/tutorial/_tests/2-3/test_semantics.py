# 2-3 semantic assertions: against the recorded transcripts and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/todo.py", "tools/todo_impl.py",
                 "tools/todo-fn/TOOL.fya", "tools/shout.py",
                 "demo_channels.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_tool_authoring_contract():
    todo = read("tools/todo.py")
    assert 'name = "todo"' in todo, "name is required (not inferred from the class name)"
    assert "args_model = TodoArgs" in todo, "args_model: declaration is the model"
    assert "async def execute(self, *, tasks_text: str) -> dict" in todo, \
        "execute: scattered parameters in, plain values out"


def test_root_declares_todo_by_file_path():
    head = read("root.fya").split("---", 1)[0]
    assert "- ./tools/todo.py" in head, \
        "a file-implemented tool is declared with an explicit file path"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "demo_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, \
            f"recorded transcript missing or empty: {name}"


def test_todo_parsed_and_counted():
    out = read("repl_output.txt")
    assert "[tool_call] todo" in out
    assert "[tool:completed] todo" in out
    assert "3 items total, 2 open" in out, \
        "todo should parse the 3 tasks (empty lines dropped) and report the open count"


def test_invalid_input_goes_error_channel():
    out = read("repl_output.txt")
    assert "[tool:error] todo" in out, \
        "an empty list should produce an error result, not an exception"
    assert "Task list is empty: provide at least one task" in out, \
        "the error reason (visible to the LLM) should be relayed faithfully"


def test_three_channels_resolve():
    out = read("demo_output.txt")
    assert "Channel A handwritten subclass" in out and "name='todo'" in out
    assert "Channel C callable pointer" in out and "name='todo-fn'" in out
    assert "Channel B @flowing_tool function" in out and "name='shout'" in out
    assert "same params schema: True" in out
