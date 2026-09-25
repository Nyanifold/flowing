# 4-1 semantic assertions: against the recorded transcripts and project
# files; does not call a real provider.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_crash.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "dir_output.txt",
                 "crash_output.txt", "crash_wc.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"transcript missing or empty: {name}"


def test_tree_jsonl_record_stream_layout():
    out = read("dir_output.txt")
    assert '"type": "meta", "format_version": 1' in out, \
        "first line of tree.jsonl is always the metadata record"
    assert "3 .flowing/agent-main/tree.jsonl" in out, \
        "one turn = meta + user + provider lines (record stream: one record per line)"


def test_meta_identity_keys():
    out = read("dir_output.txt")
    for key in ("agent_type", "parent_agent_id", "created_at", "args"):
        assert key in out, f"identity four keys missing {key}"


def test_crash_simulated():
    out = read("crash_output.txt")
    assert "Turn finished status=completed" in out
    assert "os._exit(9)" in out, "crash simulation must state the kill -9 equivalence"
    assert read("crash_wc.txt").strip().startswith("5 "), \
        "records left by the crashed process are on disk (write-behind finishes within the ms window)"


def test_recovery_replays_history():
    out = read("repl_output_run2.txt")
    assert "731" in out and "purple" in out, \
        "after recovery both the number from the clean process and the color from the crashed process are recalled (replay rebuilds the tree)"
