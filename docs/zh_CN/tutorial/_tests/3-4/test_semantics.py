# 3-4 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "agents/greeter/agent.fya",
                 "demo_model.py", "demo_budget.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_models_declare_context_window():
    models = read("models.yaml")
    assert models.count("context_window:") == 2, \
        "两个模型条目都应声明 context_window（ratio 的消费方）"


def test_tags_cover_two_entries():
    tags = read("model-tags.yaml")
    assert "default: deepseek-flash" in tags
    assert "chat: deepseek-chat" in tags


def test_transcripts_present():
    for name in ("demo_model_output.txt", "demo_budget_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_two_hop_resolution_and_runtime_switch():
    out = read("demo_model_output.txt")
    assert "model_tag='default' → model='deepseek-v4-flash'" in out
    assert "root.model_tag = 'chat' → model='deepseek-chat'" in out, \
        "运行期改 model_tag 应立即重新解析（0-1 伏笔回收）"


def test_child_declares_own_tag():
    out = read("demo_model_output.txt")
    assert "声明 model_tag='chat' → model='deepseek-chat'" in out, \
        "子 Agent 应在声明层指定自己的 model_tag"


def test_budget_anchor_and_ratio():
    out = read("demo_budget_output.txt")
    first = out.splitlines()[1]
    assert "measured=253" in first and "estimated=0" in first, \
        "首轮后应出现实测锚点（最近带 usage 的 PROVIDER 消息）"
    import re
    ratios = [float(m) for m in re.findall(r"ratio=([0-9.]+)", out)]
    assert len(ratios) == 7 and all(0 <= r < 1 for r in ratios), \
        "usage_ratio 是合法比率（此处远小于 1；不 clamp，>1 即溢出信号）"
    assert "tokens=21 measured=None estimated=21" in out, \
        "空树无锚点：全部按字符启发式估算"
