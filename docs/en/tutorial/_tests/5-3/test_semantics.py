# 5-3 semantic assertions: against the recorded transcript and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/echo.py",
                 "composables/rate_limit.py", "demo_composables.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_composable_form():
    src = read("composables/rate_limit.py")
    assert "def use_rate_limit(agent" in src, "use_xxx takes the agent as its first parameter"
    assert 'declare("on_rate_limited"' in src, "declares an extension hook point"
    assert 'hooks.before_tool_call(_gate, by="rate-limit"' in src, \
        "attaches to a core hook point + by grouping"
    assert "raise Intercepted" in src


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_gate_blocks_over_limit():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert "['completed', 'completed', 'completed', 'blocked', 'blocked']" in seg, \
        "within the window, first 3 pass and last 2 are hard-blocked"
    assert "on_rate_limited observed 2 over-limit calls" in seg, \
        "the declared extension hook point fires as expected (observation channel)"


def test_remove_by_owner():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "removed 1 handler(s); one more call: status=completed" in seg, \
        "after by-group removal the policy no longer applies"


def test_builtin_same_form():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1]
    assert "before_turn matches 1 handler(s)" in seg, \
        "the handler registered by the built-in Composable is observable"
