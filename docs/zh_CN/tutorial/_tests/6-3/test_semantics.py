# 6-3 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "config.yaml",
                 "alt-providers.yaml", "demo_ops.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_project_config_file_wired():
    cfg = read("config.yaml")
    assert "timeout: 45" in cfg, "项目级 config.yaml 是配置链的中层"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_config_chain_layers():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert "agent.timeout = 45（框架默认 60）" in seg, \
        "项目级覆盖框架默认"
    assert "agent.timeout = 90" in seg.split("set_config")[1], \
        "set_config 覆盖层最优先"


def test_provider_source_override():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "默认 provider 候选: ['deepseek']" in seg
    assert "set_providers 在 mount 前编程覆盖" in seg


def test_pool_and_archive():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1].split("== ④")[0]
    assert "exp-1" in read("main.py") or "exp-1" in read("demo_ops.py")
    assert "archive_agent 后 get_agent → None" in seg, \
        "归档 = 运行时遗忘（名录移除、文件留档）"


def test_snapshot_audit():
    out = read("demo_output.txt")
    seg = out.split("== ④")[1]
    assert "config_overrides={'agent.timeout': 90}" in seg
    assert "_unstable.logging" in seg, "_unstable 边界声明"
