# 4-3 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml",
                 "demo_surgery.py", "demo_synthetic.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_transcripts_present():
    for name in ("demo_surgery_output.txt", "demo_synthetic_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_fork_only_moves_cursor():
    out = read("demo_surgery_output.txt")
    seg = out.split("fork 出平行分支后")[1].split("切回 a")[0]
    assert "6 provider" in seg and "parent=5" in seg
    assert "5 user" in seg and "parent=1" in seg, \
        "南路分支应从公共节点 a(id=1) 平行开出（fork 只切游标、不建节点）"


def test_insert_merges_children():
    out = read("demo_surgery_output.txt")
    assert "b.parent=7" in out and "e.parent=7" in out, \
        "insert 在分叉点：既有直接子（b 与 e）重挂到新节点下 = 合并语义"


def test_update_and_remove_semantics():
    out = read("demo_surgery_output.txt")
    assert "（补充：带伞）" in out, "update 只改内容不动链"
    assert "d 已消失" in out and "其子 parent=3" in out, \
        "remove 的邻接保持：直接子重挂到被删消息的亲节点"


def test_synthetic_placeholder_closes_pair():
    out = read("demo_synthetic_output.txt")
    assert "synthetic-call_7" in out, "孤立 tool_call 应由确定性 id 的占位封闭"
    assert "synthetic=True" in out and "tool_status=error" in out
    assert "已构造撕裂 session" in out
