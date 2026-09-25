# 4-3 semantic assertions: against the recorded transcripts and project
# files; no real provider is invoked.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml",
                 "demo_surgery.py", "demo_synthetic.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_transcripts_present():
    for name in ("demo_surgery_output.txt", "demo_synthetic_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"transcript missing or empty: {name}"


def test_fork_only_moves_cursor():
    out = read("demo_surgery_output.txt")
    seg = out.split("After forking the parallel branch")[1].split("Back at a")[0]
    assert "6 provider" in seg and "parent=5" in seg
    assert "5 user" in seg and "parent=1" in seg, \
        "the south-road branch should fork in parallel from the shared node a(id=1) (fork only moves the cursor, creating no node)"


def test_insert_merges_children():
    out = read("demo_surgery_output.txt")
    assert "b.parent=7" in out and "e.parent=7" in out, \
        "insert at the fork point: existing direct children (b and e) are re-hung under the new node = merge semantics"


def test_update_and_remove_semantics():
    out = read("demo_surgery_output.txt")
    assert "(Note: bring an umbrella)" in out, "update changes content only and leaves the chain untouched"
    assert "d is gone" in out and "its child's parent=3" in out, \
        "remove preserves adjacency: direct children are re-hung to the deleted message's parent"


def test_synthetic_placeholder_closes_pair():
    out = read("demo_synthetic_output.txt")
    assert "synthetic-call_7" in out, "an orphaned tool_call should be closed by a deterministic-id placeholder"
    assert "synthetic=True" in out and "tool_status=error" in out
    assert "Torn session constructed" in out
