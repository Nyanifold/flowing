# 4-6 semantic assertions: target the recorded transcript and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "demo_control.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_flaky_entry_and_adapter():
    providers = read("providers.yaml")
    assert "flaky:" in providers and "adapter: flaky" in providers
    demo = read("demo_control.py")
    assert "@register_provider" in demo
    assert "RateLimitedError" in demo


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "recorded transcript missing or empty"


def test_retry_with_observation():
    out = read("demo_output.txt")
    assert "status=completed" in out.split("②")[0]
    assert "'error': 'RateLimitedError'" in out
    assert "actual calls: 3 (2 failures + 1 success)" in out


def test_before_cancel_intercepts():
    out = read("demo_output.txt")
    seg = out.split("②")[1].split("③")[0]
    assert "cancel intercepted by before_cancel" in seg
    assert "signal not set" in seg


def test_abort_turn_and_agent_alive():
    out = read("demo_output.txt")
    seg = out.split("③")[1]
    assert "abort_turn: status=cancelled" in seg
    assert "next turn as usual: status=completed" in seg, \
        "abort terminates only the current turn; the agent stays alive"
