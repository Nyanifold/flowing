# 1-4 semantic assertions: against the recorded transcript and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_root_fya_has_guard_and_observer():
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    # two mounting methods: @on declared on the class + registered inside setup()
    assert '@on("after_turn")' in script, "the observer handler is declared with @on"
    assert "self.hooks.before_tool_call(_guard)" in script, \
        "the interception handler is registered inside setup()"
    assert "raise Intercepted" in script, "the interception exit is the hard-block signal"


def test_setup_signature_matches_args():
    root_fya = read("root.fya")
    head = root_fya.split("---", 1)[0]
    script = root_fya.split("$script:", 1)[1]
    assert "user_name: str" in head
    assert "async def setup(self, user_name: str)" in script, \
        "setup parameters must correspond one-to-one with the args declaration (creation-time validation)"


def test_transcripts_present():
    for name in ("repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"recorded transcript missing or empty: {name}"


def test_rm_was_blocked():
    out = read("repl_output.txt")
    assert "[tool:blocked]" in out, "the rm command should be hard-blocked into blocked by Intercepted"
    assert "Deletion not allowed" in out, "the blocked result should carry the interception reason (visible to the LLM)"


def test_ls_was_allowed():
    out = read("repl_output.txt")
    assert "[tool:completed] bash" in out, "the ls command should be allowed to execute normally"
    assert "使用说明.md" in out, "the actual output of ls should feed into the answer"


def test_after_turn_observed_both_turns():
    out = read("repl_output.txt")
    assert "ask_count=1" in out and "ask_count=2" in out, \
        "after_turn should fire at the end of every turn (including the intercepted one)"


def test_no_file_was_deleted():
    assert (ROOT / "notes" / "路线图.md").exists(), "the file must remain intact after the interception"
