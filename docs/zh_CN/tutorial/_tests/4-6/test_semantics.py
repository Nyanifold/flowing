# 4-6 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "demo_control.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_flaky_entry_and_adapter():
    providers = read("providers.yaml")
    assert "flaky:" in providers and "adapter: flaky" in providers
    demo = read("demo_control.py")
    assert "@register_provider" in demo
    assert "RateLimitedError" in demo


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "留档缺失或为空"


def test_retry_with_observation():
    out = read("demo_output.txt")
    assert "status=completed" in out.split("②")[0]
    assert "'error': 'RateLimitedError'" in out
    assert "实际调用次数: 3（2 次失败 + 1 次成功）" in out


def test_before_cancel_intercepts():
    out = read("demo_output.txt")
    seg = out.split("②")[1].split("③")[0]
    assert "cancel 被 before_cancel 拦截" in seg
    assert "信号未置位" in seg


def test_abort_turn_and_agent_alive():
    out = read("demo_output.txt")
    seg = out.split("③")[1]
    assert "abort_turn: status=cancelled" in seg
    assert "下一回合照常: status=completed" in seg, \
        "abort 只终止当前回合，Agent 存活"
