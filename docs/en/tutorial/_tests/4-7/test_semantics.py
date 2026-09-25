# 4-7 semantic assertions: against the recorded transcript and project files; no real provider calls.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_internals.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "recorded transcript missing or empty"


def test_custom_dequeue_drain_merge():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert "batch of 3 messages into one turn" in seg, \
        "after overriding _dequeue, three messages should merge into one consumption batch"


def test_turn_result_aggregation():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "status=completed" in seg
    assert "turn.message_ids=" in seg and "token_usage aggregate=" in seg


def test_watch_assignment_events():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1].split("== ④")[0]
    assert "(None, 'zh')" in seg and "('zh', 'en')" in seg, \
        "watch should receive (old, new) assignment event snapshots"


def test_side_query_leaves_no_trace():
    out = read("demo_output.txt")
    seg = out.split("== ④")[1]
    assert "tree nodes" in seg and "→" in seg
    nums = seg.split("tree nodes")[1].split("(")[0]
    a, b = nums.split("→")
    assert a.strip() == b.strip(), "the side_query side channel must not enter the tree or persistence"
