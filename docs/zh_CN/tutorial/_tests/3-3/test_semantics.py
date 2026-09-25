# 3-3 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml", "demo_stream.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_transcripts_present():
    p = ROOT / "demo_output.txt"
    assert p.is_file() and p.stat().st_size > 0, "留档缺失或为空: demo_output.txt"


def test_streaming_many_deltas():
    out = read("demo_output.txt")
    seg = out.split("== 非流式")[0]
    assert "共 47 条 delta" in seg or "共 " in seg, "流式应产生多条 delta"
    import re
    m = re.search(r"共 (\d+) 条 delta；finish=True", seg)
    assert m and int(m.group(1)) >= 10, "流式 delta 应逐段到达（多条）"
    assert "kind=thinking" in seg and "kind=text" in seg, \
        "delta 应分 thinking / text 两类"


def test_usage_only_on_final_frame():
    out = read("demo_output.txt")
    seg = out.split("== 非流式")[0]
    assert "usage✓" in seg, "末帧应携带 usage"
    assert seg.count("usage✓") == 1, "usage 只应出现在末帧"
    assert "message.usage 附着=是" in out


def test_nonstream_single_full_delta():
    out = read("demo_output.txt")
    seg = out.split("== 非流式")[1]
    assert "共 1 条 delta" in seg, "非流式应合成一条全量 delta"
    assert "message.usage 附着=是" in seg
