# 1-1 semantic assertions: against the recorded transcript and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ── project file completeness ─────────────────────────────────

def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_entries.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_root_fya_has_read_tools():
    root_fya = read("root.fya")
    assert "- read" in root_fya and "- glob" in root_fya


# ── recorded transcript completeness ──────────────────────────

def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "recorded transcript missing or empty: demo_output.txt"


# ── semantic key points ───────────────────────────────────────

def test_message_is_fire_and_forget():
    out = read("demo_output.txt")
    assert "message id=1" in out, "message() should return the message id immediately"
    assert "caller does not wait for the turn result" in out


def test_steer_absorbed_into_current_turn():
    out = read("demo_output.txt")
    assert "pri=STEER" in out, "the tree cursor replay should show the STEER-priority steer message"
    assert "steer received" in out, "the steer content should be seen by the current turn's model and answered"


def test_query_waits_turn_result():
    out = read("demo_output.txt")
    assert "status=completed" in out, "query() should get a TurnResult (completed)"
    assert "2 files" in out, "the query turn's answer should be based on an actual glob lookup"


def test_tree_replay_shows_turn_boundaries():
    out = read("demo_output.txt")
    assert out.count("turn_end=True") >= 2, "each of the two turns should end with one turn_end=True PROVIDER message"
