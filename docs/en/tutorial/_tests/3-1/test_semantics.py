# 3-1 semantic assertions: against the recorded transcript and project files;
# no real provider calls.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_mechanics.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, \
        "recorded transcript missing or empty: demo_output.txt"


def test_steer_absorbed_not_aborting():
    out = read("demo_output.txt")
    head = out.split("== Experiment 2")[0]
    assert "status=completed (completed = unbroken)" in head
    assert "pri=STEER" in out, "STEER message should be appended with the in-turn batch"


def test_interrupt_aborts_at_checkpoint():
    out = read("demo_output.txt")
    seg = out.split("== Experiment 2")[1].split("== Experiment 3")[0]
    assert "status=cancelled aborted=True" in seg, \
        "INTERRUPT should end the current turn at the checkpoint with cancelled"
    assert "pri=INTERRUPT" in out, \
        "INTERRUPT message should be on the tree (visible to the next turn)"


def test_pause_blocks_at_checkpoint():
    out = read("demo_output.txt")
    seg = out.split("== Experiment 3")[1].split("== Experiment 4")[0]
    assert "current_turn is not None: True" in seg
    assert "after resume status=completed" in seg


def test_cancel_races_inflight_keeps_partial():
    out = read("demo_output.txt")
    seg = out.split("== Experiment 4")[1]
    assert "status=cancelled" in seg
    assert "nodes appended this turn: 2" in seg, \
        "cancel races down in-flight generation: user + partial provider are kept on disk"


def test_turn_boundaries_visible():
    out = read("demo_output.txt")
    assert out.count("turn_end=True") >= 4, \
        "each normally finished turn carries one boundary marker"
