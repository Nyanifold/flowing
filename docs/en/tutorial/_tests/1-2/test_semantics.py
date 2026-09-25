# 1-2 semantic assertions: target the recorded transcript and project files;
# no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_messages.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_message_kinds_constructed():
    out = read("demo_output.txt")
    for kind in ("user", "provider", "tool", "event"):
        assert f"kind={kind}" in out, f"a {kind} message should be constructed"
    # The remaining values of the seven-value MessageKind are listed in the
    # chapter text (this demo covers 4 kinds)
    assert "thinking" in out and "tool_call" in out and "struct" in out


def test_media_inline_base64():
    out = read("demo_output.txt")
    assert "image" in out and "iVBORw0KGgo" in out, \
        "media blocks should be inlined as base64 (data is the authoritative representation)"


def test_pairing_anchor_enforced():
    out = read("demo_output.txt")
    assert "tool_call_id=call_9" in out and "tool_status=completed" in out
    assert out.count("ValueError") == 2, \
        "both orphan/mismatched constructions should raise ValueError"
