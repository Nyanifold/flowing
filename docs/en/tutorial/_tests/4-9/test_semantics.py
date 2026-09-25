# 4-9 semantic assertions: against the recorded transcript and the project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml",
                 "demo_parsable.py", "notes/refund.md"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "recorded transcript missing or empty"


def test_five_forms():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    for t in ("RAW", "FILE_REF", "EXPRESSION", "TEMPLATE", "LITERAL"):
        assert f"type={t}" in seg, f"five-form detection missing {t}"
    assert "→ 42" in seg, "EXPRESSION returns the native type (not stringified)"
    assert "raw text with {{ braces }}" in seg, "RAW skips all rendering"


def test_render_context():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "from an environment variable" in seg, "env entry"
    assert "→ 60" in seg, "config entry (agent.timeout defaults to 60)"
    assert "→ 'agent-main'" in seg, "self entry"


def test_resolved_reference():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1].split("== ④")[0]
    assert "Policy: Refund policy: full refund within seven days" in seg, ".resolved yields the rendered result"
    assert "Policy: $./notes/refund.md" in seg, "without .resolved only the template source shows"


def test_reserved_and_missing():
    out = read("demo_output.txt")
    seg = out.split("== ④")[1].split("== ⑥")[0]
    assert "Reserved attribute name occupied: 'env'" in seg
    assert "MissingContextError" in seg
    assert "MissingProvideError" in seg


def test_pending_field_position():
    out = read("demo_output.txt")
    seg = out.split("== ⑥")[1]
    assert "MissingFieldError" in seg
    assert "still PENDING" in seg, "field-position _ = a promise that must be fulfilled (creation-pipeline checkpoint)"
