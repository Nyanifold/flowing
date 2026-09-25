# 6-3 semantic assertions: target the recorded transcript and project files; no real provider calls.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "config.yaml",
                 "alt-providers.yaml", "demo_ops.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_project_config_file_wired():
    cfg = read("config.yaml")
    assert "timeout: 45" in cfg, \
        "project-level config.yaml is the middle layer of the configuration chain"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_config_chain_layers():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert "agent.timeout = 45 (framework default 60)" in seg, \
        "project level overrides the framework default"
    assert "agent.timeout = 90" in out.split("set_config")[1], \
        "the set_config overlay wins"


def test_provider_source_override():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "default provider candidates: ['deepseek']" in seg
    assert "set_providers overrides programmatically before mount" in seg


def test_pool_and_archive():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1].split("== ④")[0]
    assert "exp-1" in read("main.py") or "exp-1" in read("demo_ops.py")
    assert "get_agent after archive_agent → None" in seg, \
        "archive = forgotten by the runtime (removed from the registry, files kept on disk)"


def test_snapshot_audit():
    out = read("demo_output.txt")
    seg = out.split("== ④")[1]
    assert "config_overrides={'agent.timeout': 90}" in seg
    assert "_unstable.logging" in seg, "_unstable boundary statement"
