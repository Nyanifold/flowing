# 6-2 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_embed.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_host_config_override():
    out = read("demo_output.txt")
    assert "agent.timeout = 90" in out, "set_config 覆盖层最优先"


def test_hook_subscription_output():
    out = read("demo_output.txt")
    assert "输出为" in out and "宿主你好" in out, "on_provider_delta 流式上屏"
    assert "[宿主] 回合 status=completed" in out


def test_host_approval_gate():
    out = read("demo_output.txt")
    assert "[宿主审批] bash args={'command': 'echo 宿主你好'}" in out
    assert "[宿主审批] 通过" in out, "审批经 before_tool_call，人工确认后再放行"


def test_shutdown_responsibility():
    out = read("demo_output.txt")
    assert "shutdown 完成：尾部记录已排空" in out, \
        "write-behind 需要宿主负责 shutdown"
