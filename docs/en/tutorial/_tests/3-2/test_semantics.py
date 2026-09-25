# 3-2 semantic assertions: against the recorded transcripts and project files;
# no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "tools/switch_mode.py",
                 "demo_context.py", "notes/motto.md"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_prompt_blocks_layered():
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    assert "mode-explain" in script and "mode-poem" in script
    assert 'tags=["mode", "explain"]' in script
    assert "disable_by_tag" in script, "mode switching = enabling/disabling prompt blocks by tag"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt", "demo_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"recorded transcript missing or empty: {name}"


def test_context_three_fields():
    out = read("demo_output.txt")
    assert "system_prompt:" in out and "tools:" in out and "messages:" in out
    assert "('system_prompt', 'dynamic')" in out, \
        "[0] core reference block carries the dynamic cache mark"


def test_parsable_three_forms_and_live_eval():
    out = read("demo_output.txt")
    assert "literal" in out and "template" in out and "file reference" in out
    assert "Assemble first, then ask" in out, \
        "file reference $./ should read the file at assembly time"
    assert "experiment mode" in out, \
        "the same template should take the latest value after the attribute changes (live evaluation)"


def test_before_provider_gen_rewrite():
    out = read("demo_output.txt")
    assert "last segment after rewrite: marker" in out
    assert "[0] core reference block still present" in out


def test_mode_switching_via_tool():
    out = read("repl_output.txt")
    assert out.count("[tool_call] switch-mode") == 2, "the mode should be switched twice"


def test_poem_mode_answered_in_poem():
    out = read("repl_output.txt")
    # the answer between the switch to poem and the next tool call is the poem
    poem_region = out.split("[tool:completed] switch-mode ->")[1]
    poem_region = poem_region.split("[tool_call]")[0]
    lines = [ln for ln in poem_region.splitlines() if ln.strip()]
    list_markers = ("- ", "* ", "1.", "2.", "3.")
    assert len(lines) >= 3, "the poem-mode answer should be a short poem of several lines"
    assert not any(ln.lstrip().startswith(list_markers) for ln in lines), \
        "the poem-mode answer should not be a bullet or numbered list"
