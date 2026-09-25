# 4-7 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_internals.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "留档缺失或为空"


def test_custom_dequeue_drain_merge():
    out = read("demo_output.txt")
    seg = out.split("== ②")[0]
    assert "drain 合并：本批 3 条消息进一个回合" in seg, \
        "覆写 _dequeue 后三条消息应合并为一个消费批次"


def test_turn_result_aggregation():
    out = read("demo_output.txt")
    seg = out.split("== ②")[1].split("== ③")[0]
    assert "status=completed" in seg
    assert "turn.message_ids=" in seg and "token_usage 聚合=" in seg


def test_watch_assignment_events():
    out = read("demo_output.txt")
    seg = out.split("== ③")[1].split("== ④")[0]
    assert "(None, 'zh')" in seg and "('zh', 'en')" in seg, \
        "watch 应收到 (old, new) 赋值事件快照"


def test_side_query_leaves_no_trace():
    out = read("demo_output.txt")
    seg = out.split("== ④")[1]
    assert "树节点数" in seg and "→" in seg
    nums = seg.split("树节点数")[1].split("（")[0]
    a, b = nums.split("→")
    assert a.strip() == b.strip(), "side_query 副线不进树不落盘"
