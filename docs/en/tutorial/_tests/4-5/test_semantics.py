# 4-5 semantic assertions: against the recorded transcripts and the project
# files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/build.py",
                 "demo_background.py", "demo_shapes.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_build_is_async_generator():
    src = read("tools/build.py")
    assert "yield" in src, "one of the background forms: execute is an async generator"
    assert "async def execute" in src


def test_transcripts_present():
    for name in ("demo_output.txt", "demo_shapes_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, \
            f"transcript missing or empty: {name}"


def test_pending_receipt_then_segments():
    out = read("demo_output.txt")
    assert "status=pending" in out and "background_task_id=1" in out, \
        "first yield = pending receipt (the turn is not blocked)"
    assert out.count("segment") == 4, \
        "later yields are delivered segment by segment (3 progress + 1 completion summary)"
    assert "receipt" in out


def test_event_delivery_to_tree():
    out = read("demo_output.txt")
    assert "8 EVENT messages enqueued into the tree" in out, \
        "background results are enqueued into the tree as EVENT messages"
    assert "source='tool_result' async tool build: async tool build:" in out


def test_five_output_shapes():
    out = read("demo_shapes_output.txt")
    for line in ("None       → blocks=[]",
                 "str        → blocks=['text']",
                 "dict       → blocks=['struct']",
                 "plain list → blocks=['struct']",
                 "mixed list → blocks=['text', 'image']"):
        assert line in out, f"five shapes missing: {line}"


def test_forbidden_block_and_media_and_error():
    out = read("demo_shapes_output.txt")
    assert "forbidden block type" in out, \
        "forbidden blocks (ToolCallBlock) → ValueError"
    assert "Image → ['image']" in out, \
        "media carriers are normalized into media blocks (base64 inline)"
    assert "build failed: missing dependencies" in out, \
        "an error text block is appended at the end of an error result"
