# 2-4 semantic assertions: against the recorded transcripts and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "agents/coder/agent.fya",
                 "tools/todo.py", "demo_fanout.py",
                 "target/pkg/calc.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_main_provides_cwd_before_mount():
    main_py = read("main.py")
    assert 'runtime.provide("cwd", cwd)' in main_py
    assert main_py.index('provide("cwd"') < main_py.index("runtime.mount")


def test_root_declares_full_stack():
    head = read("root.fya").split("---", 1)[0]
    assert "subagent-invoke" in head and "./tools/todo.py" in head
    assert "explore-agent" in head and "./agents/coder" in head


def test_coder_declares_builtin_six():
    coder = read("agents/coder/agent.fya")
    for bare in ("- read", "- write", "- edit", "- grep", "- glob", "- bash"):
        assert bare in coder, f"coder should declare {bare} by bare name"
    assert "{{ cwd }}" in coder and 'self.inject("cwd")' in coder


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt",
                 "demo_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"transcript missing or empty: {name}"


def test_run_a_explore_then_todo():
    out = read("repl_output.txt")
    assert out.count("[tool_call] subagent-invoke") >= 1, "should dispatch explore-agent to survey the structure"
    assert "[tool_call] todo" in out, "should break down tasks with todo"
    assert "6 tasks total" in out, "todo should produce a 6-task list"


def test_run_b_coder_wrote_readme():
    out = read("repl_output_run2.txt")
    assert out.count("[tool_call] subagent-invoke") >= 1, "should dispatch coder to write the README"
    assert "self-check" in out, "coder should submit and self-check"
    readme = ROOT / "target" / "README.md"
    assert readme.is_file() and readme.stat().st_size > 500, \
        "README.md should be actually written with substantial content"
    content = readme.read_text(encoding="utf-8")
    assert "add" in content and "div" in content and "divisor must not be 0" in content, \
        "README should accurately describe the API of calc.py"


def test_fanout_parallel_coders():
    out = read("demo_output.txt")
    assert "coder-a: status=completed" in out
    assert "coder-b: status=completed" in out
    assert (ROOT / "target" / "notes" / "adder.md").is_file()
    assert (ROOT / "target" / "notes" / "divider.md").is_file()
