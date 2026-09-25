# 5-3 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/echo.py",
                 "composables/rate_limit.py", "demo_composables.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_composable_form():
    src = read("composables/rate_limit.py")
    assert "def use_rate_limit(agent" in src, "use_xxx 的首个参数是 agent"
    assert 'declare("on_rate_limited"' in src, "declare 扩展钩子点"
    assert 'hooks.before_tool_call(_gate, by="rate-limit"' in src, \
        "挂核心钩子点 + by 归组"
    assert "raise Intercepted" in src


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_gate_blocks_over_limit():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert "['completed', 'completed', 'completed', 'blocked', 'blocked']" in seg, \
        "滑窗内前 3 放行、后 2 硬阻断"
    assert "on_rate_limited 观测到 2 次超限" in seg, \
        "声明的扩展钩子点按预期触发（观测通道）"


def test_remove_by_owner():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "移除 1 条 handler；再调一次: status=completed" in seg, \
        "by 归组整组移除后策略失效"


def test_builtin_same_form():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1]
    assert "before_turn 命中 1 条 handler" in seg, \
        "调用内置 Composable 后注册的 handler 可观察到"
