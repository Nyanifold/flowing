# 4-8 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "demo_runtime.py",
                 "nested/other-project/main.py",
                 "nested/other-project/root.fya"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "留档缺失或为空"


def test_dual_runtime_at_isolation():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert "A.project_root = 4-8（外层工程）" in seg
    assert "B.project_root = other-project（嵌套目录内）" in seg
    assert "解析到 B 自己的根：True" in seg, \
        "@ 上下文按 Task 隔离：B 的 @/ 锚 B 自己的项目根"


def test_module_resolve_scope():
    out = read("demo_output.txt")
    assert "→ RuntimeError: @ context not registered" in out, \
        "launch 返回后模块级 resolve() 不可用（@ 上下文已复位）"


def test_provide_end_and_resource():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1].split("== ④")[0]
    assert "inject('app_name') = 'demo-a'" in seg
    assert "sqlite:///demo.db" in seg, \
        "Resource 树外直引（不走注入链）"


def test_pool_and_lazy_providers():
    out = read("demo_output.txt")
    seg = out.split("== ④")[1].split("== ⑤")[0]
    assert "池条目: ['agent-main']" in seg
    assert "候选 1 个 / 已实例化 0 个" in seg, \
        "构造期只扫描候选，首次 get 才实例化（零启动成本）"


def test_snapshot_two_levels():
    out = read("demo_output.txt")
    seg = out.split("== ⑤")[1]
    assert "nodes=['agent-main', 'runtime-0']" in seg
    assert "node_id=agent-main model=deepseek-v4-flash" in seg
