# 1-3 semantic assertions: against the recorded transcripts and project files; no real provider is called.
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_project_files_complete():
    for name in ("main.py", "root.fya", "providers.yaml",
                 "models.yaml", "model-tags.yaml"):
        assert (ROOT / name).is_file(), f"missing project file: {name}"


def test_args_declared_two_forms():
    root_fya = read("root.fya")
    assert "user_name: str" in root_fya, "sugar form: a bare type string → a required parameter"
    assert "locale:" in root_fya and "default: zh" in root_fya, \
        "full form: JSON Schema expansion + a default value"


def test_setup_does_typical_things():
    root_fya = read("root.fya")
    script = root_fya.split("$script:", 1)[1]
    assert "self.user_name = user_name" in script, "① store the param on self"
    assert "self.ask_count = 0" in script, "③ counter initialization"
    assert 'self.provide("locale", locale)' in script, "④ put it on the provide chain"
    assert 'self.inject("timezone")' in script, "⑤ take the upper-level provided value"


def test_main_provides_before_mount():
    main_py = read("main.py")
    provide_pos = main_py.index('runtime.provide("timezone"')
    mount_pos = main_py.index("runtime.mount")
    assert provide_pos < mount_pos, "a value that affects assembly must be provided before mount"


def test_transcripts_present():
    for name in ("cli_error_output.txt", "repl_input.txt", "repl_output.txt"):
        p = ROOT / name
        assert p.is_file() and p.stat().st_size > 0, f"recorded transcript missing or empty: {name}"


def test_missing_required_arg_fails_fast():
    out = read("cli_error_output.txt")
    assert "user_name" in out, "the missing-required-argument error should name user_name"


def test_args_reach_setup_and_template():
    out = read("repl_output.txt")
    assert "Alice" in out, "the user_name parameter should reach setup → self → the template"
    assert "en" in out, "the locale parameter (--locale en) should reach setup"
    assert "Asia/Shanghai" in out, "timezone should come from main()'s provide → inject"
