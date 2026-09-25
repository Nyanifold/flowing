# 1-2 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_messages.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0


def test_message_kinds_constructed():
    out = read("demo_output.txt")
    for kind in ("user", "provider", "tool", "event"):
        assert f"kind={kind}" in out, f"应构造 {kind} 类消息"
    # 七值 MessageKind 的其余值在正文中列出（本篇演示覆盖 4 类）
    assert "thinking" in out and "tool_call" in out and "struct" in out


def test_media_inline_base64():
    out = read("demo_output.txt")
    assert "image" in out and "iVBORw0KGgo" in out, \
        "媒体块应 base64 内联（data 是权威表示）"


def test_pairing_anchor_enforced():
    out = read("demo_output.txt")
    assert "tool_call_id=call_9" in out and "tool_status=completed" in out
    assert out.count("ValueError") == 2, "两个孤儿/错配构造都应抛 ValueError"
