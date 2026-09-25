# 6-2 semantic assertions: against the recorded transcript and project files,
# no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_embed.py"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_host_config_override():
    out = read("demo_output.txt")
    assert "agent.timeout = 90" in out, "set_config override layer wins"


def test_hook_subscription_output():
    out = read("demo_output.txt")
    assert "hello from host" in out, "on_provider_delta streams text to screen"
    assert "[host] turn status=completed" in out


def test_host_approval_gate():
    out = read("demo_output.txt")
    assert "[host approval] bash" in out
    assert "echo hello from host" in out
    assert "[host approval] approved" in out, \
        "approval goes through before_tool_call and passes after confirmation"


def test_shutdown_responsibility():
    out = read("demo_output.txt")
    assert "shutdown complete: tail records drained" in out, \
        "write-behind requires the host to call shutdown"
