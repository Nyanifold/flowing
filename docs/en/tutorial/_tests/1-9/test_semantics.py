# 1-9 semantic assertions: they target the recorded transcripts and project
# files only; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FIXED_PHRASE = "May your day be smooth, and may all things go well with you."


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml", "models.yaml",
                 "model-tags.yaml", "skills/polite.md"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_plugin_installed_before_mount():
    main_py = read("main.py")
    assert "runtime.install(SkillPlugin())" in main_py
    assert main_py.index("install(SkillPlugin") < main_py.index("runtime.mount")


def test_skill_load_declared():
    root_fya = read("root.fya")
    head = root_fya.split("---", 1)[0]
    assert "skill-load" in head, \
        "install only registers the tool body; the agent must declare it explicitly to make it visible to the LLM"
    assert "./skills/polite.md" in head


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt",
                 "repl_input_run2.txt", "repl_output_run2.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, \
            f"recorded transcript missing or empty: {name}"


def test_skill_load_called_and_content_delivered():
    out = read("repl_output.txt")
    assert "[tool_call] skill-load" in out, \
        "the LLM should call skill-load to load the skill"
    assert "4  event" in out, "the skill body should enter the tree as an EVENT message"
    assert "# Politeness rules" in out, \
        "the skill body (fixed phrase rules) should appear in the message chain"


def test_fixed_phrase_used_in_answer():
    out = read("repl_output.txt")
    # The answer written after the EVENT delivery must use the fixed
    # sentence pattern prescribed by the skill body.
    assert FIXED_PHRASE in out


def test_system_reminder_every_turn():
    for name in ("repl_output.txt", "repl_output_run2.txt"):
        out = read(name)
        # The /messages summary truncates the tail of long lines, so assert
        # on the head of the reminder text (one reminder per turn).
        assert out.count("[Reminder] Current working directory") >= 2, \
            f"{name}: a reminder message should appear at the start of every turn"


def test_prompt_until_steer_rerun():
    out = read("repl_output_run2.txt")
    assert "[steer] Acceptance condition not met" in out, \
        "a final reply without DONE should trigger a steer rerun (the steering message is visible in the same turn)"
