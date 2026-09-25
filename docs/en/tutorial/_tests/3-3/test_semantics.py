# 3-3 semantic assertions: target the transcript and project files; no real
# provider is called.
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_stream.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, \
        "transcript missing or empty: demo_output.txt"


def test_streaming_many_deltas():
    out = read("demo_output.txt")
    seg = out.split("== non-streaming")[0]
    m = re.search(r"→ (\d+) deltas total; finish=True", seg)
    assert m and int(m.group(1)) >= 10, \
        "streaming deltas should arrive piece by piece (many of them)"
    assert "kind=thinking" in seg and "kind=text" in seg, \
        "deltas should come in both thinking and text kinds"


def test_usage_only_on_final_frame():
    out = read("demo_output.txt")
    seg = out.split("== non-streaming")[0]
    assert "usage✓" in seg, "the final frame should carry usage"
    assert seg.count("usage✓") == 1, "usage should appear only on the final frame"
    assert "message.usage attached=yes" in out


def test_nonstream_single_full_delta():
    out = read("demo_output.txt")
    seg = out.split("== non-streaming")[1]
    assert "→ 1 delta total" in seg, \
        "non-streaming should synthesize one full delta"
    assert "message.usage attached=yes" in seg
