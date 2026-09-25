# 5-1 semantic assertions: they target the transcripts and project files and do not call a real provider.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/payment.py", "tools/audit.py",
                 "agents/greeter/agent.fya", "notes/handwritten.py",
                 "demo_fya.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_root_fya_full_fields():
    head = read("root.fya").split("---", 1)[0]
    assert "name: root" in head, "name consistency assertion"
    assert "user_id: str" in head, "args sugar"
    assert "default: en" in head, "args full expansion"
    assert "./tools/*.py" in head, "glob expansion"
    assert "as greet" in head, "as alias"
    assert "subagent-invoke" in head


def test_transcripts_present():
    for name in ("demo_output.txt", "repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"transcript missing or empty: {name}"


def test_compiled_equals_handwritten():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert seg.count(": True") == 3, \
        "compiled product and handwritten subclass must be equal item by item (description/template/args key set)"


def test_glob_and_alias_entry():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "tools (glob expansion): ['audit', 'payment', 'subagent-invoke']" in seg
    assert "specified={'tone': 'casual'}" in seg, "alias entry's specified fixed value"
    assert "name='greet'" in seg and "no tone" in seg, \
        "catalog view: the specified parameter is removed from the LLM-visible surface"


def test_negative_assertions():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1]
    assert "name consistency assertion: NameMismatchError" in seg
    assert "UnknownHookPointError" in seg, "@on undeclared hook point → fail-fast at creation"


def test_repl_end_to_end():
    out = read("repl_output.txt")
    assert "subagent-invoke" in out and "greet" in out
    assert "[hook] before_tool_call" in out, "the @on hook in $script should fire and print"
    assert "u-7" in out, "args reach the template via setup ({{ user_id }})"
