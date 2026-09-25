# 4-8 semantic assertions: they target the recorded transcript and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "demo_runtime.py",
                 "nested/other-project/main.py",
                 "nested/other-project/root.fya"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "recorded transcript missing or empty"


def test_dual_runtime_at_isolation():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert "A.project_root = 4-8 (outer project)" in seg
    assert "B.project_root = other-project (nested directory)" in seg
    assert "resolves to B's own root: True" in seg, \
        "@ context is isolated per Task: B's @/ anchors B's own project root"


def test_module_resolve_scope():
    out = read("demo_output.txt")
    assert "→ RuntimeError: @ context not registered" in out, \
        "module-level resolve() is unavailable after launch returns (@ context has been reset)"


def test_provide_end_and_resource():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1].split("== ④")[0]
    assert "inject('app_name') = 'demo-a'" in seg
    assert "sqlite:///demo.db" in seg, \
        "Resource is an out-of-tree direct reference (not routed through the injection chain)"


def test_pool_and_lazy_providers():
    out = read("demo_output.txt")
    seg = out.split("== ④")[1].split("== ⑤")[0]
    assert "pool entries: ['agent-main']" in seg
    assert "candidates: 1 / instantiated: 0" in seg, \
        "construction only scans candidates; the first get instantiates (zero startup cost)"


def test_snapshot_two_levels():
    out = read("demo_output.txt")
    seg = out.split("== ⑤")[1]
    assert "nodes=['agent-main', 'runtime-0']" in seg
    assert "node_id=agent-main model=deepseek-v4-flash" in seg
