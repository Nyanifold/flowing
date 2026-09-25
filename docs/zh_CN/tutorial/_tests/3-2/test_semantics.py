# 3-2 语义断言：针对留档与工程文件，不调用真实 provider。
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/switch_mode.py",
                 "demo_context.py", "notes/motto.md"):
        assert (ROOT / name).is_file(), f"缺工程文件: {name}"


def test_prompt_blocks_layered():
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    assert "mode-explain" in script and "mode-poem" in script
    assert 'tags=["mode", "explain"]' in script
    assert "disable_by_tag" in script, "模式切换 = 按 tag 启停 prompt 块"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "demo_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"留档缺失或为空: {name}"


def test_context_three_fields():
    out = read("demo_output.txt")
    assert "system_prompt:" in out and "tools:" in out and "messages:" in out
    assert "('system_prompt', 'dynamic')" in out, "[0] 核心引用块带 dynamic cache 标记"


def test_parsable_three_forms_and_live_eval():
    out = read("demo_output.txt")
    assert "字面量" in out and "模板" in out and "文件引用" in out
    assert "先组装，再提问" in out, "文件引用 $./ 应现场读文件"
    assert "实验模式" in out, "改实例属性后同一模板应取最新值（现场求值）"


def test_before_provider_gen_rewrite():
    out = read("demo_output.txt")
    assert "改写后最后一段: marker" in out
    assert "[0] 核心引用块仍在" in out


def test_mode_switching_via_tool():
    out = read("repl_output.txt")
    assert out.count("[tool_call] switch-mode") == 2, "应两次切换模式"


def test_poem_mode_answered_in_poem():
    out = read("repl_output.txt")
    poem_region = out.split("诗歌模式")[-1]
    lines = [ln for ln in poem_region.splitlines() if ln.strip()]
    poem_like = any("，" in ln or "。" in ln for ln in lines[:8])
    assert poem_like, "诗歌模式的回答应呈现诗句形态"
