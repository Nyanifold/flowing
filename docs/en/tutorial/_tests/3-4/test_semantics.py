# 3-4 semantic assertions: against the recorded transcripts and project files; no real provider calls.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "agents/greeter/agent.fya",
                 "demo_model.py", "demo_budget.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_models_declare_context_window():
    models = read("models.yaml")
    assert models.count("context_window:") == 2, \
        "both model entries should declare context_window (the consumer of ratio)"


def test_tags_cover_two_entries():
    tags = read("model-tags.yaml")
    assert "default: deepseek-flash" in tags
    assert "chat: deepseek-chat" in tags


def test_transcripts_present():
    for name in ("demo_model_output.txt", "demo_budget_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, \
            f"transcript missing or empty: {name}"


def test_two_hop_resolution_and_runtime_switch():
    out = read("demo_model_output.txt")
    assert "model_tag='default' -> model='deepseek-v4-flash'" in out
    assert "root.model_tag = 'chat' -> model='deepseek-chat'" in out, \
        "assigning model_tag at runtime should re-resolve immediately"


def test_child_declares_own_tag():
    out = read("demo_model_output.txt")
    assert "declared model_tag='chat' -> model='deepseek-chat'" in out, \
        "the subagent should declare its own model_tag at the declaration layer"


def test_budget_anchor_and_ratio():
    out = read("demo_budget_output.txt")
    first = out.splitlines()[1]
    assert "measured=198" in first and "estimated=0" in first, \
        "after round 1 a measured anchor should appear (the nearest PROVIDER message carrying usage)"
    import re
    ratios = [float(m) for m in re.findall(r"ratio=([0-9.]+)", out)]
    assert len(ratios) == 7 and all(0 <= r < 1 for r in ratios), \
        "usage_ratio is a valid ratio (far below 1 here; not clamped, >1 is the overflow signal)"
    assert "tokens=18 measured=None estimated=18" in out, \
        "empty tree has no anchor: everything is estimated by the character heuristic"
