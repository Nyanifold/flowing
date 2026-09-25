# 1-1 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ── 工程文件齐全性 ──────────────────────────────────────────

def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_entries.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_root_fya_has_read_tools():
    root_fya = read("root.fya")
    assert "- read" in root_fya and "- glob" in root_fya


# ── 留档齐全性 ──────────────────────────────────────────────

def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "留档缺失或为空: demo_output.txt"


# ── 语义关键点 ──────────────────────────────────────────────

def test_message_is_fire_and_forget():
    out = read("demo_output.txt")
    assert "消息 id=1" in out, "message() 应立即返回消息 id"
    assert "调用方不等回合结果" in out


def test_steer_absorbed_into_current_turn():
    out = read("demo_output.txt")
    assert "pri=STEER" in out, "树上游标回放应能看到 STEER 优先级的导向消息"
    assert "收到导向" in out, "导向内容应被当轮模型看到并响应"


def test_query_waits_turn_result():
    out = read("demo_output.txt")
    assert "status=completed" in out, "query() 应等到 TurnResult（completed）"
    assert "2 个文件" in out, "query 回合应答应基于 glob 的实查结果"


def test_tree_replay_shows_turn_boundaries():
    out = read("demo_output.txt")
    assert out.count("turn_end=True") >= 2, "两个回合各应有一条 turn_end=True 的 PROVIDER 消息"
