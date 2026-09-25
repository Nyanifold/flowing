# 3-1 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_mechanics.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "留档缺失或为空: demo_output.txt"


def test_steer_absorbed_not_aborting():
    out = read("demo_output.txt")
    head = out.split("== 实验 2")[0]
    assert "status=completed（completed=未被打破）" in head
    assert "pri=STEER" in out, "STEER 消息应随当轮批次挂树"


def test_interrupt_aborts_at_checkpoint():
    out = read("demo_output.txt")
    seg = out.split("== 实验 2")[1].split("== 实验 3")[0]
    assert "status=cancelled aborted=True" in seg, \
        "INTERRUPT 应使当前回合在检查点以 cancelled 收尾"
    assert "pri=INTERRUPT" in out, "INTERRUPT 消息应挂树（下一回合上下文可见）"


def test_pause_blocks_at_checkpoint():
    out = read("demo_output.txt")
    seg = out.split("== 实验 3")[1].split("== 实验 4")[0]
    assert "current_turn 非 None: True" in seg
    assert "resume 后 status=completed" in seg


def test_cancel_races_inflight_keeps_partial():
    out = read("demo_output.txt")
    seg = out.split("== 实验 4")[1]
    assert "status=cancelled" in seg
    assert "已落树节点数=2" in seg, \
        "cancel 中断在途生成：user + partial provider 保留落盘"


def test_turn_boundaries_visible():
    out = read("demo_output.txt")
    assert out.count("turn_end=True") >= 4, "每个正常收尾回合各有一条边界标记"
