# 4-5 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/build.py",
                 "demo_background.py", "demo_shapes.py"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_build_is_async_generator():
    src = read("tools/build.py")
    assert "yield" in src, "后台形态之一：execute 是 async generator"
    assert "async def execute" in src


def test_transcripts_present():
    for name in ("demo_output.txt", "demo_shapes_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_pending_receipt_then_segments():
    out = read("demo_output.txt")
    assert "status=pending" in out and "background_task_id=1" in out, \
        "首 yield = pending 收据（回合不阻塞）"
    assert out.count("segment") == 4, "后续 yield 逐段投递（3 进度 + 1 完成摘要）"
    assert "receipt" in out


def test_event_delivery_to_tree():
    out = read("demo_output.txt")
    assert "EVENT 消息 4 条进树" in out, "后台结果以 EVENT 消息入队进树"
    assert "source='tool_result'" in out


def test_five_output_shapes():
    out = read("demo_shapes_output.txt")
    for line in ("None       → blocks=[]",
                 "str        → blocks=['text']",
                 "dict       → blocks=['struct']",
                 "纯基础 list   → blocks=['struct']",
                 "混合 list    → blocks=['text', 'image']"):
        assert line in out, f"五形态缺: {line}"


def test_forbidden_block_and_media_and_error():
    out = read("demo_shapes_output.txt")
    assert "forbidden block type" in out, "违禁块（ToolCallBlock）→ ValueError"
    assert "Image → ['image']" in out, "媒体载体归一为媒体块（base64 内联）"
    assert "构建失败：缺依赖" in out, "error 结果末尾追加错误文本块"
